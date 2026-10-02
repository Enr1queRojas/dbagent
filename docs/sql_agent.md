# Agente generador de SQL

`SQLAgent` transforma una pregunta y el contexto JSON compacto de `SchemaRetriever` en un
`QueryPlan` validado. **No ejecuta SQL ni abre conexiones a la base de datos.**

```python
from agents.sql_agent import SQLAgent

plan = SQLAgent(client=my_client).generate(question, retrieval.context, history=[])
if plan.clarification:
    print(plan.clarification)
else:
    print(plan.sql, plan.params)
```

Sin `client`, el agente carga la configuración general mediante `load_settings()`, crea un
`OllamaClient` para la llamada y lo cierra al terminar. Un cliente inyectado pertenece al llamador.

## Contrato y seguridad

El resultado contiene `sql` y `params`, o una `clarification`. El modelo Pydantic estricto rechaza
campos adicionales, parámetros no escalares, consultas múltiples, operaciones que no sean lectura,
`LIMIT` y desajustes entre `?` y parámetros. La respuesta debe ser un único objeto JSON; se admite
como máximo un bloque Markdown completo con etiqueta `json`. Una respuesta marcada como truncada
siempre falla.

El prompt y el máximo de reparaciones de formato (cero o uno) están en `config/sql_agent.yml`.
La reparación es otra generación del modelo, nunca ejecución o modificación del SQL recibido.
