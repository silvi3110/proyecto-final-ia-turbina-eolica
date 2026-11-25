import json
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from io import BytesIO
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

import joblib
import numpy as np
import pandas as pd
import sklearn
from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

raiz = Path(__file__).resolve().parents[1]
ruta_modelo = raiz / "ciencia-datos" / "artefactos" / "modelo_turbina.joblib"
ruta_bd = raiz / "database" / "eolica.sqlite3"
ruta_schema = raiz / "database" / "schema.sql"
carpeta_cargas_anteriores = raiz / "database" / "cargas"
ruta_modelo_activo = raiz / "database" / "modelo_activo.joblib"
ruta_bloqueo_modelo = raiz / "database" / "modelo_activo.lock"
columna_fecha = "Date/Time"
columna_velocidad = "Wind Speed (m/s)"
columna_direccion = "Wind Direction (\N{DEGREE SIGN})"
columna_objetivo = "LV ActivePower (kW)"
formato_fecha = "%d %m %Y %H:%M"
entradas = [columna_velocidad, columna_direccion]
columnas_entrenamiento = [columna_fecha, *entradas, columna_objetivo]
tamano_maximo_csv = 10 * 1024 * 1024
tamano_maximo_cuerpo = tamano_maximo_csv + 1024 * 1024
reintentos_sqlite = 3
timeout_bloqueo_segundos = 5.0
timeout_reserva_segundos = 60.0
limite_retencion_predicciones = 10_000
bloqueo_entrenamiento = Lock()
seguridad = HTTPBasic()


class LimitarCuerpoMiddleware:
    def __init__(self, app, limite):
        self.app = app
        self.limite = limite

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        longitud = next(
            (valor for nombre, valor in scope.get("headers", []) if nombre.lower() == b"content-length"),
            None,
        )
        if longitud is not None and (not longitud.isdigit() or int(longitud) > self.limite):
            await self._respuesta_413(send)
            return

        cuerpo = bytearray()
        while True:
            mensaje = await receive()
            if mensaje["type"] == "http.disconnect":
                return
            fragmento = mensaje.get("body", b"")
            if len(cuerpo) + len(fragmento) > self.limite:
                await self._respuesta_413(send)
                return
            cuerpo.extend(fragmento)
            if not mensaje.get("more_body", False):
                break

        entregado = False

        async def recibir_reproducido():
            nonlocal entregado
            if not entregado:
                entregado = True
                return {"type": "http.request", "body": bytes(cuerpo), "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, recibir_reproducido, send)

    async def _respuesta_413(self, send):
        cuerpo = json.dumps({"detail": "El cuerpo de la solicitud es demasiado grande."}).encode()
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(cuerpo)).encode())],
        })
        await send({"type": "http.response.body", "body": cuerpo})


app = FastAPI(title="API de potencia eólica")
app.add_middleware(LimitarCuerpoMiddleware, limite=tamano_maximo_cuerpo)


def inicializar_bd():
    with closing(sqlite3.connect(ruta_bd)) as conexion:
        conexion.executescript(ruta_schema.read_text(encoding="utf-8"))
        columnas_predicciones = {fila[1] for fila in conexion.execute("PRAGMA table_info(predicciones)")}
        if "clave_idempotencia" not in columnas_predicciones:
            conexion.execute("ALTER TABLE predicciones ADD COLUMN clave_idempotencia TEXT")
        columnas_cargas = {fila[1] for fila in conexion.execute("PRAGMA table_info(cargas_entrenamiento)")}
        if "clave_idempotencia" not in columnas_cargas:
            conexion.execute("ALTER TABLE cargas_entrenamiento ADD COLUMN clave_idempotencia TEXT")
        columnas_operaciones = {fila[1] for fila in conexion.execute("PRAGMA table_info(operaciones_idempotentes)")}
        if "reserva_epoch" not in columnas_operaciones:
            conexion.execute("ALTER TABLE operaciones_idempotentes ADD COLUMN reserva_epoch REAL")
        conexion.execute("UPDATE operaciones_idempotentes SET reserva_epoch = COALESCE(reserva_epoch, unixepoch())")
        conexion.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_predicciones_idempotencia ON predicciones (clave_idempotencia) WHERE clave_idempotencia IS NOT NULL")
        conexion.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cargas_idempotencia ON cargas_entrenamiento (clave_idempotencia) WHERE clave_idempotencia IS NOT NULL")
        conexion.commit()


