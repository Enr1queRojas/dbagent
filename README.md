# Data Agents — base local con Ollama

Python 3.10+. Modelo: `qwen3:30b-instruct` (MoE, aproximadamente 19 GB descargados).

## Iniciar en Windows / PowerShell

Extrae el ZIP y abre una terminal en la carpeta `data_agents`.
Instala y abre Ollama: https://ollama.com/download/windows

```powershell
ollama pull qwen3:30b-instruct
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

No necesitas activar el entorno. También puedes seleccionar `.venv` como intérprete en VS Code.

```powershell
.\.venv\Scripts\python.exe main.py "Ayúdame a definir un reporte de ventas"
.\.venv\Scripts\python.exe main.py --agent analyst "¿Cómo calcularías el crecimiento mensual?"
.\.venv\Scripts\python.exe main.py --pipeline "Diseña un análisis de ventas por categoría"
ollama ps
```

## Responsabilidades

| Archivo | Función |
|---|---|
| main.py | Mensaje inicial, CLI y operación/secuencia de agentes |
| core/ollama_client.py | Conexión HTTP reutilizable, llamada, errores, tiempos y tokens |
| core/config.py | Lectura YAML, validación y variables de entorno |
| config/agents.yml | Modelo, parámetros y prompts por agente |
| agents/base.py | Composición de prompt y skills; comparte el cliente |
| skills/*.md | Procedimientos reutilizables en lenguaje natural |
| tools/ | Contrato previsto para funciones ejecutables; aún no implementadas |
| tests/test_client.py | Pruebas aisladas del contrato HTTP y fallos |

Edita `USER_MESSAGE` en main.py o pasa el mensaje por terminal. Edita los prompts en YAML.
Añade un agente bajo `agents` y selecciónalo con `--agent nombre`, sin tocar el cliente.
Las skills son archivos Markdown dentro de skills/, referenciados sin extensión en YAML.
Los logs no incluyen prompts ni respuestas. Salida textual: stdout; logs: stderr.
No se persiste historial entre ejecuciones. Cada agente recibe su propio prompt y mensaje.

`--pipeline` es una secuencia fija de tres llamadas, no un router autónomo. Comparte el modelo
sin cargar una copia por agente. Los resultados intermedios son borradores, no verificaciones.

## Alcance real

Esta versión consulta el LLM. **Todavía no se conecta a bases de datos ni genera reportes/gráficas.**
Los prompts lo declaran expresamente. Es la base de conexión y organización sobre la cual
implementar esos módulos cuando se conozcan motor, esquema y política de acceso.
No hay herramientas anunciadas al modelo; si devuelve una llamada a herramienta inesperada,
el cliente falla explícitamente en lugar de fingir ejecución.

## Configuración

`OLLAMA_BASE_URL` y `OLLAMA_MODEL` sobreescriben YAML. No guardar credenciales en YAML.
La API está en localhost por defecto. Para acceso remoto se requiere una capa de autenticación.
No se configura `think` porque se usa la variante Instruct.
`num_ctx=8192` es un punto de partida; incluye instrucciones, entrada y salida.
`num_predict=2048` limita generación; un aviso señala respuestas truncadas.
Los prompts largos pueden superar el contexto: esta base no implementa contabilidad exacta,
resúmenes ni RAG. El pipeline puede necesitar más contexto para borradores extensos.
Con 8 GB de VRAM, CPU/GPU combinado es esperado. No hay garantía de tokens/s sin medir tu equipo.

No se reintentan automáticamente POST fallidos: un timeout no prueba que la generación se detuvo.

## Validación

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas usan transporte simulado. La inferencia real se valida en tu PC con Ollama instalado.

## Próximo módulo

Conocer motor (PostgreSQL, SQL Server, MySQL, SQLite...), tablas/vistas disponibles y métricas.
Implementar primero catálogo y consulta de solo lectura, después reportes/gráficas y por último
routing y UI en lenguaje natural. La seguridad se aplica en las herramientas y en la BD,
no únicamente mediante instrucciones al LLM.

Referencia API: https://docs.ollama.com/api/chat
