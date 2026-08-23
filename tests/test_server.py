import json
import time

import pytest
from fastapi.testclient import TestClient

from mk2 import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()


@pytest.fixture()
def client():
    from mk2.server import app

    return TestClient(app)


def test_health_fast_and_complete(client):
    t0 = time.perf_counter()
    r = client.get("/api/health")
    ms = (time.perf_counter() - t0) * 1000
    assert r.status_code == 200 and ms < 2000  # must never hang
    body = r.json()
    for key in ("ok", "voice", "llm_online", "tools", "version"):
        assert key in body


def test_sse_stream_done_and_end(client, monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
    # fast path: deterministic, no model needed
    events = []
    with client.stream("POST", "/api/chat/stream", json={"text": "what time is it"}) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data:"):
                ev = json.loads(line[5:])
                events.append(ev)
                if ev["type"] == "end":
                    break
    kinds = [e["type"] for e in events]
    assert "done" in kinds and kinds[-1] == "end"


def test_audit_endpoint_lists_actions(client, tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
    db.audit("open_app", '{"target":"calc"}', True, "Opening calculator.")
    r = client.get("/api/audit")
    assert any(a["tool"] == "open_app" for a in r.json())
