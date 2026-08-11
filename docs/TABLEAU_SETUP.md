# Tableau: complete setup, click by click

From nothing installed → a published dashboard with a public link, in about
90 minutes. No prior Tableau experience assumed.

**Before you start, be clear on why you are doing this.** You already have a
working, verified, interactive dashboard (`docs/index.html`). Tableau adds one
thing: **the word "Tableau" on your CV, backed by a real published workbook.**
That is a genuinely useful thing for BI job applications — most listings name a
BI tool explicitly. But if you are short on time, the HTML dashboard is the
stronger artefact. Do this when you have a clear afternoon.

---

## Honest status of the included `.twb`

There is a file at `dashboards/tableau/sales_intelligence.twb`. **I could not
test it** — Tableau is a graphical app and was not installed when this project
was built. The XML is well-formed and follows the documented structure, but it
has never been opened.

**You can settle this in 30 seconds now that you have Tableau installed:**
double-click the file. One of three things happens.

| What happens | What it means |
|---|---|
| It opens with charts | Great — skip ahead to Step 5 (dashboard assembly) |
| It opens but the sheets are blank | The data connections need repointing at `extracts/`. Fixable, but building from scratch is probably faster |
| Tableau refuses to open it | Expected. Close without saving and build from Step 0 |

The file declares workbook version `18.1`, which is much older than your
Tableau 2026.2 — it may upgrade it silently, or it may not.

**Either way, building it yourself is the better use of an afternoon.** It takes
~90 minutes, and it means you can actually answer questions about it in an
interview. Opening a file someone else generated teaches you nothing.

---

## Step 0 — Install Tableau **Public** (15 min, free forever)

> ### ⚠️ Get the right app — this is the easiest mistake to make
>
> Tableau's website pushes **Tableau Desktop** much harder than Tableau Public.
> They are different products:
>
> | | **Tableau Public** ← you want this | Tableau Desktop |
> |---|---|---|
> | Cost | **Free forever** | 14-day trial, then ~$75/month |
> | Publishes to | `public.tableau.com` — email login | Tableau **Cloud/Server** — needs a company site URI |
> | When the trial ends | n/a | App locks. You can't reopen your own workbook. |
>
> **How to tell which one you installed:** look in Applications. If it says
> *"Tableau Desktop"* you have the wrong one. Install Public as well — they can
> coexist.
>
> **If you land on a screen saying "Tell us where to sign in — enter your site
> URI"**, you have opened `Server → Sign In`, which is for Tableau
> Cloud/Server. Press **Cancel**. You will never need that menu item. The
> Tableau Public path is `Server → Tableau Public → Save to Tableau Public As…`
> (see Step 6).

1. Go to **<https://public.tableau.com/app/discover>**
2. Click **Sign Up** (top right) — you need an account to publish later
3. Confirm your email address
4. Go to **<https://www.tableau.com/products/public/download>**
5. Check the download button says **Tableau Public**, not "Try Tableau Desktop
   for free". Download for Mac, open the `.dmg`, drag to Applications
6. Open it. If macOS says "unidentified developer": **System Settings →
   Privacy & Security → Open Anyway**
7. Confirm the app title bar / Applications entry reads **Tableau Public**

> **Why Public and not Desktop for a portfolio?** Everything you publish on
> Public is visible to anyone — which is exactly what you want for a CV link.
> And it never expires, so you can still edit the workbook the week before an
> interview.

---

## Step 1 — Prepare the data files (2 min)

```bash
cd "/Users/dishu/sales platform"
python -m src.etl.run_pipeline
```

This writes 10 CSV files to `dashboards/tableau/extracts/`. They total about
250 KB — deliberately small, because all the heavy calculation already happened
in SQL.

Check they exist:

```bash
ls dashboards/tableau/extracts/
```

| File | Rows | What it's for |
|---|---|---|
| `mart_kpi_monthly.csv` | 25 | The trend line |
| `mart_dash_kpi.csv` | 24 | KPI tiles, pre-sliced by region × year |
| `mart_delivery_performance.csv` | 590 | The delivery/review chart |
| `mart_category_performance.csv` | 74 | Category Pareto |
| `mart_geo_performance.csv` | 27 | The map |
| `mart_cohort_retention.csv` | 220 | Cohort heatmap |
| `mart_order_funnel.csv` | 125 | Funnel |
| `mart_dash_rfm.csv` | 53 | RFM segments |
| `mart_payment_mix.csv` | 169 | Payment split |
| `mart_kpi_daily.csv` | 777 | Daily detail (optional) |

