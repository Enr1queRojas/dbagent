# Orquestación de la aplicación

`core.application.Application` coordina recuperación, generación, validación y
ejecución mediante dependencias inyectadas. `create_application()` es el único
punto que importa las implementaciones reales; así, la orquestación se puede
probar aunque esos módulos todavía no estén disponibles.

## Contrato

`handle()` recibe un mapa con `question`, `execute` (booleano, `false` por
defecto) y opcionalmente `history` (lista). Al confirmar una vista previa, la UI
también devuelve el `prepared_plan` mostrado: se valida otra vez sin regenerarlo.
Devuelve siempre `Response` con:
`status`, `question`, `answer`, `clarification`, `plan`, `data`, `warnings`,
`error` y `executed`. Los estados posibles son `clarification`, `blocked`,
`planned`, `completed` y `error`.

Solo los últimos seis turnos y un máximo de 2.000 caracteres se incorporan a la
búsqueda de esquema. Un recorte produce un warning. Los avisos del recuperador,
validador y resultado también se propagan. Un error técnico se registra y se
devuelve como error público genérico: nunca como una respuesta válida ni con su
detalle interno.

## CLI

```bash
python main.py "Ventas por mes"             # valida y devuelve el plan
python main.py "Ventas por mes" --execute   # ejecuta y redacta la respuesta
python main.py "Ventas por mes" --debug     # diagnóstico, sin credenciales ni filas
```

`--agent` y `--pipeline` seleccionan explícitamente el modo antiguo basado solo
en Ollama. Ese modo no se combina con `--execute`; de este modo no se cambia su
semántica silenciosamente.
