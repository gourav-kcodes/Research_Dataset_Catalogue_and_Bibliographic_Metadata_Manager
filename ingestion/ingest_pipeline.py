"""
ingest_pipeline.py
------------------
Abhinav Debbarma — Data Collection & Ingestion
Orchestrates the full ingestion process across 4 API sources:
  1. Zenodo     (25 records)
  2. Kaggle     (25 records)
  3. OpenAlex   (25 records)
  4. DataCite   (25 records)
Total target: 100 records saved to data/raw/all_raw.json
"""

import hashlib
import json
import os
import logging
from api_fetcher import fetch_zenodo, fetch_kaggle, fetch_openalex, fetch_datacite

# ─────────────────────────────────────────────
#  Logging Setup
# ─────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s]  %(levelname)s  - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/pipeline.log")
    ]
)
logger = logging.getLogger(__name__)

RAW_DIR = "data/raw"
os.makedirs(RAW_DIR, exist_ok=True)


# ─────────────────────────────────────────────
#  MD5 Hash — for duplicate detection
# ─────────────────────────────────────────────

def compute_md5(record):
    """
    Generates an MD5 hash (unique fingerprint) for a record.
    Two identical records will always produce the same hash.
    """
    record_str = json.dumps(record, sort_keys=True)
    return hashlib.md5(record_str.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────
#  Tag each record with its source
# ─────────────────────────────────────────────

def tag_record(record, source):
    """
    Adds a '_source' field to every record so we always
    know where it came from.
    """
    record["_source"] = source
    return record


# ─────────────────────────────────────────────
#  Deduplicate using MD5 hashes
# ─────────────────────────────────────────────

def deduplicate(records):
    """
    Removes duplicate records by comparing MD5 hashes.
    If two records have the same hash, only the first is kept.
    """
    seen_hashes    = set()
    unique_records = []
    duplicate_count = 0

    for record in records:
        h = compute_md5(record)
        if h in seen_hashes:
            duplicate_count += 1
            logger.debug(f"Duplicate found and skipped — hash: {h[:10]}...")
        else:
            seen_hashes.add(h)
            record["_md5"] = h
            unique_records.append(record)

    logger.info(f"Deduplication complete — {duplicate_count} duplicates removed")
    return unique_records, duplicate_count


# ─────────────────────────────────────────────
#  MAIN PIPELINE
# ─────────────────────────────────────────────

def run_ingestion(max_records=100):
    """
    Runs the full ingestion pipeline across all 4 API sources.
    Each source contributes 25 records = 100 total.
    """
    logger.info("=" * 50)
    logger.info("INGESTION PIPELINE STARTED")
    logger.info("=" * 50)

    all_records = []
    per_source  = max_records // 4   # 25 records each

    # ── Step 1: Zenodo ──────────────────────────
    logger.info("Step 1/4 — Fetching from Zenodo...")
    zenodo_records = fetch_zenodo(query="research dataset", max_records=per_source)
    zenodo_records = [tag_record(r, "zenodo") for r in zenodo_records]
    all_records.extend(zenodo_records)
    logger.info(f"Zenodo: {len(zenodo_records)} records added")

    # ── Step 2: Kaggle ──────────────────────────
    logger.info("Step 2/4 — Fetching from Kaggle...")
    kaggle_records = fetch_kaggle(max_records=per_source)
    kaggle_records = [tag_record(r, "kaggle") for r in kaggle_records]
    all_records.extend(kaggle_records)
    logger.info(f"Kaggle: {len(kaggle_records)} records added")

    # ── Step 3: OpenAlex ────────────────────────
    logger.info("Step 3/4 — Fetching from OpenAlex...")
    openalex_records = fetch_openalex(max_records=per_source)
    openalex_records = [tag_record(r, "openalex") for r in openalex_records]
    all_records.extend(openalex_records)
    logger.info(f"OpenAlex: {len(openalex_records)} records added")

    # ── Step 4: DataCite ────────────────────────
    logger.info("Step 4/4 — Fetching from DataCite...")
    datacite_records = fetch_datacite(max_records=per_source)
    datacite_records = [tag_record(r, "datacite") for r in datacite_records]
    all_records.extend(datacite_records)
    logger.info(f"DataCite: {len(datacite_records)} records added")

    # ── Step 5: Deduplicate ──────────────────────
    logger.info("Deduplicating all collected records...")
    unique_records, dup_count = deduplicate(all_records)

    # ── Step 6: Save to data/raw/ ────────────────
    out_path = os.path.join(RAW_DIR, "all_raw.json")
    with open(out_path, "w") as f:
        json.dump(unique_records, f, indent=2)

    # ── Summary ──────────────────────────────────
    logger.info("=" * 50)
    logger.info("INGESTION PIPELINE COMPLETE")
    logger.info(f"  Total fetched    : {len(all_records)}")
    logger.info(f"  Duplicates found : {dup_count}")
    logger.info(f"  Unique records   : {len(unique_records)}")
    logger.info(f"  Saved to         : {out_path}")
    logger.info("=" * 50)

    return unique_records


# ─────────────────────────────────────────────
#  Run directly
# ─────────────────────────────────────────────

if __name__ == "__main__":
    records = run_ingestion()
    print(f"\nDone! {len(records)} unique records saved to data/raw/all_raw.json")