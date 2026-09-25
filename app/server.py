"""FastAPI app: one HTML page + a small JSON API.

    uvicorn app.server:app --port 8000
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .config import ROOT, settings
from .extractor import ExtractionError, InvalidFileError
from .pipeline import process
from .xlsx_preview import render_xlsx_html

STATIC = ROOT / "static"
SAMPLES = ROOT / "samples"
RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")

app = FastAPI(title="AI Document Extractor (demo)", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def _run_dir(run_id: str) -> Path:
    if not RUN_ID.match(run_id):  # also blocks path traversal
        raise HTTPException(404, "Unknown run.")
    path = settings.runs_dir / run_id
    if not path.is_dir():
        raise HTTPException(404, "Unknown run.")
    return path


def _run_file(run_id: str, suffix: str) -> Path:
    matches = sorted(_run_dir(run_id).glob(f"*{suffix}"))
    if not matches:
        raise HTTPException(404, "File not found.")
    return matches[0]


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC / "index.html").read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "mode": settings.mode,
        "model": settings.model,
        "api_key_configured": settings.api_key_configured,
    }


@app.get("/api/samples")
def list_samples() -> list[dict]:
    kinds = {".pdf": "PDF", ".jpg": "Photo", ".jpeg": "Photo", ".png": "Image"}
    return [
        {"name": f.name, "kind": kinds.get(f.suffix.lower(), "File"), "size_kb": round(f.stat().st_size / 1024)}
        for f in sorted(SAMPLES.iterdir()) if f.is_file() and f.suffix.lower() in kinds
    ]


@app.get("/api/samples/{name}")
def get_sample(name: str) -> FileResponse:
    path = (SAMPLES / name).resolve()
    if path.parent != SAMPLES.resolve() or not path.is_file():
        raise HTTPException(404, "Sample not found.")
    return FileResponse(path)


@app.post("/api/extract")
async def extract_endpoint(file: UploadFile = File(...)) -> JSONResponse:
    data = await file.read()
    try:
        # The Anthropic client is synchronous: run it off the event loop.
        result = await run_in_threadpool(process, data, file.filename or "upload", keep_source=True)
    except InvalidFileError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    result.pop("out_dir", None)
    rid = result["run_id"]
    result["links"] = {
        "xlsx": f"/api/runs/{rid}/xlsx",
        "pdf": f"/api/runs/{rid}/pdf",
        "json": f"/api/runs/{rid}/json",
        "pdf_view": f"/api/runs/{rid}/pdf?inline=1",
        "xlsx_view": f"/preview/xlsx/{rid}",
        "source": f"/api/runs/{rid}/source",
    }
    return JSONResponse(result)


@app.get("/api/runs/{run_id}/{kind}")
def download(run_id: str, kind: str, inline: int = 0) -> FileResponse:
    if kind == "source":
        matches = sorted(_run_dir(run_id).glob("source.*"))
        if not matches:
            raise HTTPException(404, "File not found.")
        return FileResponse(matches[0])
    suffix = {"xlsx": ".xlsx", "pdf": ".report.pdf", "json": ".json"}.get(kind)
    if suffix is None:
        raise HTTPException(404, "Unknown file type.")
    path = _run_file(run_id, suffix)
    media = {
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
        "json": "application/json",
    }[kind]
    disposition = "inline" if inline else "attachment"
    return FileResponse(path, media_type=media, filename=f"DEMO-{path.name}", content_disposition_type=disposition)


@app.get("/preview/xlsx/{run_id}", response_class=HTMLResponse)
def preview_xlsx(run_id: str) -> HTMLResponse:
    path = _run_file(run_id, ".xlsx")
    return HTMLResponse(render_xlsx_html(path, title=f"DEMO - {path.name}"))


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.get("/api/runs/{run_id}")
def run_result(run_id: str) -> dict:
    return json.loads(_run_file(run_id, ".json").read_text(encoding="utf-8"))
