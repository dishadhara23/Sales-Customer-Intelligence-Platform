# How this whole thing works — explained simply

No jargon. If you can follow a recipe, you can follow this.

---

## The one-sentence version

We took a big pile of messy shop receipts, tidied them into neat labelled boxes,
counted them up in useful ways, and made a picture you can click on.

---

## Who does what

There are four separate things in this project. **They do not depend on each
other**, and only one of them uses AI:

| Thing | What it is | Needs internet? | Needs AI? |
|---|---|---|---|
| **The pipeline** | Python code that tidies the data | No | **No** |
| **The dashboard** | One HTML file with pictures | No | **No** |
| **The notebook** | A document showing the working-out | No | **No** |
| **The chatbot** | Ask questions in English, get answers | Yes | **Yes** |

> **A question you asked: why was the dashboard on a claude.ai link?**
> Only because that was a quick way to give you something clickable. The
> dashboard itself is a plain file — you can open it with no internet, no
> account, and no AI. It is now also saved as `docs/index.html` so GitHub can
> host it on **your** web address instead. See "Putting it on the internet" below.

---

## Part 1 — Where the data comes from

Imagine a Brazilian online shop called Olist. Between September 2016 and October
2018, **99,441 orders** were placed. Olist published all of it for free.

### Why the money needed converting

Olist is Brazilian, so every price in the data is in **Brazilian Real** (R$).
The dashboard shows **pounds** — which means the numbers were *converted*, not
just relabelled.

That sounds like a fussy distinction. It isn't. R$1 is about 22p, so if you
simply swapped the "R$" symbol for a "£" and changed nothing else, every figure
on the dashboard would be **roughly four and a half times too big** — and it
would still look completely believable. Nobody would spot it.

So three rules:

1. The stored data is never touched. It stays in Real, exactly as published.
2. The conversion happens at the last possible moment, when a number is drawn on
   screen, in one single file (`src/viz/money.py`). One rate, one place.
3. Every screen that shows converted money **says so**, including the rate used.

The rate is a fixed number rather than today's live exchange rate, on purpose.
The data is from 2016–18, so today's rate would be the wrong one anyway — and if
the project fetched a live rate, every rebuild would produce slightly different
numbers and you could never check anyone's work.

It arrives as **9 spreadsheet files**. Think of them as 9 separate notebooks:

| File | What's inside | Rows |
|---|---|---|
| `orders` | one line per order: when it was bought, when it arrived | 99,441 |
| `order_items` | one line per **thing** in an order | 112,650 |
| `customers` | who bought | 99,441 |
| `products` | what was sold | 32,951 |
| `sellers` | who sold it | 3,095 |
| `payments` | how it was paid for | 103,886 |
| `reviews` | the star rating people left | 100,000 |
| `geolocation` | where every postcode is on a map | 1,000,163 |
| `category_translation` | Portuguese → English product names | 71 |

`python scripts/download_data.py` fetches all nine.

**A small careful thing:** the script counts the rows in each file and stops if
the count is wrong. If someone swapped a file for a broken one, we'd find out
immediately instead of quietly reporting wrong numbers for weeks.

---

## Part 2 — The trap that catches almost everyone

This is the most important part of the whole project. If you only remember one
thing, remember this.

In the customer file there are two ID columns:

```
customer_id          →  a NEW code for EVERY order
customer_unique_id   →  the SAME code for the SAME person, always
```

Imagine a library that gives you a **brand new library card every time you
borrow a book**. If you counted cards, you'd conclude nobody ever borrows twice.

That's exactly what `customer_id` does.

| If you use… | "How many people came back?" |
|---|---|
| `customer_id` | **0.00%** — completely wrong |
| `customer_unique_id` | **3.12%** — correct |

Nearly every beginner analysis of this dataset gets this wrong, reports "0%
repeat customers", and never notices. We use `customer_unique_id` everywhere.

**Why it matters for a job interview:** if someone asks "what was tricky about
this dataset?", this is your answer.

---

## Part 3 — Tidying up (the "ETL pipeline")

