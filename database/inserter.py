"""
inserter.py
-----------
Gaurav Kumawat — Database Design & Schema
DS3294: DS Practice - Project #14

Reads all_normalised.json (produced by Abhinav's normaliser.py),
runs duplicate detection and merging, then inserts clean records
into the SQLite catalogue database.

Pipeline this module executes:
  1. Load  — read all_normalised.json
  2. Detect — run duplicate_detection.detect_duplicates()
  3. Merge  — run record_merger.batch_merge() on flagged duplicates
  4. Insert — insert clean records into:
               records, authors, record_authors,
               keywords, record_keywords
  5. Log    — write final counts to pipeline_run_log

Public API
----------
  run_insertion(normalised_path, db_path)  ->  InsertionResult

Usage
-----
  from database.inserter import run_insertion

  result = run_insertion(
      normalised_path="data/processed/all_normalised.json",
      db_path="database/catalogue.db"
  )
  print(result.summary())

  Or run directly:
  python inserter.py
"""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from schema              import get_connection, init_db
from duplicate_detection import detect_duplicates
from record_merger       import batch_merge, completeness_score

# ---------------------------------------------------------------------------
#  Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s]  %(levelname)s  - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/inserter.log"),
    ],
)
logger = logging.getLogger(__name__)

os.makedirs("logs", exist_ok=True)


# ---------------------------------------------------------------------------
#  Result container
# ---------------------------------------------------------------------------

@dataclass
class InsertionResult:
    """Holds counts and lists produced by run_insertion."""
    total_input:       int = 0
    inserted:          int = 0
    duplicates_exact:  int = 0
    duplicates_fuzzy:  int = 0
    skipped:           int = 0          # missing title or other hard block
    merged:            int = 0
    failed_ids:        list = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"InsertionResult(\n"
            f"  total_input      = {self.total_input}\n"
            f"  inserted         = {self.inserted}\n"
            f"  duplicates_exact = {self.duplicates_exact}\n"
            f"  duplicates_fuzzy = {self.duplicates_fuzzy}\n"
            f"  merged           = {self.merged}\n"
            f"  skipped          = {self.skipped}\n"
            f")"
        )


# ---------------------------------------------------------------------------
#  Author helpers
# ---------------------------------------------------------------------------

def _get_or_create_author(conn: sqlite3.Connection, name: str) -> int:
    """
    Return the id of an existing author row, or insert one and return
    the new id.  Names are stored as-is (normaliser already cleaned them).
    """
    name = name.strip()
    if not name:
        return None

    row = conn.execute(
        "SELECT id FROM authors WHERE name = ?", (name,)
    ).fetchone()

    if row:
        return row["id"]

    cursor = conn.execute(
        "INSERT INTO authors (name) VALUES (?)", (name,)
    )
    return cursor.lastrowid


def _link_authors(conn: sqlite3.Connection,
                  record_id: int, authors: list[str]) -> None:
    """
    For each author name in *authors*, get-or-create an authors row,
    then insert a record_authors junction row (preserving order).
    Silently skips blank names.
    """
    for order, name in enumerate(authors):
        author_id = _get_or_create_author(conn, name)
        if author_id is None:
            continue
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO record_authors
                    (record_id, author_id, author_order)
                VALUES (?, ?, ?)
                """,
                (record_id, author_id, order),
            )
        except sqlite3.Error as exc:
            logger.warning(
                "Could not link author '%s' to record %d: %s", name, record_id, exc
            )


# ---------------------------------------------------------------------------
#  Keyword helpers
# ---------------------------------------------------------------------------

def _get_or_create_keyword(conn: sqlite3.Connection, keyword: str) -> int:
    """
    Return the id of an existing keyword row, or insert one.
    Keywords are lower-cased so 'Machine Learning' and 'machine learning'
    map to the same row.
    """
    keyword = keyword.strip().lower()
    if not keyword:
        return None

    row = conn.execute(
        "SELECT id FROM keywords WHERE keyword = ?", (keyword,)
    ).fetchone()

    if row:
        return row["id"]

    cursor = conn.execute(
        "INSERT INTO keywords (keyword) VALUES (?)", (keyword,)
    )
    return cursor.lastrowid


def _link_keywords(conn: sqlite3.Connection,
                   record_id: int, keywords: list[str]) -> None:
    """
    For each keyword string, get-or-create a keywords row,
    then insert a record_keywords junction row.
    Silently skips blank strings.
    """
    for kw in keywords:
        keyword_id = _get_or_create_keyword(conn, kw)
        if keyword_id is None:
            continue
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO record_keywords
                    (record_id, keyword_id)
                VALUES (?, ?)
                """,
                (record_id, keyword_id),
            )
        except sqlite3.Error as exc:
            logger.warning(
                "Could not link keyword '%s' to record %d: %s", kw, record_id, exc
            )


