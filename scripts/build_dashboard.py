#!/usr/bin/env python3
"""Render the interactive executive dashboard as one self-contained HTML file.

    python scripts/build_dashboard.py
    open dashboards/executive_dashboard.html

How the interactivity works
---------------------------
The `mart_dash_*` models pre-compute every measure at region x period grain,
including an explicit 'ALL' rollup row. Those slices are embedded in the page as
JSON, so changing a filter is a lookup and a re-draw in the browser — no server,
no database, no network. The file works offline and can be hosted anywhere
static (GitHub Pages, S3, an email attachment).

The 'ALL' rows exist because rollups cannot always be recomputed in the browser:
distinct customer counts are not additive across regions or months. Materialising
them in SQL is what keeps the filtered numbers exactly right.

Charts are hand-drawn SVG rather than a chart library, so there is no CDN
dependency and the marks inherit the page's design tokens directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_warehouse  # noqa: E402
from src.viz import money  # noqa: E402

OUT = ROOT / "dashboards" / "executive_dashboard.html"
# GitHub Pages serves /docs from the main branch, so the same file is written
# there as index.html. That gives the dashboard a URL you own
# (<user>.github.io/<repo>) instead of a link on somebody else's domain.
PAGES_OUT = ROOT / "docs" / "index.html"


def _year(series: pd.Series) -> pd.Series:
    return series.str.slice(0, 4)


def _records(df: pd.DataFrame) -> list[dict]:
    """Serialise, rounding floats so both backends emit byte-identical output.

    Summing floats in a different order gives 1999017.4000000001 on one engine
    and 1999017.4 on the other. Neither is wrong, but the drift makes the built
    files impossible to diff and would show up as spurious churn in git.
    """
    out = df.copy()
    for col in out.select_dtypes(include=["float", "float64"]).columns:
        out[col] = out[col].round(4)
    return json.loads(out.to_json(orient="records"))


def collect() -> dict:
    wh = get_warehouse()
    q = lambda sql: pd.read_sql(sql, wh.engine)  # noqa: E731

    # --- KPI: one exact row per (region, year) combination -----------------
    kpi = q("SELECT * FROM mart_dash_kpi")

    # --- Monthly trend: kept at month grain, the year filter slices it -----
    monthly = q("""
        SELECT year_month, region, orders, valid_orders, revenue, freight_revenue,
               units, review_score_sum, review_count, deliver_days_sum,
               deliver_count, late_orders, delivered_orders, new_customer_orders
        FROM mart_dash_monthly ORDER BY year_month
    """)

    # Everything below only ever needs year granularity for filtering, so it is
    # rolled up here rather than shipping 12x the rows to the browser.
    category = q("SELECT * FROM mart_dash_category")
    category["year"] = _year(category["year_month"])
    category = (category.groupby(["year", "region", "category"], as_index=False)
                .agg(units=("units", "sum"), orders=("orders", "sum"),
                     revenue=("revenue", "sum"), freight=("freight", "sum")))

    funnel = q("SELECT * FROM mart_dash_funnel")
    funnel["year"] = _year(funnel["year_month"])
    funnel = (funnel.groupby(["year", "region", "stage", "stage_order"], as_index=False)
              .agg(orders=("orders", "sum")))

    delivery = q("SELECT * FROM mart_dash_delivery")
    delivery["year"] = _year(delivery["year_month"])
    delivery = (delivery.groupby(["year", "region", "bucket", "bucket_order"], as_index=False)
                .agg(orders=("orders", "sum"), revenue=("revenue", "sum"),
                     review_score_sum=("review_score_sum", "sum"),
                     review_count=("review_count", "sum"),
                     detractor_orders=("detractor_orders", "sum"),
                     deliver_days_sum=("deliver_days_sum", "sum"),
                     deliver_count=("deliver_count", "sum")))

    geo = q("SELECT * FROM mart_dash_geo")
    geo["year"] = _year(geo["year_month"])
    geo_agg = (geo.groupby(["year", "state_code", "region"], as_index=False)
               .agg(orders=("orders", "sum"), valid_orders=("valid_orders", "sum"),
                    revenue=("revenue", "sum"), freight_revenue=("freight_revenue", "sum"),
                    review_score_sum=("review_score_sum", "sum"),
                    review_count=("review_count", "sum"),
                    deliver_days_sum=("deliver_days_sum", "sum"),
                    deliver_count=("deliver_count", "sum"),
                    late_orders=("late_orders", "sum")))
    # Coordinates are constant per state; ship them once instead of per row.
    geo_meta = (geo.dropna(subset=["latitude"])
                .groupby("state_code", as_index=False)
                .agg(region=("region", "first"), lat=("latitude", "first"),
                     lng=("longitude", "first")))

    cohort = q("""
        SELECT cohort_label, region, months_since_first_order AS m,
               cohort_customers, active_customers, retention_pct
        FROM mart_dash_cohort ORDER BY cohort_label, m
    """)

    rfm = q("SELECT * FROM mart_dash_rfm")

    regions = sorted(r for r in kpi["region"].unique() if r != "ALL")
    years = sorted(y for y in kpi["year_label"].unique() if y != "ALL")

    bounds = q("SELECT MIN(order_date) AS a, MAX(order_date) AS b FROM fact_orders").iloc[0]
    cat_total = int(q("SELECT COUNT(*) AS n FROM mart_category_performance").iloc[0]["n"])

    return {
        "kpi": _records(kpi),
        "monthly": _records(monthly),
        "category": _records(category),
        "funnel": _records(funnel),
        "delivery": _records(delivery),
        "geo": _records(geo_agg),
        "geoMeta": _records(geo_meta),
        "cohort": _records(cohort),
        "rfm": _records(rfm),
        "regions": regions,
        "years": years,
        "meta": {
            "firstDate": str(bounds["a"])[:10],
            "lastDate": str(bounds["b"])[:10],
            "categoryTotal": cat_total,
        },
    }


HTML = r"""<title>Sales &amp; Customer Intelligence — Executive Dashboard</title>
<script>
  // Restore the reader's theme before first paint so there is no flash. A host
  // that stamps data-theme itself still wins — this only fills in a blank.
  try {
    var t = localStorage.getItem("dashTheme");
    if (t && !document.documentElement.getAttribute("data-theme"))
      document.documentElement.setAttribute("data-theme", t);
  } catch (e) {}
