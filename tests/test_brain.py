import json

import pytest

from mk2 import brain, db, tools


class TestSanitize:
    def test_never_leaks_protocol(self):
        dirty = 'TOOL RESULT (x): {"tool":"y"} Chrome is open.'
        clean = brain.sanitize_final(dirty)
        assert "TOOL RESULT" not in clean and '"tool"' not in clean

    def test_say_wrapper_unwrapped(self):
        assert brain.sanitize_final('{"say":"Done."}') == "Done."


class TestFastPath:
    def test_time_is_instant(self):
        t0 = __import__("time").perf_counter()
        r = brain.handle_turn("what time is it")
        ms = (__import__("time").perf_counter() - t0) * 1000
        assert (":" in r or "It is" in r) and ms < 750  # deterministic: no model involved

    def test_empty_message(self):
        assert "catch" in brain.handle_turn("   ").lower()


class TestToolLoop:
    def test_tool_then_final(self, monkeypatch, tmp_path):
        """Searches are QUESTIONS now: agent calls web_search, then
        synthesizes a real answer (never the old title-dump fast-lane)."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2.tools import web_tools

        monkeypatch.setattr(web_tools, "ddg_results",
                            lambda q, max_results=5: [{"title": f"{q} guide", "url": "https://x.test"}])
        seq = iter(['{"tool":"web_search","args":{"query":"evo mk2"}}',
                    '{"say":"The EVO MK2 guide covers it - short answer: yes."}'])
        monkeypatch.setattr("mk2.llm.chat_stream", lambda *a, **k: iter([next(seq)]))
        events = []
        reply = brain.handle_turn("search for evo mk2", on_event=events.append)
        assert "yes" in reply.lower()
        audit = db.recent_audit()
        assert any(a["tool"] == "web_search" for a in audit)

    def test_llm_down_reports_gracefully(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        def dead(*a, **k):
            raise llm_down()
        def llm_down():
            from mk2.llm import LLMUnavailable
            raise LLMUnavailable("all providers down")
        monkeypatch.setattr("mk2.llm.chat_stream", dead)
        reply = brain.handle_turn("tell me a joke")
        assert "unreachable" in reply.lower()

    def test_cancelled_midturn(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        monkeypatch.setattr("mk2.llm.chat_stream", lambda *a, **k: iter(["hello there friend"]))
        with pytest.raises(brain.TurnCancelled):
            brain.handle_turn("hello", cancelled=lambda: True)


class TestMemoryPolicy:
    def test_explicit_remember_stores(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        monkeypatch.setattr("mk2.llm.chat", lambda *a, **k:
            '{"facts":[{"key":"favourite colour","value":"teal"}]}')
        memory_record("remember my favourite colour is teal", "Noted.")
        assert db.search_facts("colour")[0]["value"] == "teal"

    def test_update_overwrites(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        db.remember_fact("city", "Delhi")
        monkeypatch.setattr("mk2.llm.chat", lambda *a, **k:
            '{"facts":[{"key":"city","value":"Mumbai"}]}')
        memory_record("I moved to Mumbai last week", "Nice!")
        assert db.search_facts("city")[0]["value"] == "Mumbai"

    def test_context_includes_facts_and_recent(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        db.remember_fact("laptop", "asus g14")
        db.log_message("user", "hello there")
        msgs = brain.memory.build_context_messages("what about my laptop?")
        blob = str(msgs)
        assert "asus g14" in blob and "hello there" in blob


def memory_record(user_text, reply):
    # drive the real policy path without the rate-limit gate
    import mk2.memory as mem
    mem.record_turn.__wrapped__ if hasattr(mem.record_turn, "__wrapped__") else None
    # force due by resetting counter
    with mem._lock:
        mem._state["turns_since_extract"] = 6
    mem.record_turn(user_text, reply, "console")

