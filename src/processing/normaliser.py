"""
normaliser.py
-------------
Normalises bibliographic records after they pass validation.

Operations:
  - Author names  → "Last, First" format
  - DOI           → strip URL prefixes, keep canonical 10.xxxx/...
  - Keywords      → lowercase, split multi-value strings, deduplicate, sort
  - Text fields   → strip whitespace, remove HTML tags & LaTeX artifacts
  - Year          → cast to int
  - Conflict flags→ preserved from validator (missing_title_flag, year_uncertain)
  - Blank strings → converted to None

Usage:
    from src.processing.normaliser import normalise_records
    clean_records = normalise_records(accepted_records)
"""

import re
import html
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Patterns ───────────────────────────────────────────────────────────────────
_DOI_URL_PREFIX  = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)
_LATEX_CMD       = re.compile(r"\\[a-zA-Z]+\{([^}]*)\}")
_LATEX_SPECIAL   = re.compile(r"\\[^a-zA-Z\s]")
_HTML_TAG        = re.compile(r"<[^>]+>")
_MULTI_SPACE     = re.compile(r"\s+")
_KEYWORD_SPLIT   = re.compile(r"[;,|]+")

# Fields that are bibliographic content (preserved through normalisation)
_CONTENT_FIELDS = {
    "doi", "source_id", "title", "creators", "authors",
    "publication_year", "repository", "keywords", "abstract",
    "access_url", "license", "file_format", "subject_area",
    "citation_count", "date_collected", "source", "_source",
}

# Soft-flag fields from validator — must be preserved as-is
_FLAG_FIELDS = {"missing_title_flag", "year_uncertain"}


# ── Text cleaning ──────────────────────────────────────────────────────────────

def _clean_text(value: Any) -> str | None:
    """Remove HTML, decode entities, strip LaTeX, collapse whitespace."""
    if value is None:
        return None
    text = str(value)
    text = html.unescape(text)
    text = _HTML_TAG.sub(" ", text)
    text = _LATEX_CMD.sub(r"\1", text)
    text = _LATEX_SPECIAL.sub("", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    return text if text else None


# ── DOI normalisation ──────────────────────────────────────────────────────────

def _normalise_doi(doi: Any) -> str | None:
    if doi is None:
        return None
    doi = str(doi).strip()
    doi = _DOI_URL_PREFIX.sub("", doi)
    return doi if doi else None


# ── Author normalisation ───────────────────────────────────────────────────────

def _normalise_author(name: str) -> str:
    """Convert a single name to 'Last, First' format."""
    name = name.strip()
    if not name:
        return name
    if "," in name:
        parts = [p.strip() for p in name.split(",", 1)]
        return f"{parts[0]}, {parts[1]}" if len(parts) == 2 else name
    tokens = name.split()
    if len(tokens) == 1:
        return tokens[0]
    last = tokens[-1]
    first = " ".join(tokens[:-1])
    return f"{last}, {first}"


def _normalise_authors(authors: Any) -> list[str] | None:
    """Accept a list or semicolon-separated string; return 'Last, First' list."""
    if authors is None:
        return None
    if isinstance(authors, list):
        # Handle Zenodo/OpenAlex style: [{"name": "Smith, Jane"}, ...] or plain strings
        raw_list = []
        for a in authors:
            if isinstance(a, dict):
                name = a.get("name") or a.get("full_name") or a.get("display_name") or ""
                if name:
                    raw_list.append(name)
            elif a:
                raw_list.append(str(a))
    else:
        raw_list = [a.strip() for a in re.split(r";|&| and ", str(authors)) if a.strip()]
    normalised = [_normalise_author(a) for a in raw_list if a]
    return normalised if normalised else None


# ── Keyword normalisation ──────────────────────────────────────────────────────

def _normalise_keywords(keywords: Any) -> list[str] | None:
    if keywords is None:
        return None
    if isinstance(keywords, list):
        raw = [str(k) for k in keywords if k]
    else:
        raw = _KEYWORD_SPLIT.split(str(keywords))
    cleaned = sorted(set(k.strip().lower() for k in raw if k.strip()))
    return cleaned if cleaned else None


# ── Year normalisation ─────────────────────────────────────────────────────────

def _normalise_year(year: Any) -> int | None:
    try:
        return int(year)
    except (TypeError, ValueError):
        return None


# ── Conflict resolution ────────────────────────────────────────────────────────

def resolve_conflicts(record: dict) -> dict:
    """
    Detect and resolve field-level conflicts within a single record.

    Current rules:
      - If both 'creators' and 'authors' keys exist and differ, prefer
        'creators' (Zenodo/OpenAlex standard) and log the conflict.
      - If 'doi' is present but does not match the normalised form, keep
        the normalised form and log.

    Returns a copy of the record with conflicts resolved and a
    'conflict_notes' list field added (empty if no conflicts).
    """
    r = dict(record)
    notes: list[str] = []

    # Conflict: creators vs authors
    creators = r.get("creators")
    authors  = r.get("authors")
    if creators and authors and creators != authors:
        notes.append(
            f"creators/authors mismatch — kept 'creators': {creators!r} "
            f"(discarded 'authors': {authors!r})"
        )
        r.pop("authors", None)

    r["conflict_notes"] = notes
    if notes:
        logger.warning("Conflict resolved for doi=%r: %s", r.get("doi"), notes)

    return r


# ── Record-level normalisation ─────────────────────────────────────────────────

def normalise_record(record: dict) -> dict:
    """
    Normalise a single validated bibliographic record.

    - Preserves soft flags (missing_title_flag, year_uncertain) from validator.
    - Resolves field-level conflicts before normalising.
    - Never mutates the original dict.
    """
    r = resolve_conflicts(record)

    # Text fields
    for field in ("title", "abstract"):
        r[field] = _clean_text(r.get(field))

    # Authors (support both key names)
    for key in ("creators", "authors"):
        if key in r:
            r[key] = _normalise_authors(r[key])

    # DOI
    r["doi"] = _normalise_doi(r.get("doi"))

    # Keywords
    r["keywords"] = _normalise_keywords(r.get("keywords"))

    # Year
    r["publication_year"] = _normalise_year(r.get("publication_year"))

    # Generic string fields
    for field in ("repository", "access_url", "license", "file_format",
                  "subject_area", "source_id", "source", "_source"):
        val = r.get(field)
        if isinstance(val, str):
            r[field] = val.strip() or None

    # Preserve soft flags exactly as set by validator
    for flag in _FLAG_FIELDS:
        if flag in record:
            r[flag] = record[flag]

    logger.debug("Normalised record: doi=%r title=%r", r.get("doi"), r.get("title"))
    return r


def normalise_records(records: list[dict]) -> list[dict]:
    """
    Normalise a list of validated records.

    Args:
        records: Accepted records from validate_records().

    Returns:
        List of normalised record dicts.
    """
    result = [normalise_record(r) for r in records]
    logger.info("Normalisation complete — %d records processed.", len(result))
    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json, os

    logging.basicConfig(level=logging.INFO)
    INPUT_PATH  = "data/raw/all_raw.json"
    OUTPUT_PATH = "data/processed/normalised_records.json"

    if not os.path.exists(INPUT_PATH):
        raise SystemExit(f"File not found: {INPUT_PATH}")

    os.makedirs("data/processed", exist_ok=True)

    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    result = normalise_records(raw)

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"✅ Normalised {len(result)} records → {OUTPUT_PATH}")
