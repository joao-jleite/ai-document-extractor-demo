"""One command to (re)build every demo asset with REAL Claude calls.

    python scripts/build_demo_assets.py --assets-dir ../assets/demo-a

1. scripts/run_samples.py      -> examples/output/* (+ accuracy.md vs ground truth)
2. scripts/record_demo.py      -> recordings/demo.webm + screenshots
3. scripts/make_media.py       -> docs/demo.gif, docs/demo.mp4
4. screenshots re-saved without metadata into docs/ (and --assets-dir, if given)

Needs ANTHROPIC_API_KEY (env or .env). Costs a few cents of API usage.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def run(*args: str) -> None:
    print("\n$", " ".join(args), flush=True)
    subprocess.run([PY, *args], cwd=ROOT, check=True, env={**os.environ, "EXTRACTOR_MODE": "live"})


def clean_png(src: Path, dest: Path) -> None:
    """Re-encode without any metadata chunks (no EXIF/text)."""
    with Image.open(src) as im:
        Image.frombytes(im.mode, im.size, im.tobytes()).save(dest, optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-dir", type=Path, default=None, help="extra folder to copy the media to")
    ap.add_argument("--skip-samples", action="store_true")
    args = ap.parse_args()

    rec = ROOT / "recordings"
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    if not args.skip_samples:
        run("scripts/run_samples.py")
    run("scripts/record_demo.py", "--out", str(rec))
    run("scripts/make_media.py", "--src", str(rec), "--dest", str(docs))

    shots = sorted(rec.glob("screenshot-*.png"))
    for shot in shots:
        clean_png(shot, docs / shot.name)
    if args.assets_dir:
        args.assets_dir.mkdir(parents=True, exist_ok=True)
        for f in [docs / "demo.gif", docs / "demo.mp4", *[docs / s.name for s in shots]]:
            shutil.copy2(f, args.assets_dir / f.name)
    print("\nDone:", ", ".join(p.name for p in sorted(docs.iterdir())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
