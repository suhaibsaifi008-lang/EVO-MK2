"""Latency routing: measured TTFT stickiness. Voice + speech rules."""
import json
import threading

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    tools.ensure_loaded()


class TestMeasuredRouting:
    def _provs(self):
        return [
            {"name": "freellmapi", "kind": "openai", "base": "http://f.test/v1",
             "key": "k", "default_model": "x", "timeout_bias": 0},
        ]

    def test_untried_models_keep_rank_order(self, monkeypatch):
        from mk2 import llm

        monkeypatch.setattr(llm, "_providers", self._provs)
        with llm._cd_lock:
            llm._ttft.clear()
        att = [m for p, m in llm._attempts("primary")]
        assert att == llm.PRIMARY_LADDER[:len(att)]

    def test_fast_measured_route_bubbles_up(self, monkeypatch):
        from mk2 import llm

        monkeypatch.setattr(llm, "_providers", self._provs)
        with llm._cd_lock:
            llm._ttft.clear()
        # rank-1 model measured SLOW (9s), rank-3 measured FAST (0.8s)
        llm._record_ttft("freellmapi", llm.PRIMARY_LADDER[0], 9.0)
        llm._record_ttft("freellmapi", llm.PRIMARY_LADDER[2], 0.8)
        att = [m for p, m in llm._attempts("primary")]
        assert att[0] == llm.PRIMARY_LADDER[2]      # fastest tried first
        assert att.index(llm.PRIMARY_LADDER[0]) > 2  # slow one demoted

    def test_override_pins_despite_speed(self, monkeypatch):
        from mk2 import llm

        monkeypatch.setattr(llm, "_providers", self._provs)
        llm._record_ttft("freellmapi", "inkling", 0.5)
        att = [m for p, m in llm._attempts("primary", model_override="gpt-oss-120b")]
        assert att == ["gpt-oss-120b"]

    def test_stream_records_ttft(self, monkeypatch):
        from mk2 import llm as L

        monkeypatch.setattr(L, "_providers", self._provs)
        with L._cd_lock:
            L._ttft.clear()

        def fake_raw(req_body_model):
            def raw_stream():
                yield "hel"
                yield "lo"
            return raw_stream

        class FakeReq:
            def __init__(self, *a, **k):
                pass
        monkeypatch.setattr(L.urllib.request, "Request",
                            lambda *a, **k: FakeReq())

        def fake_urlopen(req, timeout=30):
            class Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def __iter__(self):
                    yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
                    yield b"data: [DONE]\n\n"
            return Resp()
        monkeypatch.setattr(L.urllib.request, "urlopen", fake_urlopen)

        out = "".join(L.chat_stream([{"role": "user", "content": "hi"}]))
        assert out == "hi"
        assert any(v >= 0 for v in L._ttft.values())
        assert "freellmapi:" in json.dumps(L.diagnostics()["measured_ttft_s"])


class TestSpeechRules:
    def test_persona_has_natural_speech_law(self, tmp_path, monkeypatch):
        from mk2 import persona_loader

        text = persona_loader.read_raw()
        assert "Speak like a person talks" in text
        assert "announcing a list without listing" in text

    def test_v1_default_upgrades_to_natural(self, tmp_path, monkeypatch):
        from mk2 import persona_loader as pl

        old = pl.read_raw().replace(
            "Speak like a person talks\n  - contractions, varied sentence "
            "length, plain\n  words. If it would sound weird said aloud, "
            "rewrite it.\n  - If you mention", "OLD TEXT - if you mention") \
            if False else None
        p = tmp_path / "persona.md"
        p.write_text("# EVO - Persona\n## Voice\n- never reciting capability "
                     "lists anywhere.\n", encoding="utf-8")
        monkeypatch.setattr(pl, "PERSONA_PATH", p)
        pl.ensure_persona()
        assert "Speak like a person talks" in pl.read_raw()


class TestVoiceDefaults:
    def test_default_is_most_natural(self):
        from mk2.voice import tts

        assert tts._edge_voice() == "en-US-AndrewMultilingualNeural"

    def test_curated_list_leads_with_multilingual(self):
        from mk2.voice import tts

        assert tts.CURATED_VOICES[0].startswith("en-US-Andrew")