> **Why so small?** Because Tableau is only drawing here. Aggregation, business
> rules and definitions all live in SQL where they can be code-reviewed and
> tested. This is a deliberate architectural choice and a good thing to say out
> loud in an interview.

---

## Step 2 — Connect the data (10 min)

1. Open Tableau Public
2. Left panel, under **Connect → To a File**, click **Text file**
3. Navigate to `sales platform/dashboards/tableau/extracts/`
4. Select `mart_kpi_monthly.csv` → **Open**

You'll land on the Data Source tab showing a preview. Good.

Now add the other files **as separate data sources** (not joined):

5. Top-left, next to the data source name, click the **↓ arrow** → **New Data
   Source** → **Text file** → pick the next CSV
6. Repeat for all 10

> **Why separate, not joined?** Each file is already at exactly the grain its
> chart needs. Joining them would multiply rows and inflate every number — the
> classic "fan-out" bug. Keeping them separate makes it impossible.

### One required fix — the map won't work without it

7. Click the `mart_geo_performance` data source
8. In the field list find **Latitude**. Click its **#** icon → **Geographic
   Role → Latitude**
9. Do the same for **Longitude** → **Geographic Role → Longitude**

They import as plain numbers; Tableau needs to be told they're coordinates.

---

## Step 3 — Install the colour palette (1 min)

Run this — **do not copy XML into a file by hand**:

```bash
cd "/Users/dishu/sales platform"
python scripts/install_tableau_palette.py
```

Then **quit Tableau completely and reopen it.** The palette file is only read at
startup.

<details>
<summary><b>Why a script instead of "paste this XML"?</b></summary>

Hand-copying that file fails in two ways that both produce the same
unhelpful Tableau error — `Fatal Error(1,1): invalid document structure`:

- **The markdown code fence comes along for the ride.** The file ends up
  starting with ` ```xml ` instead of `<?xml`, and Tableau gives up at the very
  first character. This is the most common cause by far.
- **TextEdit saves it as RTF**, or silently converts straight quotes `'` into
  curly ones `’`, which XML will not accept.

The script writes the file directly, so neither can happen. It also backs up
whatever was there, merges rather than overwrites if you already have palettes,
and re-parses the result before finishing — so it never leaves Tableau with a
broken file.

**Already hit that error?** Just run the script; it repairs the file in place.
</details>

It installs two palettes:

| Palette | Type | Use for |
|---|---|---|
| `Warehouse Sequential Blue` | ordered-sequential | Anything showing *magnitude*: cohort heatmap, map, funnel stages, delivery bands |
| `Warehouse Categorical` | regular | Telling distinct series apart (max 3) |

Individual colours for single marks:

| Use | Hex |
|---|---|
| Main / single-series marks | `#2a78d6` |
| Secondary / context marks | `#b6b3ac` |
| Reference lines | `#eda100` |
| Bad / detractors | `#e34948` |

These aren't taste. They were checked with a contrast and colour-vision
validator (`src/viz/palette.py`) so the charts stay readable for colour-blind
viewers and in greyscale print.

**Two rules worth stating in an interview:**

- **Never put two different measures on two y-axes.** It lets a chart imply a
  relationship that isn't in the data. Where two things must share a chart, put
  them both in the same unit (as the Pareto does — both are percentages).
- **Colour follows the thing, not its rank.** If a filter removes a series, the
  survivors must not change colour.

---

## Step 4 — Build the sheets

Each sheet: bottom-left, click the **New Worksheet** icon (a small chart with a
`+`). Rename by double-clicking the tab.

### 4.1 — "Delivery vs review"  ⭐ build this one first

This is the sheet that carries your interview story. Data source:
**mart_delivery_performance**.

First, a calculated field. **Analysis → Create Calculated Field**, name it
`Weighted review score`:

