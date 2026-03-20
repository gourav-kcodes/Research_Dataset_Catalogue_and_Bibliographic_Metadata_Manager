"""
file_parser.py
--------------
Abhinav Debbarma — Data Collection & Ingestion
Parses downloaded files from IEEE DataPort and UCI ML Repository.
Supports BibTeX (.bib), JSON (.json), and CSV (.csv) formats.
"""

import json
import csv
import os
import logging

logger = logging.getLogger(__name__)

# Safely import bibtexparser
try:
    import bibtexparser
    BIBTEX_AVAILABLE = True
except ImportError:
    BIBTEX_AVAILABLE = False
    logger.warning("bibtexparser not installed. BibTeX parsing will be skipped.")
    logger.warning("Install it with: pip install bibtexparser")


# ─────────────────────────────────────────────
#  BibTeX Parser
# ─────────────────────────────────────────────

def parse_bibtex(file_path):
    """
    Parses a .bib file and returns a list of record dictionaries.
    Example fields: title, author, year, doi, journal, keywords
    """
    if not BIBTEX_AVAILABLE:
        logger.error("bibtexparser is not installed. Cannot parse BibTeX.")
        return []

    logger.info(f"Parsing BibTeX file: {file_path}")

    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        bib_database = bibtexparser.load(f)

    records = bib_database.entries  # list of dicts
    logger.info(f"BibTeX parse complete — {len(records)} records found")
    return records


# ─────────────────────────────────────────────
#  JSON Parser
# ─────────────────────────────────────────────

def parse_json(file_path):
    """
    Parses a .json file and returns a list of record dictionaries.
    Handles both a list of records and a single record object.
    """
    logger.info(f"Parsing JSON file: {file_path}")

    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return []

    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Handle both list and single object
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = [data]
    else:
        logger.error(f"Unexpected JSON format in {file_path}")
        return []

    logger.info(f"JSON parse complete — {len(records)} records found")
    return records


# ─────────────────────────────────────────────
#  CSV Parser
# ─────────────────────────────────────────────

def parse_csv(file_path):
    """
    Parses a .csv file and returns a list of record dictionaries.
    Each row becomes one dictionary with column headers as keys.
    """
    logger.info(f"Parsing CSV file: {file_path}")

    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        return []

    records = []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            records.append(dict(row))

    logger.info(f"CSV parse complete — {len(records)} records found")
    return records


# ─────────────────────────────────────────────
#  Auto Parser — detects format from extension
# ─────────────────────────────────────────────

def parse_file(file_path):
    """
    Automatically detects the file format from its extension
    and calls the appropriate parser.
    Supported: .bib, .json, .csv
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".bib":
        return parse_bibtex(file_path)
    elif ext == ".json":
        return parse_json(file_path)
    elif ext == ".csv":
        return parse_csv(file_path)
    else:
        logger.error(f"Unsupported file format: {ext} — skipping {file_path}")
        return []