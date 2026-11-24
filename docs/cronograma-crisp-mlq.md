# Cronograma CRISP-ML(Q)

**Proyecto:** Predicción de potencia activa de una turbina eólica  
**Tipo de aprendizaje:** Regresión supervisada  
**Dataset:** Wind Turbine SCADA (`T1(1).csv`)  
**Variable objetivo:** `LV ActivePower (kW)`

| Fase | Fecha prevista | Actividad | Evidencia y verificación de calidad |
|---|---|---|---|
| Comprensión del problema y los datos | Día 1, mañana | Definir qué se quiere predecir y documentar el origen del dataset. | Ficha del dataset con fuente, variable objetivo, unidades y verificación de que es nuevo para la materia. |
| Preparación de datos | Día 1, mañana | Revisar columnas, fechas, faltantes y duplicados. Separar los datos en orden cronológico. | Notebook de exploración con los períodos de entrenamiento y prueba identificados. |
| Modelado | Día 1, tarde | Entrenar un modelo de referencia y un modelo candidato que se pueda reproducir y exportar. | Código de entrenamiento, configuración y modelo exportado. |
| Evaluación | Día 1, tarde | Comparar MAE y RMSE en kW y R² sobre el mismo período de prueba. Justificar la selección del modelo. | Tabla de métricas y explicación de si el candidato mejora la referencia. |
| Despliegue e integración | Día 2, mañana | Integrar el modelo en la API REST, guardar las predicciones en la base de datos y conectar el cliente web. | Código del servicio y del cliente, Swagger/OpenAPI, diagrama y script de base de datos, y prueba de las cinco operaciones desde la interfaz. |
| Monitoreo y mantenimiento | Día 2, tarde | Consultar el historial, cargar un CSV actualizado y solicitar el reentrenamiento. | Historial con entradas y resultados, métricas por versión y evidencia de la evaluación del modelo reentrenado antes de activarlo. |
