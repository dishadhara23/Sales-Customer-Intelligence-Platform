#!/usr/bin/env python3
"""Put your Anthropic API key into .env, safely.

    python scripts/set_api_key.py sk-ant-api03-xxxxx

Hand-editing .env is where keys usually go wrong: a stray quote, a trailing
space, or the key pasted into .env.example (which IS committed to git). This
writes it to the right file, in the right format, and checks the shape.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1

    key = argv[0].strip().strip('"').strip("'")
    if not key.startswith("sk-ant-"):
        print(f"That doesn't look like an Anthropic key — they start with 'sk-ant-'.")
        print(f"You gave: {key[:12]}...")
        return 1
    if len(key) < 40:
        print("That key looks truncated. Copy the whole string from the Console.")
        return 1

    if not ENV.exists():
        ENV.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print("Created .env from .env.example")

    lines = ENV.read_text(encoding="utf-8").splitlines()
    out, replaced = [], False
    for line in lines:
        if re.match(r"\s*ANTHROPIC_API_KEY\s*=", line):
            out.append(f"ANTHROPIC_API_KEY={key}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"ANTHROPIC_API_KEY={key}")

    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Key written to {ENV}")
    print(f"  starts {key[:14]}…  ends …{key[-4:]}  ({len(key)} chars)")
    print("\n.env is git-ignored, so the key will never be committed.")
    print("Restart the chatbot and the badge should turn green: LIVE · claude-opus-5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
