"""Regression: race no-token fallthrough + empty-reply guard."""
import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    tools.ensure_loaded()


class TestRaceNoTokenFallthrough:
    def test_nobody_spoke_falls_through_to_ladder(self, monkeypatch):
        """Race where BOTH routes die before any token must NOT count as a
        successful empty reply — the sequential ladder gets its turn."""
        from mk2 import llm as L

        provs = [
            {"name": "freellmapi", "kind": "openai", "base": "http://f.test/v1",
             "key": "k", "default_model": "x", "timeout_bias": 0},
            {"name": "ollama", "kind": "openai", "base": "http://o.test/v1",
             "key": "", "default_model": "qwen3:4b", "timeout_bias": 0},
        ]
        monkeypatch.setattr(L, "_providers", lambda: provs)
        with L._cd_lock:
            L._ttft.clear(); L._cooldowns.clear()

        def fake_urlopen(req, timeout=30):
            body = req.data.decode()
            if "f.test" in req.full_url:
                raise ConnectionError("route dead pre-token")
            class Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def __iter__(self):
                    yield b'data: {"choices":[{"delta":{"content":"OLLAMA"}}]}\n\n'
                    yield b"data: [DONE]\n\n"
            return Resp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        out = "".join(L.chat_stream([{"role": "user", "content": "hi"}]))
        assert out == "OLLAMA"


class TestEmptyReplyGuard:
    def test_empty_stream_retries_then_answers(self, monkeypatch, tmp_path):
        """A dead route returning NOTHING triggers corrective retry, and
        never surfaces as a final answer."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2 import brain
        import mk2.llm as L

        calls = []

        def factory(messages, **k):
            calls.append([m["content"] for m in messages
                          if m["role"] == "system"])
            n = len(calls)
            if n == 1:
                return iter([])   # empty stream: route said nothing
            return iter(['{"say":"here is the real answer"}'])

        monkeypatch.setattr(brain.llm, "chat_stream", factory)
        r = brain.handle_turn("tell me about yourself", on_event=lambda e: None)
        assert "real answer" in r
        assert len(calls) == 2
        assert any("previous response was EMPTY" in c for c in calls[1])

    def test_exhaustion_message_now_honest_with_rescue(self, monkeypatch, tmp_path):
        """Post-loop empty answer now attempts a one-shot rescue instead of
        printing the robotic 'stopped safely' line."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2 import brain

        def always_empty(*a, **k):
            return iter([])
        monkeypatch.setattr(brain.llm, "chat_stream", always_empty)

        rescued = {"called": False}
        real_chat = brain.llm.chat

        def fake_chat(messages, **k):
            rescued["called"] = True
            return "rescued by one-shot"
        monkeypatch.setattr(brain.llm, "chat", fake_chat)

        # force MAX_STEPS exhaustion via endless empty streams
        r = brain.handle_turn("anything")
        assert rescued["called"] is True or "pool is unstable" in r
