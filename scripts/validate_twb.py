#!/usr/bin/env python3
"""Check a .twb against Tableau's content-model rules before opening it.

    python scripts/validate_twb.py [path/to/workbook.twb]

Why this exists
---------------
Tableau validates a workbook against an internal XSD and refuses to load on the
first violation. That XSD is compiled into a dylib and cannot be read, so the
only way to learn the rules was to open a file and read the rejection.

Rather than keep paying that round trip, the content models Tableau reported are
encoded here verbatim. This catches the same class of error locally, in
milliseconds. It is not a full XSD validator — it checks the rules that have
actually bitten, which is the useful subset.

Every rule below is quoted from a real Tableau error message.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "dashboards" / "tableau" / "sales_intelligence.twb"

# Elements Tableau rejected as undeclared. They appear in Tableau's own files
# only because those carry a <document-format-change-manifest> enabling them.
FORBIDDEN_ELEMENTS = {
    "simple-id": "gated behind document-format-change-manifest; omit it",
}

# "value 'table' not in enumeration" — the internal object-id column.
FORBIDDEN_ATTR_VALUES = {
    ("column", "datatype", "table"):
        "the __tableau_internal_object_id__ column; Superstore has none, omit it",
}

# "element 'detail' is not allowed for content model
#  '((color|size|text|shape|wedge-size|lod|geometry|image|tooltip|path|level|edge|custom))'"
ALLOWED_ENCODINGS = {
    "color", "size", "text", "shape", "wedge-size", "lod", "geometry",
    "image", "tooltip", "path", "level", "edge", "custom",
}

# "missing elements in content model '(datasources?, mapsources?,
#  datasource-dependencies*, filter, sort, perspectives, slices?, aggregation)'"
VIEW_REQUIRED_TAIL = "aggregation"

# "content model '(((layout-options?)|(repository-location?)), style, size?,
#  datasources, datasource-dependencies*, zones, devicelayouts)'"
DASHBOARD_REQUIRED = ["style", "datasources", "zones", "devicelayouts"]

# "content model '((cards, viewpoint?)|(viewpoints, active, device-preview))'"
WINDOW_BRANCHES = (["cards"], ["viewpoints", "active", "device-preview"])

# Containers with a "+" content model: an empty one is a load error.
# "element 'devicelayouts' is not allowed for content model '(devicelayout+)'"
# style-rule was found the same way — by diffing which elements Tableau's own
# files never leave empty.
MUST_NOT_BE_EMPTY = {
    "devicelayouts": "content model is (devicelayout+)",
    "style-rule": "Tableau never writes an empty style-rule",
    "encodings": "an encodings block with no children serves no purpose",
    "cards": "a window's cards block must describe at least one card",
}


def validate(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        return [f"not well-formed XML: {exc}"]

    for el in root.iter():
        if el.tag in FORBIDDEN_ELEMENTS:
            problems.append(f"<{el.tag}> is not declared — {FORBIDDEN_ELEMENTS[el.tag]}")
        for attr, value in el.attrib.items():
            key = (el.tag, attr, value)
            if key in FORBIDDEN_ATTR_VALUES:
                problems.append(
                    f"<{el.tag} {attr}='{value}'> rejected — {FORBIDDEN_ATTR_VALUES[key]}")

    for tag, why in MUST_NOT_BE_EMPTY.items():
        for el in root.iter(tag):
            if len(el) == 0:
                problems.append(f"<{tag}> is empty — {why}")

    for enc in root.iter("encodings"):
        for child in enc:
            if child.tag not in ALLOWED_ENCODINGS:
                problems.append(
                    f"<{child.tag}> is not a legal encoding; allowed: "
                    f"{', '.join(sorted(ALLOWED_ENCODINGS))}")

    for ws in root.findall("worksheets/worksheet"):
        name = ws.get("name")
        view = ws.find("table/view")
        if view is None:
            problems.append(f"worksheet '{name}': missing table/view")
            continue
        kids = [c.tag for c in view]
        if not kids or kids[-1] != VIEW_REQUIRED_TAIL:
            problems.append(
                f"worksheet '{name}': <view> must end with <{VIEW_REQUIRED_TAIL}> "
                f"(found {kids or 'nothing'})")

    for dash in root.findall("dashboards/dashboard"):
        kids = [c.tag for c in dash]
        for required in DASHBOARD_REQUIRED:
            if required not in kids:
                problems.append(
                    f"dashboard '{dash.get('name')}': missing <{required}> "
                    f"(has {kids})")

    for win in root.findall("windows/window"):
        kids = [c.tag for c in win]
        if not any(all(t in kids for t in branch) for branch in WINDOW_BRANCHES):
            problems.append(
                f"window '{win.get('name')}': needs either {WINDOW_BRANCHES[0]} "
                f"or {WINDOW_BRANCHES[1]} (has {kids})")

    # Every shelf reference must resolve to a declared column-instance.
    for ws in root.findall("worksheets/worksheet"):
        deps = ws.find("table/view/datasource-dependencies")
        declared = {ci.get("name") for ci in deps.findall("column-instance")} if deps is not None else set()
        for shelf in ("rows", "cols"):
            node = ws.find(f"table/{shelf}")
            text = (node.text or "").strip() if node is not None else ""
            if not text:
                continue
            inst = text[text.index("].") + 2:]
            if inst not in declared:
                problems.append(
                    f"worksheet '{ws.get('name')}': {shelf} references {inst}, "
                    f"not declared in datasource-dependencies")

    # Data files must exist, or the workbook opens with empty sheets.
    for ds in root.findall("datasources/datasource"):
        conn = ds.find("connection/named-connections/named-connection/connection")
        if conn is None:
            continue
        target = Path(conn.get("directory", "")) / conn.get("filename", "")
        if not target.exists():
            problems.append(f"datasource '{ds.get('caption')}' points at missing {target}")

    return problems


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    path = Path(argv[0]) if argv else DEFAULT
    if not path.exists():
        print(f"{path} not found")
        return 1

    problems = validate(path)
    if problems:
        print(f"{len(problems)} problem(s) in {path.name}:\n")
        for p in problems:
            print(f"  - {p}")
        return 1

    root = ET.parse(path).getroot()
    print(f"{path.name}: passes every rule Tableau has rejected so far")
    print(f"  {len(root.find('datasources'))} datasources, "
          f"{len(root.find('worksheets'))} worksheets, "
          f"{len(root.find('dashboards'))} dashboard(s)")
    print("\nNote: this is not a full XSD validator. It encodes the rules Tableau")
    print("actually reported; a clean result makes a successful load likely, not certain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
