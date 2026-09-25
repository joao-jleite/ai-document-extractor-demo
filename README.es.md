# AI Document Extractor (demo)

![Demo: una foto de una factura brasileña ficticia (DANFE) leída por Claude, validada y exportada a Excel y PDF](docs/demo.gif)

> **Proyecto de demostración.** Todos los documentos de ejemplo son ficticios: empresas, montos y claves de acceso se inventaron para este repositorio, y cada exportación lleva la marca **DEMO**. Los RUT/CNPJ son ejemplos de manual con dígito verificador válido (CNPJ `11.222.333/0001-81` y `11.444.777/0001-61`, RUT `11.111.111-1` y el ejemplo de formato de CNPJ alfanumérico `12.ABC.345/01DE-35`); cualquier coincidencia con un registro real es casual.

[English README](README.md) · [Video MP4](docs/demo.mp4) · Python 3.12 · FastAPI · Claude API · Pydantic · openpyxl · reportlab

El GIF se grabó con una llamada real a la API de Claude (`claude-sonnet-5`) sobre la foto de ejemplo. La espera del modelo va en cámara rápida, y un indicador en pantalla muestra la espera real. Es una llamada distinta de la ejecución de la tabla de abajo, así que su tiempo y sus tokens varían un poco (11,39 s, 8.566 / 1.025 tokens). Las capturas se tomaron en modo replay, así que muestran exactamente la ejecución guardada de la tabla. Las "Reader notes" son comentarios libres del propio modelo: la interfaz las muestra como información y nada las valida.

## Problema

Los equipos de compras y cuentas por pagar vuelven a digitar los datos de facturas (NF-e / DANFE de Brasil) y órdenes de compra en planillas y ERP. Los documentos llegan como PDF o fotos, en portugués, español o inglés, con formatos numéricos locales (`1.234,56`, `US$ 6.392,90`). Un dígito invertido en un total de línea o un RUT/CNPJ mal escrito se detecta recién al pagar o al conciliar.

## Solución

Se sube un PDF o una foto y se obtienen datos estructurados. En los tres documentos de ejemplo, la llamada al modelo tardó entre 9,9 y 11,4 segundos cada una (números abajo). Los cálculos y los RUT/CNPJ se verifican antes de usar los datos.

1. **Extraer.** Claude lee el PDF de forma nativa (texto y diseño) o la foto (visión) y responde en un esquema JSON definido con Pydantic (salida estructurada): encabezado, proveedor, comprador, ítems y totales.
2. **Validar.** Reglas de negocio sobre los datos tipados:
   - cantidad × precio unitario = total de la línea, en cada línea
   - suma de las líneas = subtotal impreso
   - subtotal − descuento + flete + seguro + otros cargos + impuestos = total general
   - dígitos verificadores de CNPJ (incluido el **CNPJ alfanumérico** de 2026), CPF y **RUT** chileno
   - clave de acceso de la NF-e: dígito verificador mod 11, y el CNPJ, año/mes y número dentro de la clave deben coincidir con el documento (también se aceptan claves de emisores con CNPJ alfanumérico)
   - fechas, campos obligatorios y moneda ISO 4217 (por ejemplo, CLP sin decimales)
3. **Exportar.** Planilla **XLSX** (hojas Header, Items y Validation), **informe PDF** y JSON, todos marcados como DEMO. Las salidas de los ejemplos incluidos dicen además que los datos son ficticios; las de sus propios documentos no.

El modelo transcribe lo impreso y nunca "corrige" un total, así la validación detecta errores del propio documento. La foto de ejemplo tiene un error intencional: la línea 3 dice `427,50` en vez de 25 × 18,90 = `472,50`. En la ejecución real de abajo, Claude transcribió el `427,50` tal como está impreso y la validación lo señaló dos veces: en la línea y en el subtotal.

## Resultados con los ejemplos incluidos