```
SUM([Avg Review Score] * [Orders]) / SUM([Orders])
```

> **Why not just `AVG(avg_review_score)`?** Because the file has one row per
> month × region × bucket. A plain average treats a row covering 12 orders the
> same as one covering 4,000. The weighted version is the correct number. This
> is exactly the kind of detail an interviewer probes for.

Now build it:

| Where to drag | What |
|---|---|
| **Columns** | `Delivery Speed Bucket` |
| **Rows** | `Weighted review score` |
| **Colour** (Marks card) | `Bucket Order` |
| **Label** (Marks card) | `Weighted review score` |

- Marks type: **Bar**
- Click **Colour → Edit Colors → Warehouse Sequential Blue**
- Right-click the vertical axis → **Edit Axis** → Range **Fixed**, 0 to 5
- Right-click `Delivery Speed Bucket` on Columns → **Sort** → By Field →
  `Bucket Order` → Ascending
- Click **Label**, set number format to 2 decimal places

Rename the sheet **Delivery vs review**. It should show 4.46 falling to 2.19.

<details>
<summary><b>If the bars are in the wrong order</b></summary>

Right-click `Delivery Speed Bucket` in the left field list → **Default
Properties → Sort → Manual**, and drag into this order:
`0-3 days, 4-7 days, 8-14 days, 15-30 days, 30+ days, not delivered`.
</details>

### 4.2 — "Revenue trend"

Data source: **mart_kpi_monthly**.

| Where | What |
|---|---|
| **Columns** | `Year Month` (make it **discrete** — right-click → Discrete) |
| **Rows** | `SUM(Revenue)` |
| **Filters** | `Valid Orders` → At least → `50` |

- Marks type: **Line**, colour `#2a78d6`, size one notch above default
- **Analysis → Trend Lines → Show Trend Lines** *(optional)*

Add the context line: drag `Revenue` to Rows a second time → right-click it →
**Quick Table Calculation → Moving Average**. Then right-click → **Dual Axis**?
**No — do not.** Instead right-click the second axis → **Synchronize Axis**, or
better, use **Measure Values** so both share one axis. Set its colour to
`#b6b3ac`.

> **The `Valid Orders ≥ 50` filter matters.** The first and last months of the
> dataset contain almost nothing (Sept–Oct 2018 has 20 orders, 19 cancelled).
> Without the filter the chart shows a cliff that looks like the business
> collapsing. It didn't — the data just stops.

### 4.3 — "Cohort retention"

Data source: **mart_cohort_retention**.

| Where | What |
|---|---|
| **Columns** | `Months Since First Order` (discrete) |
| **Rows** | `Cohort Label` (discrete) |
| **Colour** | `AVG(Retention Pct)` |
| **Label** | `AVG(Retention Pct)` |
| **Filters** | `Cohort Customers` ≥ 500 · `Months Since First Order` ≤ 11 |

- Marks type: **Square**
- **Colour → Edit Colors → Warehouse Sequential Blue**
- **Critical:** in that same dialog, tick **Fixed** and set **End** to `0.7`

> Month 0 is 100% by definition — everyone in a cohort bought in month 0, that's
> what makes them the cohort. On an automatic colour scale that single column
> eats the entire range and every real value washes out to white. Fixing the end
> at 0.7 makes the months that actually matter readable.

Add a caption (**Worksheet → Show Caption**) saying the scale tops out at 0.7%,
so nobody misreads a mid-blue cell as healthy retention.

### 4.4 — "Category Pareto"

Data source: **mart_category_performance**.

| Where | What |
|---|---|
| **Columns** | `Category` |
| **Rows** | `SUM(Pct Of Total Revenue)` — bars |
| **Filters** | `Revenue Rank` ≤ 15 |

- Sort Columns by `SUM(Gross Revenue)` descending
- Drag `Cumulative Revenue Pct` onto the **same** axis via **Measure Values**;
  set that measure's mark type to **Line**, colour `#b6b3ac`
- Right-click axis → **Add Reference Line** → Value `80`, colour `#eda100`,
  dashed, label "80% of revenue"

Both series are percentages, so one axis is honest. **Do not use a dual axis
here** even though most Pareto tutorials do.

### 4.5 — "Geography"

