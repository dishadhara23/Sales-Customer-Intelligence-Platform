#!/usr/bin/env python3
"""Generate a Tableau workbook (.twb) over the chart-ready extracts.

    python scripts/build_tableau.py [--extracts-dir PATH]

How this is built
-----------------
An earlier version of this script guessed at Tableau's XML from documentation.
This one does not: it was written against a workbook Tableau 2026.2.1 actually
produced on this machine (an autosave containing a real CSV connection) plus the
bundled Superstore sample for worksheet and dashboard structure. Every element
and attribute below appears in one of those two files.

Two decisions keep the output simple enough to be reliable:

* **No calculated fields.** Every measure a sheet needs already exists as a
  column in ``tab_*.csv`` — including the weighted review score, which is the
  one number a naive Tableau AVG would get wrong.
* **No filters.** Each extract is already filtered to exactly its sheet's rows.

That leaves each worksheet as a plain assignment of two or three fields to
shelves, which is the best-understood part of the format.

The absolute path to the extracts is baked into the file, because that is what
Tableau's ``textscan`` connector stores. Re-run this script if you move the
project.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_EXTRACTS = ROOT / "dashboards" / "tableau" / "extracts"
OUT = ROOT / "dashboards" / "tableau" / "sales_intelligence.twb"

VERSION = "18.1"
SOURCE_BUILD = "2026.2.1 (20262.26.0708.1337)"
ACCENT = "#2a78d6"

# pandas dtype -> (Tableau datatype, role, type, remote-type code, aggregation)
DTYPE_MAP = {
    "int64": ("integer", "measure", "quantitative", "20", "Sum"),
    "float64": ("real", "measure", "quantitative", "5", "Sum"),
    "object": ("string", "dimension", "nominal", "129", "Count"),
    "bool": ("boolean", "dimension", "nominal", "11", "Count"),
}

# Numeric columns that describe rather than measure. Left as measures, Tableau
# sums them and the sort orders become nonsense.
FORCE_DIMENSION = {
    "bucket_order", "stage_order", "month_index", "cohort_label", "year_month",
    "state_code", "region", "category", "segment", "stage", "bucket",
}


def sid(prefix: str, seed: str, length: int = 28) -> str:
    """Stable pseudo-random identifier matching Tableau's naming shape."""
    digest = hashlib.sha1(f"{prefix}:{seed}".encode()).hexdigest()
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    n = int(digest, 16)
    out = []
    for _ in range(length):
        n, r = divmod(n, len(alphabet))
        out.append(alphabet[r])
    return prefix + "." + "".join(out)


def obj_id(table: str) -> str:
    return f"{table}_{hashlib.md5(table.encode()).hexdigest().upper()}"


def stable_uuid(seed: str) -> str:
    return "{" + str(uuid.UUID(hashlib.md5(seed.encode()).hexdigest())).upper() + "}"


def caption_for(name: str) -> str:
    return name.replace("_", " ").title()


class Field:
    def __init__(self, name: str, dtype: str, ordinal: int):
        datatype, role, tp, remote_type, aggregation = DTYPE_MAP.get(
            dtype, DTYPE_MAP["object"])
        self.name, self.ordinal = name, ordinal
        self.datatype, self.remote_type = datatype, remote_type
        self.aggregation = aggregation
        if name in FORCE_DIMENSION:
            role = "dimension"
            tp = "nominal" if datatype == "string" else "ordinal"
            self.aggregation = "Count"
        self.role, self.type = role, tp


class Source:
    """One CSV, its fields, and the identifiers Tableau uses to refer to them."""

    def __init__(self, stem: str, caption: str, extracts: Path):
        self.stem, self.caption = stem, caption
        self.csv = extracts / f"{stem}.csv"
        if not self.csv.exists():
            raise FileNotFoundError(
                f"{self.csv} missing — run `python -m src.etl.run_pipeline` first.")
        df = pd.read_csv(self.csv)
        self.fields = [Field(c, str(df[c].dtype), i) for i, c in enumerate(df.columns)]
        self.ds_name = sid("federated", stem, 26)
        self.conn_name = sid("textscan", stem, 28)
        self.table = f"{stem}.csv"
        self.object_id = obj_id(self.table)
        self.directory = str(extracts)

    def field(self, name: str) -> Field:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"{name} not in {self.stem}.csv "
                       f"(have: {[f.name for f in self.fields]})")

    def instance(self, name: str, agg: str = "none") -> str:
        """Column-instance reference, e.g. ``[sum:revenue:qk]``."""
        f = self.field(name)
        kind = "qk" if (agg != "none" or f.type == "quantitative") else "nk"
        return f"[{agg}:{name}:{kind}]"

    def ref(self, name: str, agg: str = "none") -> str:
        return f"[{self.ds_name}].{self.instance(name, agg)}"


