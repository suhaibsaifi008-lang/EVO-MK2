"""Phase 7 — The Relationship: persona, style controller, initiative."""
import json

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2 import persona_loader

    monkeypatch.setattr(persona_loader, "PERSONA_PATH",
                        tmp_path / "persona.md")
    tools.ensure_loaded()
    from mk2 import style_controller as sc

    # hermetic by default: no real model calls during context building
    monkeypatch.setattr(sc, "classify", lambda t: {"tone": "neutral"})
    with sc._lock:
        sc._state["last_tone"] = None
        sc._cache.clear()


# ------------------------------------------------------------------ persona

class TestPersona:
    def test_default_created_and_injected(self):
        from mk2 import brain, persona_loader

        text = persona_loader.read_raw()
        assert "EVO" in text and "No \"As an AI\" disclaimers" in text
        msgs = brain.memory.build_context_messages("hello there")
        assert "chief of staff" in str(msgs) or "trusted" in str(msgs)

    def test_set_persona_applies_immediately(self, tmp_path):
        from mk2 import brain, persona_loader

        r = tools.call("set_persona", {
            "content": "# EVO\n## Voice\nYou are PICARD. Speak in naval "
                       "metaphors always.\n## Hard rules\nBe brief."})
        assert r["ok"] is True
        msgs = brain.memory.build_context_messages("status report")
        blob = str(msgs)
        assert "PICARD" in blob and "naval" in blob

    def test_summary_tool(self):
        r = tools.call("get_persona_summary", {})
        assert r["ok"] is True and "sections" in r["data"]

    def test_thin_reject(self):
        assert tools.call("set_persona", {"content": "be nice"})["ok"] is False


# ------------------------------------------------------------ style control

class TestStyleController:
    def _set_tone(self, tone):
        from mk2 import style_controller as sc

        monkeypatch_tone(sc, tone)

    def test_angry_produces_calm_directive(self, monkeypatch):
        from mk2 import brain, style_controller as sc

        monkeypatch.setattr(sc, "classify",
                            lambda t: {"tone": "angry"})
        msgs = brain.memory.build_context_messages("THIS IS BROKEN AGAIN")
        blob = str(msgs).lower()
        assert "shorter" in blob and "no jokes" in blob

    def test_terse_mirrors_brevity(self, monkeypatch):
        from mk2 import brain, style_controller as sc

        monkeypatch.setattr(sc, "classify", lambda t: {"tone": "terse"})
        blob = str(brain.memory.build_context_messages("k"))
        assert "minimal words" in blob.lower()

    def test_classify_uses_fast_model_and_parses(self, monkeypatch):
        from mk2 import style_controller as sc

        monkeypatch.setattr(sc, "classify", sc.real_classify)  # the real one
        monkeypatch.setattr("mk2.llm.chat",
                            lambda *a, **k: '{"tone": "joking"}')
        with sc._lock:
            sc._cache.clear()
        assert sc.classify("why did the chicken cross the road")["tone"] == \
            "joking"

    def test_classify_failure_falls_back_neutral(self, monkeypatch):
        from mk2 import style_controller as sc

        def dead(*a, **k):
            raise RuntimeError("down")
        monkeypatch.setattr("mk2.llm.chat", dead)
        assert sc.classify("anything at all really")["tone"] == "neutral"

    def test_feedback_loop_stores_preference(self, monkeypatch):
        from mk2 import style_controller as sc

        with sc._lock:
            sc._state["last_tone"] = "terse"
        assert sc.note_feedback("perfect, thanks") is True
        facts = {f["key"]: f["value"] for f in db.all_facts(40)}
        assert facts.get("feedback:terse:positive") == "1"
        # directive mentions kept register after 2 positives
        sc.note_feedback("great")
        d = sc.directive("k")
        monkeypatch.setattr(sc, "classify", lambda t: {"tone": "terse"})
        d = sc.directive("k")
        assert "keep this register" in d.lower()


class TestFeedbackNegative:
    def test_negative_feedback_recorded(self):
        from mk2 import style_controller as sc

        with sc._lock:
            sc._state["last_tone"] = "neutral"
        sc.note_feedback("wrong, that is not what i asked")
        facts = {f["key"]: f["value"] for f in db.all_facts(40)}
        assert facts.get("feedback:neutral:negative") == "1"


# ---------------------------------------------------------------- initiative

class TestInitiative:
    def test_quiet_hours_silence(self, monkeypatch):
        from mk2 import initiative_engine as ie

        monkeypatch.setenv("EVO_QUIET_START", "23")
        monkeypatch.setenv("EVO_QUIET_END", "8")

        class FakeNow:
            hour = 2          # deep night

            def strftime(self, fmt):
                return "20260101"

        monkeypatch.setattr(ie, "datetime",
                            type("DT", (), {"now": staticmethod(
                                lambda: FakeNow())}))
        sent = []
        assert ie.maybe_initiate(lambda t, p=None:
                                 sent.append(p)) is False
        assert sent == []

    def test_max_per_day_enforced(self, monkeypatch, tmp_path):
        from mk2 import initiative_engine as ie

        monkeypatch.setenv("EVO_INITIATIVE_MAX", "1")
        monkeypatch.setattr(ie, "_quiet_hours", lambda now: False)
        monkeypatch.setattr(ie, "_user_recently_active", lambda: False)
        monkeypatch.setattr(ie, "gather_candidates",
                            lambda: ["something interesting"])
        monkeypatch.setattr(ie, "compose", lambda c: c)
        with ie._lock:
            ie._state.update({"day": "", "count": 0, "last_ts": 0.0})
        sent = []
        assert ie.maybe_initiate(lambda t, p=None: sent.append(p)) is True
        assert ie.maybe_initiate(lambda t, p=None: sent.append(p)) is False
        assert len(sent) == 1

    def test_stays_silent_after_recent_conversation(self, monkeypatch):
        from mk2 import initiative_engine as ie

        monkeypatch.setattr(ie, "_quiet_hours", lambda now: False)
        rows = [{"ts": __import__("time").time() - 60,
                 "role": "user", "content": "hi"}]
        monkeypatch.setattr(db, "recent_messages", lambda n=1: rows)
        monkeypatch.setenv("EVO_INITIATIVE_MAX", "3")
        with ie._lock:
            ie._state.update({"day": "", "count": 0, "last_ts": 0.0})
        sent = []
        assert ie.maybe_initiate(lambda t, p=None: sent.append(p)) is False
        assert sent == []

    def test_no_candidates_no_message(self, monkeypatch):
        from mk2 import initiative_engine as ie

        monkeypatch.setattr(ie, "_quiet_hours", lambda now: False)
        monkeypatch.setattr(ie, "_user_recently_active", lambda: False)
        monkeypatch.setattr(ie, "gather_candidates", lambda: [])
        monkeypatch.setenv("EVO_INITIATIVE_MAX", "3")
        with ie._lock:
            ie._state.update({"day": "", "count": 0, "last_ts": 0.0})
        sent = []
        ie.maybe_initiate(lambda t, p=None: sent.append(p))
        assert sent == []

    def test_initiative_now_tool(self, monkeypatch):
        from mk2 import initiative_engine as ie

        monkeypatch.setattr(ie, "gather_candidates",
                            lambda: ["battery at 18%"])
        monkeypatch.setattr(ie, "compose", lambda c: "Battery is low, plug in.")
        r = tools.call("initiative_now", {})
        assert r["ok"] is True and "plug" in r["speech"].lower()
