#!/usr/bin/env python3
"""Regenerate every image in docs/images/ from the artefacts themselves.

    python scripts/build_screenshots.py

Screenshots go stale silently, and a stale one is worse than no image: the
README showed figures in R$ for a while after the project moved to GBP, which
made the documentation argue with itself. Regenerating them is a command rather
than an afternoon of cropping, so it actually gets done.

Three sources:

* **Notebook figures** are extracted from the executed ``.ipynb`` — the same
  bytes the notebook displays, so they cannot drift from it.
* **Dashboard shots** are captured from the built HTML in headless Chrome,
  including a dark-mode and a filtered variant.
* **Chatbot shots** need the Streamlit app, which this script starts and stops
  itself.

Chrome is required. Everything else is already a project dependency.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

IMAGES = ROOT / "docs" / "images"
NOTEBOOK = ROOT / "notebooks" / "01_sales_intelligence_walkthrough.ipynb"
DASHBOARD = ROOT / "dashboards" / "executive_dashboard.html"
APP = ROOT / "src" / "app" / "streamlit_app.py"
PORT = 8613

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
)

# Notebook figures, in the order they appear. Named rather than numbered so a
# new cell in the middle does not silently rename every file after it.
NOTEBOOK_FIGURES = [
    "nb_kpi_tiles.png",
    "nb_revenue_trend.png",
    "nb_cohort_heatmap.png",
    "nb_rfm_segments.png",
    "nb_category_pareto.png",
    "nb_geography.png",
    "nb_delivery_vs_review.png",
    "nb_funnel.png",
]


def find_chrome() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
        if found := shutil.which(candidate):
            return found
    return None


# --------------------------------------------------------------- notebook ---

def extract_notebook_figures() -> int:
    if not NOTEBOOK.exists():
        print("  notebook not built — skipping figures")
        return 0

    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    images = [
        output["data"]["image/png"]
        for cell in nb["cells"]
        for output in cell.get("outputs", [])
        if "image/png" in output.get("data", {})
    ]

    if len(images) != len(NOTEBOOK_FIGURES):
        # Writing them out anyway would shuffle the names against the README.
        print(f"  ! notebook has {len(images)} figures, expected "
              f"{len(NOTEBOOK_FIGURES)} — skipping rather than misname them.")
        print("    Update NOTEBOOK_FIGURES in this script to match.")
        return 0

    for name, payload in zip(NOTEBOOK_FIGURES, images):
        (IMAGES / name).write_bytes(base64.b64decode(payload))
        print(f"  {name}")
    return len(images)


# -------------------------------------------------------------- browser -----

def capture(chrome: str, url: str, out: Path, *, width: int, height: int,
            script: str = "", wait: float = 3.5, settle: float = 1.2,
            dark: bool = False) -> None:
    """Screenshot a URL, optionally after running some JavaScript first."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=chrome)
        page = browser.new_page(
            viewport={"width": width, "height": height},
            color_scheme="dark" if dark else "light",
            device_scale_factor=2,          # retina, so the README isn't fuzzy
        )
        page.goto(url, wait_until="load", timeout=60_000)
        page.wait_for_timeout(int(wait * 1000))
        if script:
            page.evaluate(script)
            # Waiting happens here rather than inside the script: page.evaluate
            # runs a plain function body, where `await` is a syntax error.
            page.wait_for_timeout(int(settle * 1000))
        # Streamlit leaves the view where the last interaction put it, which
        # crops the header and the answer out of the shot. It scrolls an inner
        # container rather than the window, so window.scrollTo does nothing.
        page.evaluate("""
          window.scrollTo(0, 0);
          document.querySelectorAll('section, div').forEach(n => {
            if (n.scrollTop > 0) n.scrollTop = 0;
          });
        """)
        page.wait_for_timeout(600)
        page.screenshot(path=str(out), full_page=True)
        browser.close()
    print(f"  {out.name}")


def capture_dashboard(chrome: str) -> None:
    if not DASHBOARD.exists():
        print("  dashboard not built — run scripts/build_dashboard.py first")
        return
    url = DASHBOARD.as_uri()
    capture(chrome, url, IMAGES / "dashboard_light.png", width=1500, height=1200)
    capture(chrome, url, IMAGES / "dashboard_dark.png", width=1500, height=1200,
            dark=True)
    capture(chrome, url, IMAGES / "dashboard_filtered.png", width=1500, height=900,
            script="""
              [...document.querySelectorAll('#regionChips .chip')]
                .find(b => b.textContent === 'Northeast').click();
              [...document.querySelectorAll('#yearChips .chip')]
                .find(b => b.textContent === '2018').click();
            """)


def capture_chatbot(chrome: str) -> None:
    """Start the app, photograph it, stop it again."""
    server = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(APP),
         "--server.port", str(PORT), "--server.headless", "true"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    url = f"http://localhost:{PORT}"
    try:
        import urllib.error
        import urllib.request

        for _ in range(40):
            try:
                urllib.request.urlopen(f"{url}/healthz", timeout=1)
                break
            except (urllib.error.URLError, OSError):
                time.sleep(0.5)
        else:
            print("  ! Streamlit never came up — skipping chatbot shots")
            return

        capture(chrome, url, IMAGES / "chatbot_home.png",
                width=1500, height=1000, wait=6)
        # Click the first suggested question and wait for the answer. The
        # timeout is generous because a local model may be doing the work.
        capture(chrome, url, IMAGES / "chatbot_answer.png",
                width=1500, height=1400, wait=6, settle=60,
                script="""
                  const b = [...document.querySelectorAll('button')]
                    .find(x => x.innerText.trim().endsWith('?'));
                  if (b) b.click();
                """)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def main() -> int:
    IMAGES.mkdir(parents=True, exist_ok=True)

    print("Notebook figures:")
    extract_notebook_figures()

    chrome = find_chrome()
    if chrome is None:
        print("\nChrome not found — skipping dashboard and chatbot screenshots.")
        print("Install Google Chrome, or capture those two by hand.")
        return 0

    print("\nDashboard:")
    capture_dashboard(chrome)

    print("\nChatbot:")
    capture_chatbot(chrome)

    print(f"\nWrote to {IMAGES.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
