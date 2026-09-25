"""Record the demo video and take the README screenshots with Playwright.

    python scripts/record_demo.py                 # live: real Claude calls
    python scripts/record_demo.py --out recordings

Starts the FastAPI app on a free port, drives Chromium (new headless mode, which
includes the built-in PDF viewer), records a 1280x800 video and writes
`markers.json` with the timestamps that scripts/make_media.py uses to edit it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
VIEWPORT = {"width": 1280, "height": 800}

# Playwright videos do not show the OS cursor: draw one (plus a click ripple).
CURSOR_JS = r"""
(() => {
  const install = () => {
    if (document.getElementById('__demo_cursor')) return;
    const c = document.createElement('div');
    c.id = '__demo_cursor';
    c.style.cssText = 'position:fixed;z-index:2147483647;left:-40px;top:-40px;width:24px;height:24px;pointer-events:none;';
    c.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24"><path d="M4 2.5l15.5 9.8-6.9 1.4-3.6 6.9z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(c);
    const pos = JSON.parse(sessionStorage.getItem('__cursor') || 'null');
    if (pos) { c.style.left = pos[0] + 'px'; c.style.top = pos[1] + 'px'; }
    document.addEventListener('mousemove', e => {
      c.style.left = (e.clientX - 3) + 'px'; c.style.top = (e.clientY - 2) + 'px';
      sessionStorage.setItem('__cursor', JSON.stringify([e.clientX - 3, e.clientY - 2]));
    }, true);
    document.addEventListener('mousedown', e => {
      const r = document.createElement('div');
      r.style.cssText = `position:fixed;z-index:2147483646;left:${e.clientX-18}px;top:${e.clientY-18}px;width:36px;height:36px;border-radius:50%;background:rgba(15,118,110,.35);pointer-events:none;transition:transform .45s ease-out,opacity .45s ease-out`;
      document.documentElement.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.9)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 500);
    }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int, env: dict) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.server:app", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env,
    )
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            return proc
        except OSError:
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("server did not start")


def center(page, selector: str) -> tuple[float, float]:
    box = page.locator(selector).first.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def glide_click(page, selector: str, steps: int = 18, pause: float = 0.25) -> None:
    """Move the (visible) cursor smoothly to an element, then click it."""
    x, y = center(page, selector)
    page.mouse.move(x, y, steps=steps)
    page.wait_for_timeout(int(pause * 1000))
    page.mouse.click(x, y)


def record(base: str, out: Path, sample: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    markers: dict[str, float] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium")
        ctx = browser.new_context(viewport=VIEWPORT, record_video_dir=str(out / "raw"),
                                  record_video_size=VIEWPORT, accept_downloads=True)
        ctx.add_init_script(CURSOR_JS)
        page = ctx.new_page()
        t0 = time.monotonic()
        mark = lambda name: markers.__setitem__(name, round(time.monotonic() - t0, 2))  # noqa: E731

        page.goto(base)
        page.wait_for_selector(".sample")
        page.mouse.move(640, 420)
        mark("open")
        page.wait_for_timeout(1300)

        # Upload through the real file chooser (click on the drop zone)
        x, y = center(page, "#drop")
        page.mouse.move(x, y, steps=22)
        page.wait_for_timeout(350)
        with page.expect_file_chooser() as fc_info:
            page.mouse.click(x, y)
        mark("upload")
        fc_info.value.set_files(str(sample))
        page.wait_for_selector("#stProc.show")
        mark("processing")
        page.mouse.move(820, 330, steps=15)
        page.wait_for_selector("#stResult.show", timeout=240_000)
        mark("result")
        page.wait_for_timeout(2300)

        # Scroll to the line items (flagged row), then back up
        page.mouse.move(900, 600, steps=10)
        page.evaluate("window.scrollTo({top: document.querySelector('#itemsCard').offsetTop - 250, behavior: 'smooth'})")
        page.wait_for_timeout(2100)
        page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        page.wait_for_timeout(800)
        mark("downloads")

        with page.expect_download() as dl:
            glide_click(page, "#dlXlsx")
        dl.value.save_as(out / dl.value.suggested_filename)
        page.wait_for_timeout(600)
        with page.expect_download() as dl:
            glide_click(page, "#dlPdf")
        dl.value.save_as(out / dl.value.suggested_filename)
        page.wait_for_timeout(700)

        # Show the generated PDF in Chromium's viewer
        glide_click(page, "#viewPdf")
        page.wait_for_load_state()
        mark("pdf")
        page.wait_for_timeout(2900)
        page.go_back()
        page.wait_for_selector("#stResult.show")
        page.wait_for_timeout(400)

        # Show the generated XLSX (rendered from the file itself)
        glide_click(page, "#viewXlsx")
        page.wait_for_selector(".tab")
        mark("xlsx")
        page.wait_for_timeout(1200)
        glide_click(page, "button.tab:has-text('Items')", pause=0.2)
        page.wait_for_timeout(2000)
        glide_click(page, "button.tab:has-text('Validation')", pause=0.2)
        page.wait_for_timeout(1600)
        mark("end")

        video = page.video
        ctx.close()
        shutil.move(video.path(), out / "demo.webm")
        browser.close()
    shutil.rmtree(out / "raw", ignore_errors=True)
    (out / "markers.json").write_text(json.dumps(markers, indent=2), encoding="utf-8")
    print("markers:", markers)


def screenshots(base: str, out: Path) -> None:
    """Three crisp PNGs (1920x1200: 1280x800 at 1.5x device scale)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chromium")
        ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1.5)
        page = ctx.new_page()

        def run_sample(name: str) -> None:
            page.goto(base)
            page.wait_for_selector(".sample")
            page.click(f"button.sample[data-name='{name}']")
            page.wait_for_selector("#stResult.show", timeout=240_000)
            page.wait_for_timeout(500)

        run_sample("foto-danfe-parafusos.jpg")
        page.evaluate("window.scrollTo(0, document.querySelector('#validation').offsetTop - 60)")
        page.wait_for_timeout(300)
        page.screenshot(path=str(out / "screenshot-1-photo-validation.png"))
        xlsx = page.get_attribute("#viewXlsx", "href")
        page.goto(base.rstrip("/") + xlsx + "?sheet=Items")
        page.wait_for_timeout(500)
        page.screenshot(path=str(out / "screenshot-3-xlsx-items.png"))

        run_sample("orden-compra-andina.pdf")
        page.screenshot(path=str(out / "screenshot-2-purchase-order.png"))
        browser.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "recordings")
    ap.add_argument("--sample", type=Path, default=ROOT / "samples" / "foto-danfe-parafusos.jpg")
    ap.add_argument("--skip-video", action="store_true")
    ap.add_argument("--skip-screenshots", action="store_true")
    args = ap.parse_args()

    port = free_port()
    env = {**os.environ, "RUNS_DIR": str(args.out / "runs")}
    server = start_server(port, env)
    base = f"http://127.0.0.1:{port}/"
    try:
        if not args.skip_video:
            record(base, args.out, args.sample)
        if not args.skip_screenshots:
            screenshots(base, args.out)
    finally:
        server.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