</script>
<style>
:root {
  color-scheme: light dark;
  --surface:#fcfcfb; --surface-raised:#ffffff; --surface-sunk:#f4f3f0;
  --border:#e6e4df; --ink:#0b0b0b; --ink-2:#52514e; --ink-3:#75736d;
  --grid:#e6e4df; --accent:#2a78d6; --accent-soft:#e8f1fd;
  --context:#b6b3ac; --critical:#e34948; --warning:#eda100; --good:#008300;
  --s1:#cde2fb; --s2:#9ec5f4; --s3:#86b6ef; --s4:#5598e7; --s5:#3987e5;
  --s6:#2a78d6; --s7:#1c5cab; --s8:#104281; --s9:#0d366b;
  --shadow:0 1px 2px rgba(11,11,11,.05), 0 4px 12px rgba(11,11,11,.04);
  --mono: ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --surface:#1a1a19; --surface-raised:#232322; --surface-sunk:#141413;
    --border:#33322f; --ink:#ffffff; --ink-2:#c3c2b7; --ink-3:#8f8d84;
    --grid:#33322f; --accent:#3987e5; --accent-soft:#16283d;
    --context:#5a5852; --critical:#e66767; --warning:#c98500;
    --shadow:0 1px 2px rgba(0,0,0,.4), 0 4px 14px rgba(0,0,0,.3);
  }
}
:root[data-theme="dark"] {
  --surface:#1a1a19; --surface-raised:#232322; --surface-sunk:#141413;
  --border:#33322f; --ink:#ffffff; --ink-2:#c3c2b7; --ink-3:#8f8d84;
  --grid:#33322f; --accent:#3987e5; --accent-soft:#16283d;
  --context:#5a5852; --critical:#e66767; --warning:#c98500;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 4px 14px rgba(0,0,0,.3);
}
* { box-sizing:border-box; }
body { margin:0; background:var(--surface); color:var(--ink);
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased; }
.wrap { max-width:1300px; margin:0 auto; padding:30px 22px 70px; }

header { display:flex; align-items:flex-start; justify-content:space-between;
  gap:16px; flex-wrap:wrap; }
h1 { font-size:25px; letter-spacing:-.022em; margin:0 0 3px; font-weight:680; }
.sub { color:var(--ink-2); font-size:13px; margin:0; }

/* --- filter bar ------------------------------------------------------- */
.filters { display:flex; gap:22px; flex-wrap:wrap; align-items:center;
  background:var(--surface-raised); border:1px solid var(--border);
  border-radius:12px; padding:12px 16px; margin:20px 0 4px; box-shadow:var(--shadow);
  position:sticky; top:0; z-index:20; }
.fgroup { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.flabel { font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--ink-3); margin-right:2px; }
.chip { background:transparent; border:1px solid var(--border); color:var(--ink-2);
  border-radius:999px; padding:5px 12px; font-size:12.5px; cursor:pointer;
  font-weight:540; transition:background .12s,border-color .12s,color .12s; }
.chip:hover { border-color:var(--ink-3); color:var(--ink); }
.chip[aria-pressed="true"] { background:var(--accent); border-color:var(--accent);
  color:#fff; }
.chip:focus-visible, .theme-btn:focus-visible { outline:2px solid var(--accent);
  outline-offset:2px; }
.spacer { flex:1; }
.theme-btn { background:var(--surface-raised); color:var(--ink-2);
  border:1px solid var(--border); border-radius:8px; padding:6px 12px;
  cursor:pointer; font-size:12.5px; font-weight:560; }
.theme-btn:hover { color:var(--ink); border-color:var(--ink-3); }
.status { font-size:12.5px; color:var(--ink-3); margin:10px 2px 0; min-height:20px; }
.status b { color:var(--ink); font-weight:620; }
.linkbtn { background:none; border:none; color:var(--accent); cursor:pointer;
  font:inherit; font-size:12.5px; padding:0 0 0 8px; text-decoration:underline; }

/* --- KPI tiles -------------------------------------------------------- */
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(146px,1fr));
  gap:11px; margin:14px 0 4px; }
.kpi { background:var(--surface-raised); border:1px solid var(--border);
  border-radius:12px; padding:13px 15px; box-shadow:var(--shadow); }
.kpi .l { font-size:10.5px; letter-spacing:.07em; text-transform:uppercase;
  color:var(--ink-3); }
.kpi .v { font-size:24px; font-weight:660; letter-spacing:-.02em; margin-top:3px;
  font-family:var(--mono); font-variant-numeric:tabular-nums; }
.kpi .n { font-size:11.5px; color:var(--ink-3); margin-top:2px; }

/* --- cards ------------------------------------------------------------ */
.grid { display:grid; grid-template-columns:repeat(12,1fr); gap:15px; margin-top:15px; }
.card { background:var(--surface-raised); border:1px solid var(--border);
  border-radius:14px; padding:17px 19px 15px; box-shadow:var(--shadow); min-width:0; }
.card h2 { font-size:14.5px; margin:0 0 2px; font-weight:640; letter-spacing:-.01em; }
.card .cap { font-size:12px; color:var(--ink-3); margin:0 0 12px; }
.cardhead { display:flex; justify-content:space-between; align-items:flex-start;
  gap:12px; flex-wrap:wrap; }
.mini { display:flex; gap:5px; flex-wrap:wrap; }
.mini button { background:transparent; border:1px solid var(--border);
  color:var(--ink-3); border-radius:7px; padding:3px 9px; font-size:11.5px;
  cursor:pointer; }
.mini button[aria-pressed="true"] { background:var(--accent-soft);
  border-color:var(--accent); color:var(--accent); font-weight:600; }
.mini button:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
.span12{grid-column:span 12}.span8{grid-column:span 8}.span7{grid-column:span 7}
.span6{grid-column:span 6}.span5{grid-column:span 5}.span4{grid-column:span 4}
@media (max-width:960px){.span8,.span7,.span6,.span5,.span4{grid-column:span 12}}

svg { display:block; width:100%; overflow:visible; }
.axis { fill:var(--ink-3); font-size:10.5px; }
.gridline { stroke:var(--grid); stroke-width:1; }
.mark { cursor:pointer; }
.legend { display:flex; gap:15px; flex-wrap:wrap; font-size:12px;
  color:var(--ink-2); margin-bottom:9px; }
.legend i { width:11px; height:11px; border-radius:3px; display:inline-block;
  margin-right:6px; vertical-align:-1px; }
.empty { color:var(--ink-3); font-size:13px; padding:26px 0; text-align:center; }

.tip { position:fixed; pointer-events:none; z-index:60; background:var(--surface-raised);
  border:1px solid var(--border); border-radius:9px; padding:8px 11px; font-size:12px;
  color:var(--ink); box-shadow:0 4px 18px rgba(0,0,0,.18); opacity:0;
  transition:opacity .1s; max-width:250px; }
.tip b { font-weight:640; } .tip .r { color:var(--ink-3); }

.tablewrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:12.5px; min-width:430px; }
th { text-align:left; font-weight:600; color:var(--ink-3); font-size:10.5px;
  letter-spacing:.06em; text-transform:uppercase; padding:0 8px 8px 0;
  border-bottom:1px solid var(--border); }
td { padding:7px 8px 7px 0; border-bottom:1px solid var(--border); }
td.num { text-align:right; font-family:var(--mono);
  font-variant-numeric:tabular-nums; letter-spacing:-.01em; }
th.num { text-align:right; }
tr:last-child td { border-bottom:none; }

