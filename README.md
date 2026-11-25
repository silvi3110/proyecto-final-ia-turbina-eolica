# Predicción de potencia activa de una turbina eólica 🌬️

Proyecto final de la Unidad 4. El objetivo es estimar la potencia activa
de una turbina eólica en kilovatios mediante regresión supervisada.

El proyecto utiliza el dataset Wind Turbine SCADA (`T1(1).csv`).
La variable que se busca predecir es `LV ActivePower (kW)`.

El desarrollo sigue la metodología CRISP-ML(Q) e incluye un servicio de IA
con API REST y una aplicación web cliente independiente. Desde la interfaz
se podrá predecir la potencia, consultar el historial y las métricas,
cargar un CSV de entrenamiento y solicitar el reentrenamiento del modelo.

## API local

La API protege `POST /predict`, `GET /predictions`, `POST /training-data` y `POST /retrain` con
HTTP Basic. Define las credenciales fuera del repositorio antes de iniciar el
servidor:

```powershell
$env:API_USERNAME = "usuario-local"
$env:API_PASSWORD = "contraseña-local"
python -m uvicorn backend.main:app --reload
```

En Swagger (`/docs`), pulsa **Authorize**, introduce esas mismas credenciales
y prueba las cuatro operaciones protegidas. Las solicitudes repetibles pueden
incluir una cabecera `Idempotency-Key`; una repetición devuelve el resultado
original sin crear otra predicción, carga o versión.

El historial conserva las 10.000 predicciones más recientes. Para paginar de
forma estable, solicita la primera página y usa el valor `X-Next-Before-Id`
de la respuesta como `before_id` de la siguiente página. Si se envían nuevas
predicciones entre páginas, no se repiten ni se desplazan los resultados ya
vistos.

El modelo inicial está versionado en
`ciencia-datos/artefactos/modelo_turbina.joblib`. En una copia limpia, conserva
ese archivo o ejecuta el notebook desde la raíz del proyecto o desde
`ciencia-datos`; ambos directorios generan el artefacto en la misma ruta que
lee la API. Después inicia el servicio con Uvicorn y prueba `/metrics` y
`/predict` desde Swagger.

Para respaldar y restaurar de forma consistente, detén la API y copia juntos
`database/eolica.sqlite3` y `database/modelo_activo.joblib` (además del modelo
inicial versionado). Restaurar solo SQLite no restaura el modelo publicado;
la versión visible en `/metrics` debe coincidir con el archivo activo.

## Plan de trabajo

Consulta el [cronograma CRISP-ML(Q)](docs/cronograma-crisp-mlq.md).
