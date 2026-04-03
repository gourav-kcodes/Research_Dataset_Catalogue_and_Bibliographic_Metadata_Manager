# Research Dataset Catalogue & Bibliographic Metadata Manager

> **DS3294: DS Practice - Project #14**  
> A reproducible, well-documented bibliographic metadata management system for research datasets.

---

# Running the Project (Instruction Manual)

Follow any one of the methods below to use the project.

---

##  Method 1: Use Online Version (Easiest)

You can directly access the project without installing anything:

👉 https://nikunj0305-nexus-research-engine.hf.space

> Note: We used Hugging Face Spaces to host the backend since we are not yet familiar with deploying a server directly via GitHub.

---

##  Method 2: Run on Your Computer

Follow these simple steps:

### Step 1: Clone the Repository
```bash
git clone <your-repo-url>
cd Research_Dataset_Catalogue_and_Bibliographic_Metadata_Manager
````

Or open directly in Codespaces:

[![Open in GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/gourav-kcodes/Research_Dataset_Catalogue_and_Bibliographic_Metadata_Manager)

---

### Step 2: Go to Backend Folder

```bash
cd database
```

---

### Step 3: Install Required Packages

```bash
pip install fastapi uvicorn
```

---

### Step 4: Start the Server

```bash
uvicorn server:app --reload --port 8000
```

---

### Step 5: Open in Browser

Go to:

```
http://localhost:8000
```

---

##  Quick Command Summary

If you already know the steps, just run:

```bash
pip install fastapi uvicorn
uvicorn server:app --reload --port 8000
```

Then open:

```
http://localhost:8000
```

---

##  Important Note

* This project uses **FastAPI** for the backend.
* Running locally gives full control over the database and functionality.
* The online version is provided for easy access and demonstration.





## Table of Contents

- [Project Overview](#project-overview)
- [Team Structure](#team-structure)
- [Data Sources](#data-sources)
- [Pre-processing Plan](#pre-processing-plan)
- [Pipeline & Code Structure](#pipeline--code-structure)
- [Testing & Debugging Plan](#testing--debugging-plan)

---

## Project Overview

This project involves designing and implementing a system to collect, manage, and interact with a structured database of bibliographic entries associated with research datasets. The system supports efficient search, validation, deduplication, and curation of metadata.

**Key capabilities:**
- Automated ingestion from multiple structured sources (BibTeX, JSON, CSV, APIs)
- Relational and/or document-based storage of bibliographic metadata
- Validation, normalisation, and deduplication pipeline
- Query interface for browsing by author, year, keyword, repository, or DOI
- Versioning and incremental update support

---

## Team Structure

| Member | Role | Responsibilities |
|--------|------|-----------------|
| Abhinav Debbarma | Data Collection & Ingestion | Source identification, API/file parsers, raw data pipeline |
| Gaurav Kumawat | Database Design & Schema | Schema design, duplicate detection, record merging |
| Parulekar Siyag Avinash | Validation, Normalisation & Versioning | Data cleaning, conflict resolution, versioning logic |
| Nikunj | Query Interface & Performance Analysis | Search/browse interface, indexing strategies, performance benchmarking |

---

## Data Sources

### Primary Sources

| Source | Format | Access Method |
|--------|--------|---------------|
| [Zenodo](https://zenodo.org) | JSON / REST API | Public API: `https://zenodo.org/api/records` |
| [Kaggle Datasets](https://www.kaggle.com/datasets) | JSON / REST API | Kaggle API, requires `KAGGLE_API_TOKEN` |
| [OpenAlex](https://openalex.org) | JSON / REST API | Public API: `https://api.openalex.org/works` |
| [DataCite](https://datacite.org) | JSON / REST API | Public API: `https://api.datacite.org/dois` |

### Data Fields Collected

Each bibliographic record will capture the following fields where available:

```
title, creators/authors, publication_year, repository, doi,
keywords, abstract, access_url, license, file_format,
subject_area, citation_count, date_collected, source_id
```

### Estimated Corpus Size

Target: **100 records** across at least 3 repositories for meaningful performance comparison.

---

## Pre-processing Plan

### Stage 1: Raw Ingestion
- Fetch 25 records each from Zenodo, Kaggle, OpenAlex, and DataCite REST APIs
- Parse local CSV and JSON files using `file_parser.py` for multi-format support
- Handle pagination and rate limits across all API calls using `requests`
- Compute MD5 hash for every record to detect duplicates at ingestion time
- Tag each record with its source (`_source` field) for traceability
- Store all unique raw records in `data/raw/all_raw.json` staging area

### Stage 2: Normalisation
- Standardise author name format: `Last, First` -> consistent across sources
- Normalise `publication_year` to integer; flag records with missing or malformed dates
- Clean and lower-case `keywords`; split multi-value strings into arrays
- Resolve DOI formatting: strip URL prefixes, keep canonical `10.xxxx/...` format
- Trim whitespace and remove HTML/LaTeX artifacts from text fields

### Stage 3: Validation
- Check mandatory fields: `title`, `doi` or `source_id`, `publication_year`
- Validate DOI format
- Flag records with implausible years (before 1900 or after current year)
- Log all validation failures to a separate `rejected_records` table/collection

### Stage 4: Deduplication
- Primary deduplication: exact DOI match
- Secondary deduplication: fuzzy title matching using `rapidfuzz` (threshold >= 90%)
- Manual review queue for near-duplicate records flagged by fuzzy matching
- Record merge strategy: prefer the record with more complete fields; log merge decisions

### Stage 5: Versioning
- Every update to an existing record creates a new version entry
- Store `version_number`, `updated_at`, `changed_fields` alongside each record
- Soft-delete only: records are marked `is_active = False`, never physically deleted

---

## Pipeline & Code Structure

### Workflow

```mermaid
flowchart TD
    DS1([Zenodo API]) & DS2([OpenAlex API]) & DS3([Kaggle API]) & DS4([DataCite API])
    --> A

    subgraph A1 ["Abhinav Debbarma - Data Collection & Ingestion"]
        A[Scan & Fetch Sources\nREST APIs · BibTeX · JSON · CSV]
        --> A2[Compute MD5 Hash\nDuplicate File Detection]
        --> A3[Classify Records\nAssign Source & Format Tags]
        --> A4[(data/raw/ Staging Area)]
    end

    A4 --> B & C

    subgraph B1 ["Parulekar Siyag Avinash - Validation, Normalisation & Versioning"]
        B[Validate Mandatory Fields\ntitle · doi · year]
        --> B2[Normalise Fields\nAuthor format · DOI · Keywords]
        --> B3[Flag Incomplete Records\nmissing_title · year_uncertain]
        --> B4[Version Tracking\nversion_number · changed_fields]
    end

    subgraph C1 ["Gaurav Kumawat - Database Design & Schema"]
        C[Define Schema\nRelational SQLite · Document MongoDB]
        --> C2[Duplicate Detection\nExact DOI · Fuzzy Title Match]
        --> C3[Record Merging\nPrefer most complete record]
        --> C4[(Database - Clean Records Stored)]
    end

    B4 --> C4

    C4 --> D

    subgraph D1 ["Nikunj - Query Interface & Performance"]
        D[Search Interface\nBy author · year · keyword · DOI]
        --> D2[Indexing Strategy\nIndexed vs Non-indexed Fields]
        --> D3[Benchmarking\nRelational vs Document DB]
        --> D4[Performance Report\nQuery Times · Scalability Analysis]
    end

    D4 --> E([Bibliographic Metadata Manager\n100 Records · Searchable · Versioned])
```

### Member Responsibilities

**Abhinav Debbarma - Data Collection & Ingestion**
Connects to 4 fully automated REST APIs (Zenodo, Kaggle, OpenAlex, and DataCite), fetching 25 records each for a total of 100 records. Handles pagination and rate limits for all API calls. Parses additional local files (CSV and JSON) using `file_parser.py` to demonstrate multi-format support. Computes MD5 hashes for duplicate detection, tags every record with its source, and saves all unique raw records to `data/raw/all_raw.json` for the next stage.

**Gaurav Kumawat - Database Design & Schema**
Designs and maintains the relational (SQLite) and document-based (MongoDB) schemas that store all bibliographic fields. Implements duplicate detection using exact DOI matching and fuzzy title comparison, and defines the record merging strategy that favours the most complete entry across sources.

**Parulekar Siyag Avinash - Validation, Normalisation & Versioning**
Receives raw records from ingestion and validates mandatory fields (title, DOI, year). Normalises author names, DOI formats, and keywords to a consistent standard. Flags incomplete or conflicting records for review and maintains a full version history for every updated record.

**Nikunj - Query Interface & Performance Analysis**
Builds the search and filtering interface that allows browsing by author, year, keyword, repository, and DOI. Compares indexed vs non-indexed fields and relational vs document-based storage through benchmarking, and documents query performance as the corpus grows.

---

## Testing & Debugging Plan

### Unit Tests

Each module will have a corresponding test file in `tests/`. We use **pytest** as the test framework.

| Module | Test Focus |
|--------|-----------|
| `ingestion/` | Parser correctness for each format; API mock responses; pagination handling |
| `database/` | Schema integrity; duplicate detection accuracy; merge output correctness |
| `processing/` | Normalisation edge cases; validation acceptance/rejection rates; version chain correctness |
| `query/` | Search result correctness; filter combinations; index vs non-index performance |

### Test Data

- **Valid fixture set**: 50 hand-curated records covering all formats and repositories
- **Edge case set**: Records with missing fields, malformed DOIs, duplicate entries, unicode characters, conflicting metadata
- **Performance set**: Synthetic dataset of 100 records generated via script for scalability testing

### Integration Tests

- Full pipeline test: raw file -> ingestion -> validation -> database -> query
- Cross-source deduplication test: same dataset present in two repositories
- Versioning test: update a record and verify version history is preserved

### Debugging Approach

- All pipeline stages emit structured logs using Python's `logging` module (level: DEBUG in dev, INFO in prod)
- Rejected records are written to `data/rejected/` with a reason code and timestamp
- A `pipeline_run_log` table tracks each ingestion run: source, records fetched, accepted, rejected, duplicates found
- On schema or query errors, full tracebacks are written to `logs/errors.log`
- Use `pytest --tb=short -v` during development; CI runs on every push via GitHub Actions

### Performance Benchmarking (Nikunj)

- Measure query time for keyword search, author lookup, and DOI lookup at 100 records
- Compare indexed vs non-indexed fields using `EXPLAIN ANALYZE` (MySQL) or equivalent
- Compare relational (SQLite) vs document-based (MongoDB) storage for read-heavy queries
- Results documented in `scripts/benchmark.py` output and summarised in final report

---

*Last updated: March 2026*