# ---------------------------------------------------------------------------
#  Core record inserter
# ---------------------------------------------------------------------------

def _insert_record(conn: sqlite3.Connection, record: dict) -> int | None:
    """
    Insert one normalised record into the records table.

    Returns the new row's id, or None if insertion failed.

    Fields mapped from normaliser output → schema column:
      title            → title
      publication_year → publication_year
      doi              → doi
      repository       → repository
      access_url       → access_url
      source           → _source
      _md5             → md5_hash
      completeness     → completeness  (computed here)
    """
    title = (record.get("title") or "").strip()
    if not title:
        logger.warning("Record has no title — skipping: %s", record)
        return None

    doi              = record.get("doi") or None
    publication_year = record.get("publication_year") or None
    repository       = record.get("repository") or None
    access_url       = record.get("access_url") or None
    source           = record.get("source") or record.get("_source") or None
    md5_hash         = record.get("_md5") or None
    date_collected   = datetime.now(timezone.utc).isoformat()

    # Completeness score — reuse Gaurav's scorer from record_merger
    score = completeness_score({
        "title":            title,
        "doi":              doi,
        "publication_year": publication_year,
        "repository":       repository,
        "access_url":       access_url,
        "_source":          source,
        "abstract":         record.get("abstract"),
        "license":          record.get("license"),
        "file_format":      record.get("file_format"),
        "subject_area":     record.get("subject_area"),
        "citation_count":   record.get("citation_count"),
    })

    try:
        cursor = conn.execute(
            """
            INSERT INTO records (
                title, publication_year, doi, repository,
                access_url, md5_hash, _source,
                date_collected, completeness,
                version_number, is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 1)
            """,
            (
                title, publication_year, doi, repository,
                access_url, md5_hash, source,
                date_collected, score,
            ),
        )
        return cursor.lastrowid

    except sqlite3.IntegrityError as exc:
        # Most likely a UNIQUE constraint violation on doi
        logger.warning(
            "IntegrityError inserting record (doi=%s): %s", doi, exc
        )
        return None

    except sqlite3.Error as exc:
        logger.error("Unexpected DB error inserting record: %s", exc)
        return None


# ---------------------------------------------------------------------------
#  pipeline_run_log writer
# ---------------------------------------------------------------------------

