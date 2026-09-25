"""One command to (re)build every demo asset.

    python scripts/build_demo_assets.py                 # live: REAL Claude calls
    python scripts/build_demo_assets.py --offline       # no API call (offline sample mode)
    python scripts/build_demo_assets.py --assets-dir ../assets/demo-a

1. scripts/run_samples.py      -> examples/output/* (+ accuracy.md vs ground truth)   [live only]
2. scripts/record_demo.py      -> demo.webm: a LIVE Claude call on the photo sample
3. scripts/record_demo.py      -> screenshots in REPLAY mode, so they show exactly the
                                  saved run of step 1 (the numbers in the README table)
4. scripts/make_media.py       -> docs/demo.gif, docs/demo.mp4
5. screenshots re-saved without metadata into docs/ (and --assets-dir, if given)

The GIF comes from its own live call, so its time and token counts differ slightly
from the saved run. Live mode needs ANTHROPIC_API_KEY (env or .env) and costs a few
cents of API usage. --offline records the same UI with the samples' hand-written
answer keys: the UI, the exports and therefore the recording all say "offline mode",
and no accuracy report is written (comparing the answer key with itself would prove nothing).
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


def run(*args: str, mode: str = "live") -> None:
    print("\n$", " ".join(args), flush=True)
    subprocess.run([PY, *args], cwd=ROOT, check=True, env={**os.environ, "EXTRACTOR_MODE": mode})


def clean_png(src: Path, dest: Path) -> None:
    """Re-encode without any metadata chunks (no EXIF/text)."""
    with Image.open(src) as im:
        Image.frombytes(im.mode, im.size, im.tobytes()).save(dest, optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets-dir", type=Path, default=None, help="extra folder to copy the media to")
    ap.add_argument("--rec-dir", type=Path, default=ROOT / "recordings", help="working folder for the raw recording")
    ap.add_argument("--skip-samples", action="store_true", help="live: do not re-run scripts/run_samples.py")
    ap.add_argument("--skip-video", action="store_true", help="keep the current GIF/MP4, only redo the screenshots")
    ap.add_argument("--offline", action="store_true",
                    help="record in offline sample mode (no API call, no accuracy report)")
    args = ap.parse_args()
    mode = "offline" if args.offline else "live"
    shot_mode = "offline" if args.offline else "replay"  # screenshots = the saved run in examples/output

    rec = args.rec_dir
    docs = ROOT / "docs"
    docs.mkdir(exist_ok=True)
    if mode == "live" and not args.skip_samples:
        run("scripts/run_samples.py")
    if not args.skip_video:
        run("scripts/record_demo.py", "--out", str(rec), "--mode", mode, "--skip-screenshots", mode=mode)
        run("scripts/make_media.py", "--src", str(rec), "--dest", str(docs), mode=mode)
    run("scripts/record_demo.py", "--out", str(rec), "--mode", shot_mode, "--skip-video", mode=shot_mode)

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
