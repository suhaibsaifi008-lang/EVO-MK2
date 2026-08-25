"""Voice v2 (WebRTC channel embedded in the console server)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mk2.voice import webrtc_v2


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    from mk2 import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "voice_v2.db")
    db.migrate()


@pytest.fixture()
def fresh_app():
    app = FastAPI()
    st = webrtc_v2.register(app)
    return app, st


def test_register_reports_ready(fresh_app):
    app, st = fresh_app
    assert st["available"] is True
    assert st["enabled"] is True
    assert st["client_url"] == "/voice/client/"


def test_start_handshake(fresh_app):
    app, _ = fresh_app
    client = TestClient(app)
    r = client.post("/start", json={"transport": "webrtc",
                                    "enableDefaultIceServers": True})
    assert r.status_code == 200
    body = r.json()
    assert body.get("sessionId") and body.get("transports") == ["webrtc"]
    assert "iceServers" in body.get("iceConfig", {})


def test_start_rejects_unknown_transport(fresh_app):
    app, _ = fresh_app
    r = TestClient(app).post("/start", json={"transport": "daily"})
    assert r.status_code == 400


def test_offer_route_validates_body(fresh_app):
    app, _ = fresh_app
    # missing sdp/type -> pydantic 422, proving the browser-facing route exists
    r = TestClient(app).post("/api/offer", json={})
    assert r.status_code == 422


def test_client_page_served(fresh_app):
    app, _ = fresh_app
    r = TestClient(app).get("/voice/client/")
    assert r.status_code == 200
    assert "html" in r.headers.get("content-type", "")


def test_system_instruction_shape():
    text = webrtc_v2.system_instruction()
    assert "EVO" in text
    for name in webrtc_v2.DEFAULT_TOOLS:
        assert name in text


def test_tool_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("EVO_VOICE_TOOLS", "tool_help")
    res = webrtc_v2._call_tool_checked("web_search", {"query": "x"})
    assert res["ok"] is False and "may not" in res["speech"]
    res = webrtc_v2._call_tool_checked("tool_help", {"name": "tool_help"})
    assert res["ok"] is True


class _FakeThread:
    def __init__(self, target=None, args=(), **kwargs):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


class _Shim:
    Thread = _FakeThread


def test_turn_recorder_flushes_to_memory_and_bus(monkeypatch):
    seen = {}
    calls = []

    class _ThreadShim:
        @staticmethod
        def Thread(target=None, args=(), **kw):
            class T:
                def start(self):
                    target(*args)
            return T()

    monkeypatch.setattr(webrtc_v2.threading, "Thread", _ThreadShim.Thread)

    import mk2.memory as memory

    monkeypatch.setattr(memory, "record_turn",
                        lambda u, r, s: calls.append((u, r, s)))

    rec = webrtc_v2.TurnRecorder()
    events = []
    monkeypatch.setattr(rec, "_publish", lambda u, r: events.append((u, r)))

    rec.note_assistant("Working on it.")
    rec.note_user("open youtube")           # pending assistant flushes here
    assert calls == [("", "Working on it.", "voice")]
    rec.flush()
    assert calls[-1] == ("open youtube", "", "voice")
    assert len(events) == 2                 # both pairs hit the bus


def test_health_exposes_voice_v2(tmp_path, monkeypatch):
    from mk2 import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "h.db")
    db.migrate()
    from mk2.server import app

    body = TestClient(app).get("/api/health").json()
    v2 = body.get("voice_v2", {})
    assert v2.get("enabled") in (True, False)
    if v2.get("enabled"):
        assert v2["client_url"] == "/voice/client/"
