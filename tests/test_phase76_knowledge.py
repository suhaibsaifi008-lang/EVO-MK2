"""Phase 7.6 — truth law, knowledge watcher, web capture, kg growth,
correction memory."""
import json

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2 import persona_loader

    monkeypatch.setattr(persona_loader, "PERSONA_PATH", tmp_path / "p.md")
    tools.ensure_loaded()
    from mk2 import style_controller as sc

    monkeypatch.setattr(sc, "classify", lambda t: {"tone": "neutral"})


class TestTruthLaw:
    def test_default_persona_contains_truth_section(self, tmp_path):
        from mk2 import persona_loader as pl

        text = pl.read_raw()
        assert "never invent facts" in text.lower()

    def test_edited_persona_gets_truth_appended(self, tmp_path):
        """User mandate: persona can never license lying."""
        from mk2 import persona_loader as pl

        pl.ensure_persona()
        p = pl.PERSONA_PATH
        p.write_text("# EVO - Persona\n## Voice\nBe funny. Lying is fine.\n",
                     encoding="utf-8")
        pl.ensure_persona()                      # re-run upgrade check
        assert "never lie or invent facts" in p.read_text(encoding="utf-8").lower()

    def test_truth_law_injected_last(self):
        from mk2 import memory

        msgs = memory.build_context_messages("tell me something true please")
        assert msgs[-1]["content"].startswith("TRUTH LAW")

    def test_brain_context_carries_law(self):
        from mk2 import brain

        msgs = brain.memory.build_context_messages("what is 2+2?")
        blob = str(msgs)
        assert "TRUTH LAW" in blob or "never lie" in blob.lower()


class TestKnowledgeWatcher:
    def test_watch_adds_and_updates(self, tmp_path, monkeypatch):
        from mk2 import rag

        docs = tmp_path / "docs"
        docs.mkdir(exist_ok=True)
        body = ("The EVO project uses sqlite storage for every subsystem "
                "including traces, audit ledger and reminders.")
        (docs / "a.md").write_text(body + "\n" + body, encoding="utf-8")
        monkeypatch.setattr(rag, "_watch_state", lambda: {"dirs": [str(docs)],
                                                          "files": {}})
        monkeypatch.setattr(rag, "_save_watch_state", lambda st: None)
        rag.watch_scan()
        rows = db.all_chunks()
        assert any("sqlite" in c["text"] for c in rows)

    def test_removal_drops_chunks(self, tmp_path, monkeypatch):
        from mk2 import rag

        docs = tmp_path / "docs"
        f = docs / "gone.md"
        docs.mkdir(exist_ok=True)
        f.write_text("temporary knowledge body that is long enough to pass "
                     "the chunking threshold easily.", encoding="utf-8")
        monkeypatch.setattr(rag, "_watch_state", lambda: {"dirs": [str(docs)],
                                                          "files": {}})
        monkeypatch.setattr(rag, "_save_watch_state", lambda st: None)
        rag.watch_scan()
        assert any("temporary" in c["text"] for c in db.all_chunks())
        f.unlink()
        monkeypatch.setattr(rag, "_watch_state",
                            lambda: {"dirs": [str(docs)],
                                     "files": {str(f): "1:1"}})
        rag.watch_scan()
        assert not any("temporary" in c["text"] for c in db.all_chunks())

    def test_tool_registers_watch(self, tmp_path, monkeypatch):
        from mk2 import rag
        from pathlib import Path

        docs = tmp_path / "wdocs"
        docs.mkdir()
        (docs / "n.txt").write_text("watched note content here for ingestion",
                                    encoding="utf-8")
        real_state = {"dirs": [], "files": {}}

        monkeypatch.setattr("mk2.fs_tools._safe", lambda x: Path(x))
        monkeypatch.setattr(rag, "_watch_state", lambda: dict(real_state))
        monkeypatch.setattr(rag, "_save_watch_state",
                            lambda st: real_state.update(st))
        r = tools.call("knowledge_watch", {"folder": str(docs)})
        assert r["ok"] is True, r["speech"]
        assert "wdocs" in r["data"]["dir"]


class TestWebCapture:
    def test_browsed_page_becomes_rag_knowledge(self, tmp_path, monkeypatch):
        from mk2.tools import system_tools as st

        monkeypatch.setattr("mk2.tools.web_tools.ddg_results",
                            lambda q, max_results=5:
                            [{"title": t, "url": f"https://src.test/{i}"}
                             for i, t in enumerate(["src"])])

        def fake_fetch(url, max_chars=3000):
            return ("Staro Indian Group revenue grew forty percent "
                    "in the last financial year according to filings.") * 3
        monkeypatch.setattr("mk2.tools.web_tools.fetch_page_text", fake_fetch)

        r = tools.call("web_search", {"query": "staro indian group"})
        assert r["ok"] is True
        chunks = db.all_chunks()
        assert any("Staro Indian Group" in c["text"]
                   for c in chunks if "src.test" in c["source"])

    def test_kg_grows_from_research(self, monkeypatch, tmp_path):
        from mk2 import research_tools as rt

        monkeypatch.setattr(rt, "_gather_sources",
                            lambda topic, max_sources=4: [
                                {"url": "https://x.test/1",
                                 "text": "laptops are great for gaming"}])
        monkeypatch.setattr("mk2.llm.chat",
                            lambda msgs, **k: "- point one about laptops")
        extracted = []
        monkeypatch.setattr("mk2.rag.kg_extract",
                            lambda text, source="research":
                            extracted.append((text[:30], source)) or 2)
        r = tools.call("deep_research", {"topic": "gaming laptops"})
        assert r["ok"] is True
        assert extracted and extracted[0][1] == "research"


class TestCorrectionMemory:
    def test_negative_with_instruction_creates_rule(self, monkeypatch):
        from mk2 import style_controller as sc

        with sc._lock:
            sc._state["last_tone"] = "neutral"
        user_text = ("Wrong! From now on always give me bullet points "
                     "instead of paragraphs when you research.")
        assert sc.note_feedback(user_text) is True
        rules = [f for f in db.all_facts(40) if f["key"].startswith("rule:")]
        assert any("bullet points" in f["value"] for f in rules)

    def test_standing_corrections_injected(self, monkeypatch):
        from mk2 import memory

        db.remember_fact("rule:1-no-emoji", "never use emoji", source="correction")
        msgs = memory.build_context_messages(
            "a question long enough to trigger the full context pipeline here")
        blob = str(msgs[0]["content"])
        assert "STANDING CORRECTIONS" in blob and "never use emoji" in blob
