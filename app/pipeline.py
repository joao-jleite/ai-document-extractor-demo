"""End-to-end pipeline used by both the web API and the CLI:
file bytes -> extraction (Claude, offline answer key or replay) -> validation -> XLSX + PDF + JSON."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
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


MODES = ("live", "offline", "replay")
# Shown instead of a model name wherever the extraction step did not call Claude.
OFFLINE_LABEL = "offline sample mode (answer key, no Claude call)"


def _bundled_sample(digest: str) -> Path | None:
    """Return the file in samples/ whose SHA-256 matches, if any."""
    if not settings.samples_dir.is_dir():
        return None
    for f in sorted(settings.samples_dir.iterdir()):
        if f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() == digest:
            return f
    return None


def _offline(data: bytes, filename: str) -> ExtractionResult:
    """No API call: for a bundled sample, return its hand-written answer key
    (samples/truth/<stem>.json) as the extraction. It exercises the real
    validation rules and exporters, and says so on every output."""
    prepare_input(data, filename)  # same file checks as live mode
    sample = _bundled_sample(hashlib.sha256(data).hexdigest())
    truth = settings.samples_dir / "truth" / f"{sample.stem}.json" if sample else None
    if truth is None or not truth.is_file():
        raise ExtractionError(
            "Offline mode only works with the bundled sample files: it loads their hand-written "
            "answer key (samples/truth/) instead of calling Claude. Set EXTRACTOR_MODE=live and an "
            "ANTHROPIC_API_KEY to process other documents.",
            status_code=404,
        )
    raw = json.loads(truth.read_text(encoding="utf-8"))
    document = ExtractedDocument.model_validate({k: v for k, v in raw.items() if not k.startswith("_")})
    return ExtractionResult(document=document, model=OFFLINE_LABEL, attempts=0, seconds=0.0,
                            usage={}, stop_reason="offline")


def _replay(data: bytes, filename: str) -> ExtractionResult:
    """Serve a result saved earlier by a LIVE run of scripts/run_samples.py for
    the same file (matched by SHA-256). No new API call; the UI labels it."""
    prepare_input(data, filename)  # same file checks as live mode
    digest = hashlib.sha256(data).hexdigest()
    candidates = sorted(settings.replay_dir.rglob("*.json")) if settings.replay_dir.is_dir() else []
    for candidate in candidates:
        try:
            saved = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        # Only results of real model calls can be replayed (never offline ones).
        if (saved.get("source", {}).get("sha256") == digest and "document" in saved
                and saved.get("mode") == "live"):
            return ExtractionResult(
                document=ExtractedDocument.model_validate(saved["document"]),
                model=f"{saved.get('model', '?')} (replay)",
                attempts=0,
                seconds=saved.get("timing", {}).get("extract_seconds", 0.0),
                usage=saved.get("usage", {}),
                stop_reason="replay",
            )
    if _bundled_sample(digest):
        raise ExtractionError(
            f"No saved live result for this sample in {settings.replay_dir.name}/ yet. Create them with "
            "`python scripts/run_samples.py` (needs an ANTHROPIC_API_KEY), or use EXTRACTOR_MODE=offline.",
            status_code=404,
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
    if mode not in MODES:
        raise ExtractionError(f"Unknown EXTRACTOR_MODE '{mode}' (use live, offline or replay).", status_code=500)
    started = time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    out_dir = out_dir or settings.runs_dir / run_id
    stem = _safe_stem(filename)

    # Extract and validate BEFORE touching the disk: a failed call (401, timeout,
    # unknown file) must not leave an empty folder behind in runs/.
    handler = {"live": extract, "offline": _offline, "replay": _replay}[mode]
    result = handler(data, filename)
    report = validate_document(result.document)

    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    digest = hashlib.sha256(data).hexdigest()
    # "Fictitious data" is only true for the bundled samples; a user's own file is labelled DEMO only.
    is_sample = _bundled_sample(digest) is not None
    meta = {
        "filename": filename, "model": result.model, "generated_at": generated_at,
        "seconds": result.seconds, "attempts": result.attempts, "mode": mode, "sample": is_sample,
    }
    media_type = sniff_media_type(data)
    created = not out_dir.exists()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        if keep_source:  # the web UI shows it again after a page reload
            ext = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
            (out_dir / f"source{ext.get(media_type, '.bin')}").write_bytes(data)
        xlsx_path = write_xlsx(out_dir / f"{stem}.xlsx", result.document, report, meta)
        pdf_path = write_pdf(out_dir / f"{stem}.report.pdf", result.document, report, meta)
    except Exception:
        if created:  # only remove a folder this call created (never a user's --out folder)
            shutil.rmtree(out_dir, ignore_errors=True)
        raise

    payload = {
        "demo": True,
        "notice": ("DEMO - bundled fictitious sample: the document and its outputs are fictitious." if is_sample
                   else "DEMO - output of a demo tool, not validated for production use. Review before use."),
        "run_id": run_id,
        "mode": mode,
        "source": {
            "filename": filename,
            "media_type": media_type,
            "size_bytes": len(data),
            "sha256": digest,
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
