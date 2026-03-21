"""
pipeline.py
-----------
Orchestrates Siyag's full processing pipeline:

  data/raw/all_raw.json
      → Stage 1: Validate       (validator.py)
      → Stage 2: Normalise      (normaliser.py)
      → Stage 3: Store/Version  (versioning.py → database/bibliographic.db)

Also maintains a pipeline_run_log table in the same SQLite database,
tracking every run: source file, records fetched, accepted, rejected,
soft-flagged, duplicates found, and elapsed time.

Run:
    python -m src.processing.pipeline
  or:
    python src/processing/pipeline.py
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone

from src.processing.validator import validate_records
from src.processing.normaliser import normalise_records
from src.processing.versioning import VersionManager

# ── Paths ──────────────────────────────────────────────────────────────────────
RAW_INPUT       = "data/raw/all_raw.json"
REJECTED_OUTPUT = "data/rejected/rejected_records.json"
NORMALISED_OUT  = "data/processed/normalised_records.json"
DB_PATH         = "database/bibliographic.db"
LOG_PATH        = "logs/pipeline.log"

# ── Logging ────────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data/processed", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("pipeline")

# ── pipeline_run_log schema ────────────────────────────────────────────────────
_CREATE_RUN_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS pipeline_run_log (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_started_at  TEXT    NOT NULL,
    run_finished_at TEXT,
    source_file     TEXT    NOT NULL,
    records_loaded  INTEGER NOT NULL DEFAULT 0,
    records_accepted INTEGER NOT NULL DEFAULT 0,
    records_rejected INTEGER NOT NULL DEFAULT 0,
    records_flagged  INTEGER NOT NULL DEFAULT 0,
    db_inserted      INTEGER NOT NULL DEFAULT 0,
    db_updated       INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds  REAL,
    status           TEXT    NOT NULL DEFAULT 'running'  -- running | success | failed
);
"""


def _ensure_run_log(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(_CREATE_RUN_LOG_TABLE)


def _start_run(conn: sqlite3.Connection, source_file: str) -> int:
    """Insert a new run row and return its run_id."""
    with conn:
        cur = conn.execute(
            """INSERT INTO pipeline_run_log
               (run_started_at, source_file, status)
               VALUES (?, ?, 'running')""",
            (datetime.now(timezone.utc).isoformat(), source_file),
        )
    return cur.lastrowid


def _finish_run(conn: sqlite3.Connection, run_id: int, summary: dict, status: str = "success") -> None:
    with conn:
        conn.execute(
            """UPDATE pipeline_run_log SET
               run_finished_at  = ?,
               records_loaded   = ?,
               records_accepted = ?,
               records_rejected = ?,
               records_flagged  = ?,
               db_inserted      = ?,
               db_updated       = ?,
               elapsed_seconds  = ?,
               status           = ?
               WHERE run_id = ?""",
            (
                datetime.now(timezone.utc).isoformat(),
                summary.get("raw_loaded", 0),
                summary.get("accepted", 0),
                summary.get("rejected", 0),
                summary.get("soft_flagged", 0),
                summary.get("db_inserted", 0),
                summary.get("db_updated", 0),
                summary.get("elapsed_sec", 0.0),
                status,
                run_id,
            ),
        )


# ── Pipeline ───────────────────────────────────────────────────────────────────

def run_pipeline(
    raw_input: str = RAW_INPUT,
    rejected_output: str = REJECTED_OUTPUT,
    normalised_output: str = NORMALISED_OUT,
    db_path: str = DB_PATH,
) -> dict:
    """
    Execute the full validation → normalisation → versioning pipeline.

    A single SQLite connection is shared across all stages so that:
      - rejected_records table (validator) and
      - records / record_versions / pipeline_run_log tables (versioning)
    all live in the same database file.

    Args:
        raw_input:         Path to all_raw.json from the ingestion stage.
        rejected_output:   Path to write rejected records JSON.
        normalised_output: Path to write normalised records JSON.
        db_path:           Path to the SQLite database.

    Returns:
        Summary dict with counts for each stage.
    """
    start = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("Pipeline run started at %s", start.isoformat())

    # ── Stage 0: Load ──────────────────────────────────────────────────────────
    if not os.path.exists(raw_input):
        raise FileNotFoundError(f"Raw input not found: {raw_input}")

    with open(raw_input, "r", encoding="utf-8") as f:
        raw_records = json.load(f)

    logger.info("Stage 0 — Loaded %d raw records from %s", len(raw_records), raw_input)

    # Open shared DB connection via VersionManager
    vm = VersionManager(db_path)
    conn = vm.connection

    _ensure_run_log(conn)
    run_id = _start_run(conn, raw_input)

    summary: dict = {}
    status = "success"

    try:
        # ── Stage 1: Validation ────────────────────────────────────────────────
        logger.info("Stage 1 — Validating …")
        accepted, rejected = validate_records(
            raw_records,
            conn=conn,
            rejected_path=rejected_output,
        )
        soft_flagged = [
            r for r in accepted
            if r.get("missing_title_flag") or r.get("year_uncertain")
        ]
        logger.info(
            "Stage 1 — accepted: %d | rejected: %d | soft-flagged: %d",
            len(accepted), len(rejected), len(soft_flagged),
        )

        # ── Stage 2: Normalisation ─────────────────────────────────────────────
        logger.info("Stage 2 — Normalising …")
        normalised = normalise_records(accepted)

        os.makedirs(os.path.dirname(normalised_output), exist_ok=True)
        with open(normalised_output, "w", encoding="utf-8") as f:
            json.dump(normalised, f, indent=2, ensure_ascii=False)

        logger.info("Stage 2 — %d records normalised → %s", len(normalised), normalised_output)

        # ── Stage 3: Versioning ────────────────────────────────────────────────
        logger.info("Stage 3 — Writing to database …")
        inserted = updated = 0

        for record in normalised:
            result = vm.insert_or_update(record)
            if result["action"] == "inserted":
                inserted += 1
            else:
                updated += 1

        logger.info(
            "Stage 3 — inserted: %d | updated: %d | db: %s",
            inserted, updated, db_path,
        )

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()

        summary = {
            "raw_loaded":   len(raw_records),
            "accepted":     len(accepted),
            "rejected":     len(rejected),
            "soft_flagged": len(soft_flagged),
            "normalised":   len(normalised),
            "db_inserted":  inserted,
            "db_updated":   updated,
            "elapsed_sec":  round(elapsed, 2),
        }

    except Exception as exc:
        status = "failed"
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        summary = {"elapsed_sec": round(elapsed, 2)}
        logger.error("Pipeline FAILED: %s", exc, exc_info=True)
        raise

    finally:
        _finish_run(conn, run_id, summary, status=status)
        vm.close()

    logger.info("Pipeline complete in %.2fs — %s", elapsed, summary)
    logger.info("=" * 60)
    return summary


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    summary = run_pipeline()

    print("\n" + "=" * 42)
    print("  PIPELINE SUMMARY")
    print("=" * 42)
    print(f"  Raw records loaded  : {summary['raw_loaded']}")
    print(f"  Accepted            : {summary['accepted']}")
    print(f"  Rejected            : {summary['rejected']}")
    print(f"  Soft-flagged        : {summary['soft_flagged']}")
    print(f"  Normalised          : {summary['normalised']}")
    print(f"  DB inserted         : {summary['db_inserted']}")
    print(f"  DB updated          : {summary['db_updated']}")
    print(f"  Time elapsed        : {summary['elapsed_sec']}s")
    print("=" * 42)