Data source: **mart_geo_performance**.

| Where | What |
|---|---|
| **Columns** | `AVG(Longitude)` |
| **Rows** | `AVG(Latitude)` |
| **Detail** | `State Code` |
| **Size** | `SUM(Revenue)` |
| **Colour** | `AVG(Avg Days To Deliver)` → Warehouse Sequential Blue |

Marks type: **Circle** (or **Map** if you'd rather Tableau geocode Brazilian
states — then just put `State Code` on Detail and delete the lat/long).

⚠️ **Label contrast:** the fastest states get the palest fill. White text on a
pale bubble is unreadable. Either use dark labels, or add a halo
(**Label → Font → Halo**), or only label the darker marks.

### 4.6 — "Fulfilment funnel"

Data source: **mart_order_funnel**.

| Where | What |
|---|---|
| **Rows** | `Stage`, sorted by `MIN(Stage Order)` ascending |
| **Columns** | `SUM(Orders)` |
| **Colour** | `MIN(Stage Order)` → Warehouse Sequential Blue |

Calculated field `Pct of purchased`:

```
SUM([Orders]) / TOTAL(SUM(IIF([Stage Order] = 1, [Orders], NULL)))
```

If that gives you trouble, the simple version works fine — the top of the funnel
is every order in the dataset:

```
SUM([Orders]) / 99441
```

Format as a percentage, put it on **Label**.

### 4.7 — "RFM segments"

Data source: **mart_dash_rfm**. Add filter `Region` = `ALL` (the file contains
both per-region rows and the national rollup — without the filter you'd
double-count).

| Where | What |
|---|---|
| **Rows** | `Segment`, sorted by `SUM(Revenue)` descending |
| **Columns** | `SUM(Revenue)` |
| **Label** | `SUM(Customers)` |

Marks: **Bar**, colour `#2a78d6`.

### 4.8 — KPI tiles

Data source: **mart_dash_kpi**. Filter to `Region = ALL` **and**
`Year Label = ALL`.

Make one sheet per number. For each: drag the measure to **Text** on the Marks
card, set mark type to **Text**, then **Format → Borders → none**, and hide
headers (right-click each axis → untick **Show Header**).

| Tile | Field | Format |
|---|---|---|
| Gross revenue | `SUM(Revenue)` | Currency, `R$0.00,,"M"` |
| Orders | `SUM(Valid Orders)` | Number, thousands separator |
| Customers | `SUM(Customers)` | Number, thousands separator |
| Avg review | `AVG(Avg Review Score)` | 2 decimals |
| Avg delivery days | `AVG(Avg Days To Deliver)` | 1 decimal |

Font: 26pt semibold, colour `#0b0b0b`.

---

## Step 5 — Assemble the dashboard (20 min)

1. Bottom bar → **New Dashboard** icon
2. Left panel → **Size** → **Automatic**, then set the design width to `1280`
3. Drag sheets from the left onto the canvas in this arrangement:

```
┌──────────────────────────────────────────────────────────┐
│  KPI tiles  (drag each into one horizontal container)     │
├───────────────────────────────┬──────────────────────────┤
│  Revenue trend                │  Fulfilment funnel       │
├───────────────────────────────┼──────────────────────────┤
│  Delivery vs review           │  Cohort retention        │
├───────────────────────────────┼──────────────────────────┤
│  Category Pareto              │  Geography               │
├───────────────────────────────┴──────────────────────────┤
│  RFM segments                                             │
└──────────────────────────────────────────────────────────┘
```

**Tip:** drag a **Horizontal** container from the Objects panel first, then drop
sheets into it. Far easier to control than free-floating.

### Make it interactive

This is what turns it from a poster into a dashboard.

**Filters that affect everything:**

1. On the `Delivery vs review` sheet: right-click `Region` in the data pane →
   **Show Filter**
2. On the dashboard, click the filter card → its **▾** menu → **Apply to
   Worksheets → All Using This Data Source**
3. Repeat for a `Year Month` range filter from `mart_kpi_monthly`

> Because each sheet uses a different data source, a filter only reaches its own
> source. To make one filter drive everything you need **Data → Edit Blend
> Relationships**, or simpler: add a `Region` filter to each sheet separately and
> set them all to the same value. The HTML dashboard sidesteps this entirely,
> which is a fair thing to point out if asked to compare the two.