class TestRace:
    def test_faster_route_wins_race(self, monkeypatch, tmp_path):
        from mk2 import llm as L

        provs = [
            {"name": "freellmapi", "kind": "openai", "base": "http://f.test/v1",
             "key": "k", "default_model": "x", "timeout_bias": 0},
            {"name": "gemini", "kind": "gemini", "base": "", "key": "k",
             "default_model": "gm", "timeout_bias": 0},
        ]
        monkeypatch.setattr(L, "_providers", lambda: provs)
        monkeypatch.setenv("EVO_RACE", "1")
        with L._cd_lock:
            L._ttft.clear()
        import time as _t

        def fake_urlopen(req, timeout=30):
            body = req.data.decode()
            if "gpt-oss-120b" in body:          # rank-1 route is SLOW
                _t.sleep(0.9)

                class S1:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def __iter__(self):
                        return self
                    first = True

                    def __next__(self):
                        if self.first:
                            self.first = False
                            return b'data: {"choices":[{"delta":{"content":"slow"}}]}\n\n'
                        raise StopIteration
                s1 = S1()
                s1.first = True
                return s1
            else:                                # other ladder route FAST
                class S2:
                    first = True

                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def __iter__(self):
                        return self

                    def __next__(self):
                        if self.first:
                            self.first = False
                            return b'data: {"choices":[{"delta":{"content":"fast win"}}]}\n\n'
                        raise StopIteration
                return S2()
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        out = "".join(L.chat_stream([{"role": "user", "content": "hi"}]))
        assert "fast win" in out and "slow" not in out

    def test_reminder_is_last_message(self):
        from mk2 import memory

        msgs = memory.build_context_messages(
            "a somewhat longer question so the semantic path stays quiet")
        assert msgs[-1]["role"] == "system"
        assert "TRUTH LAW" in msgs[-1]["content"]
        assert "REMINDER" in msgs[-1]["content"]

    def test_context_slimmed_for_short_queries(self):
        from mk2 import brain, memory

        msgs = memory.build_context_messages("hi")
        sysmsg = msgs[0]["content"]
        assert len(sysmsg) < 2500      # no more prompt bloat on tiny inputs


class TestRaceStall:
    def test_winner_stalling_mid_stream_terminates(self, monkeypatch, tmp_path):
        """Regression: race winner that stalls after 1 token must not hang."""
        from mk2 import llm as L
        import time as _t

        provs = [
            {"name": "freellmapi", "kind": "openai", "base": "http://f.test/v1",
             "key": "k", "default_model": "x", "timeout_bias": 0},
            {"name": "gemini", "kind": "gemini", "base": "", "key": "k",
             "default_model": "gm", "timeout_bias": 0},
        ]
        monkeypatch.setattr(L, "_providers", lambda: provs)
        monkeypatch.setenv("EVO_RACE", "1")
        with L._cd_lock:
            L._ttft.clear()

        def fake_urlopen(req, timeout=30):
            body = req.data.decode()
            if L.PRIMARY_LADDER[0] in body or "gpt-oss-120b" in body:
                class S1:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False
                    first = True

                    def __iter__(self):
                        return self

                    def __next__(self):
                        if self.first:
                            self.first = False
                            payload = b'data: {"choices":[{"delta":{"content":"I'
                            payload += b"'"
                            payload += b'm"}}]}\n\n'
                            return payload
                        raise ConnectionError("provider choked mid-stream")
                return S1()
            else:
                _t.sleep(30)   # the loser: would outlast everything

                class S2:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def __iter__(self):
                        return self

                    def __next__(self):
                        raise StopIteration
                return S2()
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        result = {}

        def run():
            try:
                result["out"] = "".join(
                    L.chat_stream([{"role": "user", "content": "hi"}]))
                result["finished"] = True
            except L.LLMStreamStalled as exc:
                result["stalled"] = True
                result["partial"] = exc.partial
                result["finished"] = True
            except Exception as exc:
                result["error"] = str(exc)[:120]
                result["finished"] = True
        th = threading.Thread(target=run, daemon=True)
        th.start()
        th.join(timeout=35)          # GAP(10s)+margin; old code hung forever
        assert th.is_alive() is False, "chat_stream hung on stalled winner"
        assert result.get("finished") is True
        assert result.get("stalled") is True
        assert "I" in result.get("partial", "")