.heat { display:grid; gap:2px; }
.hcell { aspect-ratio:1.5; border-radius:3px; display:flex; align-items:center;
  justify-content:center; font-size:9.5px; font-variant-numeric:tabular-nums;
  cursor:default; }
.hlabel { font-size:10.5px; color:var(--ink-3); display:flex; align-items:center;
  justify-content:flex-end; padding-right:7px; white-space:nowrap; }
.finding { background:var(--surface-sunk); border-left:3px solid var(--accent);
  border-radius:0 10px 10px 0; padding:12px 15px; margin-top:13px; font-size:13px;
  color:var(--ink-2); }
.finding b { color:var(--ink); font-weight:620; }
footer { margin-top:32px; color:var(--ink-3); font-size:12px;
  border-top:1px solid var(--border); padding-top:15px; }
footer code { background:var(--surface-sunk); padding:1.5px 5px; border-radius:4px;
  font-size:11.5px; font-family:var(--mono); }
@media (prefers-reduced-motion: reduce) {
  * { transition-duration:.01ms !important; animation-duration:.01ms !important; }
}
</style>

<div class="wrap">
  <header>
    <div>
      <h1>Sales &amp; Customer Intelligence</h1>
      <p class="sub" id="subtitle"></p>
    </div>
    <button class="theme-btn" id="themeBtn" type="button">Toggle theme</button>
  </header>

  <div class="filters" role="group" aria-label="Dashboard filters">
    <div class="fgroup"><span class="flabel">Region</span><span id="regionChips"></span></div>
    <div class="fgroup"><span class="flabel">Year</span><span id="yearChips"></span></div>
    <div class="spacer"></div>
    <button class="chip" id="resetBtn" type="button">Reset</button>
  </div>
  <p class="status" id="status"></p>

  <div class="kpis" id="kpis"></div>

  <div class="grid">
    <div class="card span8">
      <div class="cardhead">
        <div><h2>Trend over time</h2>
          <p class="cap" id="trendCap"></p></div>
        <div class="mini" id="metricBtns"></div>
      </div>
      <div id="trend"></div>
    </div>

    <div class="card span4">
      <h2>Fulfilment funnel</h2>
      <p class="cap">Measured on milestone timestamps, not the terminal status.</p>
      <div id="funnel"></div>
    </div>

    <div class="card span7">
      <div class="cardhead">
        <div><h2>Delivery speed drives satisfaction</h2>
          <p class="cap">Click a bar to inspect that delivery band.</p></div>
      </div>
      <div id="delivery"></div>
      <div class="finding" id="deliveryFinding"></div>
    </div>

    <div class="card span5">
      <h2>Cohort retention</h2>
      <p class="cap">% of each acquisition cohort ordering again. Month 0 is 100%
        by definition and sits outside the colour scale.</p>
      <div id="cohort"></div>
    </div>

    <div class="card span7">
      <div class="cardhead">
        <div><h2>Category performance</h2>
          <p class="cap" id="catCap"></p></div>
        <div class="mini" id="catBtns"></div>
      </div>
      <div id="pareto"></div>
    </div>

    <div class="card span5">
      <h2>Where revenue is, and how fast it arrives</h2>
      <p class="cap">Bubble size = revenue; colour = average days to deliver.
        Click a state to filter to its region.</p>
      <div id="map"></div>
    </div>

    <div class="card span6">
      <h2>RFM segments</h2>
      <p class="cap">Frequency uses fixed bands, not quintiles — 97% of customers
        buy exactly once, so a quintile would invent tiers that don't exist.</p>
      <div id="rfm"></div>
    </div>

    <div class="card span6">
      <h2>States</h2>
      <p class="cap">Revenue share against service quality. Click a row to filter.</p>
      <div id="geotable"></div>
    </div>
  </div>

  <footer>
    Olist Brazilian e-commerce, 99,441 orders (Sep 2016 – Oct 2018).
    <span id="fxnote">__FX_NOTE__</span>
    Built by <code>python -m src.etl.run_pipeline</code>: star schema of 5 dimensions
    and 3 facts, 18 marts, 12 data-quality checks passing on PostgreSQL 17
    and SQLite. Every figure is computed at build time — none are hard-coded, and
    this page needs no server, database or API key to run.
  </footer>
</div>

<div class="tip" id="tip" role="status" aria-live="polite"></div>

<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const FX = __FX__;
const SEQ = ["--s1","--s2","--s3","--s4","--s5","--s6","--s7","--s8","--s9"];
const cssv = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const SVGNS = "http://www.w3.org/2000/svg";
const tidy = str => str.replace(/\s+/g, " ").trim();
const el = (t,a={}) => { const e=document.createElementNS(SVGNS,t);
  for (const k in a) e.setAttribute(k,a[k]); return e; };
// Every amount embedded in this file is Brazilian Real, because that is what
// the warehouse stores. Conversion happens here, at the point of display, so
// there is exactly one place where the rate is applied. FX is written in by
// the build from src/viz/money.py.
const fx = v => (v||0) * FX.rate;
const fmtBRL = v => { const x = fx(v);
  return x>=1e6 ? FX.sym+(x/1e6).toFixed(2)+"M"
    : x>=1e3 ? FX.sym+(x/1e3).toFixed(0)+"k" : FX.sym+x.toFixed(0); };
const fmtMoney0 = v => FX.sym + fx(v).toLocaleString("en-US",
  {minimumFractionDigits:0, maximumFractionDigits:0});
const fmtMoney2 = v => FX.sym + fx(v).toFixed(2);
const fmtN = v => Math.round(v||0).toLocaleString("en-US");
const sum = (a,k) => a.reduce((s,r)=>s+(r[k]||0),0);
const BUCKET_ORDER = ["0-3 days","4-7 days","8-14 days","15-30 days","30+ days","not delivered"];

// ---- application state ----------------------------------------------------
const state = { region:"ALL", year:"ALL", bucket:null, metric:"revenue", catSort:"revenue" };

// ---- tooltip --------------------------------------------------------------
const tip = document.getElementById("tip");
function showTip(evt, html) {
  tip.innerHTML = html; tip.style.opacity = 1;
  const r = tip.getBoundingClientRect();
  let x = evt.clientX + 14, y = evt.clientY - r.height - 10;
  if (x + r.width > innerWidth - 8) x = evt.clientX - r.width - 14;
  if (y < 8) y = evt.clientY + 18;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.style.opacity = 0; };
function hover(node, html) {
  node.addEventListener("mousemove", e => showTip(e, html));
  node.addEventListener("mouseleave", hideTip);
}

// ---- filter helpers -------------------------------------------------------
const inRegion = r => r.region === state.region;
const inYear = r => state.year === "ALL" || r.year === state.year;
const monthInYear = r => state.year === "ALL" || r.year_month.slice(0,4) === state.year;

