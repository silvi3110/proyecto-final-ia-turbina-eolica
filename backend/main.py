import sqlite3
from contextlib import closing
from io import BytesIO
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

import joblib
import numpy as np
import pandas as pd
import sklearn
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

raiz = Path(__file__).resolve().parents[1]
ruta_modelo = raiz / "ciencia-datos" / "artefactos" / "modelo_turbina.joblib"
ruta_bd = raiz / "database" / "eolica.sqlite3"
ruta_schema = raiz / "database" / "schema.sql"
carpeta_cargas_anteriores = raiz / "database" / "cargas"
ruta_modelo_activo = raiz / "database" / "modelo_activo.joblib"

columna_fecha = "Date/Time"
columna_velocidad = "Wind Speed (m/s)"
columna_direccion = "Wind Direction (°)"
columna_objetivo = "LV ActivePower (kW)"
formato_fecha = "%d %m %Y %H:%M"
entradas = [columna_velocidad, columna_direccion]
columnas_entrenamiento = [columna_fecha, *entradas, columna_objetivo]
tamano_maximo_csv = 10 * 1024 * 1024

with closing(sqlite3.connect(ruta_bd)) as conexion:
    conexion.executescript(ruta_schema.read_text(encoding="utf-8"))

if ruta_modelo_activo.is_file():
    artefacto_activo = joblib.load(ruta_modelo_activo)
else:
    artefacto_activo = joblib.load(ruta_modelo)
    artefacto_activo["version_modelo"] = "v1"

bloqueo_entrenamiento = Lock()
app = FastAPI(title="API de potencia eólica")


class DatosViento(BaseModel):
    velocidad_ms: float = Field(ge=0, allow_inf_nan=False)
    direccion_grados: float = Field(ge=0, lt=360, allow_inf_nan=False)


class ResultadoPrediccion(BaseModel):
    potencia_kw: float = Field(description="Potencia activa estimada en kW")


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


@app.post(
    "/predict",
    response_model=ResultadoPrediccion,
    summary="Estimar la potencia activa y guardar la predicción",
)
def predecir(datos: DatosViento):
    modelo_en_uso = artefacto_activo
    entrada = pd.DataFrame([
        {
            columna_velocidad: datos.velocidad_ms,
            columna_direccion: datos.direccion_grados,
        }
    ])
    estimacion = modelo_en_uso["modelo"].predict(
        entrada[modelo_en_uso["entradas"]]
    )[0]
    potencia_kw = round(float(estimacion), 2)

    with closing(sqlite3.connect(ruta_bd)) as conexion, conexion:
        conexion.execute(
            """
            INSERT INTO predicciones
                (velocidad_ms, direccion_grados, potencia_kw, version_modelo)
            VALUES (?, ?, ?, ?)
            """,
            (
                datos.velocidad_ms,
                datos.direccion_grados,
                potencia_kw,
                modelo_en_uso["version_modelo"],
            ),
        )

    return {"potencia_kw": potencia_kw}


@app.get(
    "/metrics",
    response_model=MetricasModelo,
    summary="Consultar métricas del modelo activo",
)
def consultar_metricas():
    modelo_en_uso = artefacto_activo
    return {
        "modelo": modelo_en_uso["nombre_modelo"],
        "version_modelo": modelo_en_uso["version_modelo"],
        "metricas_prueba": modelo_en_uso["metricas_prueba"],
    }


@app.get(
    "/predictions",
    response_model=list[PrediccionGuardada],
    summary="Consultar el historial de predicciones",
)
def consultar_historial():
    with closing(sqlite3.connect(ruta_bd)) as conexion:
        conexion.row_factory = sqlite3.Row
        filas = conexion.execute(
            """
            SELECT id, velocidad_ms, direccion_grados, potencia_kw,
                   version_modelo, fecha_utc
            FROM predicciones
            ORDER BY id DESC
            """
        ).fetchall()

    return [dict(fila) for fila in filas]


