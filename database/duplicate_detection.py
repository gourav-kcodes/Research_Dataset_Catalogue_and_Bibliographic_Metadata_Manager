"""
duplicate_detection.py
----------------------
Gaurav Kumawat - Database Design & Schema
DS3294: DS Practice - Project #14

Implements two-pass duplicate detection for incoming bibliographic records:

  Pass 1 – Exact DOI match
      Any incoming record whose canonical DOI already exists in the
      database is flagged as an exact duplicate immediately.

  Pass 2 – Fuzzy title match  (rapidfuzz, threshold >= 90 %)
      Records without a DOI, or whose DOI was not found, are compared
      against existing titles using token-sort ratio.  Matches at or
      above the threshold are flagged as near-duplicates and added to a
      manual-review queue.

Public API
----------
  detect_duplicates(conn, records)  ->  DuplicateResult

  DuplicateResult
    .exact        list[dict]   – records flagged by DOI match
    .fuzzy        list[dict]   – records flagged by title similarity
    .clean        list[dict]   – records with no duplicate found
    .review_queue list[dict]   – fuzzy matches awaiting human review

Usage
-----
  from database.schema import get_connection, init_db
  from database.duplicate_detection import detect_duplicates

  conn = get_connection("database/catalogue.db")
  init_db(conn)

  result = detect_duplicates(conn, incoming_records)
  print(f"Exact dupes : {len(result.exact)}")
  print(f"Fuzzy dupes : {len(result.fuzzy)}")
  print(f"Clean       : {len(result.clean)}")
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD: float = 90.0   # minimum token-sort ratio to flag as duplicate


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class DuplicateResult:
    """Container for the three-way partition returned by detect_duplicates."""
    exact: list[dict]        = field(default_factory=list)  # exact DOI match
    fuzzy: list[dict]        = field(default_factory=list)  # fuzzy title match
    clean: list[dict]        = field(default_factory=list)  # no duplicate found
    review_queue: list[dict] = field(default_factory=list)  # fuzzy → manual review

    # --- convenience properties ---
    @property
    def total_flagged(self) -> int:
        return len(self.exact) + len(self.fuzzy)

    def summary(self) -> str:
        return (
            f"DuplicateResult("
            f"exact={len(self.exact)}, "
            f"fuzzy={len(self.fuzzy)}, "
            f"clean={len(self.clean)}, "
            f"review_queue={len(self.review_queue)})"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_existing_records(conn: sqlite3.Connection) -> tuple[set[str], list[dict]]:
    """
    Pull all active records from the database.

    Returns
    -------
    existing_dois   : set of canonical DOI strings (lower-cased)
    existing_titles : list of dicts with keys 'id', 'title', 'doi'
    """
    cursor = conn.execute(
        "SELECT id, title, doi FROM records WHERE is_active = 1"
    )
    rows = cursor.fetchall()

    existing_dois: set[str] = set()
    existing_titles: list[dict] = []

    for row in rows:
        doi = (row["doi"] or "").strip().lower()
        if doi:
            existing_dois.add(doi)
        existing_titles.append({
            "id":    row["id"],
            "title": row["title"] or "",
            "doi":   doi,
        })

    logger.debug(
        "Loaded %d existing records (%d with DOI) for duplicate check.",
        len(rows), len(existing_dois)
    )
    return existing_dois, existing_titles


def _normalise_doi(doi: str | None) -> str:
    """
    Strip URL prefixes and lower-case a DOI so comparisons are consistent.

    Examples
    --------
      "https://doi.org/10.1234/abc" -> "10.1234/abc"
      "DOI:10.1234/abc"             -> "10.1234/abc"
    """
    if not doi:
        return ""
    doi = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi


def _is_exact_doi_duplicate(
    incoming_doi: str,
    existing_dois: set[str],
) -> bool:
    """Return True if *incoming_doi* already exists in *existing_dois*."""
    if not incoming_doi:
        return False
    return incoming_doi in existing_dois


def _find_fuzzy_match(
    incoming_title: str,
    existing_titles: list[dict],
    threshold: float = FUZZY_THRESHOLD,
) -> dict[str, Any] | None:
    """
    Return the best fuzzy match for *incoming_title* among *existing_titles*,
    or None if no match meets *threshold*.

    Uses rapidfuzz token_sort_ratio so word-order differences don't matter.
    """
    if not incoming_title.strip():
        return None

    best_score = 0.0
    best_match: dict | None = None

    for existing in existing_titles:
        score = fuzz.token_sort_ratio(
            incoming_title.lower(),
            existing["title"].lower(),
        )
        if score > best_score:
            best_score = score
            best_match = existing

    if best_match and best_score >= threshold:
        return {**best_match, "similarity": round(best_score / 100.0, 4)}

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_duplicates(
    conn: sqlite3.Connection,
    records: list[dict],
    fuzzy_threshold: float = FUZZY_THRESHOLD,
) -> DuplicateResult:
    """
    Partition *records* into exact duplicates, fuzzy near-duplicates, and
    clean (non-duplicate) records.

    Parameters
    ----------
    conn            : open SQLite connection (schema already initialised)
    records         : list of normalised record dicts (from Parulekar's stage)
    fuzzy_threshold : minimum similarity score (0–100) for fuzzy flagging

    Returns
    -------
    DuplicateResult with .exact / .fuzzy / .clean / .review_queue populated.

    Side effects
    ------------
    Writes flagged duplicates to the *merge_log* table for audit purposes.
    """
    result = DuplicateResult()
    existing_dois, existing_titles = _load_existing_records(conn)

    for record in records:
        incoming_doi   = _normalise_doi(record.get("doi"))
        incoming_title = (record.get("title") or "").strip()

        # ── Pass 1: exact DOI match ───────────────────────────────────────
        if _is_exact_doi_duplicate(incoming_doi, existing_dois):
            record["_duplicate_type"]   = "exact_doi"
            record["_duplicate_score"]  = 1.0
            result.exact.append(record)

            _log_merge_event(
                conn,
                kept_doi     = incoming_doi,
                dropped_doi  = incoming_doi,
                dropped_src  = record.get("_source", "unknown"),
                reason       = "exact_doi",
                similarity   = 1.0,
            )
            logger.debug("EXACT duplicate found: DOI=%s", incoming_doi)
            continue

        # ── Pass 2: fuzzy title match ─────────────────────────────────────
        fuzzy_match = _find_fuzzy_match(
            incoming_title, existing_titles, threshold=fuzzy_threshold
        )
        if fuzzy_match:
            record["_duplicate_type"]         = "fuzzy_title"
            record["_duplicate_score"]        = fuzzy_match["similarity"]
            record["_matched_existing_id"]    = fuzzy_match["id"]
            record["_matched_existing_title"] = fuzzy_match["title"]
            result.fuzzy.append(record)
            result.review_queue.append(record)   # always needs human review

            _log_merge_event(
                conn,
                kept_doi     = fuzzy_match.get("doi", ""),
                dropped_doi  = incoming_doi,
                dropped_src  = record.get("_source", "unknown"),
                reason       = "fuzzy_title",
                similarity   = fuzzy_match["similarity"],
            )
            logger.debug(
                "FUZZY duplicate found: title='%s' ~ '%s' (%.1f%%)",
                incoming_title,
                fuzzy_match["title"],
                fuzzy_match["similarity"] * 100,
            )
            continue

        # ── Clean record ──────────────────────────────────────────────────
        result.clean.append(record)

    logger.info(
        "detect_duplicates: total=%d | exact=%d | fuzzy=%d | clean=%d",
        len(records), len(result.exact), len(result.fuzzy), len(result.clean),
    )
    return result


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def _log_merge_event(
    conn: sqlite3.Connection,
    kept_doi: str,
    dropped_doi: str,
    dropped_src: str,
    reason: str,
    similarity: float,
) -> None:
    """
    Insert one row into merge_log.

    We use kept_doi to look up the kept_record_id; if not found we use 0
    (e.g. when the incoming record is itself new but conflicts).
    """
    try:
        row = conn.execute(
            "SELECT id FROM records WHERE doi = ? LIMIT 1", (kept_doi,)
        ).fetchone()
        kept_id = row["id"] if row else 0

        with conn:
            conn.execute(
                """
                INSERT INTO merge_log
                    (kept_record_id, dropped_doi, dropped_source, merge_reason, similarity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (kept_id, dropped_doi, dropped_src, reason, similarity),
            )
    except sqlite3.Error as exc:
        logger.warning("Could not write to merge_log: %s", exc)


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.DEBUG,
                        format="%(levelname)s | %(name)s | %(message)s")

    from database.schema import get_connection, init_db

    conn = get_connection(":memory:")
    init_db(conn)

    # Seed one existing record
    with conn:
        conn.execute(
            """
            INSERT INTO records (title, doi, publication_year, _source, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            ("Deep Learning for NLP", "10.1234/nlp2020", 2020, "zenodo"),
        )

    incoming = [
        # exact DOI duplicate
        {"title": "Deep Learning for NLP", "doi": "10.1234/nlp2020",
         "_source": "openalex", "publication_year": 2020},
        # fuzzy title duplicate (same title, different DOI)
        {"title": "Deep learning for nlp tasks", "doi": "10.9999/other",
         "_source": "datacite", "publication_year": 2021},
        # clean record
        {"title": "Graph Neural Networks Survey", "doi": "10.5678/gnn2022",
         "_source": "zenodo", "publication_year": 2022},
    ]

    result = detect_duplicates(conn, incoming)
    print(result.summary())
    print("Review queue:")
    for r in result.review_queue:
        print(" ", json.dumps({
            "title":   r["title"],
            "score":   r.get("_duplicate_score"),
            "matched": r.get("_matched_existing_title"),
        }, indent=2))