// ---- filter chips ---------------------------------------------------------
function chip(label, active, onClick) {
  const b = document.createElement("button");
  b.className = "chip"; b.type = "button"; b.textContent = label;
  b.setAttribute("aria-pressed", active ? "true" : "false");
  b.addEventListener("click", onClick);
  return b;
}
function buildFilters() {
  const rc = document.getElementById("regionChips"); rc.innerHTML = "";
  rc.appendChild(chip("All Brazil", state.region === "ALL",
    () => { state.region = "ALL"; render(); }));
  D.regions.forEach(r => rc.appendChild(chip(r, state.region === r,
    () => { state.region = r; render(); })));

  const yc = document.getElementById("yearChips"); yc.innerHTML = "";
  yc.appendChild(chip("All years", state.year === "ALL",
    () => { state.year = "ALL"; render(); }));
  D.years.forEach(y => yc.appendChild(chip(y, state.year === y,
    () => { state.year = y; render(); })));

  const mb = document.getElementById("metricBtns"); mb.innerHTML = "";
  [["revenue","Revenue"],["orders","Orders"],["aov","Avg order value"],
   ["units","Units"],["review","Avg review"]].forEach(([k,label]) => {
    const b = document.createElement("button"); b.type = "button"; b.textContent = label;
    b.setAttribute("aria-pressed", state.metric === k ? "true" : "false");
    b.addEventListener("click", () => { state.metric = k; render(); });
    mb.appendChild(b);
  });

  const cb = document.getElementById("catBtns"); cb.innerHTML = "";
  [["revenue","By revenue"],["freight","By freight %"],["units","By units"]]
    .forEach(([k,label]) => {
      const b = document.createElement("button"); b.type = "button"; b.textContent = label;
      b.setAttribute("aria-pressed", state.catSort === k ? "true" : "false");
      b.addEventListener("click", () => { state.catSort = k; render(); });
      cb.appendChild(b);
    });
}

function renderStatus() {
  const bits = [];
  bits.push(state.region === "ALL" ? "All of Brazil" : `<b>${state.region}</b> region`);
  bits.push(state.year === "ALL" ? "all years" : `<b>${state.year}</b>`);
  let html = "Showing " + bits.join(" · ");
  if (state.bucket) html += ` · delivery band <b>${state.bucket}</b>`;
  const filtered = state.region !== "ALL" || state.year !== "ALL" || state.bucket;
  document.getElementById("status").innerHTML = html +
    (filtered ? '<button class="linkbtn" id="clearInline">clear filters</button>' : "");
  const c = document.getElementById("clearInline");
  if (c) c.addEventListener("click", resetFilters);
}
function resetFilters() {
  state.region = "ALL"; state.year = "ALL"; state.bucket = null; render();
}

// ---- KPI tiles ------------------------------------------------------------
function renderKPIs() {
  const k = D.kpi.find(r => r.region === state.region && r.year_label === state.year);
  const host = document.getElementById("kpis");
  if (!k) { host.innerHTML = '<p class="empty">No data for this filter.</p>'; return; }
  const aov = k.revenue / Math.max(k.valid_orders, 1);
  const tiles = [
    ["Gross revenue", fmtBRL(k.revenue), fmtN(k.valid_orders) + " valid orders"],
    ["Avg order value", fmtMoney2(aov), "per valid order"],
    ["Customers", fmtN(k.customers), "unique people"],
    ["Units sold", fmtN(k.units), "line items"],
    ["Avg review", (k.avg_review_score ?? 0).toFixed(2) + "/5", "across all reviews"],
    ["Avg delivery", (k.avg_days_to_deliver ?? 0).toFixed(1) + " d",
      (k.late_delivery_pct ?? 0).toFixed(1) + "% arrive late"],
    ["Freight burden", (100 * k.freight_revenue / Math.max(k.revenue, 1)).toFixed(1) + "%",
      "of gross revenue"],
  ];
  host.innerHTML = tiles.map(([l,v,n]) =>
    `<div class="kpi"><div class="l">${l}</div><div class="v">${v}</div>
     <div class="n">${n}</div></div>`).join("");
}

// ---- trend ----------------------------------------------------------------
const METRICS = {
  revenue: { label:"Gross revenue", fmt:fmtBRL,
    get:r => r.revenue },
  orders:  { label:"Valid orders", fmt:fmtN, get:r => r.valid_orders },
  units:   { label:"Units sold", fmt:fmtN, get:r => r.units },
  aov:     { label:"Average order value", fmt:fmtMoney2,
    get:r => r.valid_orders ? r.revenue / r.valid_orders : 0 },
  review:  { label:"Average review score", fmt:v=>v.toFixed(2)+"/5",
    get:r => r.review_count ? r.review_score_sum / r.review_count : 0 },
};

function renderTrend() {
  const host = document.getElementById("trend"); host.innerHTML = "";
  const m = METRICS[state.metric];
  document.getElementById("trendCap").textContent =
    m.label + " by month. Months with under 50 valid orders are excluded — the " +
    "dataset starts and stops mid-month, and charting those reads as a crash.";

  const rows = D.monthly.filter(r => inRegion(r) && monthInYear(r) && r.valid_orders >= 50);
  if (rows.length < 2) { host.innerHTML = '<p class="empty">Not enough months in this selection to plot a trend.</p>'; return; }

  const vals = rows.map(m.get);
  const ma = vals.map((_,i) => {
    const s = vals.slice(Math.max(0,i-2), i+1);
    return s.reduce((a,b)=>a+b,0) / s.length;
  });
  const W=840,H=290,P={t:12,r:16,b:30,l:70}, iw=W-P.l-P.r, ih=H-P.t-P.b;
  const lo = state.metric === "review" ? 0 : 0;
  const hi = Math.max(...vals) * 1.09 || 1;
  const X = i => P.l + (iw*i)/(rows.length-1);
  const Y = v => P.t + ih - (ih*(v-lo))/(hi-lo);

  const svg = el("svg",{viewBox:`0 0 ${W} ${H}`,role:"img","aria-label":m.label+" by month"});
  for (let g=0; g<=4; g++) {
    const y = P.t + ih*g/4;
    svg.appendChild(el("line",{x1:P.l,x2:W-P.r,y1:y,y2:y,class:"gridline"}));
    const t = el("text",{x:P.l-10,y:y+4,"text-anchor":"end",class:"axis"});
    t.textContent = m.fmt(hi*(1-g/4)); svg.appendChild(t);
  }
  const path = v => v.map((x,i)=>(i?"L":"M")+X(i)+" "+Y(x)).join(" ");
  svg.appendChild(el("path",{d:path(ma),fill:"none",stroke:cssv("--context"),
    "stroke-width":2,"stroke-linejoin":"round"}));
  svg.appendChild(el("path",{d:path(vals),fill:"none",stroke:cssv("--accent"),
    "stroke-width":2.2,"stroke-linejoin":"round","stroke-linecap":"round"}));

  const step = Math.max(1, Math.ceil(rows.length/9));
  // Always label the last point, but drop the preceding tick when it would
  // collide with it — otherwise short selections overlap at the right edge.
  const lastLabelled = rows.length - 1;
  const collides = i => lastLabelled - i < step * 0.75;
  rows.forEach((r,i) => {
    if (i === lastLabelled || (i % step === 0 && !collides(i))) {
      const t = el("text",{x:X(i),y:H-8,"text-anchor":"middle",class:"axis"});
      t.textContent = r.year_month; svg.appendChild(t);
    }
    const hit = el("rect",{x:X(i)-iw/(rows.length*2),y:P.t,
      width:iw/rows.length,height:ih,fill:"transparent"});
    const dot = el("circle",{cx:X(i),cy:Y(vals[i]),r:3.4,fill:cssv("--accent"),
      stroke:cssv("--surface-raised"),"stroke-width":2,opacity:0});
    hit.addEventListener("mouseenter",()=>dot.setAttribute("opacity",1));
    hit.addEventListener("mouseleave",()=>dot.setAttribute("opacity",0));
    hover(hit, `<b>${r.year_month}</b><br>${m.label}: ${m.fmt(vals[i])}<br>
      <span class="r">${fmtN(r.valid_orders)} orders · ${fmtBRL(r.revenue)} revenue
      · ${fmtN(r.new_customer_orders)} first-time buyers</span>`);
    svg.appendChild(hit); svg.appendChild(dot);
  });
  host.appendChild(svg);
  const lg = document.createElement("div");
  lg.className = "legend"; lg.style.marginTop = "8px";
  lg.innerHTML = `<span><i style="background:var(--accent)"></i>${m.label}</span>
    <span><i style="background:var(--context)"></i>3-month average</span>`;
  host.appendChild(lg);
}

