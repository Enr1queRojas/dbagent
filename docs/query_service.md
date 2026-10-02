# Ejecución y redacción de consultas

`QueryService` es una interfaz **interna** entre el validador y la base de datos. Recibe exclusivamente una decisión estructurada con `allowed: true`, `sql` y `params`; esa bandera no constituye autenticación ni debe exponerse como autorización en una API pública. El servicio hace una sola llamada a `DatabaseClient.query()` y no reintenta errores ni timeouts.

`QueryData` mantiene las columnas en orden, las tuplas y tipos originales de las filas, el indicador de truncamiento y el tiempo medido por el cliente de base de datos.

`AnswerAgent` conserva el resultado completo en el llamador y solo incorpora al prompt una muestra limitada por `config/answer_agent.yml`. El prompt identifica las celdas como datos no confiables, prohíbe seguir sus instrucciones y exige comunicar muestras y truncamiento. Si el modelo falla, se devuelve únicamente un recuento determinista y los avisos aplicables.
