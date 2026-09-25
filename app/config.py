"""Runtime settings, read from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # never overrides variables already set in the shell


@dataclass(frozen=True)
class Settings:
    model: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    # Effort controls how much the model thinks: low | medium | high.
    effort: str = os.getenv("ANTHROPIC_EFFORT", "medium")
    request_timeout_s: float = float(os.getenv("EXTRACT_TIMEOUT_SECONDS", "120"))
    max_upload_mb: float = float(os.getenv("MAX_UPLOAD_MB", "20"))
    runs_dir: Path = Path(os.getenv("RUNS_DIR", str(ROOT / "runs")))
    # "live"    calls the Claude API.
    # "offline" never calls Claude: for the bundled samples only (matched by
    #           SHA-256), the extraction step returns the sample's hand-written
    #           answer key from samples/truth/, so the UI, validation and exports
    #           can be tried without a key. Every output is labelled as such.
    # "replay"  serves results saved earlier by a live run of
    #           scripts/run_samples.py (REPLAY_DIR, matched by SHA-256).
    mode: str = os.getenv("EXTRACTOR_MODE", "live").strip().lower()
    replay_dir: Path = Path(os.getenv("REPLAY_DIR", str(ROOT / "examples" / "output")))
    samples_dir: Path = ROOT / "samples"

    @property
    def api_key_configured(self) -> bool:
        # Read at call time; an empty value (the .env.example default) counts as missing.
        return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


settings = Settings()