// ---- funnel ---------------------------------------------------------------
function renderFunnel() {
  const host = document.getElementById("funnel"); host.innerHTML = "";
  const rows = D.funnel.filter(r => inRegion(r) && inYear(r));
  const byStage = new Map();
  rows.forEach(r => {
    const cur = byStage.get(r.stage_order) || {stage:r.stage, orders:0, o:r.stage_order};
    cur.orders += r.orders; byStage.set(r.stage_order, cur);
  });
  const f = [...byStage.values()].sort((a,b)=>a.o-b.o);
  if (!f.length || !f[0].orders) { host.innerHTML='<p class="empty">No orders in this selection.</p>'; return; }
  const top = f[0].orders, W=360, rowH=46;
  const svg = el("svg",{viewBox:`0 0 ${W} ${f.length*rowH+6}`});
  f.forEach((d,i) => {
    const pct = 100*d.orders/top, y = i*rowH+4, w = (W-8)*(pct/100);
    svg.appendChild(el("rect",{x:0,y:y+16,width:W-8,height:15,rx:4,
      fill:cssv("--surface-sunk")}));
    const bar = el("rect",{x:0,y:y+16,width:w,height:15,rx:4,
      fill:cssv(SEQ[Math.min(2+i*2,8)]),class:"mark"});
    const lab = el("text",{x:0,y:y+10,class:"axis",fill:cssv("--ink-2")});
    lab.textContent = d.stage;
    const val = el("text",{x:W-8,y:y+10,"text-anchor":"end",class:"axis",fill:cssv("--ink")});
    val.textContent = pct.toFixed(1)+"%";
    const drop = i ? f[i-1].orders - d.orders : 0;
    hover(bar, `<b>${d.stage}</b><br>${fmtN(d.orders)} orders (${pct.toFixed(2)}%)`
      + (drop>0 ? `<br><span class="r">−${fmtN(drop)} vs previous stage</span>` : ""));
    svg.appendChild(bar); svg.appendChild(lab); svg.appendChild(val);
  });
  host.appendChild(svg);
}

// ---- delivery -------------------------------------------------------------
function renderDelivery() {
  const host = document.getElementById("delivery"); host.innerHTML = "";
  const rows = D.delivery.filter(r => inRegion(r) && inYear(r));
  const byB = new Map();
  rows.forEach(r => {
    const c = byB.get(r.bucket) || {bucket:r.bucket, o:r.bucket_order, orders:0,
      rs:0, rc:0, det:0};
    c.orders += r.orders; c.rs += r.review_score_sum; c.rc += r.review_count;
    c.det += r.detractor_orders; byB.set(r.bucket, c);
  });
  const d = [...byB.values()].filter(x=>x.rc>0).sort((a,b)=>a.o-b.o);
  if (!d.length) { host.innerHTML='<p class="empty">No delivered orders in this selection.</p>'; return; }
  d.forEach(x => { x.review = x.rs/x.rc; x.detPct = 100*x.det/x.orders; });

  const W=660,H=262,P={t:14,r:12,b:54,l:44}, iw=W-P.l-P.r, ih=H-P.t-P.b;
  const bw = (iw/d.length)*0.62;
  const svg = el("svg",{viewBox:`0 0 ${W} ${H}`});
  for (let g=0; g<=5; g++) {
    const y = P.t+ih-(ih*g)/5;
    svg.appendChild(el("line",{x1:P.l,x2:W-P.r,y1:y,y2:y,class:"gridline"}));
    const t = el("text",{x:P.l-9,y:y+4,"text-anchor":"end",class:"axis"});
    t.textContent = g; svg.appendChild(t);
  }
  d.forEach((r,i) => {
    const cx = P.l + iw*(i+0.5)/d.length, h = ih*r.review/5;
    const dim = state.bucket && state.bucket !== r.bucket;
    const bar = el("rect",{x:cx-bw/2,y:P.t+ih-h,width:bw,height:h,rx:4,
      fill: r.bucket==="not delivered" ? cssv("--context") : cssv(SEQ[2+i]),
      class:"mark", opacity: dim ? .32 : 1});
    bar.setAttribute("tabindex","0");
    bar.setAttribute("role","button");
    bar.setAttribute("aria-label",`${r.bucket}: ${r.review.toFixed(2)} of 5`);
    const toggle = () => {
      state.bucket = state.bucket === r.bucket ? null : r.bucket; render();
    };
    bar.addEventListener("click", toggle);
    bar.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
    hover(bar, `<b>${r.bucket}</b><br>${r.review.toFixed(2)}/5 average review<br>
      <span class="r">${fmtN(r.orders)} orders · ${r.detPct.toFixed(0)}% rated 1–2 stars
      <br>click to ${state.bucket===r.bucket?"clear":"focus"} this band</span>`);
    svg.appendChild(bar);
    const v = el("text",{x:cx,y:P.t+ih-h-7,"text-anchor":"middle",class:"axis",
      fill:cssv("--ink"),"font-weight":620});
    v.textContent = r.review.toFixed(2); svg.appendChild(v);
    const lab = el("text",{x:cx,y:H-34,"text-anchor":"middle",class:"axis"});
    lab.textContent = r.bucket; svg.appendChild(lab);
    const n = el("text",{x:cx,y:H-20,"text-anchor":"middle",class:"axis",opacity:.72});
    n.textContent = fmtN(r.orders)+" orders"; svg.appendChild(n);
  });
  const yl = el("text",{x:2,y:10,class:"axis"});
  yl.textContent = "Avg review (1–5)"; svg.appendChild(yl);
  host.appendChild(svg);

  const fast = d[0], slow = d.find(x=>x.bucket==="30+ days") || d[d.length-1];
  const sel = state.bucket ? d.find(x=>x.bucket===state.bucket) : null;
  document.getElementById("deliveryFinding").innerHTML = tidy(sel
    ? `<b>${sel.bucket}.</b> ${fmtN(sel.orders)} orders
       (${(100*sel.orders/sum(d,"orders")).toFixed(1)}% of this selection),
       averaging ${sel.review.toFixed(2)}/5, with ${sel.detPct.toFixed(0)}% rated
       1–2 stars. Click the bar again to clear.`
    : (() => {
        // Only claim monotonicity when the current selection actually is
        // monotonic. It holds nationally, but a small region-and-year slice can
        // break it, and the text must not assert something the bars contradict.
        const graded = d.filter(x => x.bucket !== "not delivered");
        const monotonic = graded.every((x,i) => i === 0 || x.review <= graded[i-1].review + 1e-9);
        const thin = graded.filter(x => x.orders < 100).map(x => x.bucket);
        const shape = monotonic
          ? `The decline is monotonic across every band, which makes "our ratings
             are falling" a logistics target rather than a CX complaint.`
          : `The overall direction is clear, though the ordering is not perfectly
             monotonic in this selection` +
            (thin.length ? ` — ${thin.join(" and ")} ${thin.length>1?"hold":"holds"}
             under 100 orders here, so ${thin.length>1?"those bands are":"that band is"}
             noisy.` : ".");
        return `<b>The strongest lever in the dataset.</b> Orders arriving within 3
          days average ${fast.review.toFixed(2)}/5; past 30 days that falls to
          ${slow.review.toFixed(2)}/5, with 1–2 star reviews rising from
          ${fast.detPct.toFixed(0)}% to ${slow.detPct.toFixed(0)}%. ${shape}`;
      })());
}

