import sqlite3
from contextlib import closing
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI
from pydantic import BaseModel, Field

raiz = Path(__file__).resolve().parents[1]
ruta_modelo = raiz / "ciencia-datos" / "artefactos" / "modelo_turbina.joblib"
ruta_bd = raiz / "database" / "eolica.sqlite3"
ruta_schema = raiz / "database" / "schema.sql"

artefacto = joblib.load(ruta_modelo)
version_modelo = "v1"

with closing(sqlite3.connect(ruta_bd)) as conexion:
    conexion.executescript(ruta_schema.read_text(encoding="utf-8"))

app = FastAPI(title="API de potencia eólica")


class DatosViento(BaseModel):
    velocidad_ms: float = Field(ge=0, allow_inf_nan=False)
    direccion_grados: float = Field(ge=0, lt=360, allow_inf_nan=False)


class ResultadoPrediccion(BaseModel):
    potencia_kw: float = Field(description="Potencia activa estimada en kW")


class MetricasModelo(BaseModel):
    modelo: str
    metricas_prueba: dict[str, float]


class PrediccionGuardada(DatosViento, ResultadoPrediccion):
    id: int
    version_modelo: str
    fecha_utc: str


@app.post(
    "/predict",
    response_model=ResultadoPrediccion,
    summary="Estimar la potencia activa y guardar la predicción",
)
def predecir(datos: DatosViento):
    entrada = pd.DataFrame([
        {
            "Wind Speed (m/s)": datos.velocidad_ms,
            "Wind Direction (°)": datos.direccion_grados,
        }
    ])
    potencia = artefacto["modelo"].predict(entrada[artefacto["entradas"]])[0]
    potencia_kw = round(float(potencia), 2)

    with closing(sqlite3.connect(ruta_bd)) as conexion, conexion:
        conexion.execute(
            """
            INSERT INTO predicciones
                (velocidad_ms, direccion_grados, potencia_kw, version_modelo)
            VALUES (?, ?, ?, ?)
            """,
            (datos.velocidad_ms, datos.direccion_grados, potencia_kw, version_modelo),
        )

    return {"potencia_kw": potencia_kw}


@app.get(
    "/metrics",
    response_model=MetricasModelo,
    summary="Consultar métricas del modelo",
)
def consultar_metricas():
    return {
        "modelo": artefacto["nombre_modelo"],
        "metricas_prueba": artefacto["metricas_prueba"],
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