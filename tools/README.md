# Herramientas (siguiente etapa)

Este directorio aún no contiene herramientas ejecutables.

Interfaces previstas: get_schema, run_readonly_query, export_report, render_chart.
El cliente actual no anuncia herramientas al modelo ni ejecuta sus salidas.

Al incorporar la base de datos:
- Credenciales en variables de entorno y usuario de BD con permisos de solo lectura.
- Permisos por usuario y tablas/vistas permitidas, aplicados fuera del prompt.
- Consultas con timeout y límites de filas/bytes; auditoría sin exponer datos sensibles.
- Validación SQL específica del dialecto; no confiar en comprobar que comienza con SELECT.
- Registro explícito de tools y validación de argumentos; nunca eval/exec del texto del modelo.
- Bucle de tool calling acotado; respuestas de herramientas asociadas a la llamada original.
- Cálculos y gráficas con código determinista sobre los resultados reales.
