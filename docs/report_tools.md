# Exportación y gráficas deterministas

`ReportTools` recibe exclusivamente un `QueryData` ya obtenido. No contiene un
cliente de base de datos ni invoca un LLM. El contrato es:

```python
data = {
    "columns": ["mes", "importe"],
    "rows": [["enero", Decimal("10.20")]],
    "truncated": False,
}

tools = ReportTools(output_dir="directorio-controlado")
csv_file = tools.export(data, "csv", title="Ventas")
chart = tools.chart(data, {
    "kind": "bar", "x": "mes", "y": "importe", "title": "Ventas"
})
```

`columns`, `rows` y `truncated` son obligatorios y no se admiten otras claves
(en particular, no se acepta una ruta en los datos). Las columnas han de ser
textos únicos y no vacíos; cada fila debe tener exactamente el mismo número de
celdas. Los límites de filas, puntos, columnas y caracteres se configuran en
`config/reports.yml`. Rebasarlos produce un error que obliga al llamador a pedir
una reducción explícita: nunca se muestrean ni descartan filas silenciosamente.

## Representación de valores

| Valor de consulta | CSV | XLSX | Gráfica |
|---|---|---|---|
| `None` | campo vacío | celda vacía | se rechaza si es `x` o `y` |
| `Decimal` | texto decimal exacto | número si cabe en 15 dígitos; si no, texto | número |
| fecha/hora | ISO 8601 | tipo fecha, o texto ISO para `time` | etiqueta/eje |
| bytes | `base64:` seguido de Base64 | el mismo texto Base64 | se rechaza en ejes |
| texto que comienza por `=`, `+`, `-` o `@` | apóstrofo inicial | apóstrofo inicial | tratado como dato, no HTML |

CSV se escribe con BOM UTF-8, comillas conforme a CSV y finales de línea de
Excel. XLSX incluye encabezados destacados, autofiltro, fila inmovilizada y
anchos legibles. Los enteros y `Decimal` de más de 15 dígitos se guardan como
texto y generan una advertencia en el `Artifact`, evitando la pérdida de
precisión de Excel.

Una gráfica requiere `y` numérica y no nula; `scatter` requiere también `x`
numérica y no nula. `bar` y `line` aceptan como `x` texto, números o fechas. El
HTML incorpora Plotly completo, por lo que no necesita CDN ni Internet.

## Seguridad, rutas y truncamiento

El nombre final lo genera la aplicación con un UUID bajo `output_dir`; el título
solamente aporta un fragmento saneado y nunca se interpreta como ruta. Se usa
un archivo nuevo y no se sobrescribe uno existente. Los títulos y etiquetas se
entregan a Plotly como texto/datos, sin construir HTML ni código dinámico.

`Artifact` contiene `kind`, `path`, `title`, `row_count`, `truncated` y
`warnings`. Si `truncated` es verdadero, el nombre incluye `TRUNCADO`, el XLSX
lo indica en sus propiedades y pie de página, y la gráfica lo muestra en el
título. CSV lo comunica en el nombre y en la advertencia del artefacto para no
insertar una fila que alteraría los datos. El consumidor **debe mostrar esa
advertencia**. Estas herramientas nunca calculan ni presentan un “total
general”, especialmente cuando el resultado es parcial.
