# Predicción de potencia activa de una turbina eólica 🌬️

Proyecto final de la Unidad 4. El objetivo es estimar la potencia activa
de una turbina eólica en kilovatios mediante regresión supervisada.

El proyecto utiliza el dataset Wind Turbine SCADA (`T1(1).csv`).
La variable que se busca predecir es `LV ActivePower (kW)`.

El desarrollo sigue la metodología CRISP-ML(Q) e incluye un servicio de IA
con API REST y una aplicación web cliente independiente. Desde la interfaz
se podrá predecir la potencia, consultar el historial y las métricas,
cargar un CSV de entrenamiento y solicitar el reentrenamiento del modelo.

## Plan de trabajo

Consulta el [cronograma CRISP-ML(Q)](docs/cronograma-crisp-mlq.md).