def build_datasource(parent: ET.Element, src: Source) -> None:
    ds = ET.SubElement(parent, "datasource", {
        "caption": src.caption, "inline": "true",
        "name": src.ds_name, "version": VERSION})
    conn = ET.SubElement(ds, "connection", {"class": "federated"})

    named = ET.SubElement(conn, "named-connections")
    nc = ET.SubElement(named, "named-connection",
                       {"caption": src.stem, "name": src.conn_name})
    ET.SubElement(nc, "connection", {
        "class": "textscan", "directory": src.directory,
        "filename": src.csv.name, "password": "", "server": ""})

    rel = ET.SubElement(conn, "relation", {
        "connection": src.conn_name, "name": src.csv.name,
        "table": f"[{src.stem}#csv]", "type": "table"})
    cols = ET.SubElement(rel, "columns", {
        "character-set": "UTF-8", "header": "yes", "locale": "en_GB",
        "separator": ","})
    for f in src.fields:
        ET.SubElement(cols, "column", {
            "datatype": f.datatype, "name": f.name, "ordinal": str(f.ordinal)})

    md = ET.SubElement(conn, "metadata-records")
    for f in src.fields:
        rec = ET.SubElement(md, "metadata-record", {"class": "column"})
        ET.SubElement(rec, "remote-name").text = f.name
        ET.SubElement(rec, "remote-type").text = f.remote_type
        ET.SubElement(rec, "local-name").text = f"[{f.name}]"
        ET.SubElement(rec, "parent-name").text = f"[{src.table}]"
        ET.SubElement(rec, "remote-alias").text = f.name
        ET.SubElement(rec, "ordinal").text = str(f.ordinal)
        ET.SubElement(rec, "local-type").text = f.datatype
        ET.SubElement(rec, "aggregation").text = f.aggregation
        ET.SubElement(rec, "contains-null").text = "true"
        ET.SubElement(rec, "object-id").text = f"[{src.object_id}]"

    ET.SubElement(ds, "aliases", {"enabled": "yes"})
    for f in sorted(src.fields, key=lambda x: caption_for(x.name)):
        ET.SubElement(ds, "column", {
            "caption": caption_for(f.name), "datatype": f.datatype,
            "name": f"[{f.name}]", "role": f.role, "type": f.type})


def build_worksheet(parent: ET.Element, name: str, src: Source, *,
                    rows: tuple[str, str] | None,
                    cols: tuple[str, str] | None,
                    mark: str,
                    colour: tuple[str, str] | None = None,
                    size: tuple[str, str] | None = None,
                    label: tuple[str, str] | None = None,
                    detail: tuple[str, str] | None = None,
                    title: str | None = None) -> None:
    """Emit one worksheet. Each shelf is a ``(field_name, aggregation)`` pair."""
    ws = ET.SubElement(parent, "worksheet", {"name": name})

    if title:
        lo = ET.SubElement(ws, "layout-options")
        t = ET.SubElement(lo, "title")
        ft = ET.SubElement(t, "formatted-text")
        ET.SubElement(ft, "run").text = title

    table = ET.SubElement(ws, "table")
    view = ET.SubElement(table, "view")
    dss = ET.SubElement(view, "datasources")
    ET.SubElement(dss, "datasource", {"caption": src.caption, "name": src.ds_name})

    used = [s for s in (rows, cols, colour, size, label, detail) if s]
    deps = ET.SubElement(view, "datasource-dependencies", {"datasource": src.ds_name})
    for fname in sorted({f for f, _ in used}):
        f = src.field(fname)
        ET.SubElement(deps, "column", {
            "datatype": f.datatype, "name": f"[{f.name}]",
            "role": f.role, "type": f.type})
    seen: set[str] = set()
    for fname, agg in used:
        inst = src.instance(fname, agg)
        if inst in seen:
            continue
        seen.add(inst)
        ET.SubElement(deps, "column-instance", {
            "column": f"[{fname}]",
            "derivation": agg.capitalize() if agg != "none" else "None",
            "name": inst, "pivot": "key",
            "type": "quantitative" if inst.endswith("qk]") else "nominal"})

    # Required trailing element of <view>. Content model is
    # (datasources?, mapsources?, datasource-dependencies*, filter, sort,
    #  perspectives, slices?, aggregation) — aggregation is not optional.
    ET.SubElement(view, "aggregation", {"value": "true"})

    ET.SubElement(table, "style")
    panes = ET.SubElement(table, "panes")
    pane = ET.SubElement(panes, "pane",
                         {"selection-relaxation-option": "selection-relaxation-allow"})
    pv = ET.SubElement(pane, "view")
    ET.SubElement(pv, "breakdown", {"value": "auto"})
    ET.SubElement(pane, "mark", {"class": mark})

    if colour or size or label or detail:
        enc = ET.SubElement(pane, "encodings")
        if colour:
            ET.SubElement(enc, "color", {"column": src.ref(*colour)})
        if size:
            ET.SubElement(enc, "size", {"column": src.ref(*size)})
        if label:
            ET.SubElement(enc, "text", {"column": src.ref(*label)})
        if detail:
            ET.SubElement(enc, "lod", {"column": src.ref(*detail)})

    formats: list[dict[str, str]] = []
    if not colour:
        formats.append({"attr": "mark-color", "value": ACCENT})
    if label:
        formats.append({"attr": "mark-labels-show", "value": "true"})
        formats.append({"attr": "mark-labels-cull", "value": "true"})
    # Only emit the style block when it has content — <style-rule> is never
    # empty in Tableau's own files, and empty containers are what it rejects.
    if formats:
        style = ET.SubElement(pane, "style")
        rule = ET.SubElement(style, "style-rule", {"element": "mark"})
        for fmt in formats:
            ET.SubElement(rule, "format", fmt)

    ET.SubElement(table, "rows").text = src.ref(*rows) if rows else ""
    ET.SubElement(table, "cols").text = src.ref(*cols) if cols else ""


