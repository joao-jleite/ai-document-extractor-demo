"""End-to-end pipeline used by both the web API and the CLI:
file bytes -> extraction (Claude or replay) -> validation -> XLSX + PDF + JSON."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import settings
from .exporters import write_pdf, write_xlsx
from .extractor import ExtractionError, ExtractionResult, extract, prepare_input, sniff_media_type
from .schema import ExtractedDocument
from .validation import validate_document


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem or "document"
    return re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")[:60] or "document"


def _replay(data: bytes, filename: str) -> ExtractionResult:
    """Serve a previously saved real extraction for the same file (by SHA-256).
    Lets people try the UI without an API key; the UI labels it as a replay."""
    prepare_input(data, filename)  # same file checks as live mode
    digest = hashlib.sha256(data).hexdigest()
    for candidate in sorted(settings.replay_dir.rglob("*.json")):
        try:
            saved = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if saved.get("source", {}).get("sha256") == digest and "document" in saved:
            return ExtractionResult(
                document=ExtractedDocument.model_validate(saved["document"]),
                model=f"{saved.get('model', '?')} (replay)",
                attempts=0,
                seconds=saved.get("timing", {}).get("extract_seconds", 0.0),
                usage=saved.get("usage", {}),
                stop_reason="replay",
            )
    raise ExtractionError(
        "Replay mode only knows the bundled sample files. Set EXTRACTOR_MODE=live and an "
        "ANTHROPIC_API_KEY to process other documents.",
        status_code=404,
    )


def process(data: bytes, filename: str, out_dir: Path | None = None, *, mode: str | None = None,
            keep_source: bool = False) -> dict:
    """Run the whole pipeline and return the result dict (also saved as JSON)."""
    mode = (mode or settings.mode).lower()
    started = time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    out_dir = out_dir or settings.runs_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(filename)

    result = _replay(data, filename) if mode == "replay" else extract(data, filename)
    report = validate_document(result.document)

    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    meta = {
        "filename": filename, "model": result.model, "generated_at": generated_at,
        "seconds": result.seconds, "attempts": result.attempts,
    }
    media_type = sniff_media_type(data)
    if keep_source:  # the web UI shows it again after a page reload
        ext = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
        (out_dir / f"source{ext.get(media_type, '.bin')}").write_bytes(data)
    xlsx_path = write_xlsx(out_dir / f"{stem}.xlsx", result.document, report, meta)
    pdf_path = write_pdf(out_dir / f"{stem}.report.pdf", result.document, report, meta)

    payload = {
        "demo": True,
        "notice": "DEMO - all sample documents and outputs are fictitious.",
        "run_id": run_id,
        "mode": mode,
        "source": {
            "filename": filename,
            "media_type": media_type,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        "model": result.model,
        "attempts": result.attempts,
        "usage": result.usage,
        "timing": {
            "extract_seconds": result.seconds,
            "total_seconds": round(time.perf_counter() - started, 2),
        },
        "generated_at": generated_at,
        "document": result.document.model_dump(mode="json"),
        "validation": report.model_dump(mode="json"),
        "files": {"xlsx": xlsx_path.name, "pdf": pdf_path.name, "json": f"{stem}.json"},
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    payload["out_dir"] = str(out_dir)
    return payload
