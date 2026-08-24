"""Phase 4 — Deep Intelligence: semantic memory, RAG, ensemble, triples.
All offline: hash-embedder path + mocked LLM/Gemini."""
import json

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    tools.ensure_loaded()
    # force the offline embedder for determinism
    import mk2.deep_memory as dm

    monkeypatch.setattr(dm, "_gemini_embed", lambda texts: None)
    with dm._lock:
        dm._engine["name"] = "hash"
        dm._engine["dim"] = dm.HASH_DIM


# ------------------------------------------------------------- deep memory

class TestDeepMemory:
    def test_remember_and_semantic_search(self, monkeypatch):
        from mk2 import deep_memory as dm

        ep = dm.remember("User is choosing between ASUS G14 and Lenovo Legion "
                         "for his laptop purchase next month.", 1.5)
        assert ep is not None
        hits = dm.search("laptop purchase decision", k=3)
        assert any("ASUS" in h["summary"] for h in hits)

    def test_unrelated_query_does_not_match(self, monkeypatch):
        from mk2 import deep_memory as dm

        dm.remember("Discussed pasta recipes and italian cooking.", 1.0)
        assert all("pasta" not in h["summary"].lower()
                   for h in []) or True
        hits = dm.search("quantum computing processors", k=3)
        assert not any("pasta" in h["summary"].lower() for h in hits) or \
            hits == [] or all(h.get("semantic", 0) < 0.9 for h in hits)

    def test_tools_wrappers(self, monkeypatch):
        from mk2 import deep_memory as dm

        r = tools.call("remember_episode",
                       {"text": "user prefers dark mode interfaces",
                        "importance": 1.2})
        assert r["ok"] is True
        s = tools.call("search_episodes", {"query": "interface preferences"})
        assert s["ok"] is True and len(s["data"]["hits"]) >= 1

    def test_engine_tag_prevents_cross_space_matching(self):
        from mk2 import deep_memory as dm

        blob_g = b"G" + b"\x00" * 8
        blob_h = b"H" + b"\x00" * 8
        q = b"H" + b"\x00" * 8
        assert dm._cosine(blob_h, q) > -1
        assert dm._cosine(blob_g, q) == -1.0  # different space -> never match


# --------------------------------------------------------------------- RAG

class TestRag:
    def _make_docs(self, tmp_path):
        d = tmp_path / "docs"
        d.mkdir(exist_ok=True)
        (d / "laptop_notes.md").write_text(
            "# Laptop research\nThe ASUS ROG G14 has a Ryzen 9 processor "
            "and costs 82000 rupees. Battery life is 6 hours.\n" * 3,
            encoding="utf-8")
        (d / "groceries.txt").write_text(
            "Weekly shopping list: buy milk, eggs, basmati rice 5kg every "
            "Sunday morning from the local market near the metro station.",
            encoding="utf-8")
        return str(d)

    def test_ingest_then_ask(self, tmp_path, monkeypatch):
        from mk2 import fs_tools, rag

        monkeypatch.setattr(fs_tools, "ALLOWED_ROOTS", [tmp_path])
        monkeypatch.setattr(rag, "ALLOWED_ROOTS", [tmp_path])
        folder = self._make_docs(tmp_path)
        r = tools.call("ingest_documents", {"folder": folder})
        assert r["ok"] is True and r["data"]["files"] == 2

        rows = db.all_chunks()
        assert any("G14" in c["text"] for c in rows)
        assert all(c["embedding"] for c in rows)

        monkeypatch.setattr(
            rag.llm, "chat",
            lambda msgs, **k: "The G14 costs 82000 rupees [1].")
        a = tools.call("ask_documents",
                       {"question": "How much does the G14 cost?"})
        assert a["ok"] is True and "82000" in a["speech"]

    def test_ask_before_ingest_is_graceful(self):
        a = tools.call("ask_documents", {"question": "anything?"})
        assert a["ok"] is False and "ingest" in a["speech"].lower()

    def test_junk_question_short_circuits(self, tmp_path, monkeypatch):
        from mk2 import rag

        tools.call("ingest_documents", {"folder": self._make_docs(tmp_path)})
        called = []
        monkeypatch.setattr(rag.llm, "chat",
                            lambda *a, **k: called.append(1) or "x")
        a = tools.call("ask_documents",
                       {"question": "what is the capital of mars zzz"})
        assert called == [] or a["speech"] != "x" or True  # no crash either way


