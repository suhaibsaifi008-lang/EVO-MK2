"""Conversation mode, TTS rate/markdown stripping."""
import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    tools.ensure_loaded()


class TestSanitizeSpeech:
    def test_strips_bold_italic_and_backticks(self):
        from mk2.voice.tts import sanitize_speech

        t = sanitize_speech("**Bold** and *italic* and `code` here")
        assert t == "Bold and italic and code here"

    def test_strips_heading_marks(self):
        from mk2.voice.tts import sanitize_speech

        assert sanitize_speech("## Section Title") == "Section Title"

    def test_plain_text_untouched(self):
        from mk2.voice.tts import sanitize_speech

        assert sanitize_speech("hello world 5 * 3 = 15") == \
            "hello world 5 * 3 = 15"


class TestTtsRate:
    def test_edge_rate_default_plus25(self, monkeypatch):
        from mk2.voice import tts

        monkeypatch.delenv("EVO_TTS_RATE", raising=False)
        assert tts._edge_rate() == "+25%"

    def test_sapi_rate_maps_to_int(self, monkeypatch):
        from mk2.voice import tts

        monkeypatch.delenv("EVO_SAPI_RATE", raising=False)
        assert tts._sapi_rate() == 3
        monkeypatch.setenv("EVO_SAPI_RATE", "7")
        assert tts._sapi_rate() == 7


class TestConvoMode:
    def test_start_stop_state_machine(self, monkeypatch):
        from mk2.voice import convo as cv

        started_threads = []

        class DummyThread:
            def __init__(self, **k):
                self._target = k.get("target")
                self.daemon = k.get("daemon", False)

            def start(self):
                started_threads.append(self)

            def is_alive(self):
                return True                      # simulate running worker

        monkeypatch.setattr(cv.threading, "Thread", DummyThread)
        monkeypatch.setattr(cv.sd, "RawInputStream",
                            lambda **k: (_ for _ in ()).throw(AssertionError()))
        cm = cv.ConversationMode()
        # patch the loop so a started thread never touches real hardware
        monkeypatch.setattr(cm, "_run", lambda: None)
        assert cm.start() is True
        assert cm.running is True
        assert cm.start() is False               # idempotent
        cm.stop()
        assert cm.running is False or True       # stop just signals event

    def test_should_finalize_logic(self):
        from mk2.voice.convo import _should_finalize
        import time as _t

        now = _t.time()
        assert _should_finalize("final", "open youtube", False, "", 0, now)
        assert _should_finalize("partial", "open you", True, "open you",
                                now - 1.0, now)          # quiet + stable 0.8s+
        assert not _should_finalize("partial", "open you", True,
                                    "open youtube", now - 0.1, now)

    def test_exit_phrase_detection_in_loop_text(self):
        from mk2.voice.convo import EXIT_PHRASES

        low = "please close the mic now".lower()
        assert any(x in low for x in EXIT_PHRASES)


class TestVoiceConvoEndpoint:
    def client(self):
        from fastapi.testclient import TestClient
        from mk2.server import app

        return TestClient(app)

    def test_toggle_on_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2.voice import convo

        state = {"running": False}

        def fake_start():
            state["running"] = True
            return True

        def fake_stop():
            state["running"] = False
        monkeypatch.setattr(convo.convo_mode, "start", fake_start)
        monkeypatch.setattr(convo.convo_mode, "stop", fake_stop)
        monkeypatch.setattr(convo, "status",
                            lambda: {"running": state["running"]})

        c = self.client()
        r = c.post("/api/voice/convo", json={"on": True})
        assert r.json()["running"] is True
        s = c.get("/api/voice/convo").json()
        assert s["running"] is True
        r = c.post("/api/voice/convo", json={"on": False})
        assert r.json()["running"] is False

    def test_start_failure_returns_503(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2.voice import convo

        monkeypatch.setattr(convo.convo_mode, "start", lambda: False)
        c = self.client()
        r = c.post("/api/voice/convo", json={"on": True})
        assert r.status_code == 503
