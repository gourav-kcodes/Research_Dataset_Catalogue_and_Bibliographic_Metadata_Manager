import hashlib
import json
import os
import logging
from api_fetcher import fetch_zenodo, fetch_kaggle, fetch_openalex, fetch_datacite
from normaliser import run_normalisation
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'database'))
from schema import get_connection, init_db

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
#  MD5 Hash — for exact duplicate detection
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
    Removes exact duplicate records by comparing MD5 hashes.
    If two records have the same hash, only the first is kept.
    """
    seen_hashes     = set()
    unique_records  = []
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

def run_ingestion(max_records=1000):
    """
    Runs the full ingestion pipeline across all 4 API sources.
    Each source contributes 250 records = 1000 total.
    """
    logger.info("=" * 50)
    logger.info("INGESTION PIPELINE STARTED")
    logger.info(f"Target: {max_records} records ({max_records // 4} per source)")
    logger.info("=" * 50)

    all_records = []
    per_source  = max_records // 4   # 250 records each

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

    # ── Step 6: Save raw to data/raw/ ────────────
    out_path = os.path.join(RAW_DIR, "all_raw.json")
    with open(out_path, "w") as f:
        json.dump(unique_records, f, indent=2)
    logger.info(f"Raw unique records saved to {out_path}")

    # ── Step 7: Normalise all records ─────────────
    logger.info("Normalising all records into standard schema...")
    normalised_records = run_normalisation(
        input_path=os.path.join(RAW_DIR, "all_raw.json"),
        output_path="data/processed/all_normalised.json"
    )
    logger.info(f"Normalisation complete — {len(normalised_records)} records ready for DB")

    # ── Step 8: Write pipeline_run_log to DB ──────
    logger.info("Writing pipeline run summary to database...")
    try:
        db_conn = get_connection("database/catalogue.db")
        init_db(db_conn)
        with db_conn:
            for source_name, source_records in [
                ("zenodo",   zenodo_records),
                ("kaggle",   kaggle_records),
                ("openalex", openalex_records),
                ("datacite", datacite_records),
            ]:
                db_conn.execute(
                    """
                    INSERT INTO pipeline_run_log
                        (source, records_fetched, records_accepted, duplicates_found)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        source_name,
                        len(source_records),
                        len(source_records),
                        dup_count,
                    )
                )
        logger.info("pipeline_run_log updated successfully")
    except Exception as e:
        logger.warning(f"Could not write to pipeline_run_log: {e} — pipeline continues")

    # ── Summary ───────────────────────────────────
    logger.info("=" * 50)
    logger.info("INGESTION PIPELINE COMPLETE")
    logger.info(f"  Total fetched      : {len(all_records)}")
    logger.info(f"  Duplicates removed : {dup_count}")
    logger.info(f"  Unique records     : {len(unique_records)}")
    logger.info(f"  Normalised records : {len(normalised_records)}")
    logger.info(f"  Raw saved to       : {out_path}")
    logger.info(f"  Normalised saved to: data/processed/all_normalised.json")
    logger.info("=" * 50)

    return unique_records


# ─────────────────────────────────────────────
#  Run directly
# ─────────────────────────────────────────────

if __name__ == "__main__":
    records = run_ingestion(max_records=1000)
    print(f"\nDone! {len(records)} unique records saved to data/raw/all_raw.json")
