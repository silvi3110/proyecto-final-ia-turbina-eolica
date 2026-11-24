CREATE TABLE IF NOT EXISTS predicciones (
    id INTEGER PRIMARY KEY,
    velocidad_ms REAL NOT NULL CHECK (velocidad_ms >= 0),
    direccion_grados REAL NOT NULL
        CHECK (direccion_grados >= 0 AND direccion_grados < 360),
    potencia_kw REAL NOT NULL,
    version_modelo TEXT NOT NULL,
    fecha_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);