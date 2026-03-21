"""
normaliser.py
-------------
Abhinav Debbarma — Data Collection & Ingestion
Normalises raw records from all 4 sources into a flat,
standard schema that Gaurav's database can insert directly.

Standard output schema:
{
    "title":            str or None,
    "authors":          list of str,
    "publication_year": int or None,
    "doi":              str or None,
    "keywords":         list of str,
    "repository":       str,
    "access_url":       str or None,
    "source":           str,
    "_md5":             str
}
"""

import json
import os
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s]  %(levelname)s  - %(message)s"
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
#  ZENODO normaliser
#  Raw structure: record["metadata"]["title"]
# ─────────────────────────────────────────────

def normalise_zenodo(record):
    """
    Flattens a raw Zenodo record into the standard schema.
    Zenodo stores everything inside a nested 'metadata' key.
    """
    meta = record.get("metadata", {})

    # Title
    title = meta.get("title", None)

    # Authors — Zenodo gives a list of dicts with 'name' key
    creators = meta.get("creators", [])
    authors = [c.get("name", "") for c in creators if c.get("name")]

    # Year — from publication_date field "YYYY-MM-DD"
    pub_date = meta.get("publication_date", "")
    try:
        publication_year = int(pub_date[:4]) if pub_date else None
    except (ValueError, TypeError):
        publication_year = None

    # DOI
    doi = record.get("doi", None)
    if not doi:
        doi = meta.get("doi", None)

    # Keywords — Zenodo gives a list of strings
    keywords = meta.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]

    # Access URL
    access_url = record.get("links", {}).get("html", None)

    return {
        "title":            title,
        "authors":          authors,
        "publication_year": publication_year,
        "doi":              doi,
        "keywords":         keywords,
        "repository":       "Zenodo",
        "access_url":       access_url,
        "source":           "zenodo",
        "_md5":             record.get("_md5", None)
    }


# ─────────────────────────────────────────────
#  KAGGLE normaliser
#  Raw structure: record["titleNullable"]
# ─────────────────────────────────────────────

def normalise_kaggle(record):
    """
    Flattens a raw Kaggle record into the standard schema.
    Kaggle uses 'Nullable' suffixed keys and has no DOI.
    """
    # Title
    title = record.get("titleNullable", None) or record.get("title", None)

    # Authors — Kaggle gives creatorNameNullable as a single string
    author = record.get("creatorNameNullable", None) or record.get("creatorName", None)
    authors = [author] if author else []

    # Year — from lastUpdated field "YYYY-MM-DD HH:MM:SS"
    last_updated = record.get("lastUpdated", "")
    try:
        publication_year = int(str(last_updated)[:4]) if last_updated else None
    except (ValueError, TypeError):
        publication_year = None

    # DOI — Kaggle datasets don't have DOIs
    doi = None

    # Keywords — Kaggle gives tags as a list of dicts with 'name'
    tags = record.get("tags", [])
    if isinstance(tags, list):
        keywords = [t.get("name", "") for t in tags if isinstance(t, dict)]
    else:
        keywords = []

    # Access URL — build from ref field
    ref = record.get("ref", None)
    access_url = f"https://www.kaggle.com/datasets/{ref}" if ref else None

    return {
        "title":            title,
        "authors":          authors,
        "publication_year": publication_year,
        "doi":              doi,
        "keywords":         keywords,
        "repository":       "Kaggle",
        "access_url":       access_url,
        "source":           "kaggle",
        "_md5":             record.get("_md5", None)
    }


# ─────────────────────────────────────────────
#  OPENALEX normaliser
#  Raw structure: record["title"] (already flat)
# ─────────────────────────────────────────────

