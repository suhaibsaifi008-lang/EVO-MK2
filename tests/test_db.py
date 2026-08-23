import pytest

from mk2 import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()


def test_migrate_idempotent():
    db.migrate(); db.migrate()  # must not raise


def test_fact_upsert_overwrites_stale():
    db.remember_fact("city", "Delhi")
    db.remember_fact("city", "Mumbai", source="inferred")
    assert db.search_facts("city")[0]["value"] == "Mumbai"


def test_sensitive_note_still_upsertable_when_explicit_only_in_caller():
    # policy lives above db; db just persists
    db.remember_fact("api key vault", "stored-via-explicit-flow")
    assert db.get_setting("x", "") == ""
    assert db.all_facts()[0]["key"].startswith("api key")


def test_messages_rolling():
    for i in range(30):
        db.log_message("user" if i % 2 else "assistant", f"m{i}")
    rows = db.recent_messages(10)
    assert len(rows) == 10 and rows[-1]["content"] == "m29"
    db.clear_messages()
    assert db.recent_messages(5) == []


def test_trace_written():
    db.trace("turn-1", "stt", 12.3, "ok")
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) n FROM traces").fetchone()["n"]
    assert n == 1
