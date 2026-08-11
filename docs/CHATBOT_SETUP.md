# The "ask your data" chatbot — how it works, and how to run it free

Type a question in English, get an answer computed from the warehouse. Behind
that sentence sits the only part of this project where something can quietly
invent a number, so most of the design below is about not letting it.

```bash
./run_chatbot.command          # double-click it in Finder
# or
python -m streamlit run src/app/streamlit_app.py
```

It opens at <http://localhost:8501> and **works immediately, with nothing
installed and no account** — see [Backend 3](#backend-3--the-query-builder-no-model)
for why.

---

## What actually happens when you ask a question

```
your question
     │
     ▼
┌─────────────────────────────────────────────┐
│ 1. a backend writes SQL                     │  Ollama, Claude, or the query builder
└─────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────┐
│ 2. the guard vets it        src/llm/sql_guard.py
│    SELECT only · one statement · known      │  ◄── the security boundary
│    tables only · LIMIT injected             │
└─────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────┐
│ 3. it runs against the warehouse            │
└─────────────────────────────────────────────┘
     │
     ├── failed?           → error goes back to step 1, up to 3 attempts
     ├── zero rows?        → "check your literals" goes back to step 1
     ├── impossible value? → "you picked the wrong column" goes back to step 1
     ├── no label column?  → "you grouped by something you didn't select"
     └── still failing?    → hand the question to the query builder
     │
     ▼
┌─────────────────────────────────────────────┐
│ 4. the result is described in English       │
│    amounts converted BRL → GBP in Python    │
└─────────────────────────────────────────────┘
```

Two things are worth pulling out of that diagram.

**The guard is the security boundary, not the prompt.** A prompt is a request;
a model can ignore it, and a user can talk it out of one. `sql_guard.py` sits
between generation and execution, so nothing reaches the database without
passing it — regardless of which backend wrote the statement, or what the
question said. It strips comments and string literals before scanning for
keywords, so `SELECT '; DROP TABLE'` is read as what it is. Staging tables are
not on the allow-list, so the `customer_id` trap is structurally unreachable.

**Checking the SQL is not enough.** A query can be valid, safe, permitted, and
still answer the wrong question. If a column that cannot be negative comes back
negative, the model picked the wrong one — that is detectable, and it gets one
retry with an explanation rather than an answer built on it. Same for a grouped
result with nothing naming the rows. See `sanity_check()` and
`grouping_is_labelled()` in `src/llm/agent.py`.

---

## The three backends

The app picks the first available one and **says on screen which answered**. An
answer from the deterministic builder is never presented as a model's.

### Backend 1 — Ollama (free, local, recommended)

A model that runs on your own machine. No account, no key, no per-question
cost, works offline, and nothing you type leaves the laptop.

**macOS** (this is how it was installed here — the standalone binary, no admin
rights, no menu-bar app):

```bash
mkdir -p ~/.local/ollama
curl -fsSL https://ollama.com/download/ollama-darwin.tgz | tar -xz -C ~/.local/ollama
~/.local/ollama/ollama pull qwen2.5-coder:7b        # ~4.7 GB, one time
```

The app looks in `~/.local/ollama/ollama` as well as on `PATH`, so it finds this
copy without any further configuration. If you would rather have the standard
menu-bar app, <https://ollama.com/download> works equally well.

**Linux:** `curl -fsSL https://ollama.com/install.sh | sh`, then
`ollama pull qwen2.5-coder:7b`. (That script is Linux-only — it will refuse to
run on macOS.)

Then start the chatbot as usual. The app starts the Ollama server itself if it
is not already running, so there is no second terminal to keep open.

Roughly 4–10 seconds per question on an M-series Mac. It needs about 6 GB of
RAM while answering.

`qwen2.5-coder:7b` is the default because it is tuned for code and holds SQL
syntax well at a size that fits comfortably in memory. Any installed model will
be used if that one is missing — set `OLLAMA_MODEL` in `.env` to choose.

**Expect it to be imperfect.** A 7B model is small. It gets simple questions
right and loses its footing on joins, which is precisely why the retry loop and
the builder fallback exist: the app is built to be useful *with* a weak model
rather than to pretend it has a strong one.

### Backend 2 — Claude (optional, paid)

Better SQL, particularly on multi-step questions. Needs an API key from
<https://console.anthropic.com/>, and is billed per question at Anthropic's
current rates — small, but not zero.

```bash
python scripts/set_api_key.py     # writes ANTHROPIC_API_KEY to .env
```

Nothing in the project requires this. It is a drop-in upgrade, not a
dependency, and the app is fully functional without it.

### Backend 3 — the query builder (no model)

If no model is available, questions are answered by `src/llm/query_builder.py`:
about 400 lines of Python that match a question against 10 known measures and
11 known breakdowns, then assemble SQL from templates.

It is not a language model and does not pretend to be. It matches vocabulary
rather than understanding sentences, so it handles "worst review score by
category in 2018" and refuses "why did sales drop in November" — and says which
it is doing, rather than guessing.

This exists so the project has no dead end. Clone the repo, run the app, ask a
question, get a real answer computed from real data — with nothing installed.
It is also fully deterministic, which is why it is the backend the notebook
pins and the one the test suite exercises end to end.

---

## Currency

Every amount in the warehouse is Brazilian Real, because Olist is a Brazilian
marketplace. The interface shows pounds, which means the figures are
**converted, not relabelled**, at a fixed rate documented in
`src/viz/money.py`, and labelled as approximate wherever they appear.

Getting this wrong would be quiet and serious: swapping the symbol without
applying a rate overstates every figure by roughly 4.5x while still looking
entirely plausible.

The model is told to write amounts in R$ and is never asked to convert —
language models are unreliable at arithmetic. The conversion happens afterwards
in Python. Set `DISPLAY_CURRENCY=BRL` in `.env` to see the source values.

---

## If something does not work

**"Streamlit isn't installed in any Python I can find."** More than one Python
is on the machine and Streamlit is in a different one. `run_chatbot.command`
searches for the right one; if it still fails,
`python3 -m pip install -r requirements.txt`.

**The badge says QUERY BUILDER but Ollama is installed.** The server was not
reachable. Run `~/.local/ollama/ollama list` (or `ollama list` if you used the
menu-bar app) — if that errors, the install did not complete. Check
`OLLAMA_HOST` in `.env` if you changed the port.

**Ollama answers, but the SQL is wrong.** Expected sometimes at 7B. Open the
attempt panes to see what it tried; the retries and the fallback are visible
there. A larger model (`ollama pull qwen2.5-coder:14b`) helps if you have the
RAM.

**"The warehouse hasn't been built yet."** Run `python -m src.etl.run_pipeline`
once — about a minute, and it needs no database server.