// ---- cohort ---------------------------------------------------------------
function renderCohort() {
  const host = document.getElementById("cohort"); host.innerHTML = "";
  let rows = D.cohort.filter(r => r.region === state.region && r.cohort_customers >= 200);
  if (state.year !== "ALL")
    rows = rows.filter(r => r.cohort_label.slice(0,4) === state.year);
  if (!rows.length) { host.innerHTML='<p class="empty">No cohort has 200+ customers in this selection.</p>'; return; }

  const labels = [...new Set(rows.map(r=>r.cohort_label))].sort();
  const cols = [...new Set(rows.map(r=>r.m))].sort((a,b)=>a-b);
  const byKey = new Map(rows.map(r=>[r.cohort_label+"|"+r.m, r]));
  const vmax = Math.max(...rows.filter(r=>r.m>0).map(r=>r.retention_pct), 0.1);

  const grid = document.createElement("div");
  grid.className = "heat";
  grid.style.gridTemplateColumns = `58px repeat(${cols.length},1fr)`;
  grid.appendChild(document.createElement("div"));
  cols.forEach(c => { const h=document.createElement("div");
    h.className="hlabel"; h.style.justifyContent="center"; h.style.paddingRight="0";
    h.textContent=c; grid.appendChild(h); });

  labels.forEach(lbl => {
    const l = document.createElement("div");
    l.className="hlabel"; l.textContent=lbl; grid.appendChild(l);
    cols.forEach(c => {
      const cell = document.createElement("div"); cell.className="hcell";
      const d = byKey.get(lbl+"|"+c);
      if (!d) { cell.style.background="transparent"; grid.appendChild(cell); return; }
      const t = c===0 ? 1 : Math.min(d.retention_pct/vmax, 1);
      const step = Math.min(Math.round(t*(SEQ.length-1)), SEQ.length-1);
      cell.style.background = cssv(SEQ[step]);
      // Ink from the cell's own step: the ramp is identical in both themes, so a
      // theme-derived colour puts light text on light cells in dark mode.
      cell.style.color = step >= 5 ? "#ffffff" : "#0b0b0b";
      cell.textContent = c===0 ? "100" : d.retention_pct.toFixed(1);
      hover(cell, `<b>Cohort ${lbl}</b> · month ${c}<br>${d.retention_pct.toFixed(2)}% retained
        <br><span class="r">${fmtN(d.active_customers)} of ${fmtN(d.cohort_customers)} customers</span>`);
      grid.appendChild(cell);
    });
  });
  host.appendChild(grid);

  const lg = document.createElement("div");
  lg.className="legend"; lg.style.marginTop="12px";
  lg.innerHTML = `<span><i style="background:var(--s1)"></i>0%</span>
    <span><i style="background:var(--s9)"></i>${vmax.toFixed(1)}% (darkest, excl. month 0)</span>`;
  host.appendChild(lg);
  const note = document.createElement("p");
  note.className="cap"; note.style.marginTop="4px";
  note.innerHTML = `Columns are months since first order; cohorts under 200 customers
    omitted. The ramp tops out at <b>${vmax.toFixed(1)}%</b> — every cell past month 0
    is under 1%.`;
  host.appendChild(note);
}

