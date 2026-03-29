"""
validator.py
------------
Validates bibliographic records received from the ingestion stage.

Two outcomes per record:
  1. HARD reject  — missing title, both doi+source_id absent, invalid DOI
                    format, missing year, or unparseable year →
                    written to rejected_records SQLite table AND
                    data/rejected/rejected_records.json
  2. SOFT flag    — record passes hard checks but is tagged with warning
                    flags (missing_title_flag, year_uncertain) for
                    downstream manual review

Usage:
    from src.processing.validator import validate_records
    accepted, rejected = validate_records(raw_records, conn)
"""

import re
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

# ── Logging ────────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
os.makedirs("data/rejected", exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("logs/errors.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
CURRENT_YEAR: int = datetime.now().year
DOI_PATTERN = re.compile(r"^10\.\d{4,9}/[^\s]+$")
_DOI_URL_PREFIX_STRIP = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)

# ── Reason codes ───────────────────────────────────────────────────────────────
class RejectReason:
    MISSING_TITLE = "MISSING_TITLE"
    MISSING_ID    = "MISSING_DOI_AND_SOURCE_ID"
    MISSING_YEAR  = "MISSING_PUBLICATION_YEAR"
    INVALID_DOI   = "INVALID_DOI_FORMAT"
    INVALID_YEAR  = "INVALID_PUBLICATION_YEAR"


# ── SQLite schema ──────────────────────────────────────────────────────────────
_CREATE_REJECTED_TABLE = """
CREATE TABLE IF NOT EXISTS rejected_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    reason_code   TEXT    NOT NULL,
    rejected_at   TEXT    NOT NULL,
    title         TEXT,
    doi           TEXT,
    source_id     TEXT,
    raw_record    TEXT    NOT NULL
);
"""


# ── Helpers ────────────────────────────────────────────────────────────────────

def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _validate_doi(doi: str) -> bool:
    return bool(DOI_PATTERN.match(doi.strip()))


def _validate_year(year: Any) -> bool:
    try:
        y = int(year)
        return 1900 <= y <= CURRENT_YEAR
    except (TypeError, ValueError):
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_rejected_table(conn: sqlite3.Connection) -> None:
    """Create the rejected_records table if it doesn't exist."""
    with conn:
        conn.execute(_CREATE_REJECTED_TABLE)


def _persist_rejection(
    conn: sqlite3.Connection | None, record: dict, reason: str
) -> None:
    """Write a rejected record to the SQLite rejected_records table."""
    if conn is None:
        return
    with conn:
        conn.execute(
            """INSERT INTO rejected_records
               (reason_code, rejected_at, title, doi, source_id, raw_record)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                reason,
                _now(),
                record.get("title"),
                record.get("doi"),
                record.get("source_id"),
                json.dumps(record, ensure_ascii=False, default=str),
            ),
        )


# ── Core validation ────────────────────────────────────────────────────────────

def validate_record(record: dict) -> tuple[bool, str | None]:
    """
    Hard-validate a single bibliographic record.

    Returns:
        (True, None)          — passes, send to normaliser
        (False, reason_code)  — hard reject
    """
    if _is_blank(record.get("title")):
        return False, RejectReason.MISSING_TITLE

    doi = record.get("doi", "")
    source_id = record.get("source_id", "")
    if _is_blank(doi) and _is_blank(source_id):
        return False, RejectReason.MISSING_ID

    # Strip URL prefix before checking format — DataCite/Zenodo often include it
    if not _is_blank(doi):
        doi_stripped = _DOI_URL_PREFIX_STRIP.sub("", str(doi).strip())
        if not _validate_doi(doi_stripped):
            return False, RejectReason.INVALID_DOI
          
    if _is_blank(record.get("publication_year")):
        return False, RejectReason.MISSING_YEAR

    if not _validate_year(record.get("publication_year")):
        return False, RejectReason.INVALID_YEAR

    return True, None


def _apply_soft_flags(record: dict) -> dict:
    """
    Attach soft warning flags to an accepted record for downstream review.

    Flags:
      missing_title_flag — title present but suspiciously short (< 3 chars)
      year_uncertain     — year equals current year (may be incomplete data)
    """
    r = dict(record)

    title = (r.get("title") or "").strip()
    r["missing_title_flag"] = len(title) < 3

    try:
        r["year_uncertain"] = int(r.get("publication_year", 0)) == CURRENT_YEAR
    except (TypeError, ValueError):
        r["year_uncertain"] = True

    return r


def validate_records(
    raw_records: list[dict],
    conn: sqlite3.Connection | None = None,
    rejected_path: str = "data/rejected/rejected_records.json",
) -> tuple[list[dict], list[dict]]:
    """
    Validate a list of raw bibliographic records.

    Args:
        raw_records:    List of dicts from Abhinav's ingestion stage.
        conn:           Open SQLite connection. Rejections are written to the
                        rejected_records table when provided.
        rejected_path:  JSON file path for rejected records (always written).

    Returns:
        (accepted, rejected)
          accepted — hard-passed records with soft flags applied
          rejected — failed records with reason_code and rejected_at
    """
    if conn is not None:
        ensure_rejected_table(conn)

    accepted: list[dict] = []
    rejected: list[dict] = []

    for record in raw_records:
        is_valid, reason = validate_record(record)
        if is_valid:
            accepted.append(_apply_soft_flags(record))
        else:
            logger.warning(
                "Record rejected [%s]: title=%r doi=%r",
                reason, record.get("title"), record.get("doi"),
            )
            rejected.append({
                "reason_code": reason,
                "rejected_at": _now(),
                "record": record,
            })
            _persist_rejection(conn, record, reason)

    os.makedirs(os.path.dirname(rejected_path), exist_ok=True)
    with open(rejected_path, "w", encoding="utf-8") as f:
        json.dump(rejected, f, indent=2, ensure_ascii=False)

    logger.info(
        "Validation complete — accepted: %d, rejected: %d",
        len(accepted), len(rejected),
    )
    return accepted, rejected


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    INPUT_PATH = "data/raw/all_raw.json"

    if not os.path.exists(INPUT_PATH):
        logger.error("Input file not found: %s", INPUT_PATH)
        raise SystemExit(1)

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    accepted, rejected = validate_records(raw)
    flagged = [r for r in accepted if r.get("missing_title_flag") or r.get("year_uncertain")]

    print(f"\n✅ Accepted          : {len(accepted)}")
    print(f"❌ Rejected          : {len(rejected)}")
    print(f"⚠️  Soft-flagged      : {len(flagged)}")
