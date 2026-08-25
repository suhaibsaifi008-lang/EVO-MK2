"""Voice v3: /ws/voice transport (streamed text + sentence-pipelined audio)
and Piper-first TTS ordering."""
import asyncio
import io
import json
import wave

import pytest
from fastapi.testclient import TestClient

from mk2 import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "ws.db")
    db.migrate()


def _fake_wav_bytes(ms: int = 50, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x01\x02" * int(sr * ms / 1000))
    return buf.getvalue()


@pytest.fixture()
def fast_brain(monkeypatch):
    """Scripted brain: streams two sentences as deltas, returns final."""
    from mk2 import brain

    def scripted(text, on_event=None, cancelled=None, **kw):
        def emit(t, chunk):
            if on_event:
                on_event({"type": t, "text": chunk})

        emit("thinking", "")
        emit("delta", "Yes sir. ")
        emit("delta", "Systems are online. ")
        emit("done", "Yes sir. Systems are online.")
        return "Yes sir. Systems are online."

    monkeypatch.setattr(brain, "handle_turn", scripted)


@pytest.fixture()
def instant_synth(monkeypatch):
    """Piper-free synthesis for CI: tiny wav written instantly."""
    from mk2.voice import tts_best

    data = _fake_wav_bytes()

    def fake(text, force_engine=None):
        p = tts_best._tts.TTS_DIR / "fake_ws.wav"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    monkeypatch.setattr(tts_best, "synthesize_best", fake)
    return tts_best


def _recv_json(ws, timeout=5.0):
    return json.loads(ws.receive_text())


class TestSplitSentences:
    def test_basic(self):
        from mk2.server import _split_sentences

        sents, rest = _split_sentences("One. Two? Three! tail")
        assert sents == ["One.", "Two?", "Three!"]
        assert rest == "tail"

    def test_decimals_do_not_split(self):
        from mk2.server import _split_sentences

        sents, rest = _split_sentences("It costs 3.5 dollars total.")
        assert sents == ["It costs 3.5 dollars total."]
        assert rest == ""


class TestWsVoice:
    def test_full_turn_streams_text_and_audio_in_order(
            self, fast_brain, instant_synth):
        from mk2.server import app

        client = TestClient(app)
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_json({"type": "say", "text": "status"})
            events = []
            audio_headers = []
            blob_sizes = []
            while True:
                ev = _recv_json(ws)
                if ev["type"] == "audio":
                    audio_headers.append(ev)
                    blob = ws.receive_bytes()
                    blob_sizes.append(len(blob))
                    continue
                events.append(ev)
                if ev["type"] == "final":
                    break
            # deltas streamed
            types = [e["type"] for e in events]
            assert "thinking" in types and "delta" in types
            final = [e for e in events if e["type"] == "final"][0]
            assert "online" in final["reply"]
            # two sentences synthesized, headers before bytes, in order
            assert [h["i"] for h in audio_headers] == [0, 1]
            assert all(b > 44 for b in blob_sizes)      # real payloads
            assert blob_sizes[0] == audio_headers[0]["bytes"]
            # final arrives AFTER both audio pushes (worker joined first)
            assert len(blob_sizes) == 2

    def test_voice_false_suppresses_audio(self, fast_brain, instant_synth):
        from mk2.server import app

        client = TestClient(app)
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_json({"type": "say", "text": "status", "voice": False})
            saw_audio = False
            while True:
                line = ws.receive()
                if "text" not in line:
                    continue
                ev = json.loads(line["text"])
                if ev["type"] == "audio":
                    saw_audio = True
                if ev["type"] == "final":
                    break
            assert saw_audio is False

    def test_tts_only_roundtrip(self, fast_brain, instant_synth):
        from mk2.server import app

        client = TestClient(app)
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_json({"type": "tts", "text": "Reminder armed."})
            ev = _recv_json(ws)
            assert ev["type"] == "audio" and ev["i"] == 0
            assert len(ws.receive_bytes()) == ev["bytes"]

    def test_busy_rejects_parallel_say(self, monkeypatch):
        import threading
        from mk2 import brain
        from mk2.server import app

        gate = threading.Event()

        def slow(text, on_event=None, cancelled=None, **kw):
            gate.wait(timeout=5)
            return "ok"

        monkeypatch.setattr(brain, "handle_turn", slow)
        client = TestClient(app)
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_json({"type": "say", "text": "one"})
            ws.send_json({"type": "say", "text": "two"})
            ev = _recv_json(ws)
            assert ev["type"] == "error" and ev["text"] == "busy"
            gate.set()


class TestPiperEngineOrder:
    def test_piper_first_when_available(self, tmp_path, monkeypatch):
        from mk2.voice import tts_best, tts as T

        calls = []

        def fake_piper(text):
            calls.append("piper")
            return tmp_path / "p.wav"

        def fail(*a, **k):
            calls.append(("unexpected", a))
            return None

        monkeypatch.setattr(T, "_piper_wav", fake_piper)
        monkeypatch.setattr(T, "_sapi_wav", fail)
        out = tts_best.synthesize_best("hello")
        assert calls == ["piper"]
        assert out == tmp_path / "p.wav"

    def test_sapi_fallback_then_edge_last(self, tmp_path, monkeypatch):
        from mk2.voice import tts_best, tts as T

        calls = []
        monkeypatch.setattr(T, "_piper_wav",
                            lambda t: calls.append("piper") or None)

        def sapi(text):
            calls.append("sapi")
            return None                      # force through to edge

        monkeypatch.setattr(T, "_sapi_wav", sapi)

        def edge(text, stop):
            calls.append("edge")
            return tmp_path / "e.mp3"

        monkeypatch.setattr(T, "_edge_mp3", edge)
        out = tts_best.synthesize_best("hello there")
        assert calls == ["piper", "sapi", "edge"]
        assert out == tmp_path / "e.mp3"

    def test_force_sapi_skips_everything(self, tmp_path, monkeypatch):
        from mk2.voice import tts_best, tts as T

        monkeypatch.setattr(T, "_piper_wav",
                            lambda t: (_ for _ in ()).throw(AssertionError()))
        monkeypatch.setattr(T, "_sapi_wav", lambda t: tmp_path / "s.wav")
        out = tts_best.synthesize_best("hi", force_engine="sapi")
        assert out == tmp_path / "s.wav"


class TestPiperWavReal:
    """Real Piper must be installed + voice downloaded (verified env)."""

    def test_real_synthesis(self):
        pytest.importorskip("piper")
        from mk2.voice import tts as T

        p = T._piper_wav("Testing one two three.")
        assert p is not None and p.exists() and p.stat().st_size > 4000
        with wave.open(str(p), "rb") as w:
            dur = w.getnframes() / w.getframerate()
        assert dur > 0.5


class TestFastLadderRouting:
    """voice=True must drive role='fast'; typed turns keep 'primary'."""

    def _capture_role(self, monkeypatch, **kw):
        from mk2 import brain

        seen = {}

        def fake_stream(messages, temperature=0.4, role="primary", **k):
            seen["role"] = role
            yield "Done."

        monkeypatch.setattr(brain.llm, "chat_stream", fake_stream)
        brain.handle_turn("say something clever", **kw)
        return seen["role"]

    def test_voice_turn_uses_fast(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "r1.db")
        db.migrate()
        assert self._capture_role(monkeypatch, voice=True) == "fast"

    def test_typed_turn_keeps_primary(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "r2.db")
        db.migrate()
        assert self._capture_role(monkeypatch) == "primary"

    def test_ws_voice_passes_voice_true(self, tmp_path, monkeypatch):
        import json as _json
        from mk2 import brain
        from mk2.server import app

        monkeypatch.setattr(db, "DB_PATH", tmp_path / "r3.db")
        db.migrate()
        got = {}

        def scripted(text, on_event=None, cancelled=None, **kw):
            got["voice"] = kw.get("voice")
            if on_event:
                on_event({"type": "done", "text": "ok"})
            return "ok"

        monkeypatch.setattr(brain, "handle_turn", scripted)
        client = TestClient(app)
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_json({"type": "say", "text": "hi"})
            while True:
                ev = _json.loads(ws.receive_text())
                if ev["type"] == "final":
                    break
        assert got["voice"] is True
