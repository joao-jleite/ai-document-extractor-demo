"""Turn recordings/demo.webm into docs/demo.gif (<= 5 MB) and docs/demo.mp4 (<= 4 MB).

    python scripts/make_media.py --src recordings --dest docs

Editing is driven by recordings/markers.json (written by record_demo.py):
- the wait for the model is fast-forwarded, with a visible "fast-forward" badge
  that states the real duration (nothing is hidden, just shortened);
- the rest plays at real speed, or slightly faster if the clip would exceed ~24 s.
Encoding uses imageio + the ffmpeg binary shipped with imageio-ffmpeg
(H.264 for MP4, palettegen/paletteuse for a sharp GIF). No metadata is written.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def load_font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        if Path(name).exists():
            return ImageFont.truetype(name, size)
    return ImageFont.load_default()


def badge(frame: np.ndarray, text: str) -> np.ndarray:
    """Draw a dark 'fast-forward' pill with two triangles in the top-right corner."""
    img = Image.fromarray(frame)
    d = ImageDraw.Draw(img)
    font = load_font(19)
    tw = d.textlength(text, font=font)
    w, h = int(tw) + 70, 40
    x, y = img.width - w - 24, 44
    d.rounded_rectangle([x, y, x + w, y + h], radius=20, fill=(15, 23, 42))
    tx, ty = x + 16, y + 11  # two play triangles = fast-forward
    for dx in (0, 11):
        d.polygon([(tx + dx, ty), (tx + dx + 11, ty + 9), (tx + dx, ty + 18)], fill=(253, 230, 138))
    d.text((x + 50, y + 8), text, font=font, fill=(255, 255, 255))
    return np.asarray(img)


def plan(markers: dict, max_len: float, ff_len: float) -> list[tuple[float, float, float, str | None]]:
    """Segments (src_start, src_end, speed, badge_text)."""
    start = max(0.0, markers["open"] - 0.3)
    ff_a = markers["processing"] + 1.2
    ff_b = markers["result"] - 0.5
    end = markers["end"] + 0.2
    segs = []
    wait = ff_b - ff_a
    if wait > ff_len + 1:
        real = markers["result"] - markers["processing"]
        segs.append((start, ff_a, 1.0, None))
        segs.append((ff_a, ff_b, wait / ff_len, f"fast-forward  ·  real wait {real:.1f} s"))
        rest_from = ff_b
    else:
        rest_from = start
    fixed = sum((b - a) / s for a, b, s, _ in segs)
    rest = end - rest_from
    speed = max(1.0, rest / max(1.0, max_len - fixed))  # speed up the rest only if needed
    segs.append((rest_from, end, speed, None))
    return segs


def edited_frames(src: Path, segs, out_fps: float, size: tuple[int, int] | None):
    """Yield edited frames by streaming through the source video once."""
    reader = imageio.get_reader(src, "ffmpeg")
    src_fps = reader.get_meta_data().get("fps", 25) or 25
    wanted = []  # (source frame index, badge text)
    for a, b, speed, label in segs:
        t = a
        while t < b:
            wanted.append((int(round(t * src_fps)), label))
            t += speed / out_fps
    wi = 0
    last = None
    for idx, frame in enumerate(reader):
        while wi < len(wanted) and wanted[wi][0] <= idx:
            f = frame if wanted[wi][0] == idx or last is None else last
            if wanted[wi][1]:
                f = badge(f, wanted[wi][1])
            if size:
                f = np.asarray(Image.fromarray(f).resize(size, Image.LANCZOS))
            yield f
            wi += 1
        last = frame
        if wi >= len(wanted):
            break
    reader.close()


def stabilize(frames, threshold: int = 6, block: int = 8, big: float = 0.25, hold: int = 2):
    """Kill video-codec flicker: an 8x8 block is only updated when it really
    changed (max channel difference > threshold); otherwise the previous pixels
    are kept. Static UI then stays byte-identical between frames, which is what
    makes GIF delta frames small. During continuous full-screen motion (scroll,
    page change) only every (hold+1)-th frame is refreshed."""
    prev = None
    since_big = hold
    for f in frames:
        f = f.astype(np.int16)
        if prev is None:
            prev = f
        else:
            h, w = f.shape[:2]
            hb, wb = h // block * block, w // block * block
            diff = np.abs(f[:hb, :wb] - prev[:hb, :wb]).max(axis=2)
            changed = diff.reshape(hb // block, block, wb // block, block).max(axis=(1, 3)) > threshold
            if changed.mean() > big:  # scroll / page change
                if since_big >= hold:
                    prev, since_big = f, 0
                else:
                    since_big += 1  # hold the previous frame a little longer
            else:
                since_big = hold
                # grow the mask by one block so faint edges next to real changes update too
                grown = changed.copy()
                grown[1:, :] |= changed[:-1, :]
                grown[:-1, :] |= changed[1:, :]
                grown[:, 1:] |= changed[:, :-1]
                grown[:, :-1] |= changed[:, 1:]
                mask = np.repeat(np.repeat(grown, block, axis=0), block, axis=1)
                out = prev.copy()
                out[:hb, :wb][mask] = f[:hb, :wb][mask]
                out[hb:, :] = f[hb:, :]
                out[:, wb:] = f[:, wb:]
                prev = out
        yield prev.astype(np.uint8)


def write_mp4(frames, path: Path, fps: float, crf: int) -> None:
    writer = imageio.get_writer(
        path, fps=fps, codec="libx264", quality=None, macro_block_size=16,
        ffmpeg_params=["-crf", str(crf), "-preset", "slow", "-pix_fmt", "yuv420p",
                       "-movflags", "+faststart", "-map_metadata", "-1", "-an"],
    )
    for f in frames:
        writer.append_data(f)
    writer.close()


def _pipe(frames_factory, args: list[str]) -> None:
    """Feed raw RGB frames to ffmpeg through stdin (no lossy intermediate file)."""
    first = True
    proc = None
    for f in frames_factory():
        if first:
            h, w = f.shape[:2]
            proc = subprocess.Popen([FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                     "-s", f"{w}x{h}", "-r", "10", "-i", "-", *args], stdin=subprocess.PIPE)
            first = False
        proc.stdin.write(np.ascontiguousarray(f).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")


def write_gif(frames_factory, dest: Path, fps: int, colors: int) -> None:
    palette = dest.with_suffix(".palette.png")
    _pipe(frames_factory, ["-vf", f"palettegen=max_colors={colors}:stats_mode=full", str(palette)])
    _pipe(frames_factory, ["-i", str(palette), "-lavfi",
                           f"[0:v][1:v]paletteuse=dither=none:diff_mode=rectangle,setpts=N/({fps}*TB)",
                           "-r", str(fps), "-loop", "0", "-map_metadata", "-1", str(dest)])
    palette.unlink(missing_ok=True)


def duration(path: Path) -> float:
    reader = imageio.get_reader(path, "ffmpeg")
    meta = reader.get_meta_data()
    reader.close()
    return float(meta.get("duration", 0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=ROOT / "recordings")
    ap.add_argument("--dest", type=Path, default=ROOT / "docs")
    ap.add_argument("--max-len", type=float, default=23.0, help="target clip length in seconds")
    ap.add_argument("--ff-len", type=float, default=2.2, help="seconds the model wait is shown for")
    args = ap.parse_args()
    args.dest.mkdir(parents=True, exist_ok=True)
    markers = json.loads((args.src / "markers.json").read_text(encoding="utf-8"))
    video = args.src / "demo.webm"
    # The video starts at the first paint, slightly after the script's clock:
    # align markers so that "end" matches the end of the video.
    offset = max(0.0, markers["end"] + 0.15 - duration(video))
    markers = {k: max(0.0, v - offset) for k, v in markers.items()}
    print(f"marker offset {offset:.2f} s")
    segs = plan(markers, args.max_len, args.ff_len)
    for a, b, s, label in segs:
        print(f"segment {a:6.2f}-{b:6.2f}s  speed x{s:.2f}  {label or ''}")

    # MP4: 1280x800, 25 fps, H.264; raise CRF until it fits in 4 MB
    mp4 = args.dest / "demo.mp4"
    for crf in (24, 27, 30, 33):
        write_mp4(stabilize(edited_frames(video, segs, 25, None)), mp4, 25, crf)
        if mp4.stat().st_size <= 3_900_000:  # stay safely under 4 MB
            break
    print(f"mp4  {mp4.stat().st_size / 1e6:.2f} MB  {duration(mp4):.1f} s  (crf {crf})")

    # GIF: 960x600, two passes over stabilized raw frames (palette, then encode)
    gif = args.dest / "demo.gif"
    for fps, colors in ((12, 192), (10, 160), (10, 128), (9, 128), (8, 112)):
        write_gif(lambda: stabilize(edited_frames(video, segs, fps, (960, 600))), gif, fps, colors)
        if gif.stat().st_size <= 4_900_000:  # stay safely under 5 MB
            break
    print(f"gif  {gif.stat().st_size / 1e6:.2f} MB  {fps} fps  {colors} colors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
