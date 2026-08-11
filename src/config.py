"""Central configuration and warehouse-connection resolution.

The project targets **PostgreSQL** as its warehouse. To keep it runnable with
zero setup (and to keep CI honest), it degrades gracefully to a local SQLite
file when Postgres is not reachable. Every SQL model in ``sql/models`` is
written against a small macro layer (see :mod:`src.etl.sql_runner`) so the same
model files compile on either backend.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]

load_dotenv(PROJECT_ROOT / ".env")

RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SQL_DIR = PROJECT_ROOT / "sql"
EXTRACT_DIR = PROJECT_ROOT / "dashboards" / "tableau" / "extracts"

DEFAULT_POSTGRES_URL = "postgresql+psycopg2://olist:olist@localhost:5432/olist"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Warehouse:
    """A resolved warehouse connection.

    ``dialect`` is the key the SQL macro layer switches on, so it is carried
    alongside the engine rather than re-derived at each call site.
    """

    engine: Engine
    dialect: str  # "postgresql" | "sqlite"
    url: str

    @property
    def is_postgres(self) -> bool:
        return self.dialect == "postgresql"


def _sqlite_url() -> str:
    path = os.getenv("SQLITE_PATH", "data/processed/olist.db")
    abs_path = (PROJECT_ROOT / path).resolve() if not Path(path).is_absolute() else Path(path)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{abs_path}"


def _try_postgres(url: str) -> Engine | None:
    """Return a live Postgres engine, or ``None`` if it cannot be reached."""
    try:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception as exc:  # driver missing, server down, bad creds
        log.info("Postgres unavailable (%s) — falling back to SQLite.", type(exc).__name__)
        return None


def get_warehouse(prefer: str | None = None) -> Warehouse:
    """Resolve the warehouse connection.

    Resolution order:

    1. ``prefer`` argument, if given ("postgres" or "sqlite")
    2. ``WAREHOUSE_BACKEND`` env var
    3. ``DATABASE_URL`` -> Postgres, if reachable
    4. SQLite fallback
    """
    backend = (prefer or os.getenv("WAREHOUSE_BACKEND") or "").strip().lower()

    if backend in {"sqlite", "sqlite3"}:
        url = _sqlite_url()
        return Warehouse(create_engine(url, future=True), "sqlite", url)

    pg_url = os.getenv("DATABASE_URL", DEFAULT_POSTGRES_URL)
    if backend in {"postgres", "postgresql"}:
        engine = _try_postgres(pg_url)
        if engine is None:
            raise RuntimeError(
                f"WAREHOUSE_BACKEND=postgres was requested but {pg_url} is unreachable. "
                "Start it with `docker compose up -d`."
            )
        return Warehouse(engine, "postgresql", pg_url)

    engine = _try_postgres(pg_url)
    if engine is not None:
        return Warehouse(engine, "postgresql", pg_url)

    url = _sqlite_url()
    return Warehouse(create_engine(url, future=True), "sqlite", url)


# --- LLM settings ----------------------------------------------------------

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
ANTHROPIC_EFFORT = os.getenv("ANTHROPIC_EFFORT", "medium")
SQL_ROW_LIMIT = int(os.getenv("SQL_ROW_LIMIT", "1000"))
