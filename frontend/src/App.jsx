import { useEffect, useState } from 'react'
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  CircleAlert,
  CircleCheck,
  CloudUpload,
  Gauge,
  RefreshCw,
  Wind,
} from 'lucide-react'

const API_ROOT = '/api'
const PAGE_SIZE = 8

async function apiRequest(path, { method = 'GET', body, idempotencyKey } = {}) {
  const headers = {}
  if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey
  if (body && !(body instanceof FormData)) headers['Content-Type'] = 'application/json'

  let response
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      response = await fetch(`${API_ROOT}${path}`, {
        method,
        headers,
        body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
      })
    } catch (error) {
      if (method !== 'GET' || attempt === 19) {
        throw new Error('No se pudo conectar con la API local. Comprueba que esté iniciada.', { cause: error })
      }
    }
    if (response?.ok || method !== 'GET' || response?.status < 500 || attempt === 19) break
    await new Promise((resolve) => setTimeout(resolve, 500))
  }

  const raw = await response.text()
  let result = null
  try { result = raw ? JSON.parse(raw) : null } catch { result = raw }
  if (!response.ok) {
    const detail = result?.detail
    const message = Array.isArray(detail)
      ? detail.map((item) => `${item.loc?.slice(-1)[0] ?? 'campo'}: ${item.msg}`).join('. ')
      : detail || (typeof result === 'string' ? result : `La API respondió ${response.status}.`)
    throw new Error(message)
  }
  return { data: result, headers: response.headers }
}

function idempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function formatNumber(value, digits = 2) {
  return Number(value).toLocaleString('es-ES', { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

function dateLabel(value) {
  const parsed = new Date(`${value.replace(' ', 'T')}Z`)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('es-ES', { dateStyle: 'medium', timeStyle: 'short' })
}

function MetricTile({ label, value, unit }) {
  return (
    <div className="metric-tile">
      <span className="metric-label">{label}</span>
      <strong>{value == null ? '—' : formatNumber(value)}</strong>
      {unit && <span className="metric-unit">{unit}</span>}
    </div>
  )
}

function StatusMessage({ kind = 'info', children }) {
  if (!children) return null
  const Icon = kind === 'success' ? CircleCheck : CircleAlert
  return <div className={`status-message ${kind}`} role="status"><Icon size={16} aria-hidden="true" /><span>{children}</span></div>
}

function App() {
  const [metrics, setMetrics] = useState(null)
  const [metricsError, setMetricsError] = useState('')
  const [speed, setSpeed] = useState('8.0')
  const [direction, setDirection] = useState('180')
  const [prediction, setPrediction] = useState(null)
  const [predictBusy, setPredictBusy] = useState(false)
  const [predictMessage, setPredictMessage] = useState('')
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [cursorStack, setCursorStack] = useState([null])
  const [hasNext, setHasNext] = useState(false)
  const [nextBeforeId, setNextBeforeId] = useState(null)
  const [uploadFile, setUploadFile] = useState(null)
  const [uploadBusy, setUploadBusy] = useState(false)
  const [uploadMessage, setUploadMessage] = useState('')
  const [acceptedUpload, setAcceptedUpload] = useState(null)
  const [retrainBusy, setRetrainBusy] = useState(false)
  const [retrainMessage, setRetrainMessage] = useState('')
  const [retrainResult, setRetrainResult] = useState(null)

  async function refreshMetrics() {
    setMetricsError('')
    try {
      const { data } = await apiRequest('/metrics')
      setMetrics(data)
      return data
    } catch (error) {
      setMetricsError(error.message)
      return null
    }
  }

  async function refreshHistory({ cursor = null, page = 1, reset = false } = {}) {
    setHistoryLoading(true)
    setHistoryError('')
    try {
      const params = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) })
      if (cursor != null) params.set('before_id', cursor)
      const { data, headers } = await apiRequest(`/predictions?${params}`)
      setHistory(data)
      setHasNext(headers.get('x-has-next') === 'true')
      setNextBeforeId(headers.get('x-next-before-id'))
      if (reset) setCursorStack([null])
      return data
    } catch (error) {
      setHistoryError(error.message)
      return null
    } finally {
      setHistoryLoading(false)
    }
  }

  useEffect(() => {
    refreshMetrics()
    refreshHistory({ cursor: null, page: 1, reset: true })
  }, [])

  async function submitPrediction(event) {
    event.preventDefault()
    setPredictMessage('')
    const windSpeed = Number(speed)
    const windDirection = Number(direction)
    if (!Number.isFinite(windSpeed) || windSpeed < 0) {
      setPredictMessage('La velocidad debe ser un número mayor o igual a 0 m/s.')
      return
    }
    if (!Number.isFinite(windDirection) || windDirection < 0 || windDirection >= 360) {
      setPredictMessage('La dirección debe estar entre 0° y menos de 360°.')
      return
    }
    setPredictBusy(true)
    try {
      const { data } = await apiRequest('/predict', {
        method: 'POST', idempotencyKey: idempotencyKey(),
        body: { velocidad_ms: windSpeed, direccion_grados: windDirection },
      })
      setPrediction({ ...data, speed: windSpeed, direction: windDirection })
      setPredictMessage('Predicción guardada en el historial.')
      await refreshHistory({ cursor: null, page: 1, reset: true })
    } catch (error) {
      setPredictMessage(error.message)
    } finally {
      setPredictBusy(false)
    }
  }

  async function submitUpload(event) {
    event.preventDefault()
    setUploadMessage('')
    setAcceptedUpload(null)
    if (!uploadFile) {
      setUploadMessage('Selecciona un archivo CSV.')
      return
    }
    const formData = new FormData()
    formData.append('archivo', uploadFile)
    setUploadBusy(true)
    try {
      const { data } = await apiRequest('/training-data', {
        method: 'POST', body: formData, idempotencyKey: idempotencyKey(),
      })
      setAcceptedUpload(data)
      setUploadMessage(`Carga aceptada: ${data.filas.toLocaleString('es-ES')} filas.`)
    } catch (error) {
      setUploadMessage(error.message)
    } finally {
      setUploadBusy(false)
    }
  }

  async function submitRetrain() {
    if (!acceptedUpload) return
    setRetrainBusy(true)
    setRetrainMessage('Entrenando y publicando el nuevo modelo. Esta operación puede tardar varios minutos.')
    setRetrainResult(null)
    try {
      const { data } = await apiRequest('/retrain', {
        method: 'POST', idempotencyKey: idempotencyKey(),
        body: { id_carga: acceptedUpload.id_carga },
      })
      setRetrainResult(data)
      setRetrainMessage(`Modelo ${data.version_modelo} publicado. Ejecuta una predicción para comprobar su versión en el historial.`)
      await refreshMetrics()
      await refreshHistory({ cursor: null, page: 1, reset: true })
    } catch (error) {
      setRetrainMessage(error.message)
    } finally {
      setRetrainBusy(false)
    }
  }

  const metricEntries = metrics?.metricas_prueba ? Object.entries(metrics.metricas_prueba) : []
  const metricOrder = ['MAE (kW)', 'RMSE (kW)', 'R²']
  metricEntries.sort(([a], [b]) => {
    const indexA = metricOrder.indexOf(a)
    const indexB = metricOrder.indexOf(b)
    return (indexA < 0 ? 99 : indexA) - (indexB < 0 ? 99 : indexB)
  })
  const currentPage = cursorStack.length
  const historyVersion = history[0]?.version_modelo

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#inicio" aria-label="Viento, inicio">
          <span className="brand-mark"><Wind size={20} strokeWidth={2.2} /></span>
          <span>VIENTO<span className="brand-dot">.</span></span>
        </a>
        <span className={`api-indicator ${metrics ? 'online' : metricsError ? 'offline' : ''}`}>
          <span className="indicator-dot" />
          {metrics ? 'API disponible' : metricsError ? 'API sin conexión' : 'Conectando API'}
        </span>
      </header>

      <main id="inicio" className="dashboard">
        <section className="page-heading">
          <div>
            <p className="eyebrow">CONTROL DE AEROGENERADOR <span> / </span> MODELO ACTIVO</p>
            <h1>Predicción de potencia</h1>
            <p className="page-subtitle">Estimación de potencia activa a partir de las condiciones del viento.</p>
          </div>
          <div className="model-chip">
            <span className="model-chip-label">MODELO EN SERVICIO</span>
            <strong>{metrics?.version_modelo ?? '—'}</strong>
          </div>
        </section>

        <section className="primary-grid" aria-label="Predicción y métricas">
          <form className="panel prediction-form" onSubmit={submitPrediction}>
            <div className="panel-heading">
              <div className="heading-icon teal-icon"><Gauge size={18} /></div>
              <div><h2>Nueva predicción</h2><p>Introduce las condiciones actuales</p></div>
              <span className="step-label">01 <span>/</span> 02</span>
            </div>
            <div className="input-grid">
              <label className="field-label" htmlFor="speed">Velocidad del viento <span>m/s</span>
                <input id="speed" type="number" min="0" step="any" required value={speed} onChange={(event) => setSpeed(event.target.value)} />
              </label>
              <label className="field-label" htmlFor="direction">Dirección del viento <span>grados</span>
                <input id="direction" type="number" min="0" max="359.999999" step="any" required value={direction} onChange={(event) => setDirection(event.target.value)} />
              </label>
            </div>
            <div className="direction-scale" aria-hidden="true"><span>0° N</span><span>90° E</span><span>180° S</span><span>270° O</span></div>
            <div className="form-footer">
              <span className="auth-hint">La predicción se añadirá al historial.</span>
              <button className="primary-button" type="submit" disabled={predictBusy}>
                {predictBusy ? <span className="spinner" /> : <Activity size={17} />}
                {predictBusy ? 'Calculando' : 'Estimar potencia'}
              </button>
            </div>
            <StatusMessage kind={predictMessage && !predictMessage.includes('guardada') ? 'error' : 'success'}>{predictMessage}</StatusMessage>
          </form>

          <section className="panel result-panel" aria-live="polite">
            <div className="result-topline"><span>RESULTADO ESTIMADO</span><Activity size={17} /></div>
            <div className="result-value">
              <strong>{prediction ? formatNumber(prediction.potencia_kw) : '—'}</strong>
              <span>kW</span>
            </div>
            <div className="result-rule"><span /></div>
            <div className="result-meta">
              <span>{prediction ? `${formatNumber(prediction.speed, 1)} m/s · ${formatNumber(prediction.direction, 1)}°` : 'Sin predicción reciente'}</span>
              {prediction && <span className="version-tag">{historyVersion ? `REGISTRADA ${historyVersion}` : 'REGISTRADA'}</span>}
            </div>
            <div className="result-watermark"><Wind size={132} strokeWidth={0.65} /></div>
          </section>
        </section>

        <section className="metrics-section" aria-labelledby="metrics-title">
          <div className="section-title-row">
            <div className="section-title"><Activity size={16} /><h2 id="metrics-title">Rendimiento del modelo</h2></div>
          </div>
          {metricsError ? <StatusMessage kind="error">{metricsError}</StatusMessage> : null}
          <div className="metrics-band">
            <div className="active-model">
              <span className="metric-label">MODELO ACTIVO</span>
              <strong>{metrics?.modelo ?? 'Esperando respuesta'}</strong>
              <span className="model-version">Versión {metrics?.version_modelo ?? '—'}</span>
            </div>
            {metricOrder.map((key) => {
              const entry = metricEntries.find(([label]) => label === key)
              return <MetricTile key={key} label={key} value={entry?.[1]} unit={entry ? (key.includes('kW') ? 'kW' : 'coeficiente') : null} />
            })}
            {metricEntries.filter(([key]) => !metricOrder.includes(key)).map(([key, value]) => <MetricTile key={key} label={key} value={value} />)}
          </div>
        </section>

        <section className="secondary-grid">
          <section className="panel history-panel" aria-labelledby="history-title">
            <div className="panel-heading section-panel-heading">
              <div className="heading-icon blue-icon"><ArrowDownToLine size={17} /></div>
              <div><h2 id="history-title">Historial de predicciones</h2><p>Registros guardados por el modelo</p></div>
            </div>
            {historyError ? <StatusMessage kind="error">{historyError}</StatusMessage> : (
              <>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Fecha</th><th>Velocidad</th><th>Dirección</th><th>Potencia</th><th>Modelo</th></tr></thead>
                    <tbody>
                      {history.map((item) => <tr key={item.id}>
                        <td>{dateLabel(item.fecha_utc)}</td><td>{formatNumber(item.velocidad_ms, 1)} m/s</td><td>{formatNumber(item.direccion_grados, 1)}°</td><td className="table-power">{formatNumber(item.potencia_kw)} kW</td><td><span className="version-tag">{item.version_modelo}</span></td>
                      </tr>)}
                    </tbody>
                  </table>
                  {!history.length && !historyLoading && <div className="empty-state">Todavía no hay predicciones guardadas.</div>}
                  {historyLoading && <div className="table-loading"><span className="spinner small" /> Cargando historial</div>}
                </div>
                <div className="pagination">
                  <span>Página {currentPage}</span>
                  <div>
                    <button className="icon-button" type="button" aria-label="Página anterior" title="Página anterior" disabled={currentPage <= 1 || historyLoading} onClick={() => {
                      const nextStack = cursorStack.slice(0, -1)
                      setCursorStack(nextStack)
                      refreshHistory({ cursor: nextStack.at(-1), page: nextStack.length })
                    }}><ArrowLeft size={16} /></button>
                    <button className="icon-button" type="button" aria-label="Página siguiente" title="Página siguiente" disabled={!hasNext || historyLoading} onClick={() => {
                      if (nextBeforeId == null) return
                      const nextStack = [...cursorStack, nextBeforeId]
                      setCursorStack(nextStack)
                      refreshHistory({ cursor: nextBeforeId, page: nextStack.length })
                    }}><ArrowRight size={16} /></button>
                  </div>
                </div>
              </>
            )}
          </section>

          <section className="panel training-panel" aria-labelledby="training-title">
            <div className="panel-heading section-panel-heading">
              <div className="heading-icon amber-icon"><CloudUpload size={17} /></div>
              <div><h2 id="training-title">Datos y reentrenamiento</h2><p>Actualiza el modelo con nuevos registros</p></div>
            </div>
            <form className="upload-form" onSubmit={submitUpload}>
              <label className={`file-drop ${uploadFile ? 'has-file' : ''}`} htmlFor="training-file">
                <input id="training-file" type="file" accept=".csv,text/csv" onChange={(event) => { setUploadFile(event.target.files?.[0] ?? null); setUploadMessage(''); setAcceptedUpload(null) }} />
                <span className="upload-icon"><CloudUpload size={20} /></span>
                <span className="file-copy"><strong>{uploadFile ? uploadFile.name : 'Selecciona un archivo CSV'}</strong><small>{uploadFile ? `${(uploadFile.size / 1024 / 1024).toFixed(2)} MiB` : 'Formato CSV · máximo 10 MiB'}</small></span>
                <span className="browse-link">Examinar</span>
              </label>
              <button className="secondary-button" type="submit" disabled={uploadBusy || !uploadFile}>
                {uploadBusy ? <span className="spinner small" /> : <CloudUpload size={16} />}{uploadBusy ? 'Cargando datos' : 'Cargar datos'}
              </button>
            </form>
            <StatusMessage kind={uploadMessage && !uploadMessage.startsWith('Carga aceptada') ? 'error' : 'success'}>{uploadMessage}</StatusMessage>
            {acceptedUpload && <div className="accepted-load"><span><CircleCheck size={15} /> Carga aceptada</span><code>{acceptedUpload.id_carga}</code><small>{acceptedUpload.filas.toLocaleString('es-ES')} filas disponibles</small></div>}
            <div className="retrain-block">
              <div><strong>Reentrenar y publicar</strong><span>El modelo activo se actualizará al finalizar.</span></div>
              <button className="retrain-button" type="button" disabled={!acceptedUpload || retrainBusy} onClick={submitRetrain}>
                {retrainBusy ? <span className="spinner small" /> : <RefreshCw size={15} />}{retrainBusy ? 'Entrenando' : 'Reentrenar'}
              </button>
            </div>
            <StatusMessage kind={retrainResult ? 'success' : retrainMessage && !retrainBusy ? 'error' : 'info'}>{retrainMessage}</StatusMessage>
            {retrainResult && <div className="training-result"><div><span>VERSIÓN</span><strong>{retrainResult.version_modelo}</strong></div><div><span>ENTRENAMIENTO</span><strong>{retrainResult.filas_entrenamiento.toLocaleString('es-ES')} filas</strong></div><div><span>PRUEBA</span><strong>{retrainResult.filas_prueba.toLocaleString('es-ES')} filas</strong></div></div>}
          </section>
        </section>

        <footer className="page-footer"><span>Potencia activa · kW</span><span>Predicción supervisada · turbina eólica</span></footer>
      </main>
    </div>
  )
}

export default App