def build_dashboard(parent: ET.Element, name: str,
                    layout: list[tuple[str, int, int, int, int]]) -> None:
    dash = ET.SubElement(parent, "dashboard", {"name": name})
    lo = ET.SubElement(dash, "layout-options")
    t = ET.SubElement(lo, "title")
    ft = ET.SubElement(t, "formatted-text")
    ET.SubElement(ft, "run").text = (
        "Sales & Customer Intelligence · Olist Brazilian marketplace · "
        "99,441 orders · Sep 2016 – Oct 2018")
    ET.SubElement(dash, "style")
    # Superstore writes only minwidth/minheight here; maxwidth/maxheight do not
    # appear in any Tableau-authored file, so don't invent them.
    ET.SubElement(dash, "size", {"minheight": "1400", "minwidth": "1280"})
    ET.SubElement(dash, "datasources")
    zones = ET.SubElement(dash, "zones")
    root = ET.SubElement(zones, "zone", {
        "h": "100000", "id": "1", "type-v2": "layout-basic",
        "w": "100000", "x": "0", "y": "0"})
    for i, (sheet, x, y, w, h) in enumerate(layout, start=4):
        ET.SubElement(root, "zone", {
            "h": str(h), "id": str(i), "name": sheet,
            "w": str(w), "x": str(x), "y": str(y)})
    # Content model is (devicelayout+): an empty <devicelayouts> is rejected.
    # Tableau's own dashboards carry one per device with just a sizing mode.
    layouts = ET.SubElement(dash, "devicelayouts")
    for device in ("Phone", "Tablet"):
        dl = ET.SubElement(layouts, "devicelayout", {"name": device})
        ET.SubElement(dl, "size", {"sizing-mode": "automatic"})