ETL stands for **E**xtract, **T**ransform, **L**oad. It just means:
*get it, clean it, put it away neatly.*

Run it with one command:

```bash
python -m src.etl.run_pipeline
```

Here's what happens, in order.

### Step 1 — Copy the files into a database (raw shelf)

A database is just a very good filing cabinet. We copy the 9 files in almost
unchanged. These are called **staging** tables (`stg_orders`, `stg_customers`…).
"Staging" = the messy shelf you keep things on before sorting them.

We only do tiny tidying here:
- Turn "12/03/2017" text into a real date the computer understands
- Make `"SP "`, `"sp"` and `"Sp"` all become `sp`
- Delete exact duplicate rows

**One exception.** The geolocation file has **1 million rows** — several map
readings per postcode. If we joined that to orders, every order would multiply
into ~30 copies and revenue would look 30× bigger. So we squash it first to
**one row per postcode**, taking the middle coordinate. 1,000,163 rows → 19,010.

### Step 2 — Sort into proper boxes (the "star schema")

Now the real organising. We split everything into two kinds of box:

**Dimension boxes = the nouns.** Descriptions of things.

| Box | One row per | Rows |
|---|---|---|
| `dim_customer` | **person** (not per order!) | 96,096 |
| `dim_product` | product | 32,951 |
| `dim_seller` | seller | 3,095 |
| `dim_date` | calendar day | 777 |
| `dim_geography` | postcode area | 19,010 |

**Fact boxes = the verbs.** Things that happened, with numbers attached.

| Box | One row per | Rows |
|---|---|---|
| `fact_orders` | order | 99,441 |
| `fact_order_items` | item inside an order | 112,650 |
| `fact_payments` | payment | 103,886 |

It's called a **star schema** because if you draw it, the fact box sits in the
middle with dimension boxes around it — like a star. It's the standard way
companies organise data for reporting, and it's what the job ads mean when they
say "dimensional modelling".

<details>
<summary><b>Why is `dim_date` a whole box just for dates?</b></summary>

So you can ask "how do weekends compare to weekdays?" without every single
query having to work out what day of the week it was. We calculate it once,
store it, and every chart reuses it. It also has **every single day** in it,
even days with zero orders — otherwise a quiet day would just vanish from a
chart instead of showing as a dip.
</details>

### Step 3 — Do the maths once (the "marts")

A **mart** is a pre-calculated answer. Instead of every chart working out
"revenue per month" from scratch, we work it out **once**, save it, and every
chart just reads it. Faster, and — more importantly — everyone gets the *same*
number.

There are 17 of them. The main ones:

| Mart | Answers |
|---|---|
| `mart_kpi_monthly` | How much did we sell each month? |
| `mart_rfm` | Which customers are valuable? |
| `mart_cohort_retention` | Do people come back? |
| `mart_order_funnel` | Where do orders get stuck? |
| `mart_category_performance` | Which products make money? |
| `mart_geo_performance` | Which parts of Brazil? |
| `mart_delivery_performance` | Does slow delivery upset people? |
| `mart_dash_*` (8 more) | The same things, pre-sliced by region and year so the dashboard filters are instant |

### Step 4 — Check our work (the bit most people skip)

Before anything is allowed out, **12 checks** must pass. If one fails, the
pipeline stops and refuses to build the dashboard.

Examples:

- *"Are there exactly 99,441 orders?"* — if not, we lost or duplicated data
- *"Does revenue counted per-order match revenue counted per-item?"* — must be
  within R$1
- *"Is month 0 of every cohort exactly 100%?"* — it must be, by definition

**Two of these caught real mistakes while building this.** The revenue one and
the cohort one. That's not a nice story — that's the whole point of having them.

```
12/12 checks passed.
```

---

## Part 4 — The dashboard (and how the filters work)

Open `dashboards/executive_dashboard.html`. Double-click it. That's it.

**It is one single file.** Inside it there are three things glued together:

1. **The numbers** — already calculated, written into the file as text
2. **The drawing instructions** — code that turns numbers into shapes
3. **The styling** — colours, spacing, fonts

No server. No database. No internet. No AI. You could email it to someone and
it would work on their laptop on a plane.

### What you can click

| Control | What it does |
|---|---|
| **Region** buttons | Whole dashboard switches to that region only |
| **Year** buttons | Whole dashboard switches to that year only |
| **Revenue / Orders / AOV / Units / Avg review** | Changes what the trend line shows |
| **By revenue / freight % / units** | Re-sorts the category chart |
| **Click a delivery bar** | Focuses that band; the summary text rewrites itself |
| **Click a map bubble** | Filters to that state's region |
| **Click a table row** | Same |
| **Hover anything** | Details pop up |
| **Toggle theme** | Light ↔ dark |
| **Reset** | Back to everything |

### The clever bit (and why it's honest)

Here's a real problem. Say you want "how many customers in the South in 2018".

You might think: just add up the monthly numbers. **That's wrong.** If the same
person bought in March *and* in July, adding the months counts them twice.

This is called a **non-additive measure** — you can add up money, but you cannot
add up *people*.

So the pipeline pre-calculates **every combination** ahead of time
(6 regions × 4 year options = 24 exact answers) and the dashboard just looks up
the right one. It costs 24 rows of storage and removes an entire category of
wrong-number bug.

There are tests that prove the regions add up to the national total:

```python
def test_dash_kpi_regions_sum_to_all(q):
    ...
    assert total["orders"].iloc[0] == parts["orders"].sum()
```

---

## Part 5 — The chatbot (this is the only AI part)

```bash
./run_chatbot.command        # double-click it in Finder
```

You type: *"Which categories have the worst review scores?"*

Here's the whole journey:

```
1. YOU type a question in English
        ↓
2. Something translates it into SQL (a database question).
   That something is free — see "It costs nothing" below
        ↓
3. ⚠️ THE GUARD checks that SQL before it runs
        ↓
4. If safe → run it on the database → get numbers back
        ↓
5. Check the numbers make sense. A negative delivery time
   means the wrong column was used — try again, don't answer
        ↓
6. Describe the numbers in plain English
        ↓
7. YOU see the answer, the SQL it wrote, and the table
```

### Why step 4 exists

The AI writes the SQL. We should never blindly trust text a language model
produced. So there is a **guard** that inspects it first:

| Attempted | Result |
|---|---|
| `SELECT COUNT(*) FROM fact_orders` | ✅ allowed |
| `DROP TABLE fact_orders` | ❌ blocked |
| `SELECT * FROM orders; DELETE FROM customers` | ❌ blocked (two commands) |
| `SELECT * FROM stg_orders` | ❌ blocked (staging is off-limits) |

**Important idea:** the protection is in *code*, not in the instructions we give
the AI. You can talk a chatbot out of following instructions. You cannot talk
code out of an `if` statement.

There are **39 tests** for the guard. 19 of them are inputs it must refuse.

**Why staging is hidden:** remember the `customer_id` trap? The AI never sees
`stg_customers`, so it *cannot* use the broken key even if it wanted to. The
rule isn't a suggestion — it's enforced by what we let it see.

### Why step 5 exists

Checking the SQL is not enough. A question can produce SQL that is valid, safe,
allowed — and still answers the wrong question.

The real example: asked for average delivery time, a model picked the column
that measures *how early or late* a parcel was against its estimate. Both are
"days". Both are real columns. But one of them is negative when a parcel arrives
early, so the answer came out as **minus 11 days to deliver**.

Nothing about that query is unsafe. It just isn't true. So the app checks the
answer as well as the question: a delivery time cannot be negative, and revenue
cannot be negative, so if one comes back that way the app says "you used the
wrong column" and asks again — instead of printing a confident, impossible
number.

### It costs nothing to run

There are three things that can do step 2, and the app uses the first one it
finds. **It tells you on screen which one answered.**

