#!/usr/bin/env python3
"""Install the project's colour palette into Tableau's Preferences.tps.

    python scripts/install_tableau_palette.py

Why this script exists instead of "copy this XML into a file"
------------------------------------------------------------
Hand-copying the XML from documentation fails in two ways that produce the
same unhelpful Tableau error — `Fatal Error(1,1): invalid document structure`:

1. The markdown code fence (```xml ... ```) gets copied along with the content,
   so the file starts with a backtick instead of `<`.
2. TextEdit saves as RTF by default, or substitutes smart quotes (' ') for the
   straight quotes XML requires.

Writing the file programmatically removes both. The script also merges into an
existing Preferences.tps rather than overwriting it, backs up whatever was
there, and validates the result before leaving it in place.
"""

from __future__ import annotations

import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.viz.palette import CATEGORICAL_LIGHT, SEQUENTIAL_BLUE  # noqa: E402

PREFS = Path.home() / "Documents" / "My Tableau Repository" / "Preferences.tps"

PALETTES = [
    ("Warehouse Sequential Blue", "ordered-sequential", list(SEQUENTIAL_BLUE)),
    ("Warehouse Categorical", "regular", list(CATEGORICAL_LIGHT)),
]


def build_document(existing: ET.Element | None) -> ET.ElementTree:
    """Return a Preferences document with our palettes present exactly once."""
    root = existing if existing is not None else ET.Element("workbook")
    prefs = root.find("preferences")
    if prefs is None:
        prefs = ET.SubElement(root, "preferences")

    ours = {name for name, _t, _c in PALETTES}
    for node in list(prefs.findall("color-palette")):
        if node.get("name") in ours:
            prefs.remove(node)  # replace rather than duplicate on re-run

    for name, kind, colours in PALETTES:
        node = ET.SubElement(prefs, "color-palette", {"name": name, "type": kind})
        for hex_value in colours:
            ET.SubElement(node, "color").text = hex_value

    ET.indent(root, space="  ")
    return ET.ElementTree(root)


def main() -> int:
    if not PREFS.parent.exists():
        print(f"'{PREFS.parent}' not found.")
        print("Open Tableau once so it creates My Tableau Repository, then re-run.")
        return 1

    existing = None
    if PREFS.exists():
        backup = PREFS.with_suffix(".tps.bak")
        shutil.copy2(PREFS, backup)
        print(f"Backed up existing file -> {backup}")
        try:
            existing = ET.parse(PREFS).getroot()
        except ET.ParseError as exc:
            # A file broken by copy-paste is the normal case here, not an error.
            print(f"Existing file is not valid XML ({exc}); replacing it.")

    tree = build_document(existing)
    tree.write(PREFS, encoding="utf-8", xml_declaration=True)

    ET.parse(PREFS)  # fail loudly rather than leave Tableau with a broken file
    print(f"Wrote {PREFS}")
    for name, _kind, colours in PALETTES:
        print(f"  {name}: {len(colours)} colours")
    print("\nQuit Tableau completely and reopen it — the file is only read at startup.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
