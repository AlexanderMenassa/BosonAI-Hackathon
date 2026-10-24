"""Check that verification rejects wrong outcomes and isolates local settings."""
import sqlite3

import pytest

from scripts.verify import check_outcome, isolated_env


def test_verification_environment_overrides_local_storage_and_provider_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HANGON_DB", "/do-not-touch/existing.db")
    monkeypatch.setenv("HANGON_MAX_TURNS", "1")
    monkeypatch.setenv("BOSON_API_KEY", "fake-test-value")
    monkeypatch.setenv("OLLAMA_URL", "http://do-not-call")
    env = isolated_env(tmp_path)
    assert env["HANGON_DB"] == str(tmp_path / "calls.db")
    assert env["HANGON_REPORTS_DIR"] == str(tmp_path / "reports")
    assert not {"BOSON_API_KEY", "OLLAMA_URL", "HANGON_MAX_TURNS"} & env.keys()


def test_verification_rejects_wrong_outcome_or_block_state(tmp_path):
    db = tmp_path / "calls.db"
    with sqlite3.connect(db) as connection:
        connection.executescript("""
            CREATE TABLE calls (id INTEGER PRIMARY KEY, outcome TEXT);
            CREATE TABLE blocklist (number TEXT);
            INSERT INTO calls VALUES (1, 'forwarded');
        """)
    check_outcome(db, "forwarded", False, "+14165550111")
    with pytest.raises(RuntimeError):
        check_outcome(db, "scam_ended", False, "+14165550111")
    with pytest.raises(RuntimeError):
        check_outcome(db, "forwarded", True, "+14165550111")
