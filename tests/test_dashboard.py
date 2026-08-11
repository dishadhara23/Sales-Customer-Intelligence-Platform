"""Render the dashboard in a real browser and assert every panel drew.

Why this exists
---------------
The dashboard's JavaScript is generated from a Python string. A stray bracket
produces a file that is perfectly valid HTML, opens without complaint, and
renders a page of empty boxes — the charts simply never draw. Nothing in the
Python test suite would notice, and neither would a quick glance at a
screenshot thumbnail.

That is not hypothetical: it happened once during development. This test drives
headless Chrome, waits for the scripts to run, and checks each panel actually
has content, so a syntax error fails the build instead of shipping.

Skips cleanly when Chrome or the built dashboard is unavailable.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.config import PROJECT_ROOT

DASHBOARD = PROJECT_ROOT / "dashboards" / "executive_dashboard.html"
PAGES_COPY = PROJECT_ROOT / "docs" / "index.html"

CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
    "chromium-browser",
]

# Panels the page must fill in at run time. Each is empty in the source HTML and
# only gets content if the JavaScript executed successfully.
PANELS = [
    "kpis", "trend", "funnel", "delivery", "deliveryFinding",
    "cohort", "pareto", "map", "rfm", "geotable", "status",
]


def _chrome() -> str | None:
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
        if found := shutil.which(candidate):
            return found
    return None


def _render(extra_script: str = "") -> str:
    """Return the DOM after scripts have run."""
    chrome = _chrome()
    if chrome is None:
        pytest.skip("No Chrome/Chromium available to render the dashboard.")
    if not DASHBOARD.exists():
        pytest.skip("Dashboard not built — run `python scripts/build_dashboard.py`.")

    target = DASHBOARD
    if extra_script:
        tmp = PROJECT_ROOT / "data" / "processed" / "_dashboard_test.html"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(DASHBOARD.read_text(encoding="utf-8")
                       + f"\n<script>{extra_script}</script>", encoding="utf-8")
        target = tmp

    result = subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--no-sandbox",
         "--virtual-time-budget=9000", "--dump-dom", target.as_uri()],
        capture_output=True, text=True, timeout=180,
    )
    return result.stdout


def _panel_text(dom: str, panel_id: str) -> str:
    match = re.search(rf'id="{panel_id}"[^>]*>(.*?)</div>', dom, re.S)
    return match.group(1) if match else ""


@pytest.fixture(scope="module")
def dom() -> str:
    return _render()


@pytest.mark.parametrize("panel", PANELS)
def test_panel_renders(dom, panel):
    """A JS error leaves the panel exactly as the template shipped it: empty."""
    content = _panel_text(dom, panel)
    assert len(content.strip()) > 40, (
        f"Panel '{panel}' is empty — the dashboard JavaScript most likely threw. "
        "Open the file in a browser and check the console."
    )


def test_headline_numbers_are_present(dom):
    """Guards against the page rendering but with the data block missing."""
    # Computed from the source figure rather than hard-coded, so the assertion
    # follows DISPLAY_CURRENCY instead of breaking every time the rate changes.
    from src.viz.money import compact

    revenue = compact(15_735_527)
    assert revenue in dom, f"national gross revenue ({revenue}) missing from the KPI tiles"
    assert "96,096" in dom, "customer count missing from the KPI tiles"
    assert "4.46" in dom, "fastest-delivery review score missing"
    assert "2.19" in dom, "slowest-delivery review score missing"


def test_no_unreplaced_template_placeholder():
    html = DASHBOARD.read_text(encoding="utf-8")
    assert "__DATA__" not in html, "the data placeholder was never substituted"


def test_dashboard_is_self_contained():
    """No external requests: the page must work offline and under a strict CSP."""
    html = DASHBOARD.read_text(encoding="utf-8")
    for pattern in ("<script src=", "<link rel=\"stylesheet\"", "@import",
                    "https://cdn", "http://cdn", "fonts.googleapis"):
        assert pattern not in html, f"dashboard reaches out to the network: {pattern}"


def test_pages_copy_matches_dashboard():
    """docs/index.html is what GitHub Pages serves; it must not go stale."""
    if not PAGES_COPY.exists():
        pytest.skip("GitHub Pages copy not built yet.")
    assert PAGES_COPY.read_text(encoding="utf-8") == DASHBOARD.read_text(encoding="utf-8")


def test_region_filter_changes_the_numbers():
    """Exercise the real controls, not the internal state object."""
    dom = _render(
        "[...document.querySelectorAll('#regionChips .chip')]"
        ".find(b => b.textContent === 'South').click();"
    )
    assert "South" in _panel_text(dom, "status")
    # National revenue must no longer be on screen once a region is selected.
    assert "R$15.74M" not in dom, "KPI tiles did not respond to the region filter"


def test_delivery_bar_click_focuses_a_band():
    dom = _render(
        "const b = document.querySelectorAll('#delivery rect.mark');"
        "if (b[4]) b[4].dispatchEvent(new MouseEvent('click', {bubbles:true}));"
    )
    finding = _panel_text(dom, "deliveryFinding")
    assert "Click the bar again to clear" in finding, (
        "clicking a delivery bar did not focus that band"
    )


def test_converted_amounts_are_labelled_as_converted(dom):
    """An unlabelled converted figure is the failure that actually matters.

    The data is Brazilian Real. Showing pounds without saying so would overstate
    every number on the page by roughly 4.5x while looking entirely plausible.
    """
    from src.viz.money import active

    cur = active()
    if not cur.is_converted:
        pytest.skip("showing the source currency")
    assert cur.symbol in dom, "converted symbol missing from the page"
    assert "Brazilian Real" in dom, "the conversion is not disclosed anywhere"
    assert f"{cur.rate_from_brl:.2f}" in dom, "the rate used is not stated"


def test_no_source_currency_leaks_into_a_converted_page(dom):
    """A page mixing R$ and £ figures would be worse than either alone."""
    from src.viz.money import active

    if not active().is_converted:
        pytest.skip("showing the source currency")
    # 'per R$1' in the conversion note is the one legitimate mention.
    body = dom.replace("per R$1", "")
    assert "R$" not in body, "an unconverted R$ amount is still on the page"