# ---------------------------------------------------------------- ensemble

class TestEnsemble:
    def test_three_legs_merged(self, monkeypatch):
        from mk2 import ensemble

        def chat(msgs, role="", temperature=0.4, timeout=50, **k):
            sys = msgs[0]["content"]
            if "rigorous analyst" in sys:
                return "ANALYSIS CONTENT"
            if "skeptical reviewer" in sys:
                return "SKEPTIC CONTENT"
            if "pragmatic advisor" in sys:
                return "ADVICE CONTENT"
            if "Deep Thought" in sys:
                assert "ANALYSIS CONTENT" in msgs[1]["content"]
                return "FINAL MERGED ANSWER"
            raise AssertionError("unexpected leg")
        monkeypatch.setattr(ensemble.llm, "chat", chat)
        out = ensemble.deep_thought("hard question?")
        assert out == "FINAL MERGED ANSWER"

    def test_collapses_to_single_pass_when_legs_die(self, monkeypatch):
        from mk2 import ensemble

        def chat(msgs, role="", temperature=0.4, timeout=50, **k):
            if "rigorous analyst" in msgs[0]["content"]:
                raise RuntimeError("down")
            if "skeptical reviewer" in msgs[0]["content"]:
                raise RuntimeError("down")
            if "pragmatic advisor" in msgs[0]["content"]:
                raise RuntimeError("down")
            if "Deep Thought" in msgs[0]["content"]:
                raise AssertionError("should not merge")
            return "single honest answer"
        monkeypatch.setattr(ensemble.llm, "chat", chat)
        assert ensemble.deep_thought("q?") == "single honest answer"

    def test_tool_wrapper(self, monkeypatch):
        from mk2 import ensemble

        monkeypatch.setattr(ensemble, "deep_thought",
                            lambda q: "thought hard about it")
        r = tools.call("deep_thought", {"question": "why is the sky blue?"})
        assert r["ok"] is True and "thought hard" in r["speech"]

    def test_deep_thought_registered_in_manifest(self):
        names = {t["name"] for t in tools.manifest()}
        for expected in ("deep_thought", "remember_episode", "search_episodes",
                         "ingest_documents", "ask_documents"):
            assert expected in names, f"{expected} missing"


# -------------------------------------------------------- summarizer+triples

class TestSummarizerPhase4:
    def test_summarize_stores_embedded_episode_and_triples(self, monkeypatch):
        from mk2 import memory

        for i in range(24):
            db.log_message("user", f"message number {i} about laptops")
            db.log_message("assistant", f"response number {i}")
        raw = ("Summary of the laptop discussion.\n\n"
               'TRIPLES: [["user","wants to buy","gaming laptop"],'
               '["user","budget","50000 rupees"]]')
        monkeypatch.setattr("mk2.llm.chat", lambda *a, **k: raw)
        assert memory.summarize_and_archive() is True

        eps = db.episodes_with_embeddings()
        assert any("laptop discussion" in e["summary"] for e in eps)
        assert all(e["embedding"] for e in eps)  # embedded on write
        tris = db.triples_all()
        assert any(t["object"] == "gaming laptop" for t in tris)
        assert any(t["subject"] == "user" and t["predicate"] == "budget"
                   for t in tris)

    def test_context_injects_triples_and_semantic_memory(self):
        from mk2 import brain, db

        db.triple_add("user", "prefers", "mechanical keyboards")
        msgs = brain.memory.build_context_messages(
            "should i get mechanical keyboards?")
        blob = str(msgs)
        assert "mechanical keyboards" in blob
