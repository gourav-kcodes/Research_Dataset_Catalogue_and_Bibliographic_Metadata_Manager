"""
tests/test_processing.py
------------------------
Unit and integration tests for Siyag's processing pipeline:
  - validator.py   (hard rejects + soft flags + rejected_records table)
  - normaliser.py  (normalisation + conflict resolution + flag preservation)
  - versioning.py  (insert, update, soft-delete, history, flags in DB)
  - pipeline.py    (end-to-end + pipeline_run_log table)

Run:
    pytest tests/test_processing.py -v
"""

import json
import os
import sqlite3
from datetime import datetime

import pytest

from src.processing.validator import (
    validate_record,
    validate_records,
    RejectReason,
    ensure_rejected_table,
)
from src.processing.normaliser import (
    normalise_record,
    normalise_records,
    resolve_conflicts,
    _normalise_doi,
    _normalise_author,
    _normalise_keywords,
    _clean_text,
)
from src.processing.versioning import VersionManager
from src.processing.pipeline import run_pipeline


# ══════════════════════════════════════════════════════════════════════════════
# Shared fixtures
# ══════════════════════════════════════════════════════════════════════════════

VALID_RECORD = {
    "title": "A Study on Climate Data",
    "doi": "10.1234/climate.2023",
    "publication_year": 2023,
    "creators": ["Jane Smith", "John Doe"],
    "keywords": ["climate; data; science"],
    "abstract": "<p>An &amp; interesting study.</p>",
    "repository": "Zenodo",
    "source_id": "zenodo-12345",
}


@pytest.fixture
def valid_record():
    return dict(VALID_RECORD)


@pytest.fixture
def tmp_db(tmp_path):
    db_file = str(tmp_path / "test.db")
    vm = VersionManager(db_file)
    yield vm
    vm.close()


