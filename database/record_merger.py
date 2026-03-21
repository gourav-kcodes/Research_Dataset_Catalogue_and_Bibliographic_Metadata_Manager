"""
record_merger.py
----------------
Gaurav Kumawat - Database Design & Schema
DS3294: DS Practice - Project #14

Implements the record-merging strategy described in the project README:

  "prefer the record with more complete fields; log merge decisions"

When a duplicate is detected (by duplicate_detection.py), this module
decides which record to KEEP and which to DROP, then:

  1. Computes a completeness score (fraction of non-empty fields) for
     each candidate.
  2. Merges field-by-field: for each field, keep the value from whichever
     record has it populated; the higher-completeness record wins ties.
  3. Writes the merged record back to the database (updating the existing
     row in-place and bumping its version_number).
  4. Appends a snapshot to the versions table for full audit history.
  5. Soft-deletes the losing record (is_active = 0).
  6. Inserts a row in merge_log summarising the decision.

Public API
----------
  merge_duplicates(conn, existing_record, incoming_record, reason, similarity)
      -> MergeResult

  batch_merge(conn, duplicate_result)
      -> list[MergeResult]

Usage
-----
  from database.schema             import get_connection, init_db
  from database.duplicate_detection import detect_duplicates
  from database.record_merger       import batch_merge

  conn   = get_connection("database/catalogue.db")
  result = detect_duplicates(conn, normalised_records)
  merges = batch_merge(conn, result)
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fields considered when computing completeness
# ---------------------------------------------------------------------------

SCORED_FIELDS: list[str] = [
    "title", "doi", "publication_year", "repository",
    "abstract", "access_url", "license", "file_format",
    "subject_area", "citation_count", "_source",
]

# Fields that are always taken from the EXISTING (kept) record unchanged
IMMUTABLE_FIELDS: set[str] = {"id", "created_at", "version_number", "is_active"}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MergeResult:
    kept_id:         int
    dropped_id:      int | None
    merged_fields:   list[str]       # field names actually updated
    completeness_before: float
    completeness_after:  float
    reason:          str             # 'exact_doi' | 'fuzzy_title'
    similarity:      float


# ---------------------------------------------------------------------------
# Completeness scoring
# ---------------------------------------------------------------------------

def completeness_score(record: dict) -> float:
    """
    Return the fraction of SCORED_FIELDS that are non-empty in *record*.

    Score is in [0.0, 1.0].
    """
    populated = sum(
        1 for f in SCORED_FIELDS
        if record.get(f) not in (None, "", [], {})
    )
    return round(populated / len(SCORED_FIELDS), 4)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fetch_record(conn: sqlite3.Connection, record_id: int) -> dict | None:
    """Fetch a single record row by primary key."""
    row = conn.execute(
        "SELECT * FROM records WHERE id = ?", (record_id,)
    ).fetchone()
    return dict(row) if row else None


def _fetch_record_by_doi(conn: sqlite3.Connection, doi: str) -> dict | None:
    """Fetch a single active record by canonical DOI."""
    row = conn.execute(
        "SELECT * FROM records WHERE doi = ? AND is_active = 1 LIMIT 1",
        (doi,),
    ).fetchone()
    return dict(row) if row else None


def _field_merge(
    existing: dict,
    incoming: dict,
    prefer_existing: bool,
) -> tuple[dict, list[str]]:
    """
    Merge *incoming* into *existing* field-by-field.

    Strategy
    --------
    - If existing has a value and incoming doesn't → keep existing.
    - If incoming has a value and existing doesn't → take incoming.
    - If both have a value → prefer the record flagged by *prefer_existing*.
    - IMMUTABLE_FIELDS are never overwritten.

    Returns (merged_dict, list_of_changed_field_names).
    """
    merged = dict(existing)
    changed: list[str] = []

    for key, incoming_val in incoming.items():
        if key in IMMUTABLE_FIELDS or key.startswith("_duplicate"):
            continue

        existing_val = existing.get(key)
        has_existing = existing_val not in (None, "", [], {})
        has_incoming = incoming_val not in (None, "", [], {})

        if has_existing and not has_incoming:
            pass  # keep existing value
        elif has_incoming and not has_existing:
            merged[key] = incoming_val
            changed.append(key)
        elif has_existing and has_incoming and not prefer_existing:
            # both populated → incoming wins (it's the richer record)
            if incoming_val != existing_val:
                merged[key] = incoming_val
                changed.append(key)

    return merged, changed


def _bump_version(conn: sqlite3.Connection, record_id: int,
                  changed_fields: list[str], snapshot: dict) -> None:
    """Increment version_number and append a row to the versions table."""
    conn.execute(
        "UPDATE records SET version_number = version_number + 1, "
        "updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), record_id),
    )
    new_version = conn.execute(
        "SELECT version_number FROM records WHERE id = ?", (record_id,)
    ).fetchone()["version_number"]

    conn.execute(
        """
        INSERT INTO versions (record_id, version_number, changed_fields, snapshot)
        VALUES (?, ?, ?, ?)
        """,
        (
            record_id,
            new_version,
            json.dumps(changed_fields),
            json.dumps(snapshot, default=str),
        ),
    )


def _soft_delete(conn: sqlite3.Connection, record_id: int) -> None:
    """Mark a record as inactive (soft-delete, never physical delete)."""
    conn.execute(
        "UPDATE records SET is_active = 0, updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), record_id),
    )
    logger.debug("Soft-deleted record id=%d", record_id)


def _update_completeness(conn: sqlite3.Connection,
                         record_id: int, score: float) -> None:
    conn.execute(
        "UPDATE records SET completeness = ? WHERE id = ?",
        (score, record_id),
    )


# ---------------------------------------------------------------------------
# Core merge function
# ---------------------------------------------------------------------------

def merge_duplicates(
    conn: sqlite3.Connection,
    existing_record: dict,
    incoming_record: dict,
    reason: str    = "exact_doi",
    similarity: float = 1.0,
) -> MergeResult:
    """
    Merge *incoming_record* into *existing_record*.

    The record with the higher completeness score is the "winner":
    its field values take priority in case of conflict.  The merged
    result overwrites the existing database row; the loser is soft-deleted.

    Parameters
    ----------
    conn             : open SQLite connection
    existing_record  : dict fetched from the database (has 'id')
    incoming_record  : normalised incoming dict (may or may not have 'id')
    reason           : 'exact_doi' | 'fuzzy_title'
    similarity       : duplicate confidence score (0.0–1.0)

    Returns
    -------
    MergeResult with full audit information.
    """
    score_existing = completeness_score(existing_record)
    score_incoming = completeness_score(incoming_record)

    # Winner = higher completeness; existing wins ties
    prefer_existing = score_existing >= score_incoming

    winner   = existing_record if prefer_existing else incoming_record
    loser    = incoming_record if prefer_existing else existing_record

    logger.info(
        "Merging: existing(id=%s, completeness=%.2f) vs incoming(completeness=%.2f) "
        "→ prefer_existing=%s | reason=%s | similarity=%.2f",
        existing_record.get("id"), score_existing, score_incoming,
        prefer_existing, reason, similarity,
    )

    # Merge fields (winner's values dominate)
    merged, changed_fields = _field_merge(existing_record, incoming_record,
                                          prefer_existing=prefer_existing)

    # Recompute completeness after merge
    score_after = completeness_score(merged)

    kept_id    = existing_record["id"]
    dropped_id = loser.get("id")

    with conn:
        # 1. Update the kept record's columns
        if changed_fields:
            set_clause = ", ".join(f"{f} = ?" for f in changed_fields
                                   if f in merged)
            values     = [merged[f] for f in changed_fields if f in merged]
            if set_clause:
                conn.execute(
                    f"UPDATE records SET {set_clause} WHERE id = ?",
                    [*values, kept_id],
                )

        # 2. Update completeness score
        _update_completeness(conn, kept_id, score_after)

        # 3. Bump version + snapshot
        if changed_fields:
            _bump_version(conn, kept_id, changed_fields, merged)

        # 4. Soft-delete loser (only if it exists in the DB)
        if dropped_id and dropped_id != kept_id:
            _soft_delete(conn, dropped_id)

        # 5. Update merge_log
        conn.execute(
            """
            INSERT INTO merge_log
                (kept_record_id, dropped_doi, dropped_source, merge_reason, similarity)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                kept_id,
                loser.get("doi", ""),
                loser.get("_source", "unknown"),
                reason,
                similarity,
            ),
        )

    logger.info(
        "Merge complete: kept id=%d | changed_fields=%s | completeness %.2f → %.2f",
        kept_id, changed_fields, score_existing, score_after,
    )

    return MergeResult(
        kept_id              = kept_id,
        dropped_id           = dropped_id,
        merged_fields        = changed_fields,
        completeness_before  = score_existing,
        completeness_after   = score_after,
        reason               = reason,
        similarity           = similarity,
    )


