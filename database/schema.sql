CREATE TABLE IF NOT EXISTS predicciones (
    id INTEGER PRIMARY KEY,
    velocidad_ms REAL NOT NULL CHECK (velocidad_ms >= 0),
    direccion_grados REAL NOT NULL
        CHECK (direccion_grados >= 0 AND direccion_grados < 360),
    potencia_kw REAL NOT NULL,
    version_modelo TEXT NOT NULL,
    fecha_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cargas_entrenamiento (
    id_carga TEXT PRIMARY KEY NOT NULL,
    fecha_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS observaciones_entrenamiento (
    id_carga TEXT NOT NULL REFERENCES cargas_entrenamiento(id_carga),
    fecha_medicion TEXT NOT NULL,
    velocidad_ms REAL NOT NULL CHECK (velocidad_ms >= 0),
    direccion_grados REAL NOT NULL
        CHECK (direccion_grados >= 0 AND direccion_grados < 360),
    potencia_real_kw REAL NOT NULL,
    PRIMARY KEY (id_carga, fecha_medicion)
);