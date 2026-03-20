"""
database/
---------
Gaurav Kumawat - Database Design & Schema
DS3294: DS Practice - Project #14

Exposes the three core components of the database layer:
  schema              – SQLite table/index creation
  duplicate_detection – exact DOI + fuzzy title matching
  record_merger       – field-level merging, versioning, soft-delete
"""

from database.schema              import get_connection, init_db
from database.duplicate_detection import detect_duplicates, DuplicateResult, FUZZY_THRESHOLD
from database.record_merger       import batch_merge, merge_duplicates, completeness_score

__all__ = [
    "get_connection", "init_db",
    "detect_duplicates", "DuplicateResult", "FUZZY_THRESHOLD",
    "batch_merge", "merge_duplicates", "completeness_score",
]
