# Interfaz local de conversación

Esta interfaz está pensada para que una persona sin conocimientos técnicos explore una base mediante preguntas en español. Muestra por separado los planes, las aclaraciones, los resultados, los bloqueos y los errores. El SQL queda en un panel técnico plegable.

## Instalación y ejecución

Desde la raíz del proyecto:

```bash
python -m pip install -r requirements.txt
python -m streamlit run ui/app.py
```

La UI espera que `core.application.create_application()` devuelva un objeto con un método `handle(request)`. Cada petición contiene `question`, `history` y `execute`. Primero se envía `execute=false`; una consulta planificada solo se ejecuta después de pulsar **Ejecutar consulta validada**. La segunda petición incluye `prepared_plan`, por lo que vuelve a validar y ejecuta exactamente el plan revisado en lugar de pedir otro al modelo. Si esa integración aún no existe, la pantalla lo indica y no inventa resultados.

El historial y los artefactos temporales son propios de la sesión. No se usan cachés globales para preguntas ni resultados. Las exportaciones CSV/XLSX y las gráficas solo se habilitan cuando está disponible `tools.report_tools.ReportTools`, y se crean únicamente por una acción explícita.

## Alcance y seguridad

Esta entrega es una aplicación **local y de un solo usuario**. No debe exponerse directamente en una red. La autenticación, la autorización multiusuario, el aislamiento entre usuarios de un despliegue compartido y el endurecimiento de producción están fuera de alcance.
