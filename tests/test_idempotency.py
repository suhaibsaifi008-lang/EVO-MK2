"""Tests for Exactly-Once Execution and Idempotency Ledger (M8.3)."""
import time
import pytest
from mk2 import db
from mk2.idempotency import execute_exactly_once, idempotent, generate_idempotency_key


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_idempotency.db")
    db.migrate()


def test_idempotency_exactly_once_execution():
    """Verify function runs once and subsequent calls return cached result."""
    counter = {"calls": 0}

    def process_payment():
        counter["calls"] += 1
        return {"transaction_id": "tx_9988", "amount": 250.0}

    key = generate_idempotency_key("finance", {"user": "alice", "intent": "invoice_123"})

    # 1st call: executes
    executed, res1 = execute_exactly_once(key, "finance", process_payment)
    assert executed is True
    assert res1["transaction_id"] == "tx_9988"
    assert counter["calls"] == 1

    # 2nd call: exactly-once cache hit, does not execute fn
    executed, res2 = execute_exactly_once(key, "finance", process_payment)
    assert executed is False
    assert res2["transaction_id"] == "tx_9988"
    assert counter["calls"] == 1


def test_idempotent_decorator():
    """Verify decorator applies exactly-once semantics across invocations."""
    dispatched = []

    @idempotent(scope="notification", time_window_s=60.0)
    def send_alert(user_id: str, message: str):
        dispatched.append(message)
        return {"sent": True}

    # First invocation
    r1 = send_alert("usr_1", "Low battery warning")
    assert r1["sent"] is True
    assert len(dispatched) == 1

    # Immediate duplicate invocation within time window
    r2 = send_alert("usr_1", "Low battery warning")
    assert r2["sent"] is True
    assert len(dispatched) == 1  # Not executed second time
