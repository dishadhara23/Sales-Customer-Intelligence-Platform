"""End-to-end pipeline: raw CSV -> staging -> star schema -> marts -> extracts.

    python -m src.etl.run_pipeline                # full run
    python -m src.etl.run_pipeline --backend sqlite
    python -m src.etl.run_pipeline --skip-load    # rebuild SQL models only
    python -m src.etl.run_pipeline --no-extracts
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from src.config import get_warehouse
from src.etl.checks import run_checks
from src.etl.extract_load import load_staging, raw_files_present
from src.etl.export_extracts import export_all
from src.etl.export_tableau import export_tableau_sheets
from src.etl.sql_runner import run_models

log = logging.getLogger("pipeline")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=["postgres", "sqlite"], default=None,
                        help="Force a warehouse backend (default: Postgres, else SQLite).")
    parser.add_argument("--skip-load", action="store_true",
                        help="Skip CSV -> staging; rebuild the SQL models only.")
    parser.add_argument("--no-extracts", action="store_true",
                        help="Skip writing the Tableau extract CSVs.")
    parser.add_argument("--no-checks", action="store_true",
                        help="Skip data-quality assertions.")
    parser.add_argument("--select", default=None,
                        help="Only build models whose name contains this substring.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    started = time.perf_counter()

    missing = raw_files_present()
    if missing and not args.skip_load:
        log.error("Missing raw files: %s", ", ".join(p.name for p in missing))
        log.error("Run `python scripts/download_data.py` first.")
        return 1

    wh = get_warehouse(args.backend)
    log.info("=" * 78)
    log.info("Warehouse: %s  (%s)", wh.dialect, wh.url.split("@")[-1])
    log.info("=" * 78)

    if not args.skip_load:
        log.info("[1/4] Loading raw CSVs into staging ...")
        load_staging(wh)
    else:
        log.info("[1/4] Skipping staging load (--skip-load).")

    log.info("[2/4] Building SQL models ...")
    result = run_models(wh, select=args.select)
    print()
    print(result.summary())
    print()

    if not args.no_checks:
        log.info("[3/4] Running data-quality checks ...")
        report = run_checks(wh)
        print(report.render())
        if report.failed:
            log.error("%d data-quality check(s) FAILED.", report.failed)
            return 2
    else:
        log.info("[3/4] Skipping checks (--no-checks).")

    if not args.no_extracts:
        log.info("[4/4] Writing Tableau extracts ...")
        paths = export_all(wh) + export_tableau_sheets(wh)
        for p in paths:
            log.info("  wrote %s", p.relative_to(p.parents[4]) if len(p.parents) > 4 else p)
    else:
        log.info("[4/4] Skipping extracts (--no-extracts).")

    log.info("Done in %.1fs.", time.perf_counter() - started)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
