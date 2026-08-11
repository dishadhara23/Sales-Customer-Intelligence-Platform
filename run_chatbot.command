#!/bin/bash
# Double-click this file in Finder to start the "ask your data" chatbot.
# It finds a Python that actually has Streamlit installed, which is the usual
# reason `streamlit run ...` fails from a terminal.

cd "$(dirname "$0")" || exit 1

PY=""
for candidate in \
    "/opt/anaconda3/bin/python" \
    "$(command -v python3)" \
    "/usr/local/bin/python3" \
    "/opt/homebrew/bin/python3"; do
  [ -x "$candidate" ] || continue
  if "$candidate" -c "import streamlit" >/dev/null 2>&1; then PY="$candidate"; break; fi
done

if [ -z "$PY" ]; then
  echo "Streamlit isn't installed in any Python I can find."
  echo "Install it with:  python3 -m pip install -r requirements.txt"
  echo; read -r -p "Press Return to close."
  exit 1
fi

if [ ! -f data/processed/olist.db ]; then
  echo "The warehouse hasn't been built yet. Building it now (about a minute)..."
  "$PY" -m src.etl.run_pipeline || { read -r -p "Build failed. Press Return."; exit 1; }
fi

echo "Starting the chatbot with: $PY"
echo "Your browser should open at http://localhost:8501"
echo "Leave this window open while you use it. Press Ctrl-C here to stop."
echo

"$PY" -m streamlit run src/app/streamlit_app.py --server.port 8501