inicializar_bd()


def conectar_bd():
    conexion = sqlite3.connect(ruta_bd, timeout=0.25)
    conexion.execute("PRAGMA busy_timeout = 250")
    conexion.execute("PRAGMA foreign_keys = ON")
    return conexion


def ejecutar_bd(operacion, escritura=False):
    for intento in range(reintentos_sqlite):
        try:
            with closing(conectar_bd()) as conexion:
                resultado = operacion(conexion)
                if escritura:
                    conexion.commit()
                return resultado
        except sqlite3.OperationalError as error:
            ocupado = any(palabra in str(error).lower() for palabra in ("locked", "busy"))
            if not ocupado or intento == reintentos_sqlite - 1:
                if ocupado:
                    raise HTTPException(status_code=503, detail="La base de datos está ocupada; inténtalo de nuevo.") from error
                raise
            time.sleep(0.05 * (intento + 1))


def usuario_autenticado(credenciales: HTTPBasicCredentials = Depends(seguridad)):
    usuario = os.getenv("API_USERNAME")
    contrasena = os.getenv("API_PASSWORD")
    if not usuario or not contrasena:
        raise HTTPException(status_code=503, detail="La autenticación no está configurada en el entorno.")
    if credenciales.username != usuario or credenciales.password != contrasena:
        raise HTTPException(status_code=401, detail="Credenciales inválidas.", headers={"WWW-Authenticate": "Basic"})
    return credenciales.username


def cargar_modelo_activo():
    if ruta_modelo_activo.is_file():
        try:
            return joblib.load(ruta_modelo_activo)
        except (EOFError, OSError, ValueError, KeyError) as error:
            raise HTTPException(status_code=503, detail="El modelo activo está dañado; restaura el respaldo del artefacto.") from error
    if not ruta_modelo.is_file():
        raise HTTPException(status_code=503, detail="Falta el modelo inicial; ejecuta el notebook desde el README.")
    artefacto = joblib.load(ruta_modelo)
    artefacto.setdefault("version_modelo", "v1")
    return artefacto