class TestStallRecovery:
    def test_brain_resets_and_retries_after_stall(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2 import brain
        import mk2.llm as L

        streams = []

        def stream1(*a, **k):
            yield "Hey"
            raise L.LLMStreamStalled(partial="Hey")

        def stream2(*a, **k):
            yield "Full fresh answer."

        def fake_chat_stream(*a, **k):
            streams.append(1)
            if len(streams) == 1:
                return stream1()
            return stream2()
        monkeypatch.setattr("mk2.llm.chat_stream", fake_chat_stream)
        events = []
        reply = brain.handle_turn("hey evo", on_event=events.append)
        assert reply == "Full fresh answer."
        kinds = [e["type"] for e in events]
        assert "reset" in kinds and "delta" in kinds

    def test_double_stall_falls_back_to_oneshot(self, monkeypatch, tmp_path):
        """Two stalled streams -> non-streaming one-shot completes the turn."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2 import brain
        import mk2.llm as L

        n = {"streams": 0}

        def fake_chat_stream(*a, **k):
            n["streams"] += 1

            def gen():
                yield "It is"
                raise L.LLMStreamStalled(partial="It is")
            return gen()
        monkeypatch.setattr("mk2.llm.chat_stream", fake_chat_stream)
        monkeypatch.setattr("mk2.llm.chat",
                            lambda msgs, **k: "ONE-SHOT COMPLETE ANSWER")
        events = []
        reply = brain.handle_turn("explain quantum tunneling on mars",
                                  on_event=events.append)
        assert "ONE-SHOT" in reply
        resets = [e for e in events if e["type"] == "reset"]
        assert len(resets) >= 2      # reset before each retry

    def test_stall_penalty_is_long(self):
        from mk2 import llm as L
        import time as _t

        with L._cd_lock:
            L._cooldowns.clear()
        L.penalize_stall("freellmapi", "gpt-oss-120b")
        with L._cd_lock:
            remain = L._cooldowns["freellmapi:gpt-oss-120b"] - _t.time()
        assert remain > 800          # ~15 min bench, not a 60s slap


class TestPromptDiet:
    def test_relevant_tools_get_full_specs(self):
        from mk2 import brain, tools

        m = brain.compact_manifest("summarize a youtube video for me",
                                   tools.manifest())
        assert "youtube_summarize:" in m          # relevant -> detailed line
        assert "youtube_summarize" in m

    def test_irrelevant_tools_ship_as_names_only(self):
        from mk2 import brain, tools

        m = brain.compact_manifest(
            "a question about ancient roman aqueducts", tools.manifest())
        assert "Other tools (use tool_help" in m
        # non-core, irrelevant tool specs must be compacted away
        assert "calendar_today: Today's calendar events" not in m
        assert "calendar_today" in m               # name still listed

    def test_tool_help_registered(self):
        names = {t["name"] for t in tools.manifest()}
        assert "tool_help" in names
        r = tools.call("tool_help", {"name": "web_search"})
        assert r["ok"] is True and "args_schema" in r["data"]


class TestGeminiStt:
    def _wav16k(self):
        import io
        import struct as st

        n = 8000
        pcm = b"\x00" * (n * 2)
        buf = io.BytesIO()
        buf.write(b"RIFF" + st.pack("<I", 36 + len(pcm)) + b"WAVEfmt ")
        buf.write(st.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16))
        buf.write(b"data" + st.pack("<I", len(pcm)) + pcm)
        return buf.getvalue()

    def test_auto_prefers_gemini(self, monkeypatch):
        from mk2.voice import stt as stt

        used = {}

        def fake_gemini(pcm):
            used["g"] = True
            return "open youtube"
        monkeypatch.setattr(stt, "_transcribe_gemini", fake_gemini)
        monkeypatch.setattr(stt, "_transcribe_whisper",
                            lambda pcm: (_ for _ in ()).throw(AssertionError("whisper")))
        monkeypatch.setattr(stt, "_transcribe_vosk",
                            lambda pcm: (_ for _ in ()).throw(AssertionError("vosk")))
        assert stt.transcribe_wav(self._wav16k()) == "open youtube"
        assert used.get("g") is True

    def test_gemini_error_falls_to_whisper_then_vosk(self, monkeypatch):
        from mk2.voice import stt as stt

        order = []
        monkeypatch.setattr(stt, "_transcribe_gemini",
                            lambda pcm: (_ for _ in ()).throw(RuntimeError("503 up down")))
        monkeypatch.setattr(stt, "_transcribe_whisper",
                            lambda pcm: order.append("w") or "whisper text")
        monkeypatch.setattr(stt, "_transcribe_vosk",
                            lambda pcm: (_ for _ in ()).throw(AssertionError("vosk used")))
        out = stt.transcribe_wav(self._wav16k())
        assert out == "whisper text" and order == ["w"]

    def test_forced_vosk_skips_everything(self, monkeypatch):
        from mk2.voice import stt as stt

        monkeypatch.setenv("EVO_STT_ENGINE", "vosk")
        monkeypatch.setattr(stt, "_transcribe_gemini",
                            lambda pcm: (_ for _ in ()).throw(AssertionError("gemini")))
        monkeypatch.setattr(stt, "_transcribe_whisper",
                            lambda pcm: (_ for _ in ()).throw(AssertionError("whisper")))
        monkeypatch.setattr(stt, "_transcribe_vosk", lambda pcm: "v-text")
        assert stt.transcribe_wav(self._wav16k()) == "v-text"

    def test_empty_gemini_text_falls_through(self, monkeypatch):
        from mk2.voice import stt as stt

        monkeypatch.setattr(stt, "_transcribe_gemini", lambda pcm: "")
        monkeypatch.setattr(stt, "_transcribe_whisper", lambda pcm: "w")
        assert stt.transcribe_wav(self._wav16k()) == "w"
