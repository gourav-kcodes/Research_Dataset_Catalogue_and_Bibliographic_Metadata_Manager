"""
versioning.py
-------------
Manages versioned bibliographic records in SQLite.

Tables (auto-created):
  records         — current live records with is_active, soft flags,
                    conflict_notes, version tracking
  record_versions — full change history per record (version_number,
                    changed_fields, full snapshot)

Soft-delete only: records are marked is_active = 0, never deleted.

Usage:
    from src.processing.versioning import VersionManager
    with VersionManager("database/bibliographic.db") as vm:
        vm.insert_record(record)
        vm.update_record(doi="10.1234/abc", updated_fields={"title": "New"})
        vm.get_history(doi="10.1234/abc")
"""

import sqlite3
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────────────

_CREATE_RECORDS_TABLE = """
CREATE TABLE IF NOT EXISTS records (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    doi                 TEXT UNIQUE,
    source_id           TEXT,
    title               TEXT NOT NULL,
    creators            TEXT,
    publication_year    INTEGER,
    repository          TEXT,
    keywords            TEXT,
    abstract            TEXT,
    access_url          TEXT,
    license             TEXT,
    file_format         TEXT,
    subject_area        TEXT,
    citation_count      INTEGER,
    date_collected      TEXT,
    source              TEXT,
    -- Soft flags from validator
    missing_title_flag  INTEGER NOT NULL DEFAULT 0,
    year_uncertain      INTEGER NOT NULL DEFAULT 0,
    -- Conflict resolution notes (JSON list)
    conflict_notes      TEXT,
    -- Record lifecycle
    is_active           INTEGER NOT NULL DEFAULT 1,
    current_version     INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL
);
"""