Una ejecución real por documento el 2026-09-25, modelo `claude-sonnet-5`, effort `medium`. Cada extracción se compara campo por campo con el resultado esperado (`samples/truth/`): 14 campos de encabezado, 4 campos por empresa (nombre, RUT/CNPJ, tipo, país), la cantidad de ítems y 6 campos por ítem. El texto se compara sin distinguir mayúsculas ni acentos, y los números e identificadores sin puntuación ni ceros a la izquierda (las reglas exactas están al inicio de `accuracy.md`). Una comparación estricta, sin normalizar nada, también está en el informe y da los mismos valores en esta ejecución. El informe completo está en [`examples/output/accuracy.md`](examples/output/accuracy.md), junto con el JSON, el XLSX y el PDF de cada ejecución.

| Documento | Campos correctos | Validación | Llamada al modelo | Tokens (entrada / salida) |
|---|---|---|---|---|
| `danfe-exemplo-industrial.pdf` | 53/53 | todo OK | 11,33 s | 7.617 / 1.035 |
| `foto-danfe-parafusos.jpg` | 47/47 | 2 alertas: total de la línea 3 y suma de ítems vs subtotal (el error intencional) | 11,37 s | 8.566 / 1.004 |
| `orden-compra-andina.pdf` | 53/53 | todo OK | 9,91 s | 6.293 / 879 |

La primera ejecución real no salió perfecta, y su informe se conserva en [`examples/output/accuracy-first-run.md`](examples/output/accuracy-first-run.md). En ese momento el prompt pedía la clave de la NF-e como "44 dígitos, sin espacios", y en las dos DANFE la clave volvió con 1 o 2 dígitos de menos (52/53 y 46/47 campos). La validación de la clave detectó los dos casos. Ahora la clave se copia tal como está impresa, con los espacios entre los grupos de 4 dígitos, y la tabla de arriba es la ejecución posterior a ese cambio.

Son tres documentos sintéticos y una ejecución por documento: muestran el flujo completo funcionando, no son un benchmark. Documentos reales pueden ser más difíciles, y para eso está la validación.

## Capturas

| Foto: la validación marca el error | Orden de compra en español (USD, RUT) | XLSX generado, hoja Items |
|---|---|---|
| ![Lista de validación de la foto, con dos alertas](docs/screenshot-1-photo-validation.png) | ![Resultado de la orden de compra, todo OK](docs/screenshot-2-purchase-order.png) | ![Hoja Items del XLSX generado, con la línea 3 marcada](docs/screenshot-3-xlsx-items.png) |

## Stack