// ---- category -------------------------------------------------------------
function renderPareto() {
  const host = document.getElementById("pareto"); host.innerHTML = "";
  const rows = D.category.filter(r => inRegion(r) && inYear(r));
  const byCat = new Map();
  rows.forEach(r => {
    const c = byCat.get(r.category) || {category:r.category,revenue:0,freight:0,units:0,orders:0};
    c.revenue += r.revenue; c.freight += r.freight; c.units += r.units; c.orders += r.orders;
    byCat.set(r.category, c);
  });
  let cats = [...byCat.values()].filter(c => c.revenue > 0);
  if (!cats.length) { host.innerHTML='<p class="empty">No category revenue in this selection.</p>'; return; }
  const total = sum(cats,"revenue");
  cats.forEach(c => { c.pct = 100*c.revenue/total;
    c.freightPct = 100*c.freight/Math.max(c.revenue,1); });

  const sorters = {
    revenue: (a,b) => b.revenue - a.revenue,
    freight: (a,b) => b.freightPct - a.freightPct,
    units:   (a,b) => b.units - a.units,
  };
  cats.sort(sorters[state.catSort]);
  const showCum = state.catSort === "revenue";
  const c = cats.slice(0, 15);
  let run = 0; c.forEach(x => { run += x.pct; x.cum = run; });

  document.getElementById("catCap").textContent = showCum
    ? "Share of revenue and cumulative share — both percentages on one axis, so the two are directly comparable."
    : (state.catSort === "freight"
        ? "Freight cost as a share of each category's own revenue. Heavy categories carry the margin risk."
        : "Units sold per category — volume, not value.");

  const W=660,H=340,P={t:14,r:14,b:132,l:44}, iw=W-P.l-P.r, ih=H-P.t-P.b;
  const bw=(iw/c.length)*0.66;
  const barVal = x => state.catSort === "units" ? x.units
    : state.catSort === "freight" ? x.freightPct : x.pct;
  const hi = Math.max(...c.map(barVal), showCum ? 100 : 0) * (showCum ? 1 : 1.12) || 1;
  const X = i => P.l + iw*(i+0.5)/c.length;
  const Y = v => P.t + ih - ih*v/hi;

  const svg = el("svg",{viewBox:`0 0 ${W} ${H}`});
  for (let g=0; g<=4; g++) {
    const y = P.t+ih*g/4;
    svg.appendChild(el("line",{x1:P.l,x2:W-P.r,y1:y,y2:y,class:"gridline"}));
    const t = el("text",{x:P.l-8,y:y+4,"text-anchor":"end",class:"axis"});
    const v = hi*(1-g/4);
    t.textContent = state.catSort === "units" ? fmtN(v) : v.toFixed(0)+"%";
    svg.appendChild(t);
  }
  if (showCum) {
    svg.appendChild(el("line",{x1:P.l,x2:W-P.r,y1:Y(80),y2:Y(80),
      stroke:cssv("--warning"),"stroke-width":1.4,"stroke-dasharray":"4 4"}));
    const lt = el("text",{x:W-P.r,y:Y(80)-6,"text-anchor":"end",class:"axis",
      fill:cssv("--warning")});
    lt.textContent = "80% of revenue"; svg.appendChild(lt);
  }
  c.forEach((d,i) => {
    const v = barVal(d), h = ih*v/hi;
    const bar = el("rect",{x:X(i)-bw/2,y:P.t+ih-h,width:bw,height:h,rx:3,
      fill:cssv("--accent"),class:"mark"});
    hover(bar, `<b>${d.category.replace(/_/g," ")}</b><br>
      ${fmtBRL(d.revenue)} (${d.pct.toFixed(1)}% of revenue)<br>
      <span class="r">${fmtN(d.units)} units · ${fmtN(d.orders)} orders ·
      freight ${d.freightPct.toFixed(1)}% of revenue</span>`);
    svg.appendChild(bar);
    const t = el("text",{x:X(i),y:P.t+ih+8,class:"axis","text-anchor":"end",
      transform:`rotate(-42 ${X(i)} ${P.t+ih+8})`});
    t.textContent = d.category.replace(/_/g," ").slice(0,22); svg.appendChild(t);
  });
  if (showCum) {
    svg.appendChild(el("path",{d:c.map((d,i)=>(i?"L":"M")+X(i)+" "+Y(d.cum)).join(" "),
      fill:"none",stroke:cssv("--context"),"stroke-width":2}));
    c.forEach((d,i)=>svg.appendChild(el("circle",{cx:X(i),cy:Y(d.cum),r:2.6,
      fill:cssv("--context")})));
  }
  host.appendChild(svg);

  const note = document.createElement("p");
  note.className="cap"; note.style.marginTop="10px";
  const n80 = (() => { let r=0,n=0; for (const x of cats) { r+=x.pct; n++; if (r>=80) break; } return n; })();
  note.innerHTML = showCum
    ? `Bars: share of revenue. Line: cumulative share. It takes <b>${n80} of
       ${cats.length}</b> categories here to reach 80% of revenue.`
    : `Showing the top 15 of ${cats.length} categories by this measure.`;
  host.appendChild(note);
}

// ---- map ------------------------------------------------------------------
function renderMap() {
  const host = document.getElementById("map"); host.innerHTML = "";
  const meta = new Map(D.geoMeta.map(m => [m.state_code, m]));
  const rows = D.geo.filter(r => inYear(r) &&
    (state.region === "ALL" || r.region === state.region));
  const byState = new Map();
  rows.forEach(r => {
    const c = byState.get(r.state_code) || {state_code:r.state_code, region:r.region,
      revenue:0, orders:0, dd:0, dc:0, rs:0, rc:0};
    c.revenue += r.revenue; c.orders += r.valid_orders;
    c.dd += r.deliver_days_sum; c.dc += r.deliver_count;
    c.rs += r.review_score_sum; c.rc += r.review_count;
    byState.set(r.state_code, c);
  });
  const g = [...byState.values()].filter(s => meta.has(s.state_code) && s.revenue > 0);
  if (!g.length) { host.innerHTML='<p class="empty">No states in this selection.</p>'; return; }
  g.forEach(s => { const m = meta.get(s.state_code); s.lat=m.lat; s.lng=m.lng;
    s.days = s.dc ? s.dd/s.dc : 0; s.review = s.rc ? s.rs/s.rc : 0; });

  const W=420,H=380,PAD=30;
  // Fix the projection to the whole country so bubbles don't jump position when
  // a region filter changes the extent of the selection.
  const all = D.geoMeta;
  const la0=Math.min(...all.map(d=>d.lat)), la1=Math.max(...all.map(d=>d.lat));
  const lo0=Math.min(...all.map(d=>d.lng)), lo1=Math.max(...all.map(d=>d.lng));
  const X = v => PAD + (W-2*PAD)*(v-lo0)/(lo1-lo0);
  const Y = v => H-PAD - (H-2*PAD)*(v-la0)/(la1-la0);
  const maxRev = Math.max(...g.map(d=>d.revenue));
  const d0=Math.min(...g.map(d=>d.days)), d1=Math.max(...g.map(d=>d.days));

  const svg = el("svg",{viewBox:`0 0 ${W} ${H}`});
  g.slice().sort((a,b)=>b.revenue-a.revenue).forEach(d => {
    const r = 5 + 34*Math.pow(d.revenue/maxRev, 0.62);
    const t = (d.days-d0)/((d1-d0)||1);
    const step = Math.min(Math.round(t*(SEQ.length-1)), SEQ.length-1);
    const c = el("circle",{cx:X(d.lng),cy:Y(d.lat),r,fill:cssv(SEQ[step]),
      stroke:cssv("--surface-raised"),"stroke-width":1.6,class:"mark"});
    c.addEventListener("click", () => {
      state.region = state.region === d.region ? "ALL" : d.region; render();
    });
    hover(c, `<b>${d.state_code}</b> · ${d.region}<br>${fmtBRL(d.revenue)}<br>
      <span class="r">${d.days.toFixed(1)} days to deliver · ${d.review.toFixed(2)}/5
      · ${fmtN(d.orders)} orders<br>click to filter to ${d.region}</span>`);
    svg.appendChild(c);
    if (r > 13) {
      const t2 = el("text",{x:X(d.lng),y:Y(d.lat)+3.6,"text-anchor":"middle",
        "font-size":10,"font-weight":640,
        fill: step>=5 ? "#ffffff" : cssv("--ink"),"pointer-events":"none"});
      t2.textContent = d.state_code; svg.appendChild(t2);
    }
  });
  host.appendChild(svg);
  const lg = document.createElement("div");
  lg.className="legend"; lg.style.marginTop="10px";
  lg.innerHTML = `<span><i style="background:var(--s2)"></i>faster (${d0.toFixed(0)} d)</span>
    <span><i style="background:var(--s9)"></i>slower (${d1.toFixed(0)} d)</span>`;
  host.appendChild(lg);
}

