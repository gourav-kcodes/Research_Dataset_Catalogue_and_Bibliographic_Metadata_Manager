# Deduplication Coordination

## Why deduplication happens twice

This pipeline performs two separate deduplication passes at different stages.
They are **complementary**, not redundant.

---

## Pass 1 — MD5 hash deduplication (Abhinav / ingest_pipeline.py)

**Where**: `ingest_pipeline.py` → `deduplicate()` function  
**When**: Immediately after fetching raw records from all 4 APIs  
**Input**: Raw API response records (still in source-specific JSON shapes)  
**Method**: MD5 hash of the entire raw record JSON (sorted keys)  
**Catches**: Exact byte-level duplicates — same record returned twice
            by the same API across paginated requests  

**What it does NOT catch**:  
- The same dataset appearing in Zenodo AND OpenAlex with different raw JSON  
- Two records with the same DOI but slightly different metadata fields  
- Near-duplicate titles from different sources  

**Output**: `all_raw.json` — unique raw records, each tagged with `_source`
            and `_md5`

---

## Pass 2 — DOI + fuzzy title deduplication (Gaurav / duplicate_detection.py)

**Where**: `duplicate_detection.py` → `detect_duplicates()` function  
**When**: After normalisation, before DB insert (inside `inserter.py`)  
**Input**: Flat normalised records from `all_normalised.json`  
**Method**:  
  - Pass 2a: Exact canonical DOI match against existing DB records  
  - Pass 2b: Fuzzy title match using rapidfuzz `token_sort_ratio ≥ 90%`
             for records without a DOI  
**Catches**:  
  - Same dataset from two different sources (e.g. Zenodo + OpenAlex)  
  - Same dataset with slightly different titles ("Deep Learning" vs
    "Deep learning for NLP tasks")  

**What it does NOT catch**:  
- Records that passed MD5 dedup but have subtle content differences
  (those are intentionally kept as separate records)  

**Output**: `DuplicateResult` with `.exact`, `.fuzzy`, `.clean` partitions  
            Flagged pairs are merged by `record_merger.py`  
            All decisions written to `merge_log` table for audit  

---

## Flow summary

```
API responses
    │
    ▼
[Pass 1 — MD5 dedup]         ← ingest_pipeline.py
    │ removes exact raw duplicates within same source
    ▼
all_raw.json
    │
    ▼
normaliser.py                ← flattens all 4 source schemas
    │
    ▼
all_normalised.json
    │
    ▼
[Pass 2a — DOI dedup  ]      ← duplicate_detection.py
[Pass 2b — fuzzy dedup]      ← duplicate_detection.py
    │ removes cross-source duplicates on normalised content
    ▼
record_merger.py             ← merges flagged pairs, keeps richer record
    │
    ▼
inserter.py                  ← inserts clean records into SQLite
    │
    ▼
catalogue.db
```

---

## Key rule

**Never remove Pass 1 assuming Pass 2 covers it**, and vice versa.  
Pass 1 runs on raw JSON before normalisation — it is fast and catches
pagination duplicates cleanly.  
Pass 2 runs on normalised content against the live DB — it is the only
pass that can catch cross-source semantic duplicates.  

Both passes must remain in place for the pipeline to be correct.