_CREATE_VERSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS record_versions (
    version_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id       INTEGER NOT NULL,
    version_number  INTEGER NOT NULL,
    changed_fields  TEXT,
    snapshot        TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    FOREIGN KEY (record_id) REFERENCES records(id)
);
"""

_JSON_FIELDS    = {"creators", "keywords", "authors", "conflict_notes"}
_BOOL_FIELDS    = {"missing_title_flag", "year_uncertain"}

_CONTENT_FIELDS = {
    "doi", "source_id", "title", "creators", "authors",
    "publication_year", "repository", "keywords", "abstract",
    "access_url", "license", "file_format", "subject_area",
    "citation_count", "date_collected", "source",
    "missing_title_flag", "year_uncertain", "conflict_notes",
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _serialise(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def _deserialise_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    for field in _JSON_FIELDS:
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    for field in _BOOL_FIELDS:
        if field in d and d[field] is not None:
            d[field] = bool(d[field])
    return d


def _diff(old: dict, new: dict) -> dict:
    changes = {}
    for key in set(old) | set(new):
        if old.get(key) != new.get(key):
            changes[key] = {"old": old.get(key), "new": new.get(key)}
    return changes


# ── VersionManager ─────────────────────────────────────────────────────────────

class VersionManager:
    """
    Manages versioned bibliographic records in SQLite.

    Args:
        db_path: Path to SQLite file (created if absent).
    """

    def __init__(self, db_path: str = "database/bibliographic.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()
        logger.info("VersionManager connected to %s", db_path)

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(_CREATE_RECORDS_TABLE)
            self._conn.execute(_CREATE_VERSIONS_TABLE)

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the connection so pipeline.py can share it with validator."""
        return self._conn

    # ── Internal lookup ────────────────────────────────────────────────────────

    def _get_row(
        self,
        doi: str | None = None,
        source_id: str | None = None,
        include_inactive: bool = False,
    ) -> dict | None:
        active_clause = "" if include_inactive else "AND is_active = 1"
        if doi:
            cur = self._conn.execute(
                f"SELECT * FROM records WHERE doi = ? {active_clause}", (doi,)
            )
        elif source_id:
            cur = self._conn.execute(
                f"SELECT * FROM records WHERE source_id = ? {active_clause}", (source_id,)
            )
        else:
            raise ValueError("Provide doi or source_id.")
        row = cur.fetchone()
        return _deserialise_row(row) if row else None

    # ── Public API ─────────────────────────────────────────────────────────────

    def insert_record(self, record: dict) -> int:
        """
        Insert a new bibliographic record at version 1.

        Returns:
            New record's integer id.

        Raises:
            ValueError: If a record with the same DOI already exists.
        """
        doi = record.get("doi")
        if doi and self._get_row(doi=doi):
            raise ValueError(
                f"Record with DOI '{doi}' already exists. Use update_record()."
            )

        now = _now()
        row = {
            "doi":               doi,
            "source_id":         record.get("source_id"),
            "title":             record.get("title"),
            "creators":          _serialise(record.get("creators") or record.get("authors")),
            "publication_year":  record.get("publication_year"),
            "repository":        record.get("repository"),
            "keywords":          _serialise(record.get("keywords")),
            "abstract":          record.get("abstract"),
            "access_url":        record.get("access_url"),
            "license":           record.get("license"),
            "file_format":       record.get("file_format"),
            "subject_area":      record.get("subject_area"),
            "citation_count":    record.get("citation_count"),
            "date_collected":    record.get("date_collected"),
            "source":            record.get("source") or record.get("_source"),
            "missing_title_flag":int(bool(record.get("missing_title_flag", False))),
            "year_uncertain":    int(bool(record.get("year_uncertain", False))),
            "conflict_notes":    _serialise(record.get("conflict_notes", [])),
            "is_active":         1,
            "current_version":   1,
            "created_at":        now,
            "updated_at":        now,
        }

        cols   = ", ".join(row.keys())
        params = ", ".join("?" * len(row))
        with self._conn:
            cur = self._conn.execute(
                f"INSERT INTO records ({cols}) VALUES ({params})",
                list(row.values()),
            )
            record_id = cur.lastrowid
            self._conn.execute(
                """INSERT INTO record_versions
                   (record_id, version_number, changed_fields, snapshot, updated_at)
                   VALUES (?, 1, ?, ?, ?)""",
                (
                    record_id,
                    json.dumps({}),
                    json.dumps(record, ensure_ascii=False, default=str),
                    now,
                ),
            )

        logger.info("Inserted record id=%d doi=%r (v1)", record_id, doi)
        return record_id

    def update_record(
        self,
        updated_fields: dict,
        doi: str | None = None,
        source_id: str | None = None,
    ) -> int:
        """
        Update an existing record. Diffs against current version and stores
        only what changed. Creates a new entry in record_versions.

        Returns:
            New version number (unchanged version number if no diff found).

        Raises:
            ValueError: If no active record is found.
        """
        current = self._get_row(doi=doi, source_id=source_id)
        if not current:
            raise ValueError(
                f"No active record found for doi={doi!r} source_id={source_id!r}."
            )

        record_id   = current["id"]
        now         = _now()
        new_version = current["current_version"] + 1

        old_content = {k: current.get(k) for k in _CONTENT_FIELDS}
        new_content = {**old_content, **updated_fields}
        changes     = _diff(old_content, new_content)

        if not changes:
            logger.info("No changes for record id=%d — skipping.", record_id)
            return current["current_version"]

        set_clauses = []
        values      = []
        for field, val in updated_fields.items():
            if field in _CONTENT_FIELDS:
                set_clauses.append(f"{field} = ?")
                values.append(_serialise(val))

        set_clauses += ["current_version = ?", "updated_at = ?"]
        values      += [new_version, now, record_id]

        snapshot = {**current, **updated_fields}

        with self._conn:
            self._conn.execute(
                f"UPDATE records SET {', '.join(set_clauses)} WHERE id = ?",
                values,
            )
            self._conn.execute(
                """INSERT INTO record_versions
                   (record_id, version_number, changed_fields, snapshot, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    record_id,
                    new_version,
                    json.dumps(changes, ensure_ascii=False, default=str),
                    json.dumps(snapshot, ensure_ascii=False, default=str),
                    now,
                ),
            )

        logger.info(
            "Updated record id=%d → v%d. Changed: %s",
            record_id, new_version, list(changes.keys()),
        )
        return new_version

    def soft_delete(
        self,
        doi: str | None = None,
        source_id: str | None = None,
    ) -> bool:
        """
        Mark a record as inactive. Record is never physically removed.

        Returns:
            True if found and deactivated, False otherwise.
        """
        current = self._get_row(doi=doi, source_id=source_id)
        if not current:
            logger.warning(
                "soft_delete: no active record for doi=%r source_id=%r", doi, source_id
            )
            return False
        with self._conn:
            self._conn.execute(
                "UPDATE records SET is_active = 0, updated_at = ? WHERE id = ?",
                (_now(), current["id"]),
            )
        logger.info("Soft-deleted record id=%d doi=%r", current["id"], doi)
        return True

    def get_active(
        self,
        doi: str | None = None,
        source_id: str | None = None,
    ) -> dict | None:
        """Return the current active record or None."""
        return self._get_row(doi=doi, source_id=source_id)

    def get_history(
        self,
        doi: str | None = None,
        source_id: str | None = None,
    ) -> list[dict]:
        """Return all version entries for a record, oldest first."""
        row = self._get_row(doi=doi, source_id=source_id, include_inactive=True)
        if not row:
            return []
        cur = self._conn.execute(
            "SELECT * FROM record_versions WHERE record_id = ? ORDER BY version_number ASC",
            (row["id"],),
        )
        history = []
        for r in cur.fetchall():
            entry = dict(r)
            for field in ("changed_fields", "snapshot"):
                if isinstance(entry.get(field), str):
                    try:
                        entry[field] = json.loads(entry[field])
                    except json.JSONDecodeError:
                        pass
            history.append(entry)
        return history

    def insert_or_update(self, record: dict) -> dict:
        """
        Insert if new, update if DOI/source_id already exists.

        Returns:
            {"action": "inserted"|"updated", "version": int}
        """
        doi       = record.get("doi")
        source_id = record.get("source_id")
        existing  = self._get_row(doi=doi, source_id=source_id)

        if existing is None:
            self.insert_record(record)
            return {"action": "inserted", "version": 1}
        else:
            updates     = {k: v for k, v in record.items() if k in _CONTENT_FIELDS}
            new_version = self.update_record(updates, doi=doi, source_id=source_id)
            return {"action": "updated", "version": new_version}

    def close(self) -> None:
        self._conn.close()
        logger.info("Database connection closed.")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