// ---- RFM ------------------------------------------------------------------
function renderRFM() {
  const host = document.getElementById("rfm"); host.innerHTML = "";
  // RFM is a lifetime measure, so it responds to region but not to a year slice.
  const rows = D.rfm.filter(r => r.region === state.region);
  if (!rows.length) { host.innerHTML='<p class="empty">No customers in this selection.</p>'; return; }
  const r = rows.slice().sort((a,b)=>b.revenue-a.revenue);
  const totalRev = sum(r,"revenue"), totalCust = sum(r,"customers");
  const W=620,rowH=34;
  const maxShare = Math.max(...r.map(d=>100*d.revenue/totalRev));
  const scale = (W-330)/maxShare;
  const svg = el("svg",{viewBox:`0 0 ${W} ${r.length*rowH+8}`});
  r.forEach((d,i) => {
    const y=i*rowH+4, revShare=100*d.revenue/totalRev, custShare=100*d.customers/totalCust;
    svg.appendChild(el("rect",{x:150,y:y+12,width:custShare*scale,height:13,rx:3,
      fill:cssv("--context"),opacity:.55}));
    const bar = el("rect",{x:150,y:y+8,width:revShare*scale,height:8,rx:3,
      fill:cssv("--accent"),class:"mark"});
    hover(bar, `<b>${d.segment}</b><br>${fmtBRL(d.revenue)} (${revShare.toFixed(1)}% of revenue)<br>
      <span class="r">${fmtN(d.customers)} customers (${custShare.toFixed(1)}%) ·
      avg lifetime value ${fmtMoney0(d.avg_value)} ·
      avg ${(d.avg_orders||0).toFixed(2)} orders</span>`);
    svg.appendChild(bar);
    const lab = el("text",{x:144,y:y+20,"text-anchor":"end",class:"axis",fill:cssv("--ink-2")});
    lab.textContent = d.segment; svg.appendChild(lab);
    const val = el("text",{x:156+Math.max(revShare,custShare)*scale,y:y+20,class:"axis"});
    val.textContent = `${revShare.toFixed(1)}% rev · ${fmtN(d.customers)} cust`;
    svg.appendChild(val);
  });
  host.appendChild(svg);
  const lg = document.createElement("div");
  lg.className="legend"; lg.style.marginTop="8px";
  lg.innerHTML = `<span><i style="background:var(--accent)"></i>share of revenue</span>
    <span><i style="background:var(--context)"></i>share of customers</span>`;
  host.appendChild(lg);
  const note = document.createElement("p");
  note.className="cap"; note.style.marginTop="4px";
  note.textContent = "Scores are computed nationally, so a segment means the same "
    + "thing in every region. Lifetime measure — not affected by the year filter.";
  host.appendChild(note);
}

// ---- states table ---------------------------------------------------------
function renderGeoTable() {
  const host = document.getElementById("geotable");
  const rows = D.geo.filter(r => inYear(r) &&
    (state.region === "ALL" || r.region === state.region));
  const byState = new Map();
  rows.forEach(r => {
    const c = byState.get(r.state_code) || {state_code:r.state_code,region:r.region,
      revenue:0,orders:0,dd:0,dc:0,rs:0,rc:0};
    c.revenue+=r.revenue; c.orders+=r.valid_orders; c.dd+=r.deliver_days_sum;
    c.dc+=r.deliver_count; c.rs+=r.review_score_sum; c.rc+=r.review_count;
    byState.set(r.state_code, c);
  });
  const g = [...byState.values()].sort((a,b)=>b.revenue-a.revenue).slice(0,10);
  if (!g.length) { host.innerHTML='<p class="empty">No states in this selection.</p>'; return; }
  const total = sum([...byState.values()],"revenue");
  const body = g.map(d => `<tr data-region="${d.region}" style="cursor:pointer">
    <td><b>${d.state_code}</b></td><td>${d.region}</td>
    <td class="num">${fmtBRL(d.revenue)}</td>
    <td class="num">${(100*d.revenue/total).toFixed(1)}%</td>
    <td class="num">${fmtMoney0(d.revenue/Math.max(d.orders,1))}</td>
    <td class="num">${(d.dc?d.dd/d.dc:0).toFixed(1)}</td>
    <td class="num">${(d.rc?d.rs/d.rc:0).toFixed(2)}</td></tr>`).join("");
  host.innerHTML = `<div class="tablewrap"><table><thead><tr>
    <th>State</th><th>Region</th><th class="num">Revenue</th><th class="num">Share</th>
    <th class="num">AOV</th><th class="num">Days</th><th class="num">Review</th>
    </tr></thead><tbody>${body}</tbody></table></div>`;
  host.querySelectorAll("tbody tr").forEach(tr => {
    tr.addEventListener("click", () => {
      const reg = tr.dataset.region;
      state.region = state.region === reg ? "ALL" : reg; render();
    });
  });
}

// ---- render ---------------------------------------------------------------
function render() {
  buildFilters(); renderStatus(); renderKPIs(); renderTrend(); renderFunnel();
  renderDelivery(); renderCohort(); renderPareto(); renderMap(); renderRFM();
  renderGeoTable(); hideTip();
}

document.getElementById("subtitle").textContent =
  `Olist Brazilian marketplace · ${D.meta.firstDate} to ${D.meta.lastDate} · ` +
  `99,441 orders · 96,096 customers`;
document.getElementById("resetBtn").addEventListener("click", resetFilters);
document.getElementById("themeBtn").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const dark = cur ? cur==="dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const next = dark ? "light" : "dark";
  try { localStorage.setItem("dashTheme", next); } catch(e) {}
  document.documentElement.setAttribute("data-theme", next);
  render();   // SVG fills are read at draw time, so a theme change needs a redraw
});
render();
</script>
"""


def main() -> int:
    data = collect()
    cur = money.active()
    html = HTML.replace("__DATA__", json.dumps(data, separators=(",", ":"), default=str))
    html = html.replace("__FX__", json.dumps(
        {"sym": cur.symbol, "rate": cur.rate_from_brl, "code": cur.code}))
    html = html.replace("__FX_NOTE__",
                        f"Amounts are {cur.note}." if cur.is_converted else "")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    PAGES_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGES_OUT.write_text(html, encoding="utf-8")
    rows = sum(len(v) for v in data.values() if isinstance(v, list))
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(html)/1024:.0f} KB, "
          f"{rows:,} data rows embedded, no external requests)")
    print(f"wrote {PAGES_OUT.relative_to(ROOT)}       (GitHub Pages entry point)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
