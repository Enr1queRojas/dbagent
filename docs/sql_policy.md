# Política de validación de SQL generado

`SQLValidator` analiza T-SQL localmente con SQLGlot y **no ejecuta** la consulta. La
configuración inicial (`config/sql_policy.yml`) está deshabilitada y no autoriza
objetos. En ese estado toda validación devuelve `allowed=false` con una explicación.

## Activación

1. Instale las dependencias fijadas: `pip install -r requirements.txt`.
2. Revise el catálogo y agregue cada tabla, con sus dos partes, a la lista explícita:

   ```yaml
   enabled: true
   allowed_objects:
     - dbo.Customers
     - dbo.Orders
   ```

   El contexto seleccionado por el retriever **no concede autorización**: un objeto
   debe aparecer tanto en el contexto JSON como en `allowed_objects`.
3. Mantenga `allow_views: false` salvo que se hayan revisado la definición y todas
   las dependencias de cada vista. Incluir una vista en la lista no revisa ni limita
   automáticamente los objetos que usa internamente.
4. Ajuste, de forma conservadora, las listas de funciones escalares/de tabla y los
   límites. Una construcción que el analizador no pueda resolver se rechaza.

El plan tiene la forma `{"sql": "... WHERE id = ?", "params": [10248]}` y se
valida mediante `SQLValidator().validate(plan, context)`. El validador no reescribe,
repara ni ejecuta el SQL rechazado.

## Límite de seguridad

El parser y esta lista de autorización son defensa en profundidad, **no sustituyen
los permisos de SQL Server**. La identidad que ejecute consultas de agentes debe
tener únicamente permisos de lectura sobre los objetos autorizados (idealmente una
cuenta/rol dedicado y probado). `readonly=True`, desactivar autocommit o ejecutar
`rollback` no convierten una conexión en solo lectura ni deshacen necesariamente
todos los efectos. También deben aplicarse límites de recursos, auditoría y tiempos
de espera en el servidor.