**Click-to-filter:**

4. Hover the `Delivery vs review` sheet on the dashboard → click the small
   **funnel icon** in its top-right → this makes it **Use as Filter**
5. Now clicking a delivery bar filters the other sheets

**Tooltips:** on each sheet, click **Tooltip** on the Marks card and add the
supporting fields (orders, revenue, review score).

### Final polish

- Dashboard background: **Format → Dashboard → Shading** → `#fcfcfb`
- Each sheet: **Layout → Border** → 1px `#e6e4df`, Background white
- Add a **Text** object at the top: "Sales & Customer Intelligence · Olist
  Brazilian marketplace · 99,441 orders · Sep 2016 – Oct 2018"
- Delete all gridlines you don't need: **Format → Lines → Grid Lines → None**

---

## Step 6 — Publish (5 min)

1. **Server → Tableau Public → Save to Tableau Public As…**

   ⚠️ **Not** `Server → Sign In`. That one is for Tableau Cloud/Server and asks
   for a "site URI" you do not have. If you see that prompt, press Cancel and
   use the menu path above.

2. Sign in with the tableau.com account from Step 0 — **email and password
   only, no site URI**
3. Name it: `Sales & Customer Intelligence — Olist Brazilian E-Commerce`
4. **Before saving, untick "Show Sheets as Tabs"** — otherwise viewers land on a
   worksheet instead of your dashboard
5. Save. A browser opens with your published workbook.

On the published page:

6. Click **Edit Details** → write a description:

> Star schema over 99,441 real Brazilian e-commerce orders. Python + SQL ETL,
> 5 dimensions, 3 facts, 18 marts, 12 data-quality checks.
> Source: github.com/&lt;your-username&gt;/sales-platform

7. Copy the URL. It looks like:

```
https://public.tableau.com/app/profile/your.name/viz/SalesCustomerIntelligence/Dashboard
```

**That's the link for your CV.**

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Tell us where to sign in — enter your site URI" | You opened `Server → Sign In` (Tableau Cloud/Server). Press Cancel. Use `Server → Tableau Public → Save to Tableau Public As…` instead — Step 6 |
| No "Tableau Public" option under the Server menu | You installed Tableau **Desktop**, not Tableau **Public**. Install Public — Step 0 |
| Tableau asks for a licence key or says the trial expired | Same cause: that's Tableau Desktop. Public is free and never expires |
| "This file cannot be opened" | Re-run `python -m src.etl.run_pipeline` — the extracts may not exist |
| Map shows one dot in Africa | Latitude/Longitude aren't set as geographic roles — go back to Step 2.7 |
| Cohort heatmap is all white | Colour scale end isn't fixed at 0.7 — Step 4.3 |
| Review scores look wrong (~2.9) | You used `AVG(avg_review_score)` instead of the weighted calculation — Step 4.1 |
| Revenue trend falls off a cliff | Missing the `Valid Orders ≥ 50` filter — Step 4.2 |
| Numbers don't match the HTML dashboard | Check you filtered `mart_dash_*` sources to `Region = ALL`; they contain rollup rows *and* per-region rows |
| Filter only affects one sheet | Filter card **▾** → **Apply to Worksheets → All Using This Data Source** |
| Colours aren't in the palette list | Quit Tableau **completely** (Cmd-Q) and reopen — it only reads the file at startup |
| `Fatal Error(1,1): invalid document structure` on Preferences.tps | The file starts with something other than `<?xml` — usually a copied ` ```xml ` code fence, or TextEdit saved it as RTF. Run `python scripts/install_tableau_palette.py` to repair it |

---

## What to say about it

If an interviewer asks why the calculations are in SQL rather than in Tableau:

> "The marts are built and tested in SQL so the definitions are version
> controlled and covered by data-quality checks — the pipeline won't publish if
> revenue doesn't reconcile between the order and line-item grains. Tableau then
> does presentation only. It means the dashboard, the notebook and the chatbot
> all read the same numbers, and if a definition changes I fix it in one place."

That answer is worth more than the dashboard itself.
