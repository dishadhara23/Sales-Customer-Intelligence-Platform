"""Structural checks on the generated Tableau workbook.

I cannot open Tableau from here, so these do not prove the workbook renders.
What they do prove is that it is well-formed, that every sheet references a
field that actually exists in its CSV, and that every data file it points at is
on disk — which is where a generated workbook realistically goes wrong.
"""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pytest

from src.config import EXTRACT_DIR, PROJECT_ROOT

TWB = PROJECT_ROOT / "dashboards" / "tableau" / "sales_intelligence.twb"


def _module():
    spec = importlib.util.spec_from_file_location(
        "build_tableau", PROJECT_ROOT / "scripts" / "build_tableau.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def root():
    if not TWB.exists():
        pytest.skip("Workbook not generated — run `python scripts/build_tableau.py`.")
    return ET.parse(TWB).getroot()


def test_workbook_is_well_formed(root):
    assert root.tag == "workbook"
    assert root.get("version") == "18.1"


def test_has_expected_structure(root):
    assert len(root.find("datasources")) == 8
    assert len(root.find("worksheets")) == 8
    assert len(root.find("dashboards")) == 1


def test_every_datasource_points_at_a_file_that_exists(root):
    for ds in root.findall("datasources/datasource"):
        conn = ds.find("connection/named-connections/named-connection/connection")
        path = Path(conn.get("directory")) / conn.get("filename")
        assert path.exists(), f"{ds.get('caption')} points at missing {path}"


def test_declared_columns_match_the_csv(root):
    """A renamed or dropped column would leave the sheet referencing nothing."""
    for ds in root.findall("datasources/datasource"):
        conn = ds.find("connection/named-connections/named-connection/connection")
        path = Path(conn.get("directory")) / conn.get("filename")
        actual = list(pd.read_csv(path, nrows=1).columns)
        declared = [c.get("name") for c in ds.findall("connection/relation/columns/column")]
        assert declared == actual, f"{conn.get('filename')}: {declared} != {actual}"


def test_every_shelf_reference_resolves(root):
    """Each rows/cols entry must name a column-instance the sheet declares."""
    for ws in root.findall("worksheets/worksheet"):
        deps = ws.find("table/view/datasource-dependencies")
        declared = {ci.get("name") for ci in deps.findall("column-instance")}
        for shelf in ("rows", "cols"):
            text = (ws.find(f"table/{shelf}").text or "").strip()
            if not text:
                continue
            # "[federated.abc].[sum:revenue:qk]" -> "[sum:revenue:qk]"
            instance = text[text.index("].") + 2:]
            assert instance in declared, (
                f"{ws.get('name')} {shelf} references {instance}, "
                f"which is not declared (has {sorted(declared)})")


def test_dashboard_zones_name_real_worksheets(root):
    sheets = {w.get("name") for w in root.findall("worksheets/worksheet")}
    for zone in root.findall("dashboards/dashboard/zones/zone/zone"):
        name = zone.get("name")
        if name:
            assert name in sheets, f"dashboard zone names unknown sheet '{name}'"


def test_generation_is_deterministic():
    """Rebuilding must not churn the file — identifiers are hashes, not randoms."""
    mod = _module()
    first, _ = mod.build(EXTRACT_DIR.resolve())
    second, _ = mod.build(EXTRACT_DIR.resolve())
    assert first == second


def test_no_calculated_fields_or_filters(root):
    """The whole point of the chart-ready extracts: keep the XML simple."""
    assert root.find(".//calculation") is None, "a calculated field crept in"
    assert root.find(".//filter") is None, "a filter crept in"


def test_workbook_passes_tableau_content_model_rules():
    """Encodes the rules Tableau reported when it rejected an earlier build.

    Tableau's XSD is compiled into a dylib and unreadable, so these rules were
    recovered from real load errors. Catching them here costs milliseconds
    instead of a round trip through the GUI.
    """
    import importlib.util

    if not TWB.exists():
        pytest.skip("workbook not built")

    spec = importlib.util.spec_from_file_location(
        "validate_twb", PROJECT_ROOT / "scripts" / "validate_twb.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    problems = mod.validate(TWB)
    assert not problems, "workbook violates Tableau's schema:\n  " + "\n  ".join(problems)