def _write_pipeline_run_log(
    conn: sqlite3.Connection,
    source: str,
    records_fetched: int,
    records_accepted: int,
    records_rejected: int,
    duplicates_found: int,
) -> None:
    """Insert one row into pipeline_run_log for audit purposes."""
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO pipeline_run_log
                    (source, records_fetched, records_accepted,
                     records_rejected, duplicates_found)
                VALUES (?, ?, ?, ?, ?)
                """,
                (source, records_fetched, records_accepted,
                 records_rejected, duplicates_found),
            )
        logger.info(
            "pipeline_run_log written — source=%s fetched=%d accepted=%d "
            "rejected=%d dupes=%d",
            source, records_fetched, records_accepted,
            records_rejected, duplicates_found,
        )
    except sqlite3.Error as exc:
        logger.warning("Could not write pipeline_run_log: %s", exc)


# ---------------------------------------------------------------------------
#  Public API
# ---------------------------------------------------------------------------

def run_insertion(
    normalised_path: str = "data/processed/all_normalised.json",
    db_path: str = "database/catalogue.db",
) -> InsertionResult:
    """
    Full insertion pipeline:
      1. Load normalised records from *normalised_path*
      2. Initialise DB at *db_path*
      3. Detect duplicates (exact DOI + fuzzy title)
      4. Merge flagged duplicates
      5. Insert clean records + authors + keywords
      6. Write pipeline_run_log

    Returns an InsertionResult with full counts.
    """
    result = InsertionResult()

    logger.info("=" * 50)
    logger.info("INSERTION PIPELINE STARTED")
    logger.info("=" * 50)

    # ── Step 1: Load normalised records ──────────────────────────────────
    if not os.path.exists(normalised_path):
        logger.error("Normalised file not found: %s", normalised_path)
        return result

    with open(normalised_path, "r") as f:
        records = json.load(f)

    result.total_input = len(records)
    logger.info("Loaded %d normalised records from %s",
                len(records), normalised_path)

    # ── Step 2: Initialise database ───────────────────────────────────────
    conn = get_connection(db_path)
    init_db(conn)
    logger.info("Database initialised at %s", db_path)

    # ── Step 3: Duplicate detection ───────────────────────────────────────
    # NOTE on double-dedup:
    # Abhinav's ingest_pipeline.py already deduplicates raw API records
    # using MD5 hashing (exact byte-level match on raw JSON).
    # This stage re-checks on NORMALISED content using:
    #   - Pass 1: canonical DOI match (catches same dataset from 2 sources)
    #   - Pass 2: fuzzy title match ≥90% (catches slight title variations)
    # The two passes are complementary — MD5 won't catch cross-source
    # duplicates where raw JSON differs but content is the same.
    logger.info("Running duplicate detection...")
    dup_result = detect_duplicates(conn, records)

    result.duplicates_exact = len(dup_result.exact)
    result.duplicates_fuzzy = len(dup_result.fuzzy)
    logger.info(
        "Duplicate detection complete — exact=%d fuzzy=%d clean=%d",
        result.duplicates_exact, result.duplicates_fuzzy, len(dup_result.clean),
    )

    # ── Step 4: Merge duplicates ──────────────────────────────────────────
    if dup_result.exact or dup_result.fuzzy:
        logger.info("Merging %d duplicate pairs...",
                    len(dup_result.exact) + len(dup_result.fuzzy))
        merge_results = batch_merge(conn, dup_result)
        result.merged = len(merge_results)
        logger.info("Merge complete — %d pairs merged", result.merged)

    # ── Step 5: Insert clean records ──────────────────────────────────────
    logger.info("Inserting %d clean records...", len(dup_result.clean))

    with conn:
        for record in dup_result.clean:
            record_id = _insert_record(conn, record)

            if record_id is None:
                result.skipped += 1
                result.failed_ids.append(record.get("doi") or record.get("title"))
                continue

            # Link authors
            authors = record.get("authors", [])
            if authors:
                _link_authors(conn, record_id, authors)

            # Link keywords
            keywords = record.get("keywords", [])
            if keywords:
                _link_keywords(conn, record_id, keywords)

            result.inserted += 1
            logger.debug(
                "Inserted record id=%d title='%s'",
                record_id, record.get("title", "")[:60],
            )

    # ── Step 6: Write pipeline_run_log ────────────────────────────────────
    _write_pipeline_run_log(
        conn,
        source          = "all_sources",
        records_fetched = result.total_input,
        records_accepted= result.inserted,
        records_rejected= result.skipped,
        duplicates_found= result.duplicates_exact + result.duplicates_fuzzy,
    )

    # ── Summary ───────────────────────────────────────────────────────────
    logger.info("=" * 50)
    logger.info("INSERTION PIPELINE COMPLETE")
    logger.info("  Total input      : %d", result.total_input)
    logger.info("  Inserted         : %d", result.inserted)
    logger.info("  Exact duplicates : %d", result.duplicates_exact)
    logger.info("  Fuzzy duplicates : %d", result.duplicates_fuzzy)
    logger.info("  Merged pairs     : %d", result.merged)
    logger.info("  Skipped          : %d", result.skipped)
    if result.failed_ids:
        logger.warning("  Failed records   : %s", result.failed_ids)
    logger.info("=" * 50)

    return result


# ---------------------------------------------------------------------------
#  Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = run_insertion(
        normalised_path="data/processed/all_normalised.json",
        db_path="database/catalogue.db",
    )
    print(result.summary())