| | Cost | What it is |
|---|---|---|
| **Ollama** | free | An AI model running on *your* laptop. No account, no key, works with the wifi off, and nothing you type ever leaves the machine |
| **Claude** | paid | Anthropic's API. Better at complicated questions. Completely optional |
| **Query builder** | free | Not AI at all — a few hundred lines of ordinary Python that recognise words like "revenue", "by region" and "worst", and build the SQL from a template |

The third one matters most. It means somebody can download this project and
immediately ask it a real question, having installed nothing. It is limited —
it knows 10 measures and 11 breakdowns, and it recognises words rather than
understanding sentences — but when it doesn't understand something it *says so*
and lists what it does know, instead of guessing.

That is the whole design principle: **never guess at an answer, and never let a
guess look like a fact.** If the AI fails three times, the plain-Python builder
takes over — and the app then says the builder answered, rather than quietly
taking credit for it.

Setup instructions: [CHATBOT_SETUP.md](CHATBOT_SETUP.md).

### One more rule: the AI is never allowed to do sums

Language models are bad at arithmetic and confident about it. One of them added
up a year of monthly revenue and got a total that was 13% wrong — while sounding
completely certain.

So it isn't allowed to. It may only *quote* numbers that are already in the
table it was shown. Every total, percentage and conversion you see was worked
out by the database or by Python, never by the AI.

---

## Part 6 — What we actually found

| Finding | The number |
|---|---|
| **Slow delivery destroys ratings** | 3 days → **4.46/5**. Over 30 days → **2.19/5** |
| **Almost nobody comes back** | 3.1% ever order twice |
| **Orders arrive — just late** | 97% delivered, only 90.5% *on time* |
| **São Paulo is the business** | 37.4% of all revenue |
| **No star products** | Need 18 of 74 categories to reach 80% of sales |
| **Delivery costs a lot** | Shipping = 14.2% of revenue |

The first one is the good one. It turns a vague complaint ("customers are
unhappy") into a specific fixable thing ("orders taking over 30 days are the
problem, and there are 4,296 of them").

---

## Part 7 — Putting it on the internet (your own address)

The dashboard is saved twice: once in `dashboards/` and once as
`docs/index.html`. That second copy is for **GitHub Pages** — free hosting.

```bash
cd "/Users/dishu/sales platform"
git add -A
git commit -m "Sales & Customer Intelligence Platform"
gh repo create sales-platform --public --source=. --push
```

Then on github.com:

1. Open your repo → **Settings**
2. Left sidebar → **Pages**
3. **Source**: `Deploy from a branch`
4. **Branch**: `main`, folder: **`/docs`** → **Save**
5. Wait ~1 minute

Your dashboard is now at:

```
https://<your-username>.github.io/sales-platform/
```

**That is the link to put on your CV.** It's your domain, it's free, it never
expires, and it doesn't mention any AI tool.

---

## Cheat sheet

```bash
python scripts/download_data.py          # get the data (once)
python -m src.etl.run_pipeline           # build everything
pytest                                   # check nothing broke (64 tests)

open dashboards/executive_dashboard.html # the dashboard
streamlit run src/app/streamlit_app.py   # the chatbot
jupyter lab notebooks/                   # the working-out

python scripts/build_dashboard.py        # rebuild dashboard after data changes
python scripts/build_notebook.py         # rebuild + re-run the notebook
```

---

## Words people will use in interviews

| Word | What it actually means |
|---|---|
| **ETL** | Get data, clean data, store data |
| **Star schema** | Facts in the middle, descriptions around the edge |
| **Grain** | "One row means one ______" — the most important question to ask |
| **Fan-out** | Joining badly so rows multiply and numbers inflate |
| **Dimension** | A description (customer, product, date) |
| **Fact** | Something that happened, with numbers |
| **Mart** | A pre-calculated answer |
| **Cohort** | A group who started at the same time |
| **RFM** | Recency, Frequency, Monetary — how good is this customer? |
| **AOV** | Average order value |
| **Additive measure** | Something you can safely add up (money — yes; people — no) |
| **Idempotent** | Running it twice gives the same result, safely |
