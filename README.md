# dbagent — consultas locales con Ollama y SQL Server

Aplicación para Python 3.10+ que recupera metadatos, genera T-SQL con Ollama,
valida una política de lectura, ejecuta con límites, explica los resultados y
permite exportarlos desde Streamlit.

> **Seguro por defecto:** `config/sql_policy.yml` mantiene `enabled: false` y
> `allowed_objects: []`. No lo habilites hasta comprobar una cuenta de SQL
> Server con permisos exclusivamente de lectura y declarar cada objeto
> permitido. La opción ODBC `readonly` no sustituye permisos en el servidor.

## Instalación única (Windows / PowerShell)

1. Instala Python 3.10 o posterior, Ollama y **Microsoft ODBC Driver 17 for SQL
   Server**. Abre PowerShell en la raíz del repositorio.
2. Crea el entorno e instala el único conjunto reproducible de dependencias:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
ollama pull qwen3:30b-instruct
```

No es necesario activar el entorno. Las versiones directas están fijadas en
`requirements.txt`; ya no hay archivos de requisitos opcionales que puedan
instalar combinaciones diferentes.

## Configuración segura

1. Revisa `config/database.yml` **sin introducir contraseñas en el repositorio**.
   La configuración incluida usa autenticación integrada de Windows. Para una
   configuración local no versionada, copia el archivo como
   `config/database.local.yml` y úsalo únicamente con scripts que acepten una
   ruta explícita.
2. Antes de ejecutar SQL, comprueba manualmente servidor, base, identidad,
   permisos de lectura y ausencia de permisos de escritura. Puedes ejecutar
   `python main_db.py test`; esta operación sí contacta la base configurada.
3. Solo después, edita `config/sql_policy.yml`: enumera nombres
   `esquema.objeto` verificados en `allowed_objects` y cambia `enabled` a
   `true`. No uses el contexto recuperado como autorización.
4. `schema/business.yml` es el contrato de métricas mantenido por el usuario.
   No se regenera durante instalación ni arranque. `refresh_schema.py` consulta
   una base real y debe ejecutarse únicamente de forma consciente.

## Arranque

Preparar y validar un plan (no ejecuta SQL):

```powershell
.\.venv\Scripts\python.exe main.py "Ventas netas por producto en 1997"
```

Ejecutar desde CLI requiere además la política habilitada y confirmación
explícita mediante `--execute`:

```powershell
.\.venv\Scripts\python.exe main.py --execute "Ventas netas por producto en 1997"
```

La UI siempre muestra primero el plan y ofrece un botón separado para ejecutar
exactamente el plan mostrado, que vuelve a pasar por el validador:

```powershell
.\.venv\Scripts\python.exe -m streamlit run ui/app.py
```

Los modos heredados, solo LLM, siguen disponibles con `--agent` y `--pipeline`;
no admiten `--execute`.

## Pruebas y alcance de la validación

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall agents core tools ui tests
```

**Automáticas y simuladas (no acceden a una base ni a Ollama):** contrato HTTP y
fallos de Ollama; recuperación; pregunta ambigua/contexto insuficiente; plan y
rechazo de política; cero filas; truncamiento; ejecución con cliente falso;
respuesta; exportación y gráficas. La regresión de 1997 utiliza un fixture
sintético identificado como tal y compara el flujo contra otra consulta de
referencia: ambas usan `Order Details.UnitPrice`, descuento de línea y
`Orders.OrderDate`; no afirma ni inventa resultados de Northwind.

**Pendientes de validación local real:** disponibilidad/rendimiento del modelo,
driver ODBC, conectividad, catálogo de la instancia, permisos efectivos de la
cuenta de solo lectura, objetos que se autorizarán y resultados reales de
Northwind. No se ejecuta SQL generado contra una base real en la suite.

## Flujo integrado

`pregunta → recuperación → plan → validación → ejecución → explicación → exportación`

La ejecución falla de forma cerrada si la política está deshabilitada, el
objeto no está permitido o el AST no cumple los límites. Los resultados se
acotan, indican truncamiento y los exportadores trabajan solo con las filas ya
obtenidas; nunca vuelven a consultar la base.