@app.post(
    "/training-data",
    response_model=ArchivoEntrenamientoCargado,
    status_code=201,
    summary="Validar un CSV y guardar sus observaciones en la base",
)
def cargar_datos_entrenamiento(archivo: UploadFile = File(...)):
    if not (archivo.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="Selecciona un archivo .csv.")

    contenido = archivo.file.read(tamano_maximo_csv + 1)
    if len(contenido) > tamano_maximo_csv:
        raise HTTPException(status_code=413, detail="El CSV supera los 10 MiB.")

    try:
        datos = pd.read_csv(BytesIO(contenido), encoding="utf-8-sig")
    except (UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as error:
        raise HTTPException(
            status_code=422,
            detail="El archivo debe ser un CSV válido en UTF-8, separado por comas.",
        ) from error

    faltantes = sorted(set(columnas_entrenamiento) - set(datos.columns))
    if faltantes:
        raise HTTPException(
            status_code=422, detail=f"Faltan columnas: {', '.join(faltantes)}."
        )

    datos = datos[columnas_entrenamiento].copy()
    if len(datos) < 50 or datos.isna().any().any():
        raise HTTPException(
            status_code=422,
            detail="El CSV debe contener al menos 50 registros completos.",
        )

    numericas = [*entradas, columna_objetivo]
    try:
        datos[columna_fecha] = pd.to_datetime(
            datos[columna_fecha], format=formato_fecha, errors="raise"
        )
        datos[numericas] = datos[numericas].apply(pd.to_numeric, errors="raise")
        valores = datos[numericas].to_numpy(dtype=float)
    except (ValueError, TypeError, OverflowError) as error:
        raise HTTPException(
            status_code=422,
            detail="Revisa los valores numéricos y el formato de fecha DD MM AAAA HH:MM.",
        ) from error

    if datos[columna_fecha].isna().any() or datos[columna_fecha].duplicated().any():
        raise HTTPException(
            status_code=422, detail="El CSV contiene fechas inválidas o repetidas."
        )

    if (
        not np.isfinite(valores).all()
        or (datos[columna_velocidad] < 0).any()
        or (datos[columna_direccion] < 0).any()
        or (datos[columna_direccion] >= 360).any()
    ):
        raise HTTPException(
            status_code=422,
            detail="Revisa los valores numéricos y los rangos de velocidad y dirección.",
        )

    id_carga = uuid4()
    filas = [
        (
            id_carga.hex,
            fecha.isoformat(timespec="minutes"),
            float(velocidad),
            float(direccion),
            float(potencia),
        )
        for fecha, velocidad, direccion, potencia in datos.itertuples(
            index=False, name=None
        )
    ]

    with closing(sqlite3.connect(ruta_bd)) as conexion:
        conexion.execute("PRAGMA foreign_keys = ON")
        with conexion:
            conexion.execute(
                "INSERT INTO cargas_entrenamiento (id_carga) VALUES (?)",
                (id_carga.hex,),
            )
            conexion.executemany(
                """
                INSERT INTO observaciones_entrenamiento (
                    id_carga, fecha_medicion, velocidad_ms,
                    direccion_grados, potencia_real_kw
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                filas,
            )

    return {"id_carga": id_carga, "filas": len(filas)}


def leer_datos_entrenamiento(id_carga: UUID) -> pd.DataFrame:
    with closing(sqlite3.connect(ruta_bd)) as conexion:
        filas = conexion.execute(
            """
            SELECT fecha_medicion, velocidad_ms, direccion_grados, potencia_real_kw
            FROM observaciones_entrenamiento
            WHERE id_carga = ?
            ORDER BY fecha_medicion
            """,
            (id_carga.hex,),
        ).fetchall()

    if filas:
        datos = pd.DataFrame(filas, columns=columnas_entrenamiento)
        datos[columna_fecha] = pd.to_datetime(
            datos[columna_fecha], format="%Y-%m-%dT%H:%M"
        ).dt.strftime(formato_fecha)
        return datos

    # Compatibilidad con cargas validadas antes de guardarlas en SQLite.
    ruta_csv = carpeta_cargas_anteriores / f"{id_carga.hex}.csv"
    if ruta_csv.is_file():
        return pd.read_csv(ruta_csv)

    raise HTTPException(
        status_code=404,
        detail="No existe esa carga de entrenamiento.",
    )


@app.post(
    "/retrain",
    response_model=ResultadoReentrenamiento,
    summary="Reentrenar el modelo con las observaciones de una carga",
)
def reentrenar(solicitud: SolicitudReentrenamiento):
    global artefacto_activo

    datos = leer_datos_entrenamiento(solicitud.id_carga)

    with bloqueo_entrenamiento:
        # Las observaciones se reciben en orden cronológico.
        corte = int(len(datos) * 0.8)
        entrenamiento = datos.iloc[:corte]
        prueba = datos.iloc[corte:]

        modelo = HistGradientBoostingRegressor(
            max_iter=150,
            early_stopping=False,
            random_state=42,
        )
        modelo.fit(entrenamiento[entradas], entrenamiento[columna_objetivo])
        reales = prueba[columna_objetivo]
        estimadas = modelo.predict(prueba[entradas])

        metricas = {
            "MAE (kW)": float(mean_absolute_error(reales, estimadas)),
            "RMSE (kW)": float(mean_squared_error(reales, estimadas) ** 0.5),
            "R²": float(r2_score(reales, estimadas)),
        }
        if not all(np.isfinite(valor) for valor in metricas.values()):
            raise HTTPException(
                status_code=422, detail="No se obtuvieron métricas finitas con este CSV."
            )

        nueva_version = f"v{int(artefacto_activo['version_modelo'][1:]) + 1}"
        nuevo_artefacto = {
            "modelo": modelo,
            "nombre_modelo": "Gradient boosting",
            "version_modelo": nueva_version,
            "entradas": entradas,
            "objetivo": columna_objetivo,
            "metricas_prueba": metricas,
            "fecha_corte": prueba.iloc[0][columna_fecha],
            "version_sklearn": sklearn.__version__,
        }

        ruta_temporal = ruta_modelo_activo.with_name("modelo_activo.joblib.tmp")
        try:
            joblib.dump(nuevo_artefacto, ruta_temporal)
            ruta_temporal.replace(ruta_modelo_activo)
        finally:
            ruta_temporal.unlink(missing_ok=True)

        artefacto_activo = nuevo_artefacto

    return {
        "id_carga": solicitud.id_carga,
        "modelo": nuevo_artefacto["nombre_modelo"],
        "version_modelo": nueva_version,
        "metricas_prueba": metricas,
        "filas_entrenamiento": len(entrenamiento),
        "filas_prueba": len(prueba),
    }