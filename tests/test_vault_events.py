import json

import pytest
from fastapi.testclient import TestClient

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2 import config

    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr("mk2.vault.VAULT_DIR", tmp_path / "data" / "vault")


@pytest.fixture()
def client():
    from mk2.server import app

    return TestClient(app)


class TestVault:
    def test_write_read_search(self):
        tools.call("vault_write", {"topic": "user profile",
                                   "content": "Prefers concise answers."})
        read = tools.call("vault_read", {"topic": "user profile"})
        assert read["ok"] and "concise" in read["speech"]
        hits = tools.call("vault_search", {"query": "concise answers"})
        assert hits["ok"] and "user-profile" in hits["speech"]

    def test_update_overwrites_topic(self):
        tools.call("vault_write", {"topic": "project evo", "content": "v1 notes"})
        tools.call("vault_write", {"topic": "project evo", "content": "v2 notes"})
        r = tools.call("vault_read", {"topic": "project evo"})
        assert "v2 notes" in r["speech"] and "v1 notes" not in r["speech"]

    def test_missing_note_is_honest(self):
        r = tools.call("vault_read", {"topic": "ghost"})
        assert r["ok"] is False


class TestFaceAndEvents:
    def test_face_page_serves(self, client):
        r = client.get("/face")
        assert r.status_code == 200
        assert "canvas" in r.text

    def test_events_route_exists_and_bus_delivers(self, client):
        # Route registered (live curl check happens in manual QA):
        paths = [r.path for r in client.app.routes]
        assert "/api/events" in paths

        # Bus -> async subscriber delivery contract:
        import asyncio

        from mk2.bus import bus

        async def scenario():
            sub, q = bus.subscribe_async("**")
            bus.publish("convo.turn", {"text": "what time is it",
                                       "reply": "It is 21:32."})
            return await asyncio.wait_for(q.get(), timeout=3)

        ev = asyncio.run(scenario())
        assert ev.payload["text"] == "what time is it"