# ---------------------------------------------------------------------------
# Batch merge (processes a full DuplicateResult)
# ---------------------------------------------------------------------------

def batch_merge(conn: sqlite3.Connection, duplicate_result: Any) -> list[MergeResult]:
    """
    Merge all flagged duplicates from a DuplicateResult.

    Parameters
    ----------
    conn             : open SQLite connection
    duplicate_result : DuplicateResult returned by detect_duplicates()

    Returns
    -------
    List of MergeResult, one per merged pair.

    Notes
    -----
    - Exact duplicates are merged automatically.
    - Fuzzy duplicates are also merged here; they were already added to
      duplicate_result.review_queue for human inspection, but the system
      still performs the merge to keep the pipeline non-blocking.
    """
    results: list[MergeResult] = []

    # ── Exact DOI duplicates ──────────────────────────────────────────────
    for incoming in duplicate_result.exact:
        doi = (incoming.get("doi") or "").strip().lower()
        if not doi:
            logger.warning("Exact duplicate missing DOI – skipping merge.")
            continue

        existing = _fetch_record_by_doi(conn, doi)
        if not existing:
            logger.warning("Could not find existing record with DOI %s – skipping.", doi)
            continue

        mr = merge_duplicates(
            conn, existing, incoming,
            reason="exact_doi", similarity=1.0,
        )
        results.append(mr)

    # ── Fuzzy title near-duplicates ───────────────────────────────────────
    for incoming in duplicate_result.fuzzy:
        existing_id = incoming.get("_matched_existing_id")
        similarity  = incoming.get("_duplicate_score", 0.0)

        if not existing_id:
            logger.warning("Fuzzy duplicate missing _matched_existing_id – skipping.")
            continue

        existing = _fetch_record(conn, existing_id)
        if not existing:
            logger.warning("Could not find existing record id=%s – skipping.", existing_id)
            continue

        mr = merge_duplicates(
            conn, existing, incoming,
            reason="fuzzy_title", similarity=similarity,
        )
        results.append(mr)

    logger.info(
        "batch_merge complete: %d merges performed.", len(results)
    )
    return results


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG,
                        format="%(levelname)s | %(name)s | %(message)s")

    from database.schema             import get_connection, init_db
    from database.duplicate_detection import detect_duplicates

    conn = get_connection(":memory:")
    init_db(conn)

    # Seed an existing record (sparse – missing abstract, url, etc.)
    with conn:
        conn.execute(
            """
            INSERT INTO records
                (title, doi, publication_year, repository, _source, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            ("Climate Change Impact Study", "10.1111/climate2021",
             2021, "zenodo", "zenodo"),
        )

    # Incoming record – same DOI but more complete
    incoming = [
        {
            "title":            "Climate Change Impact Study",
            "doi":              "10.1111/climate2021",
            "publication_year": 2021,
            "repository":       "zenodo",
            "abstract":         "A comprehensive study on climate change impacts worldwide.",
            "access_url":       "https://zenodo.org/record/12345",
            "license":          "CC-BY-4.0",
            "keywords":         ["climate", "environment", "global warming"],
            "_source":          "openalex",
        }
    ]

    dup_result = detect_duplicates(conn, incoming)
    print("Duplicate result:", dup_result.summary())

    merge_results = batch_merge(conn, dup_result)
    for mr in merge_results:
        print(f"\nMerge: kept_id={mr.kept_id} | "
              f"changed={mr.merged_fields} | "
              f"completeness {mr.completeness_before:.2f} → {mr.completeness_after:.2f}")

    # Verify final state
    row = conn.execute("SELECT * FROM records WHERE id = ?",
                       (merge_results[0].kept_id,)).fetchone()
    print("\nFinal record:")
    for key in row.keys():
        print(f"  {key:20s}: {row[key]}")