def build(extracts: Path) -> tuple[str, list[str]]:
    def S(stem: str, cap: str) -> Source:
        return Source(stem, cap, extracts)

    kpi = S("tab_kpi", "KPI summary")
    trend = S("tab_trend", "Monthly trend")
    delivery = S("tab_delivery", "Delivery performance")
    cohort = S("tab_cohort", "Cohort retention")
    category = S("tab_category", "Category performance")
    geo = S("tab_geo", "Geography")
    funnel = S("tab_funnel", "Fulfilment funnel")
    rfm = S("tab_rfm", "RFM segments")
    sources = [kpi, trend, delivery, cohort, category, geo, funnel, rfm]

    wb = ET.Element("workbook", {
        "original-version": VERSION, "source-build": SOURCE_BUILD,
        "source-platform": "mac", "version": VERSION,
        "xmlns:user": "http://www.tableausoftware.com/xml/user"})

    prefs = ET.SubElement(wb, "preferences")
    palette = ET.SubElement(prefs, "color-palette", {
        "name": "Warehouse Sequential Blue", "type": "ordered-sequential"})
    for hexval in ["#cde2fb", "#9ec5f4", "#86b6ef", "#5598e7", "#3987e5",
                   "#2a78d6", "#1c5cab", "#104281", "#0d366b"]:
        ET.SubElement(palette, "color").text = hexval

    ds_root = ET.SubElement(wb, "datasources")
    for src in sources:
        build_datasource(ds_root, src)

    ws = ET.SubElement(wb, "worksheets")

    build_worksheet(ws, "Delivery vs review", delivery,
                    cols=("bucket", "none"), rows=("avg_review_score", "sum"),
                    mark="Bar", colour=("bucket_order", "sum"),
                    label=("avg_review_score", "sum"),
                    title="Review score collapses as delivery slows")

    build_worksheet(ws, "Revenue trend", trend,
                    cols=("year_month", "none"), rows=("revenue", "sum"),
                    mark="Line", title="Monthly gross revenue")

    build_worksheet(ws, "Cohort retention", cohort,
                    cols=("month_index", "none"), rows=("cohort_label", "none"),
                    mark="Square", colour=("retention_pct", "sum"),
                    label=("retention_pct", "sum"),
                    title="Monthly cohort retention (%)")

    build_worksheet(ws, "Category Pareto", category,
                    cols=("category", "none"), rows=("pct_of_revenue", "sum"),
                    mark="Bar", label=("pct_of_revenue", "sum"),
                    title="Revenue concentration by category")

    build_worksheet(ws, "Geography", geo,
                    cols=("longitude", "sum"), rows=("latitude", "sum"),
                    mark="Circle", colour=("avg_days_to_deliver", "sum"),
                    size=("revenue", "sum"), detail=("state_code", "none"),
                    title="Revenue and delivery time by state")

    build_worksheet(ws, "Fulfilment funnel", funnel,
                    rows=("stage", "none"), cols=("orders", "sum"),
                    mark="Bar", colour=("stage_order", "sum"),
                    label=("pct_of_purchased", "sum"),
                    title="Fulfilment funnel")

    build_worksheet(ws, "RFM segments", rfm,
                    rows=("segment", "none"), cols=("revenue", "sum"),
                    mark="Bar", label=("customers", "sum"),
                    title="Revenue by RFM segment")

    build_worksheet(ws, "KPI summary", kpi,
                    rows=None, cols=None, mark="Text",
                    label=("revenue", "sum"), title="Gross revenue")

    dash_root = ET.SubElement(wb, "dashboards")
    build_dashboard(dash_root, "Executive Dashboard", [
        ("Revenue trend",         0,     0, 62000, 30000),
        ("Fulfilment funnel", 62000,     0, 38000, 30000),
        ("Delivery vs review",    0, 30000, 52000, 35000),
        ("Cohort retention",  52000, 30000, 48000, 35000),
        ("Category Pareto",       0, 65000, 52000, 35000),
        ("Geography",         52000, 65000, 48000, 35000),
    ])

    sheet_names = [w.get("name") for w in ws]
    # <window> content model is ((cards, viewpoint?) | (viewpoints, active,
    # device-preview)). Only worksheet windows are emitted, using the first
    # branch: they are pure UI state, and a malformed one fails the whole load.
    windows = ET.SubElement(wb, "windows", {"source-height": "30"})
    for sheet in sheet_names:
        win = ET.SubElement(windows, "window", {"class": "worksheet", "name": sheet})
        cards = ET.SubElement(win, "cards")
        left = ET.SubElement(cards, "edge", {"name": "left"})
        strip = ET.SubElement(left, "strip", {"size": "160"})
        for card in ("pages", "filters", "marks"):
            ET.SubElement(strip, "card", {"type": card})
        top = ET.SubElement(cards, "edge", {"name": "top"})
        for card in ("columns", "rows"):
            s2 = ET.SubElement(top, "strip", {"size": "2147483647"})
            ET.SubElement(s2, "card", {"type": card})
        s3 = ET.SubElement(top, "strip", {"size": "31"})
        ET.SubElement(s3, "card", {"type": "title"})

    ET.indent(wb, space="  ")
    body = ET.tostring(wb, encoding="unicode")
    return ("<?xml version='1.0' encoding='utf-8' ?>\n\n"
            "<!-- generated by scripts/build_tableau.py -->\n"
            f"{body}\n"), sheet_names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extracts-dir", type=Path, default=DEFAULT_EXTRACTS,
                    help="Folder holding tab_*.csv (its absolute path is baked "
                         "into the .twb, because that is what Tableau stores)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    extracts = args.extracts_dir.resolve()
    try:
        xml, sheets = build(extracts)
    except FileNotFoundError as exc:
        print(exc)
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(xml, encoding="utf-8")
    ET.parse(args.out)  # well-formed?

    # ...and does it satisfy the content-model rules Tableau enforces? Writing a
    # workbook that cannot load is worse than writing none, so refuse to finish.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from validate_twb import validate

    problems = validate(args.out)
    if problems:
        print(f"REFUSING to ship {args.out.name} — {len(problems)} schema problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        args.out.unlink()
        return 2

    print(f"wrote {args.out}")
    print(f"  {len(sheets)} worksheets + 1 dashboard")
    print(f"  reads data from: {extracts}")
    print("\nOpen it by double-clicking the file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
