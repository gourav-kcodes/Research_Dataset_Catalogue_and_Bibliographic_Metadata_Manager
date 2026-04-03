import requests
import time
import json
import os
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s]  %(levelname)s  - %(message)s"
)
logger = logging.getLogger(__name__)

RAW_DIR = "data/raw"
os.makedirs(RAW_DIR, exist_ok=True)

MAX_RETRIES = 3  # Retry up to 3 times on failure


# ─────────────────────────────────────────────
#  1. ZENODO
# ─────────────────────────────────────────────

def fetch_zenodo(query="research dataset", max_records=250):
    """
    Fetches records from the Zenodo public API.
    Returns a list of raw record dictionaries.
    """
    logger.info(f"Starting Zenodo fetch | query='{query}' | max={max_records}")

    url = "https://zenodo.org/api/records"
    records = []
    page = 1
    page_size = 50

    while len(records) < max_records:
        params = {
            "q":    query,
            "type": "dataset",
            "size": page_size,
            "page": page
        }

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(url, params=params, timeout=15)

                if response.status_code == 429:
                    logger.warning("Rate limit hit on Zenodo. Waiting 10 seconds...")
                    time.sleep(10)
                    continue

                response.raise_for_status()
                data = response.json()
                hits = data.get("hits", {}).get("hits", [])

                if not hits:
                    logger.info("No more records from Zenodo.")
                    records = records[:max_records]
                    out_path = os.path.join(RAW_DIR, "zenodo_raw.json")
                    with open(out_path, "w") as f:
                        json.dump(records, f, indent=2)
                    logger.info(f"Zenodo raw data saved to {out_path}")
                    return records

                records.extend(hits)
                logger.debug(f"Zenodo page {page} fetched — {len(hits)} records")
                page += 1
                time.sleep(1)
                success = True
                break

            except requests.exceptions.RequestException as e:
                logger.warning(f"Zenodo attempt {attempt}/{MAX_RETRIES} failed: {e}")
                time.sleep(5 * attempt)

        if not success:
            logger.error("Zenodo fetch aborted after max retries.")
            break

    records = records[:max_records]
    logger.info(f"Zenodo fetch complete — {len(records)} records collected")

    out_path = os.path.join(RAW_DIR, "zenodo_raw.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info(f"Zenodo raw data saved to {out_path}")

    return records


# ─────────────────────────────────────────────
#  2. KAGGLE
# ─────────────────────────────────────────────

def fetch_kaggle(max_records=250):
    """
    Fetches dataset metadata from the Kaggle public API.
    Requires KAGGLE_API_TOKEN set as environment variable.
    Kaggle caps pageSize at 20 — this handles that correctly.
    Returns a list of raw record dictionaries.
    """
    logger.info(f"Starting Kaggle fetch | max={max_records}")

    api_token = os.environ.get("KAGGLE_API_TOKEN")
    if not api_token:
        logger.error("KAGGLE_API_TOKEN not set. Skipping Kaggle fetch.")
        return []

    url = "https://www.kaggle.com/api/v1/datasets/list"
    records = []
    page = 1
    page_size = 20  # Kaggle hard caps at 20 per page

    while len(records) < max_records:
        params = {
            "page":     page,
            "pageSize": page_size,
            "sortBy":   "votes"
        }

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {api_token}"},
                    timeout=15
                )

                if response.status_code == 429:
                    logger.warning("Rate limit hit on Kaggle. Waiting 10 seconds...")
                    time.sleep(10)
                    continue

                response.raise_for_status()
                data = response.json()

                if not data:
                    logger.info("No more records from Kaggle.")
                    records = records[:max_records]
                    out_path = os.path.join(RAW_DIR, "kaggle_raw.json")
                    with open(out_path, "w") as f:
                        json.dump(records, f, indent=2)
                    logger.info(f"Kaggle raw data saved to {out_path}")
                    return records

                records.extend(data)
                logger.debug(f"Kaggle page {page} fetched — {len(data)} records")
                page += 1
                time.sleep(1)
                success = True
                break

            except requests.exceptions.RequestException as e:
                logger.warning(f"Kaggle attempt {attempt}/{MAX_RETRIES} failed: {e}")
                time.sleep(5 * attempt)

        if not success:
            logger.error("Kaggle fetch aborted after max retries.")
            break

    records = records[:max_records]
    logger.info(f"Kaggle fetch complete — {len(records)} records collected")

    out_path = os.path.join(RAW_DIR, "kaggle_raw.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info(f"Kaggle raw data saved to {out_path}")

    return records


# ─────────────────────────────────────────────
#  3. OPENALEX  (cursor-based pagination)
# ─────────────────────────────────────────────

def fetch_openalex(max_records=250):
    """
    Fetches dataset records from the OpenAlex public API.
    Uses cursor-based pagination (more reliable than page numbers).
    No API key needed.
    Returns a list of raw record dictionaries.
    """
    logger.info(f"Starting OpenAlex fetch | max={max_records}")

    url = "https://api.openalex.org/works"
    records = []
    cursor = "*"       # OpenAlex cursor starts with "*"
    page_size = 100

    while len(records) < max_records:
        params = {
            "filter":   "type:dataset",
            "per_page": page_size,
            "cursor":   cursor,
            "select":   "id,title,authorships,publication_year,doi,keywords,primary_location"
        }

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers={"User-Agent": "ds3294-project/1.0 (student@university.edu)"},
                    timeout=15
                )

                if response.status_code == 429:
                    logger.warning("Rate limit hit on OpenAlex. Waiting 10 seconds...")
                    time.sleep(10)
                    continue

                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])

                if not results:
                    logger.info("No more records from OpenAlex.")
                    records = records[:max_records]
                    out_path = os.path.join(RAW_DIR, "openalex_raw.json")
                    with open(out_path, "w") as f:
                        json.dump(records, f, indent=2)
                    logger.info(f"OpenAlex raw data saved to {out_path}")
                    return records

                records.extend(results)
                logger.debug(f"OpenAlex cursor fetch — {len(results)} records (total so far: {len(records)})")

                # Get next cursor from response metadata
                next_cursor = data.get("meta", {}).get("next_cursor", None)
                if not next_cursor:
                    logger.info("OpenAlex cursor exhausted.")
                    break

                cursor = next_cursor
                time.sleep(1)
                success = True
                break

            except requests.exceptions.RequestException as e:
                logger.warning(f"OpenAlex attempt {attempt}/{MAX_RETRIES} failed: {e}")
                time.sleep(5 * attempt)

        if not success:
            logger.error("OpenAlex fetch aborted after max retries.")
            break

    records = records[:max_records]
    logger.info(f"OpenAlex fetch complete — {len(records)} records collected")

    out_path = os.path.join(RAW_DIR, "openalex_raw.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info(f"OpenAlex raw data saved to {out_path}")

    return records


# ─────────────────────────────────────────────
#  4. DATACITE
# ─────────────────────────────────────────────

def fetch_datacite(max_records=250):
    """
    Fetches dataset DOI records from the DataCite public API.
    No API key needed.
    Returns a list of raw record dictionaries.
    """
    logger.info(f"Starting DataCite fetch | max={max_records}")

    url = "https://api.datacite.org/dois"
    records = []
    page = 1
    page_size = 100

    while len(records) < max_records:
        params = {
            "resource-type-id": "dataset",
            "page[size]":       page_size,
            "page[number]":     page,
            "sort":             "-created"
        }

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = requests.get(url, params=params, timeout=15)

                if response.status_code == 429:
                    logger.warning("Rate limit hit on DataCite. Waiting 10 seconds...")
                    time.sleep(10)
                    continue

                response.raise_for_status()
                data = response.json()
                results = data.get("data", [])

                if not results:
                    logger.info("No more records from DataCite.")
                    records = records[:max_records]
                    out_path = os.path.join(RAW_DIR, "datacite_raw.json")
                    with open(out_path, "w") as f:
                        json.dump(records, f, indent=2)
                    logger.info(f"DataCite raw data saved to {out_path}")
                    return records

                records.extend(results)
                logger.debug(f"DataCite page {page} fetched — {len(results)} records")
                page += 1
                time.sleep(1)
                success = True
                break

            except requests.exceptions.RequestException as e:
                logger.warning(f"DataCite attempt {attempt}/{MAX_RETRIES} failed: {e}")
                time.sleep(5 * attempt)

        if not success:
            logger.error("DataCite fetch aborted after max retries.")
            break

    records = records[:max_records]
    logger.info(f"DataCite fetch complete — {len(records)} records collected")

    out_path = os.path.join(RAW_DIR, "datacite_raw.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info(f"DataCite raw data saved to {out_path}")

    return records


# ─────────────────────────────────────────────
#  EXECUTION BLOCK
# ─────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Starting bulk fetch of 1000 datasets (250 per source)...")

    zenodo_data   = fetch_zenodo(max_records=250)
    kaggle_data   = fetch_kaggle(max_records=250)
    openalex_data = fetch_openalex(max_records=250)
    datacite_data = fetch_datacite(max_records=250)

    total = len(zenodo_data) + len(kaggle_data) + len(openalex_data) + len(datacite_data)
    logger.info(f"Collection complete. Total datasets gathered: {total}")
    
