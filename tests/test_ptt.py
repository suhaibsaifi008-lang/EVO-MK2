import json
import queue

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from mk2 import db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2.server import app

    return TestClient(app)


class TestFastLane:
    def test_open_app_never_calls_llm(self, client, monkeypatch):
        calls = []
        monkeypatch.setattr("mk2.llm.chat_stream",
                            lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError("LLM used!")))
        seen = []
        from mk2.tools import system_tools as st
        monkeypatch.setattr(st.os, "startfile", lambda p: seen.append(p))
        r = client.post("/api/chat", json={"text": "open calculator"}).json()
        assert any("calc" in str(p).lower() for p in seen)
        assert "opening" in r["reply"].lower()
        assert calls == []

    def test_search_now_goes_through_brain(self, client, monkeypatch):
        """'search for X' is a question: LLM plans the tool call (no regex lane)."""
        from mk2.tools import web_tools
        monkeypatch.setattr(web_tools, "ddg_results",
                            lambda q, max_results=5: [{"title": f"{q} guide", "url": "https://x.test"}])
        seq = iter(['{"tool":"web_search","args":{"query":"evo mk2"}}',
                    '{"say":"Here is what I found about evo mk2."}'])
        monkeypatch.setattr("mk2.llm.chat_stream", lambda *a, **k: iter([next(seq)]))
        r = client.post("/api/chat", json={"text": "search for evo mk2"}).json()
        assert "found" in r["reply"].lower()


class TestPTT:
    def test_transcribe_rejects_tiny_audio(self, client):
        r = client.post("/api/transcribe", content=b"x" * 50)
        assert r.status_code in (400, 422)  # tiny audio rejected

    def test_tts_returns_audio(self, client, monkeypatch, tmp_path):
        from mk2.voice import tts_best as t
        fake = tmp_path / "a.wav"
        fake.write_bytes(b"RIFFxxxxWAVEfmt " + b"\x00" * 64)
        monkeypatch.setattr(t, "synthesize_best", lambda text: fake)
        r = client.get("/api/tts", params={"text": "hello"})
        assert r.status_code == 200
        assert "audio/wav" in r.headers["content-type"]
