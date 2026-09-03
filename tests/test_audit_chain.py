"""Tests for Cryptographic Tamper-Evident Audit Chain (M8.5)."""
import pytest
from mk2 import db
from mk2.audit_chain import record_audit_event, verify_audit_chain, GENESIS_HASH


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_audit_chain.db")
    db.migrate()


def test_audit_chain_record_and_verify():
    """Verify appending sequential records maintains 100% cryptographic integrity."""
    e1 = record_audit_event("user", "tool_call", {"tool": "web_search", "query": "python"})
    assert e1["prev_hash"] == GENESIS_HASH
    assert len(e1["entry_hash"]) == 64
    assert len(e1["signature"]) == 64

    e2 = record_audit_event("user", "tool_call", {"tool": "weather", "city": "London"})
    assert e2["prev_hash"] == e1["entry_hash"]

    e3 = record_audit_event("autonomous_runner", "proposal", {"id": 101, "action": "bid"})
    assert e3["prev_hash"] == e2["entry_hash"]

    valid, msg, count = verify_audit_chain()
    assert valid is True
    assert count == 3
    assert "100% cryptographic integrity" in msg


def test_audit_chain_tamper_detection():
    """Verify tampering with a past record is immediately detected."""
    record_audit_event("user", "tool_call", {"target": "doc1"})
    record_audit_event("user", "tool_call", {"target": "doc2"})
    record_audit_event("user", "tool_call", {"target": "doc3"})

    # Tamper with row 2 action directly in database
    with db.connect() as conn:
        conn.execute("UPDATE audit_chain SET action='forged_action' WHERE id=2")

    valid, msg, count = verify_audit_chain()
    assert valid is False
    assert "Tamper detected at record #2" in msg


def test_audit_chain_deletion_detection():
    """Verify deleting an intermediate row breaks the hash chain."""
    record_audit_event("user", "action_1", {})
    record_audit_event("user", "action_2", {})
    record_audit_event("user", "action_3", {})

    # Delete row 2
    with db.connect() as conn:
        conn.execute("DELETE FROM audit_chain WHERE id=2")

    valid, msg, count = verify_audit_chain()
    assert valid is False
    assert "Chain break at record #3" in msg
