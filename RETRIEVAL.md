# Recuperación local del esquema

Coloca core/schema_retriever.py en core/, config/retrieval.yml en config/ y main_schema.py
en la raíz junto a main.py. Conserva tus archivos actuales en schema/. No modifica business.yml.
No necesitas paquetes nuevos ni embeddings. No se llama a SQL Server ni a Ollama.

```powershell
python main_schema.py "ventas por producto y cliente"
python main_schema.py "inventario de productos por categoría"
```

Uso desde Python:
```python
from core.schema_retriever import SchemaRetriever
result = SchemaRetriever().retrieve("ventas por producto y cliente")
print(result.selected_objects)
print(result.warnings)
context = result.context
```

`context` es JSON compacto para adjuntar como datos al prompt, junto con la pregunta original.
Revisa warnings: si no hay objetos, pide aclaración; si hay rutas incompletas, amplía la recuperación.
No ejecutes SQL que mencione objetos/columnas no recuperados sin resolver antes esas dependencias.
main.py aún no está integrado: esta entrega permite probar y afinar la recuperación por separado.

## Qué hace

- Verifica el SHA256 del catálogo contra el manifiesto y que los tres orígenes coincidan.
- Lee business.yml en cada solicitud para reflejar tus ediciones sin reiniciar.
- Puntúa coincidencias léxicas en nombre, descripción, keywords, grain, role y columnas.
- Divide CamelCase, normaliza acentos e incorpora sinónimos configurables español/inglés.
- Selecciona top_k candidatos y añade tablas intermedias de rutas FK hasta max_join_hops.
- Preserva todos los pares de columnas de cada FK compuesta; no inventa joins de vistas.
- Incluye todas las columnas de cada objeto seleccionado. El recorte es por objetos completos,
  no por caracteres cortados a mitad de JSON ni por columnas que podrían hacer falta.
- Respeta max_objects y max_context_chars; emite advertencias si descarta candidatos.

Los scores son relevancia heurística, no probabilidades/confianza. Esto no es búsqueda semántica:
si no encuentra coincidencias, devuelve selección vacía. Los sinónimos son editables y pueden
introducir coincidencias demasiado amplias. Con business.yml vacío habrá ambigüedad entre tablas
y vistas de ventas; usa las descripciones/keywords para afinar y prueba preguntas representativas.
Las rutas mínimas por FK son candidatas, no una validación de granularidad o significado de negocio.
No se exige que todos los resultados estén conectados: warnings identifica candidatos sin ruta.

## Métricas opcionales

Para recuperar una métrica, usa este contrato dentro de business.yml (ejemplo conceptual,
valida la fórmula y definición según tu negocio):

```yaml
metrics:
  sales_amount:
    description: Importe de venta después de descuento, sin flete
    keywords: [ventas, importe, facturación]
    objects: ['[dbo].[Order Details]']
    formula: UnitPrice * Quantity * (1 - Discount)
```

Una métrica coincidente eleva la prioridad de sus objetos y solo se incluye si todos caben.
Una métrica sin objects válidos se omite con aviso. Las fórmulas son datos de documentación:
este módulo no las evalúa ni las valida. No renombres claves de objetos en business.yml.

## Contexto e integridad

16,000 caracteres no equivalen a 16,000 tokens. Reserva espacio para instrucciones, pregunta,
herramientas y respuesta dentro de num_ctx; la contabilidad exacta dependerá del tokenizer.
El límite se aplica a result.context, no al pretty print del CLI. Las advertencias y scores
son atributos separados para la aplicación. Las definiciones extensas pueden hacer que un
objeto completo no quepa: reduce notas o aumenta el presupuesto.

No registra preguntas ni resultados en archivos. No usa el catálogo como control de acceso.
Si refresh_schema está en curso, reintenta tras terminar. Evita editar business.yml a mitad
de una lectura; si hay YAML inválido se detiene sin modificar nada.

Pruebas: python -m unittest discover -s tests -p test_retriever.py -v

base_table_boost (2.0 por defecto) favorece tablas base frente a vistas; usa 1.0 para neutralizarlo.