def normalise_openalex(record):
    """
    Flattens a raw OpenAlex record into the standard schema.
    OpenAlex is already relatively flat — minimal transformation needed.
    """
    # Title
    title = record.get("title", None)

    # Authors — from authorships list
    authorships = record.get("authorships", [])
    authors = []
    for a in authorships:
        author_name = a.get("author", {}).get("display_name", None)
        if author_name:
            authors.append(author_name)

    # Year
    try:
        publication_year = int(record.get("publication_year", 0)) or None
    except (ValueError, TypeError):
        publication_year = None

    # DOI — OpenAlex gives full URL "https://doi.org/10.xxxx/..."
    doi_raw = record.get("doi", None)
    if doi_raw and "doi.org/" in doi_raw:
        doi = doi_raw.split("doi.org/")[-1]
    else:
        doi = doi_raw

    # Keywords
    keywords_raw = record.get("keywords", [])
    if isinstance(keywords_raw, list):
        keywords = [k.get("keyword", "") if isinstance(k, dict) else str(k)
                    for k in keywords_raw]
    else:
        keywords = []

    # Access URL
    primary_location = record.get("primary_location", {}) or {}
    access_url = primary_location.get("landing_page_url", None)

    return {
        "title":            title,
        "authors":          authors,
        "publication_year": publication_year,
        "doi":              doi,
        "keywords":         keywords,
        "repository":       "OpenAlex",
        "access_url":       access_url,
        "source":           "openalex",
        "_md5":             record.get("_md5", None)
    }


# ─────────────────────────────────────────────
#  DATACITE normaliser
#  Raw structure: record["attributes"]["titles"][0]["title"]
# ─────────────────────────────────────────────

def normalise_datacite(record):
    """
    Flattens a raw DataCite record into the standard schema.
    DataCite stores everything inside a deeply nested 'attributes' key.
    """
    attrs = record.get("attributes", {})

    # Title — stored as a list of dicts
    titles = attrs.get("titles", [])
    title = titles[0].get("title", None) if titles else None

    # Authors — stored as 'creators' list
    creators = attrs.get("creators", [])
    authors = []
    for c in creators:
        name = c.get("name", None)
        if name:
            authors.append(name)

    # Year
    try:
        publication_year = int(attrs.get("publicationYear", 0)) or None
    except (ValueError, TypeError):
        publication_year = None

    # DOI
    doi = attrs.get("doi", None)

    # Keywords — stored as 'subjects' list
    subjects = attrs.get("subjects", [])
    keywords = [s.get("subject", "") for s in subjects if s.get("subject")]

    # Access URL
    url = attrs.get("url", None)

    return {
        "title":            title,
        "authors":          authors,
        "publication_year": publication_year,
        "doi":              doi,
        "keywords":         keywords,
        "repository":       "DataCite",
        "access_url":       url,
        "source":           "datacite",
        "_md5":             record.get("_md5", None)
    }


# ─────────────────────────────────────────────
#  MAIN normalise function
#  Auto-detects source and calls the right normaliser
# ─────────────────────────────────────────────

def normalise_record(record):
    """
    Automatically detects which source a record came from
    using the '_source' tag added by ingest_pipeline.py,
    and calls the appropriate normaliser.
    """
    source = record.get("_source", "")

    if source == "zenodo":
        return normalise_zenodo(record)
    elif source == "kaggle":
        return normalise_kaggle(record)
    elif source == "openalex":
        return normalise_openalex(record)
    elif source == "datacite":
        return normalise_datacite(record)
    else:
        logger.warning(f"Unknown source '{source}' — skipping record")
        return None


# ─────────────────────────────────────────────
#  Run normalisation on all_raw.json
# ─────────────────────────────────────────────

def run_normalisation(
    input_path="data/raw/all_raw.json",
output_path="data/processed/all_normalised.json"
):
    """
    Reads all_raw.json, normalises every record into the
    standard flat schema, and saves to all_normalised.json.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    logger.info("=" * 50)
    logger.info("NORMALISATION STARTED")
    logger.info("=" * 50)

    # Load raw records
    with open(input_path, "r") as f:
        raw_records = json.load(f)
    logger.info(f"Loaded {len(raw_records)} raw records from {input_path}")

    # Normalise each record
    normalised = []
    skipped = 0
    for record in raw_records:
        result = normalise_record(record)
        if result:
            normalised.append(result)
        else:
            skipped += 1

    # Save normalised output
    with open(output_path, "w") as f:
        json.dump(normalised, f, indent=2)

    logger.info("=" * 50)
    logger.info("NORMALISATION COMPLETE")
    logger.info(f"  Total input    : {len(raw_records)}")
    logger.info(f"  Normalised     : {len(normalised)}")
    logger.info(f"  Skipped        : {skipped}")
    logger.info(f"  Saved to       : {output_path}")
    logger.info("=" * 50)

    return normalised


# ─────────────────────────────────────────────
#  Run directly
# ─────────────────────────────────────────────

if __name__ == "__main__":
    records = run_normalisation()
    print(f"\nDone! {len(records)} normalised records saved to data/processed/all_normalised.json")