| Capa | Elección |
|---|---|
| Extracción | [Claude API](https://docs.claude.com) con el SDK oficial `anthropic`: PDF nativo, visión para fotos, salida estructurada (`messages.parse` con un modelo Pydantic). Modelo por defecto `claude-sonnet-5`, configurable con `ANTHROPIC_MODEL`. |
| Validación | modelos Pydantic v2 + un motor de reglas simple (`app/validation.py`) |
| Web | FastAPI + **una** página HTML/JS, sin framework de front-end |
| Exportación | openpyxl (XLSX), reportlab (PDF) |
| Herramientas | pytest, Playwright (grabación de la demo), imageio + imageio-ffmpeg (GIF/MP4) |

## Funciones

- **Interfaz web**: subir por arrastrar y soltar o con un clic en los ejemplos, vista de procesamiento con cronómetro, resumen, lista de validaciones, ítems con las diferencias resaltadas, descargas y vista previa del PDF y del XLSX generados.
- **CLI**: `python extract.py <archivo>` muestra la extracción y las validaciones y escribe JSON, XLSX y PDF. Código de salida `0` = OK, `1` = requiere revisión, `2` = no se pudo procesar.
- **Entrada robusta**: el tipo de archivo se detecta por el contenido, no por la extensión. Las fotos se rotan según EXIF, se reducen y se les quitan los metadatos. Archivos vacíos, truncados o demasiado grandes se rechazan antes de llamar a la API. Una ejecución fallida no deja carpetas vacías.
- **Reintentos explícitos**: un reintento ante timeout, error de red, 408/429/5xx o respuesta estructurada inválida o truncada. Una clave ausente o inválida, un modelo inexistente, un rechazo del modelo o cualquier otro error 4xx fallan de inmediato con un mensaje claro: código de salida 2 en el CLI y un error JSON en la API web.
- **Dos formas de probar sin clave de API** (solo con los ejemplos incluidos, identificados por SHA-256):
  - `replay` muestra los resultados guardados de la ejecución real en `examples/output/`, marcados como replay.
  - `offline` no llama a Claude: carga el resultado esperado escrito a mano (`samples/truth/`). La interfaz, las exportaciones y la CLI lo indican en cada salida. Sirve para probar la validación y las exportaciones; no dice nada sobre la calidad de la extracción.
- **Pruebas**: 54 pruebas sin red: algoritmos de RUT/CNPJ/CPF, reglas de negocio, exportaciones, validación de archivos, política de reintentos (cliente falso), los tres modos, el caso sin clave (CLI y API) y la solicitud que arma el SDK (transporte HTTP simulado).

## Nota de diseño: dos esquemas

`app/schema.py` tiene el modelo que usa el resto del código (`ExtractedDocument`, con campos opcionales) y otro más estricto que se envía a Claude (`WireDocument`). La primera solicitud real enviaba `ExtractedDocument` tal cual, y la API respondió `400 Schema is too complex`: tenía 28 campos opcionales y 27 uniones anulables (`X | None`). En `WireDocument` todos los campos son obligatorios, el texto no impreso vuelve como `""` y solo los números pueden ser `null`. Quedan 0 campos opcionales y 11 uniones. `to_document()` convierte la respuesta, y una prueba verifica que la conversión de ida y vuelta no pierde datos.

## Documentos de ejemplo (`samples/`)

Generados por `scripts/make_samples.py`. El resultado esperado está en `samples/truth/`.

| Archivo | Qué es | Validación esperada |
|---|---|---|
| `danfe-exemplo-industrial.pdf` | DANFE de NF-e en portugués, BRL, 5 ítems, IPI, flete y descuento; el comprador tiene CNPJ alfanumérico | todo OK |
| `orden-compra-andina.pdf` | Orden de compra en español de una empresa chilena (RUT) a un proveedor brasileño (CNPJ), USD, CIF | todo OK |
| `foto-danfe-parafusos.jpg` | "Foto de celular" de una DANFE impresa (perspectiva, sombra, ruido) | **2 alertas**: total de la línea 3 y suma de ítems vs subtotal (error intencional) |

## Cómo ejecutar

Se necesita Python 3.12. El modo real (`live`) también necesita una [clave de API de Anthropic](https://console.anthropic.com/); los modos `replay` y `offline` no.

### Windows (PowerShell)

```powershell
git clone https://github.com/joao-jleite/ai-document-extractor-demo.git
cd ai-document-extractor-demo
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env                           # luego edite .env y ponga su ANTHROPIC_API_KEY
.venv\Scripts\python -m uvicorn app.server:app --port 8000   # abra http://localhost:8000
```

CLI: `.venv\Scripts\python extract.py samples\orden-compra-andina.pdf`

Llamar a `.venv\Scripts\python` directamente funciona sin activar el entorno virtual. Si prefiere activarlo y PowerShell bloquea `Activate.ps1` ("la ejecución de scripts está deshabilitada en este sistema"), permita scripts solo en la ventana actual y luego active:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

### Linux / macOS

```bash
git clone https://github.com/joao-jleite/ai-document-extractor-demo.git
cd ai-document-extractor-demo
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # luego edite .env y ponga su ANTHROPIC_API_KEY
uvicorn app.server:app --port 8000 # abra http://localhost:8000
```

CLI: `python extract.py samples/orden-compra-andina.pdf`

### Sin clave de API

```powershell
# Windows (PowerShell)
$env:EXTRACTOR_MODE = "replay"
.venv\Scripts\python -m uvicorn app.server:app --port 8000
.venv\Scripts\python extract.py samples\orden-compra-andina.pdf --replay     # resultados guardados de la ejecución real
.venv\Scripts\python extract.py samples\foto-danfe-parafusos.jpg --offline   # resultado esperado, sin llamar a Claude
```

```bash
# Linux/macOS
EXTRACTOR_MODE=replay uvicorn app.server:app --port 8000
python extract.py samples/orden-compra-andina.pdf --replay
python extract.py samples/foto-danfe-parafusos.jpg --offline
```

`EXTRACTOR_MODE=offline` se usa igual para el modo sin Claude. Ambos modos solo aceptan los tres ejemplos incluidos. Si el modo real se ejecuta sin clave, el CLI termina con código de salida 2 y la página web muestra el mismo mensaje, que indica estos dos modos.

### Configuración (`.env`)

| Variable | Valor por defecto | Significado |
|---|---|---|
| `ANTHROPIC_API_KEY` | ninguno | obligatoria en modo `live` |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | cualquier modelo Claude con salida estructurada |
| `ANTHROPIC_EFFORT` | `medium` | `low` / `medium` / `high` |
| `EXTRACT_TIMEOUT_SECONDS` | `120` | timeout por solicitud (un reintento) |
| `MAX_UPLOAD_MB` | `20` | tamaño máximo del archivo |
| `EXTRACTOR_MODE` | `live` | `live`, `replay` u `offline` |

### Pruebas y herramientas

```powershell
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m playwright install chromium   # una vez, para grabar la demo (Playwright 1.58.0)
.venv\Scripts\python -m pytest                        # 54 pruebas sin red ni clave de API
.venv\Scripts\python scripts\make_samples.py          # regenera los ejemplos ficticios y el resultado esperado
.venv\Scripts\python scripts\run_samples.py           # extracción real de los ejemplos + informe de precisión
.venv\Scripts\python scripts\run_samples.py --rescore # vuelve a puntuar la ejecución guardada (sin API)
.venv\Scripts\python scripts\build_demo_assets.py     # ejemplos + GIF/MP4 en vivo + capturas en modo replay
```

En Linux/macOS, con el entorno activado: `pip install -r requirements-dev.txt`, `python -m playwright install chromium`, `pytest` y `python scripts/...`.

## Limitaciones

- Es una demo, no un validador fiscal. No consulta la SEFAZ ni recalcula ICMS/IPI.
- La extracción con un LLM puede equivocarse, sobre todo con fotos malas (la primera ejecución de arriba se equivocó en las claves de la NF-e). Por eso cada número pasa por las reglas de validación y la interfaz marca lo que requiere revisión humana.
- Los documentos se envían a la API de Anthropic. Con datos reales, revise antes sus requisitos de tratamiento de datos.
- Las claves de NF-e de un emisor con CNPJ alfanumérico siguen la NT 2025.001 según mi lectura (las letras valen su código ASCII − 48 en el dígito verificador, como en el propio CNPJ). Solo se probaron con claves sintéticas.

## Autor

Hecho por **João Vitor Sousa Leite** ([github.com/joao-jleite](https://github.com/joao-jleite)). Antes de dedicarme al desarrollo freelance, trabajé como comprador y analista de TI (único del área) en Zitron Brasil (2025–2026). Las NF-e brasileñas y las órdenes de compra con proveedores chilenos en BRL, USD y CLP eran parte de mi trabajo diario, así que estas reglas automatizan el tipo de verificación (cálculo de líneas, totales, RUT/CNPJ) que yo hacía a mano.

## Licencia

[MIT](LICENSE). Los documentos de ejemplo son ficticios y de libre uso.
