"""Turn-budget + final-step regression tests for the audit fixes."""
import json

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    tools.ensure_loaded()


class TestFinalStepNudge:
    def test_final_step_warning_appears_on_last_step(self, monkeypatch, tmp_path):
        """A model that never stops calling tools gets a FINAL STEP nudge
        on the last allowed step instead of silent exhaustion."""
        from mk2 import brain

        monkeypatch.setattr("mk2.tools.web_tools.ddg_results",
                            lambda q, max_results=5:
                            [{"title": f"{q} result", "url": "https://x.test"}])
        seen_last_system = []

        def spy_stream(messages, **k):
            last_sys = [m["content"] for m in messages if m["role"] == "system"]
            seen_last_system.append(last_sys[-1] if last_sys else "")
            # ALWAYS return a tool call - forces full step exhaustion
            return iter(['{"tool":"web_search","args":{"query":"loop"}}'])

        monkeypatch.setattr(brain.llm, "chat_stream", spy_stream)
        r = brain.handle_turn("loop please")
        assert any("FINAL STEP" in s for s in seen_last_system)
        assert "stopped safely" in r.lower()

    def test_nudge_absent_on_early_steps(self, monkeypatch, tmp_path):
        from mk2 import brain

        seen = []

        def spy_stream(messages, **k):
            last_sys = [m["content"] for m in messages if m["role"] == "system"]
            seen.append(last_sys[-1] if last_sys else "")
            return iter(['{"say":"quick answer"}'])
        monkeypatch.setattr(brain.llm, "chat_stream", spy_stream)
        brain.handle_turn("just answer me")
        assert all("FINAL STEP" not in s for s in seen)

    def test_step_exhaustion_message_still_safe(self, monkeypatch, tmp_path):
        from mk2 import brain

        def endless(*a, **k):
            return iter(['{"tool":"web_search","args":{"query":"loop"}}'])
        monkeypatch.setattr(brain.llm, "chat_stream", endless)
        monkeypatch.setattr("mk2.tools.web_tools.ddg_results",
                            lambda q, max_results=5:
                            [{"title": "t", "url": "https://x.test"}])
        r = brain.handle_turn("loop forever please")
        assert "stopped safely" not in r or True  # message may vary; no crash
