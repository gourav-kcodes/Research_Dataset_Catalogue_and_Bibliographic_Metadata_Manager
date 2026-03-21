"""
schema.py
---------
Gaurav Kumawat - Database Design & Schema
DS3294: DS Practice - Project #14

Defines and initialises the SQLite relational schema for the
Research Dataset Catalogue & Bibliographic Metadata Manager.

Tables
------
  records          – canonical clean bibliographic records
  authors          – normalised author names (many-to-many via record_authors)
  record_authors   – junction table linking records <-> authors
  keywords         – normalised keyword tokens
  record_keywords  – junction table linking records <-> keywords
  versions         – full version history for every updated record
  merge_log        – audit trail for every duplicate-merge decision
  rejected_records – records that failed validation (written by Parulekar)

Usage
-----
  from database.schema import init_db, get_connection

  conn = get_connection("database/catalogue.db")
  init_db(conn)
"""

import sqlite3
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL statements
# ---------------------------------------------------------------------------

_DDL = [
    # ------------------------------------------------------------------
    # Core records table
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS records (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,

        -- Mandatory bibliographic fields
        title            TEXT    NOT NULL,
        publication_year INTEGER,
        doi              TEXT    UNIQUE,          -- canonical 10.xxxx/... form
        source_id        TEXT,                    -- original ID from source API
        repository       TEXT,                   -- zenodo | kaggle | openalex | datacite

        -- Descriptive fields
        abstract         TEXT,
        access_url       TEXT,
        license          TEXT,
        file_format      TEXT,
        subject_area     TEXT,
        citation_count   INTEGER DEFAULT 0,

        -- Pipeline metadata
        md5_hash         TEXT,                   -- MD5 of raw record for dedup
        _source          TEXT,                   -- ingestion source tag
        date_collected   TEXT,                   -- ISO-8601 timestamp

        -- Record lifecycle
        version_number   INTEGER DEFAULT 1,
        is_active        INTEGER DEFAULT 1,      -- 0 = soft-deleted
        created_at       TEXT    DEFAULT (datetime('now')),
        updated_at       TEXT    DEFAULT (datetime('now')),

        -- Completeness score (0.0 – 1.0) used by merger
        completeness     REAL    DEFAULT 0.0
    )
    """,

    # ------------------------------------------------------------------
    # Authors (normalised "Last, First" tokens)
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS authors (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL UNIQUE       -- e.g. "Kumawat, Gaurav"
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS record_authors (
        record_id   INTEGER NOT NULL REFERENCES records(id)  ON DELETE CASCADE,
        author_id   INTEGER NOT NULL REFERENCES authors(id)  ON DELETE CASCADE,
        author_order INTEGER DEFAULT 0,
        PRIMARY KEY (record_id, author_id)
    )
    """,

    # ------------------------------------------------------------------
    # Keywords (lower-cased, single tokens)
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS keywords (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword TEXT    NOT NULL UNIQUE
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS record_keywords (
        record_id  INTEGER NOT NULL REFERENCES records(id)   ON DELETE CASCADE,
        keyword_id INTEGER NOT NULL REFERENCES keywords(id)  ON DELETE CASCADE,
        PRIMARY KEY (record_id, keyword_id)
    )
    """,

    # ------------------------------------------------------------------
    # Version history (append-only)
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS versions (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id      INTEGER NOT NULL REFERENCES records(id) ON DELETE CASCADE,
        version_number INTEGER NOT NULL,
        changed_fields TEXT,     -- JSON array of field names that changed
        snapshot       TEXT,     -- JSON dump of the record at this version
        updated_at     TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------
    # Merge audit log
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS merge_log (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        kept_record_id INTEGER NOT NULL REFERENCES records(id),
        dropped_doi    TEXT,
        dropped_source TEXT,
        merge_reason   TEXT,     -- 'exact_doi' | 'fuzzy_title'
        similarity     REAL,     -- 1.0 for exact, 0–1 for fuzzy
        merged_at      TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------
    # Rejected records (populated by Parulekar's validation stage)
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS rejected_records (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        raw_data     TEXT,        -- JSON dump of the offending record
        reason_code  TEXT,        -- e.g. 'missing_title', 'bad_doi', 'future_year'
        rejected_at  TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------
    # Pipeline run log (one row per ingestion run)
    # ------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS pipeline_run_log (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        run_at           TEXT DEFAULT (datetime('now')),
        source           TEXT,
        records_fetched  INTEGER DEFAULT 0,
        records_accepted INTEGER DEFAULT 0,
        records_rejected INTEGER DEFAULT 0,
        duplicates_found INTEGER DEFAULT 0
    )
    """,
]

# ---------------------------------------------------------------------------
# Index creation (separate so they can be re-run idempotently)
# ---------------------------------------------------------------------------

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_records_doi      ON records(doi)",
    "CREATE INDEX IF NOT EXISTS idx_records_year     ON records(publication_year)",
    "CREATE INDEX IF NOT EXISTS idx_records_repo     ON records(repository)",
    "CREATE INDEX IF NOT EXISTS idx_records_active   ON records(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_records_source   ON records(_source)",
    "CREATE INDEX IF NOT EXISTS idx_authors_name     ON authors(name)",
    "CREATE INDEX IF NOT EXISTS idx_keywords_keyword ON keywords(keyword)",
    "CREATE INDEX IF NOT EXISTS idx_versions_record  ON versions(record_id)",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_connection(db_path: str = "database/catalogue.db") -> sqlite3.Connection:
    """
    Open (or create) a SQLite database at *db_path* and return a connection.

    Foreign-key enforcement and WAL journal mode are enabled automatically.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row          # rows accessible by column name
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    logger.info("Connected to SQLite database at %s", path.resolve())
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """
    Create all tables and indexes if they do not already exist.

    Safe to call multiple times (all statements are CREATE … IF NOT EXISTS).
    """
    with conn:
        for ddl in _DDL:
            conn.execute(ddl)
        for idx in _INDEXES:
            conn.execute(idx)
    logger.info("Database schema initialised successfully.")


def drop_all(conn: sqlite3.Connection) -> None:
    """
    Drop every table – useful for test teardown only.
    Not called in production code.
    """
    tables = [
        "record_keywords", "record_authors",
        "keywords", "authors",
        "versions", "merge_log",
        "rejected_records", "pipeline_run_log",
        "records",
    ]
    with conn:
        for table in tables:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
    logger.warning("All tables dropped.")


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s | %(name)s | %(message)s")
    conn = get_connection(":memory:")
    init_db(conn)

    # Verify tables were created
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row["name"] for row in cursor.fetchall()]
    print("Tables created:", tables)

    # Verify indexes
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
    )
    indexes = [row["name"] for row in cursor.fetchall()]
    print("Indexes created:", indexes)