@pytest.fixture
def mem_conn():
    """In-memory SQLite connection for validator table tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_rejected_table(conn)
    yield conn
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# validator.py — hard validation
# ══════════════════════════════════════════════════════════════════════════════

class TestValidateRecord:

    def test_valid_record_passes(self, valid_record):
        ok, reason = validate_record(valid_record)
        assert ok is True and reason is None

    def test_missing_title(self, valid_record):
        valid_record["title"] = ""
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.MISSING_TITLE

    def test_none_title(self, valid_record):
        valid_record["title"] = None
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.MISSING_TITLE

    def test_missing_both_ids(self, valid_record):
        valid_record.pop("doi", None)
        valid_record.pop("source_id", None)
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.MISSING_ID

    def test_source_id_alone_accepted(self, valid_record):
        valid_record.pop("doi", None)
        ok, _ = validate_record(valid_record)
        assert ok

    def test_invalid_doi_format(self, valid_record):
        valid_record["doi"] = "http://example.com/not-a-doi"
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.INVALID_DOI

    def test_doi_url_prefix_rejected(self, valid_record):
        valid_record["doi"] = "https://doi.org/10.1234/abc"
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.INVALID_DOI

    def test_missing_year(self, valid_record):
        valid_record.pop("publication_year")
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.MISSING_YEAR

    def test_year_before_1900(self, valid_record):
        valid_record["publication_year"] = 1899
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.INVALID_YEAR

    def test_future_year_rejected(self, valid_record):
        valid_record["publication_year"] = datetime.now().year + 1
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.INVALID_YEAR

    def test_year_as_string_accepted(self, valid_record):
        valid_record["publication_year"] = "2020"
        ok, _ = validate_record(valid_record)
        assert ok

    def test_non_numeric_year_rejected(self, valid_record):
        valid_record["publication_year"] = "not-a-year"
        ok, reason = validate_record(valid_record)
        assert not ok and reason == RejectReason.INVALID_YEAR


class TestSoftFlags:

    def test_normal_record_not_flagged(self, valid_record, tmp_path):
        accepted, _ = validate_records([valid_record], rejected_path=str(tmp_path / "r.json"))
        assert accepted[0]["missing_title_flag"] is False

    def test_short_title_flagged(self, valid_record, tmp_path):
        valid_record["title"] = "Ab"
        accepted, _ = validate_records([valid_record], rejected_path=str(tmp_path / "r.json"))
        assert accepted[0]["missing_title_flag"] is True

    def test_current_year_flagged(self, valid_record, tmp_path):
        valid_record["publication_year"] = datetime.now().year
        accepted, _ = validate_records([valid_record], rejected_path=str(tmp_path / "r.json"))
        assert accepted[0]["year_uncertain"] is True

    def test_past_year_not_flagged(self, valid_record, tmp_path):
        valid_record["publication_year"] = 2020
        accepted, _ = validate_records([valid_record], rejected_path=str(tmp_path / "r.json"))
        assert accepted[0]["year_uncertain"] is False


class TestRejectedTable:

    def test_rejection_written_to_sqlite(self, valid_record, mem_conn, tmp_path):
        bad = dict(valid_record, title="")
        validate_records([bad], conn=mem_conn, rejected_path=str(tmp_path / "r.json"))
        rows = mem_conn.execute("SELECT * FROM rejected_records").fetchall()
        assert len(rows) == 1
        assert rows[0]["reason_code"] == RejectReason.MISSING_TITLE

    def test_accepted_not_in_rejected_table(self, valid_record, mem_conn, tmp_path):
        validate_records([valid_record], conn=mem_conn, rejected_path=str(tmp_path / "r.json"))
        count = mem_conn.execute("SELECT COUNT(*) FROM rejected_records").fetchone()[0]
        assert count == 0

    def test_rejected_json_written(self, valid_record, tmp_path):
        bad = dict(valid_record, title="")
        path = str(tmp_path / "rejected.json")
        _, rejected = validate_records([bad], rejected_path=path)
        assert os.path.exists(path)
        with open(path) as f:
            assert len(json.load(f)) == 1


# ══════════════════════════════════════════════════════════════════════════════
# normaliser.py
# ══════════════════════════════════════════════════════════════════════════════

class TestCleanText:
    def test_strips_html(self):        assert _clean_text("<b>Hi</b>") == "Hi"
    def test_decodes_entities(self):   assert _clean_text("A &amp; B") == "A & B"
    def test_strips_latex(self):       assert _clean_text(r"\emph{word}") == "word"
    def test_collapses_spaces(self):   assert _clean_text("a  b") == "a b"
    def test_none_returns_none(self):  assert _clean_text(None) is None
    def test_blank_returns_none(self): assert _clean_text("   ") is None


class TestNormaliseDoi:
    def test_strips_https(self):        assert _normalise_doi("https://doi.org/10.1/x") == "10.1/x"
    def test_strips_http_dx(self):      assert _normalise_doi("http://dx.doi.org/10.1/x") == "10.1/x"
    def test_canonical_unchanged(self): assert _normalise_doi("10.1234/abc") == "10.1234/abc"
    def test_none_returns_none(self):   assert _normalise_doi(None) is None
    def test_strips_whitespace(self):   assert _normalise_doi("  10.1/a  ") == "10.1/a"


class TestNormaliseAuthor:
    def test_first_last(self):      assert _normalise_author("John Smith") == "Smith, John"
    def test_already_correct(self): assert _normalise_author("Smith, John") == "Smith, John"
    def test_initial_last(self):    assert _normalise_author("J. Smith") == "Smith, J."
    def test_single_name(self):     assert _normalise_author("Aristotle") == "Aristotle"


class TestNormaliseKeywords:
    def test_semicolon_split(self):
        assert _normalise_keywords("a; b; c") == ["a", "b", "c"]
    def test_list_deduplicated(self):
        assert _normalise_keywords(["X", "x", "Y"]) == ["x", "y"]
    def test_none_returns_none(self):
        assert _normalise_keywords(None) is None


class TestConflictResolution:

    def test_creators_authors_mismatch_keeps_creators(self):
        r = resolve_conflicts({
            "creators": ["Smith, Jane"],
            "authors":  ["Jane Smith"],
            "doi": "10.1/x",
        })
        assert "authors" not in r
        assert r["creators"] == ["Smith, Jane"]
        assert len(r["conflict_notes"]) == 1

    def test_no_conflict_empty_notes(self):
        r = resolve_conflicts({"creators": ["Smith, Jane"]})
        assert r["conflict_notes"] == []

    def test_same_value_no_conflict(self):
        r = resolve_conflicts({"creators": ["Smith, Jane"], "authors": ["Smith, Jane"]})
        assert r["conflict_notes"] == []


class TestNormaliseRecord:

    def test_doi_url_stripped(self, valid_record):
        valid_record["doi"] = "https://doi.org/10.1234/climate.2023"
        assert normalise_record(valid_record)["doi"] == "10.1234/climate.2023"

    def test_abstract_cleaned(self, valid_record):
        result = normalise_record(valid_record)
        assert "<p>" not in result["abstract"]
        assert "&amp;" not in result["abstract"]

    def test_year_int(self, valid_record):
        valid_record["publication_year"] = "2023"
        assert normalise_record(valid_record)["publication_year"] == 2023

    def test_keywords_list(self, valid_record):
        assert isinstance(normalise_record(valid_record)["keywords"], list)

    def test_soft_flags_preserved(self, valid_record):
        valid_record["missing_title_flag"] = True
        valid_record["year_uncertain"] = False
        result = normalise_record(valid_record)
        assert result["missing_title_flag"] is True
        assert result["year_uncertain"] is False

    def test_original_not_mutated(self, valid_record):
        original_doi = valid_record["doi"]
        normalise_record(valid_record)
        assert valid_record["doi"] == original_doi


# ══════════════════════════════════════════════════════════════════════════════
# versioning.py
# ══════════════════════════════════════════════════════════════════════════════

class TestVersionManager:

    def test_insert(self, tmp_db, valid_record):
        assert tmp_db.insert_record(valid_record) > 0

    def test_get_active(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        assert tmp_db.get_active(doi=valid_record["doi"])["title"] == valid_record["title"]

    def test_duplicate_raises(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        with pytest.raises(ValueError):
            tmp_db.insert_record(valid_record)

    def test_soft_flags_stored(self, tmp_db, valid_record):
        valid_record["missing_title_flag"] = True
        valid_record["year_uncertain"] = True
        tmp_db.insert_record(valid_record)
        rec = tmp_db.get_active(doi=valid_record["doi"])
        assert rec["missing_title_flag"] is True
        assert rec["year_uncertain"] is True

    def test_conflict_notes_stored(self, tmp_db, valid_record):
        valid_record["conflict_notes"] = ["creators/authors mismatch"]
        tmp_db.insert_record(valid_record)
        rec = tmp_db.get_active(doi=valid_record["doi"])
        assert isinstance(rec["conflict_notes"], list)

    def test_update_version(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        ver = tmp_db.update_record({"title": "Updated"}, doi=valid_record["doi"])
        assert ver == 2

    def test_no_change_no_new_version(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        ver = tmp_db.update_record({"title": valid_record["title"]}, doi=valid_record["doi"])
        assert ver == 1

    def test_history_length(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        tmp_db.update_record({"title": "v2"}, doi=valid_record["doi"])
        tmp_db.update_record({"title": "v3"}, doi=valid_record["doi"])
        assert len(tmp_db.get_history(doi=valid_record["doi"])) == 3

    def test_diff_in_history(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        tmp_db.update_record({"title": "New"}, doi=valid_record["doi"])
        diff = tmp_db.get_history(doi=valid_record["doi"])[1]["changed_fields"]
        assert diff["title"]["new"] == "New"

    def test_soft_delete(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        assert tmp_db.soft_delete(doi=valid_record["doi"])
        assert tmp_db.get_active(doi=valid_record["doi"]) is None

    def test_soft_delete_history_preserved(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        tmp_db.soft_delete(doi=valid_record["doi"])
        assert len(tmp_db.get_history(doi=valid_record["doi"])) >= 1

    def test_insert_or_update_insert(self, tmp_db, valid_record):
        assert tmp_db.insert_or_update(valid_record) == {"action": "inserted", "version": 1}

    def test_insert_or_update_update(self, tmp_db, valid_record):
        tmp_db.insert_record(valid_record)
        result = tmp_db.insert_or_update(dict(valid_record, title="Changed"))
        assert result["action"] == "updated"

    def test_context_manager(self, tmp_path, valid_record):
        with VersionManager(str(tmp_path / "ctx.db")) as vm:
            vm.insert_record(valid_record)
            assert vm.get_active(doi=valid_record["doi"]) is not None


# ══════════════════════════════════════════════════════════════════════════════
# pipeline.py — end-to-end
# ══════════════════════════════════════════════════════════════════════════════

class TestPipeline:

    def _write_raw(self, path, records):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(records, f)

    def test_full_pipeline_runs(self, tmp_path):
        raw = str(tmp_path / "data/raw/all_raw.json")
        self._write_raw(raw, [
            {"title": "Ocean Study", "doi": "10.9999/ocean",
             "publication_year": 2022, "source_id": "z-1"},
            {"doi": "10.0000/bad", "publication_year": 2020, "source_id": "z-2"},  # no title
        ])
        s = run_pipeline(raw, str(tmp_path/"r.json"), str(tmp_path/"n.json"), str(tmp_path/"db.db"))
        assert s["raw_loaded"] == 2
        assert s["accepted"] == 1
        assert s["rejected"] == 1
        assert s["db_inserted"] == 1

    def test_pipeline_run_log_written(self, tmp_path):
        raw = str(tmp_path / "data/raw/all_raw.json")
        db  = str(tmp_path / "db.db")
        self._write_raw(raw, [dict(VALID_RECORD)])
        run_pipeline(raw, str(tmp_path/"r.json"), str(tmp_path/"n.json"), db)

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM pipeline_run_log").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["status"] == "success"
        assert rows[0]["db_inserted"] == 1

    def test_missing_input_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            run_pipeline(str(tmp_path/"nope.json"), str(tmp_path/"r.json"),
                         str(tmp_path/"n.json"), str(tmp_path/"db.db"))

    def test_second_run_updates_not_inserts(self, tmp_path):
        raw = str(tmp_path / "data/raw/all_raw.json")
        db  = str(tmp_path / "db.db")
        self._write_raw(raw, [dict(VALID_RECORD)])
        run_pipeline(raw, str(tmp_path/"r.json"), str(tmp_path/"n.json"), db)
        s2 = run_pipeline(raw, str(tmp_path/"r2.json"), str(tmp_path/"n2.json"), db)
        assert s2["db_inserted"] == 0
