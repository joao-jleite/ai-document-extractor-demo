# AI Document Extractor (demo)

![Demo: una foto de una factura brasileña (DANFE) leída por Claude, validada y exportada a Excel y PDF](docs/demo.gif)

> **Proyecto de demostración.** Todos los documentos de ejemplo son ficticios (empresas, RUT/CNPJ, montos). Cada exportación lleva la marca **DEMO**.

[English README](README.md) · [Video MP4](docs/demo.mp4)

## Problema

Los equipos de compras y cuentas por pagar vuelven a digitar los datos de facturas (NF-e / DANFE de Brasil) y órdenes de compra en planillas y ERP. Los documentos llegan como PDF o fotos, en portugués, español o inglés, con formatos numéricos locales. Un dígito invertido o un RUT mal escrito se detecta recién al pagar o al conciliar.

## Solución

1. **Extraer**: Claude lee el PDF (de forma nativa) o la foto (visión) y responde en un esquema JSON definido con Pydantic (salida estructurada). El resultado incluye encabezado, proveedor, comprador, ítems y totales.
2. **Validar**: se comprueba cantidad × precio = total de línea, suma de líneas = subtotal y el total general. También se verifican los dígitos verificadores de **RUT**, CNPJ (incluido el CNPJ alfanumérico de 2026) y CPF, la clave de acceso de la NF-e, las fechas, los campos obligatorios y la moneda ISO (por ejemplo, CLP sin decimales).
3. **Exportar**: planilla **XLSX** (hojas Header, Items y Validation), **informe PDF** y JSON, todos marcados como DEMO.

El modelo transcribe lo impreso sin corregir totales, así la validación detecta errores del propio documento. La foto de ejemplo tiene un error intencional en la línea 3, y la demo lo señala.

## Stack

Python 3.12 · FastAPI + una página HTML/JS · Claude API (SDK `anthropic`, modelo por defecto `claude-sonnet-5`) · Pydantic · openpyxl · reportlab · pytest · Playwright

## Cómo ejecutar

```bash
python3.12 -m venv .venv && source .venv/bin/activate   # Windows: py -3.12 -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env            # agregue su ANTHROPIC_API_KEY (Windows: Copy-Item .env.example .env)
uvicorn app.server:app --port 8000
```

Abra http://localhost:8000. Por línea de comandos: `python extract.py samples/orden-compra-andina.pdf`.
Sin clave de API: `EXTRACTOR_MODE=replay` (usa los resultados guardados de los ejemplos).

Todos los documentos de ejemplo son ficticios. Licencia MIT.
