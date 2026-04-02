import requests
import time
import json
import os
import logging

# Set up logging so every step is recorded
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s]  %(levelname)s  - %(message)s"
)
logger = logging.getLogger(__name__)

RAW_DIR = "data/raw"
os.makedirs(RAW_DIR, exist_ok=True)


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
    page_size = 50  # Increased to fetch in larger batches

    while len(records) < max_records:
        params = {
            "q": query,
            "type": "dataset",
            "size": page_size,
            "page": page
        }

        try:
            response = requests.get(url, params=params, timeout=10)

            # Handle rate limit
            if response.status_code == 429:
                logger.warning("Rate limit hit on Zenodo. Waiting 10 seconds...")
                time.sleep(10)
                continue

            response.raise_for_status()  # raise error for 4xx/5xx responses
            data = response.json()
            hits = data.get("hits", {}).get("hits", [])

            if not hits:
                logger.info("No more records from Zenodo.")
                break

            records.extend(hits)
            logger.debug(f"Zenodo page {page} fetched — {len(hits)} records")
            page += 1

            # Be polite to the API
            time.sleep(1)

        except requests.exceptions.RequestException as e:
            logger.error(f"Zenodo request failed: {e}")
            break

    # Trim to max_records
    records = records[:max_records]
    logger.info(f"Zenodo fetch complete — {len(records)} records collected")

    # Save raw output
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
    Returns a list of raw record dictionaries.
    """
    logger.info(f"Starting Kaggle fetch | max={max_records}")

    api_token = os.environ.get("KAGGLE_API_TOKEN")

    if not api_token:
        logger.error("KAGGLE_API_TOKEN not set in environment variables.")
        return []

    url = "https://www.kaggle.com/api/v1/datasets/list"
    records = []
    page = 1

    while len(records) < max_records:
        params = {
            "page":     page,
            "pageSize": 100, # Increased from 20 to 100 to reduce API calls
            "sortBy":   "votes"
        }

        try:
            response = requests.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {api_token}"},
                timeout=10
            )

            if response.status_code == 429:
                logger.warning("Rate limit hit on Kaggle. Waiting 10 seconds...")
                time.sleep(10)
                continue

            response.raise_for_status()
            data = response.json()

            if not data:
                logger.info("No more records from Kaggle.")
                break

            records.extend(data)
            logger.debug(f"Kaggle page {page} fetched — {len(data)} records")
            page += 1
            
            # Be polite to the API
            time.sleep(1)

        except requests.exceptions.RequestException as e:
            logger.error(f"Kaggle request failed: {e}")
            break

    records = records[:max_records]
    logger.info(f"Kaggle fetch complete — {len(records)} records collected")

    out_path = os.path.join(RAW_DIR, "kaggle_raw.json")
    with open(out_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info(f"Kaggle raw data saved to {out_path}")

    return records


# ─────────────────────────────────────────────
#  3. OPENALEX
# ─────────────────────────────────────────────
 
def fetch_openalex(max_records=250):
    """
    Fetches dataset records from the OpenAlex public API.
    No API key needed — completely free and open.
    Returns a simplified list of record dictionaries.
    """
    logger.info(f"Starting OpenAlex fetch | max={max_records}")
 
    url = "https://api.openalex.org/works"
    records = []
    page = 1
    page_size = 100 # Increased from 25 to 100
 
    while len(records) < max_records:
        params = {
            "filter":    "type:dataset",
            "per_page":  page_size,
            "page":      page,
            "select":    "id,title,authorships,publication_year,doi,keywords,primary_location"
        }
        try:
            response = requests.get(
                url,
                params=params,
                headers={"User-Agent": "ds3294-project/1.0 (abhinav@student.edu)"},
                timeout=10
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
                break
            records.extend(results)
            logger.debug(f"OpenAlex page {page} fetched — {len(results)} records")
            page += 1
            
            time.sleep(1) # Courtesy delay
            
        except requests.exceptions.RequestException as e:
            logger.error(f"OpenAlex request failed: {e}")
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
    No API key needed — completely free and open.
    Returns a simplified list of record dictionaries.
    """
    logger.info(f"Starting DataCite fetch | max={max_records}")
 
    url = "https://api.datacite.org/dois"
    records = []
    page = 1
    page_size = 100 # Increased from 25 to 100
 
    while len(records) < max_records:
        params = {
            "resource-type-id": "dataset",
            "page[size]":       page_size,
            "page[number]":     page,
            "sort":             "-created"
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 429:
                logger.warning("Rate limit hit on DataCite. Waiting 10 seconds...")
                time.sleep(10)
                continue
            response.raise_for_status()
            data = response.json()
            results = data.get("data", [])
            if not results:
                logger.info("No more records from DataCite.")
                break
            records.extend(results)
            logger.debug(f"DataCite page {page} fetched — {len(results)} records")
            page += 1
            
            time.sleep(1) # Courtesy delay
            
        except requests.exceptions.RequestException as e:
            logger.error(f"DataCite request failed: {e}")
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
    
    zenodo_data = fetch_zenodo()
    kaggle_data = fetch_kaggle()
    openalex_data = fetch_openalex()
    datacite_data = fetch_datacite()
    
    total_fetched = len(zenodo_data) + len(kaggle_data) + len(openalex_data) + len(datacite_data)
    logger.info(f"Collection complete. Total datasets gathered: {total_fetched}")