@contextmanager
def bloqueo_interproceso():
    inicio = time.monotonic()
    ruta_bloqueo_modelo.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta_bloqueo_modelo, "a+b") as archivo:
        archivo.seek(0, os.SEEK_END)
        if archivo.tell() == 0:
            archivo.write(b"0")
            archivo.flush()
        archivo.seek(0)
        if os.name == "nt":
            import msvcrt
            adquirido = False
            while time.monotonic() - inicio < timeout_bloqueo_segundos:
                try:
                    archivo.seek(0)
                    msvcrt.locking(archivo.fileno(), msvcrt.LK_NBLCK, 1)
                    adquirido = True
                    break
                except OSError:
                    time.sleep(0.05)
            if not adquirido:
                raise HTTPException(status_code=503, detail="El reentrenamiento está ocupado.")
        else:
            import fcntl
            adquirido = False
            while time.monotonic() - inicio < timeout_bloqueo_segundos:
                try:
                    fcntl.flock(archivo.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    adquirido = True
                    break
                except OSError:
                    time.sleep(0.05)
            if not adquirido:
                raise HTTPException(status_code=503, detail="El reentrenamiento está ocupado.")
        try:
            yield
        finally:
            archivo.seek(0)
            if os.name == "nt":
                msvcrt.locking(archivo.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(archivo.fileno(), fcntl.LOCK_UN)


@contextmanager
def bloqueo_reentrenamiento():
    if not bloqueo_entrenamiento.acquire(timeout=timeout_bloqueo_segundos):
        raise HTTPException(status_code=503, detail="El reentrenamiento está ocupado.")
    try:
        with bloqueo_interproceso():
            yield
    finally:
        bloqueo_entrenamiento.release()


def respuesta_prediccion(fila):
    return {"potencia_kw": float(fila[0])}


def recuperar_idempotencia_en_bd(conexion, alcance, clave):
    if alcance == "predict":
        fila = conexion.execute("SELECT potencia_kw FROM predicciones WHERE clave_idempotencia = ?", (clave,)).fetchone()
        return respuesta_prediccion(fila) if fila else None
    if alcance == "training-data":
        fila = conexion.execute("SELECT id_carga, (SELECT COUNT(*) FROM observaciones_entrenamiento o WHERE o.id_carga = c.id_carga) FROM cargas_entrenamiento c WHERE clave_idempotencia = ?", (clave,)).fetchone()
        return {"id_carga": str(UUID(hex=fila[0])), "filas": fila[1]} if fila else None
    return None


def iniciar_idempotencia(alcance, clave, recuperador=None):
    def iniciar(conexion):
        insercion = conexion.execute("INSERT OR IGNORE INTO operaciones_idempotentes (alcance, clave, estado, reserva_epoch) VALUES (?, ?, 'procesando', ?)", (alcance, clave, time.time()))
        reserva_nueva = insercion.rowcount == 1
        fila = conexion.execute("SELECT estado, respuesta_json, reserva_epoch FROM operaciones_idempotentes WHERE alcance = ? AND clave = ?", (alcance, clave)).fetchone()
        if reserva_nueva:
            return None
        if fila[0] == "completada":
            return json.loads(fila[1])
        if recuperador:
            respuesta = recuperador(conexion)
            if respuesta is not None:
                conexion.execute("UPDATE operaciones_idempotentes SET estado = 'completada', respuesta_json = ?, reserva_epoch = ? WHERE alcance = ? AND clave = ?", (json.dumps(respuesta, ensure_ascii=False), time.time(), alcance, clave))
                return respuesta
        if time.time() - float(fila[2] or 0) > timeout_reserva_segundos:
            conexion.execute("UPDATE operaciones_idempotentes SET reserva_epoch = ? WHERE alcance = ? AND clave = ?", (time.time(), alcance, clave))
            return None
        raise HTTPException(status_code=409, detail="La operación con esa clave sigue en curso.")
    return ejecutar_bd(iniciar, escritura=True)


def cancelar_idempotencia(alcance, clave):
    try:
        ejecutar_bd(lambda conexion: conexion.execute("DELETE FROM operaciones_idempotentes WHERE alcance = ? AND clave = ? AND estado = 'procesando'", (alcance, clave)), escritura=True)
    except HTTPException:
        pass


class DatosViento(BaseModel):
    velocidad_ms: float = Field(ge=0, allow_inf_nan=False)
    direccion_grados: float = Field(ge=0, lt=360, allow_inf_nan=False)


class ResultadoPrediccion(BaseModel):
    potencia_kw: float


class MetricasModelo(BaseModel):
    modelo: str
    version_modelo: str
    metricas_prueba: dict[str, float]


class PrediccionGuardada(DatosViento, ResultadoPrediccion):
    id: int
    version_modelo: str
    fecha_utc: str


class ArchivoEntrenamientoCargado(BaseModel):
    id_carga: UUID
    filas: int


class SolicitudReentrenamiento(BaseModel):
    id_carga: UUID


class ResultadoReentrenamiento(MetricasModelo):
    id_carga: UUID
    filas_entrenamiento: int
    filas_prueba: int


@app.post("/predict", response_model=ResultadoPrediccion)
def predecir(datos: DatosViento, _usuario: str = Depends(usuario_autenticado), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    del _usuario
    if idempotency_key:
        anterior = iniciar_idempotencia("predict", idempotency_key, lambda conexion: recuperar_idempotencia_en_bd(conexion, "predict", idempotency_key))
        if anterior is not None:
            return anterior
    try:
        activo = cargar_modelo_activo()
        entrada = pd.DataFrame([{columna_velocidad: datos.velocidad_ms, columna_direccion: datos.direccion_grados}])
        potencia_kw = round(float(activo["modelo"].predict(entrada[activo["entradas"]])[0]), 2)

        def guardar(conexion):
            if idempotency_key:
                conexion.execute("INSERT OR IGNORE INTO predicciones (velocidad_ms, direccion_grados, potencia_kw, version_modelo, clave_idempotencia) VALUES (?, ?, ?, ?, ?)", (datos.velocidad_ms, datos.direccion_grados, potencia_kw, activo["version_modelo"], idempotency_key))
                fila = conexion.execute("SELECT potencia_kw FROM predicciones WHERE clave_idempotencia = ?", (idempotency_key,)).fetchone()
                respuesta = respuesta_prediccion(fila)
                conexion.execute("UPDATE operaciones_idempotentes SET estado = 'completada', respuesta_json = ?, reserva_epoch = ? WHERE alcance = 'predict' AND clave = ?", (json.dumps(respuesta), time.time(), idempotency_key))
            else:
                conexion.execute("INSERT INTO predicciones (velocidad_ms, direccion_grados, potencia_kw, version_modelo) VALUES (?, ?, ?, ?)", (datos.velocidad_ms, datos.direccion_grados, potencia_kw, activo["version_modelo"]))
            conexion.execute("DELETE FROM predicciones WHERE id NOT IN (SELECT id FROM predicciones ORDER BY id DESC LIMIT ?)", (limite_retencion_predicciones,))
            return respuesta if idempotency_key else {"potencia_kw": potencia_kw}

        return ejecutar_bd(guardar, escritura=True)
    except Exception:
        if idempotency_key:
            cancelar_idempotencia("predict", idempotency_key)
        raise


@app.get("/metrics", response_model=MetricasModelo)
def consultar_metricas():
    activo = cargar_modelo_activo()
    return {"modelo": activo["nombre_modelo"], "version_modelo": activo["version_modelo"], "metricas_prueba": activo["metricas_prueba"]}


@app.get("/predictions", response_model=list[PrediccionGuardada])
def consultar_historial(response: Response, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), before_id: int | None = Query(None, ge=1), _usuario: str = Depends(usuario_autenticado)):
    del _usuario
    if page > 1 and before_id is None:
        raise HTTPException(status_code=422, detail="Usa before_id del resultado anterior para conservar una página estable.")
    def leer(conexion):
        conexion.row_factory = sqlite3.Row
        if before_id is None:
            return conexion.execute("SELECT id, velocidad_ms, direccion_grados, potencia_kw, version_modelo, fecha_utc FROM predicciones ORDER BY id DESC LIMIT ?", (page_size + 1,)).fetchall()
        return conexion.execute("SELECT id, velocidad_ms, direccion_grados, potencia_kw, version_modelo, fecha_utc FROM predicciones WHERE id < ? ORDER BY id DESC LIMIT ?", (before_id, page_size + 1)).fetchall()
    filas = ejecutar_bd(leer)
    hay_siguiente = len(filas) > page_size
    filas = filas[:page_size]
    response.headers["X-Page"] = str(page)
    response.headers["X-Has-Next"] = str(hay_siguiente).lower()
    if hay_siguiente and filas:
        response.headers["X-Next-Before-Id"] = str(filas[-1]["id"])
    return [dict(fila) for fila in filas]


def validar_csv(contenido):
    try:
        datos = pd.read_csv(BytesIO(contenido), encoding="utf-8-sig")
    except (UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        raise HTTPException(status_code=422, detail="El archivo debe ser un CSV válido en UTF-8, separado por comas.") from error
    faltantes = sorted(set(columnas_entrenamiento) - set(datos.columns))
    if faltantes:
        raise HTTPException(status_code=422, detail=f"Faltan columnas: {', '.join(faltantes)}.")
    datos = datos[columnas_entrenamiento].copy()
    if len(datos) < 50 or datos.isna().any().any():
        raise HTTPException(status_code=422, detail="El CSV debe contener al menos 50 registros completos.")
    try:
        datos[columna_fecha] = pd.to_datetime(datos[columna_fecha], format=formato_fecha, errors="raise")
        datos[[*entradas, columna_objetivo]] = datos[[*entradas, columna_objetivo]].apply(pd.to_numeric, errors="raise")
        valores = datos[[*entradas, columna_objetivo]].to_numpy(dtype=float)
    except (ValueError, TypeError, OverflowError) as error:
        raise HTTPException(status_code=422, detail="Revisa los valores numéricos y el formato de fecha DD MM AAAA HH:MM.") from error
    if datos[columna_fecha].duplicated().any() or not np.isfinite(valores).all() or (datos[columna_velocidad] < 0).any() or (datos[columna_direccion] < 0).any() or (datos[columna_direccion] >= 360).any():
        raise HTTPException(status_code=422, detail="Revisa las fechas, los valores numéricos y sus rangos.")
    return datos


@app.post("/training-data", response_model=ArchivoEntrenamientoCargado, status_code=201)
def cargar_datos_entrenamiento(archivo: UploadFile = File(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), _usuario: str = Depends(usuario_autenticado)):
    del _usuario
    if not (archivo.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Selecciona un archivo .csv.")
    contenido = archivo.file.read(tamano_maximo_csv + 1)
    if len(contenido) > tamano_maximo_csv:
        raise HTTPException(status_code=413, detail="El CSV supera los 10 MiB.")
    if idempotency_key:
        anterior = iniciar_idempotencia("training-data", idempotency_key, lambda conexion: recuperar_idempotencia_en_bd(conexion, "training-data", idempotency_key))
        if anterior is not None:
            return anterior
    try:
        datos = validar_csv(contenido)
        id_carga = uuid4()
        filas = [(id_carga.hex, fecha.isoformat(timespec="minutes"), float(velocidad), float(direccion), float(potencia)) for fecha, velocidad, direccion, potencia in datos.itertuples(index=False, name=None)]
        def guardar(conexion):
            if idempotency_key:
                conexion.execute("INSERT OR IGNORE INTO cargas_entrenamiento (id_carga, clave_idempotencia) VALUES (?, ?)", (id_carga.hex, idempotency_key))
                existente = conexion.execute("SELECT id_carga FROM cargas_entrenamiento WHERE clave_idempotencia = ?", (idempotency_key,)).fetchone()
                id_real = existente[0]
                if id_real == id_carga.hex:
                    conexion.executemany("INSERT OR IGNORE INTO observaciones_entrenamiento (id_carga, fecha_medicion, velocidad_ms, direccion_grados, potencia_real_kw) VALUES (?, ?, ?, ?, ?)", filas)
                cuenta = conexion.execute("SELECT COUNT(*) FROM observaciones_entrenamiento WHERE id_carga = ?", (id_real,)).fetchone()[0]
                respuesta = {"id_carga": str(UUID(hex=id_real)), "filas": cuenta}
                conexion.execute("UPDATE operaciones_idempotentes SET estado = 'completada', respuesta_json = ?, reserva_epoch = ? WHERE alcance = 'training-data' AND clave = ?", (json.dumps(respuesta), time.time(), idempotency_key))
                return respuesta
            conexion.execute("INSERT INTO cargas_entrenamiento (id_carga) VALUES (?)", (id_carga.hex,))
            conexion.executemany("INSERT INTO observaciones_entrenamiento (id_carga, fecha_medicion, velocidad_ms, direccion_grados, potencia_real_kw) VALUES (?, ?, ?, ?, ?)", filas)
            return {"id_carga": str(id_carga), "filas": len(filas)}
        return ejecutar_bd(guardar, escritura=True)
    except Exception:
        if idempotency_key:
            cancelar_idempotencia("training-data", idempotency_key)
        raise


def leer_datos_entrenamiento(id_carga: UUID):
    filas = ejecutar_bd(lambda conexion: conexion.execute("SELECT fecha_medicion, velocidad_ms, direccion_grados, potencia_real_kw FROM observaciones_entrenamiento WHERE id_carga = ? ORDER BY fecha_medicion", (id_carga.hex,)).fetchall())
    if filas:
        datos = pd.DataFrame(filas, columns=columnas_entrenamiento)
        datos[columna_fecha] = pd.to_datetime(datos[columna_fecha], format="%Y-%m-%dT%H:%M")
        return datos
    ruta_csv = carpeta_cargas_anteriores / f"{id_carga.hex}.csv"
    if ruta_csv.is_file():
        return pd.read_csv(ruta_csv)
    raise HTTPException(status_code=404, detail="No existe esa carga de entrenamiento.")


def recuperar_reentrenamiento(clave, id_carga):
    activo = cargar_modelo_activo()
    if activo.get("id_carga") == str(id_carga) and activo.get("idempotency_key") == clave:
        return {"id_carga": str(id_carga), "modelo": activo["nombre_modelo"], "version_modelo": activo["version_modelo"], "metricas_prueba": activo["metricas_prueba"], "filas_entrenamiento": activo["filas_entrenamiento"], "filas_prueba": activo["filas_prueba"]}
    return None


@app.post("/retrain", response_model=ResultadoReentrenamiento)
def reentrenar(solicitud: SolicitudReentrenamiento, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), _usuario: str = Depends(usuario_autenticado)):
    del _usuario
    if idempotency_key:
        anterior = iniciar_idempotencia("retrain", idempotency_key, lambda conexion: recuperar_reentrenamiento(idempotency_key, solicitud.id_carga))
        if anterior is not None:
            return anterior
    publicado = False
    try:
        datos = leer_datos_entrenamiento(solicitud.id_carga)
        datos[columna_fecha] = pd.to_datetime(datos[columna_fecha], format=formato_fecha, errors="raise")
        datos = datos.sort_values(columna_fecha, kind="stable").reset_index(drop=True)
        with bloqueo_reentrenamiento():
            corte = int(len(datos) * 0.8)
            entrenamiento, prueba = datos.iloc[:corte], datos.iloc[corte:]
            modelo = HistGradientBoostingRegressor(max_iter=150, early_stopping=False, random_state=42)
            modelo.fit(entrenamiento[entradas], entrenamiento[columna_objetivo])
            estimadas = modelo.predict(prueba[entradas])
            reales = prueba[columna_objetivo]
            metricas = {"MAE (kW)": float(mean_absolute_error(reales, estimadas)), "RMSE (kW)": float(mean_squared_error(reales, estimadas) ** 0.5), "R\u00b2": float(r2_score(reales, estimadas))}
            if not all(np.isfinite(valor) for valor in metricas.values()):
                raise HTTPException(status_code=422, detail="No se obtuvieron métricas finitas con este CSV.")
            actual = cargar_modelo_activo()
            nueva_version = f"v{int(actual['version_modelo'][1:]) + 1}"
            nuevo = {"modelo": modelo, "nombre_modelo": "Gradient boosting", "version_modelo": nueva_version, "entradas": entradas, "objetivo": columna_objetivo, "metricas_prueba": metricas, "fecha_corte": prueba.iloc[0][columna_fecha], "version_sklearn": sklearn.__version__, "id_carga": str(solicitud.id_carga), "idempotency_key": idempotency_key, "filas_entrenamiento": len(entrenamiento), "filas_prueba": len(prueba)}
            temporal = ruta_modelo_activo.with_name(f"modelo_activo.{os.getpid()}.tmp")
            joblib.dump(nuevo, temporal)
            temporal.replace(ruta_modelo_activo)
            publicado = True
        respuesta = {"id_carga": str(solicitud.id_carga), "modelo": nuevo["nombre_modelo"], "version_modelo": nueva_version, "metricas_prueba": metricas, "filas_entrenamiento": len(entrenamiento), "filas_prueba": len(prueba)}
        if idempotency_key:
            ejecutar_bd(lambda conexion: conexion.execute("UPDATE operaciones_idempotentes SET estado = 'completada', respuesta_json = ?, reserva_epoch = ? WHERE alcance = 'retrain' AND clave = ?", (json.dumps(respuesta), time.time(), idempotency_key)), escritura=True)
        return respuesta
    except Exception:
        if idempotency_key and not publicado:
            cancelar_idempotencia("retrain", idempotency_key)
        raise
