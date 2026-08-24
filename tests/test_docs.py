"""Document creation tools: real .docx delivery, markdown -> Word."""
import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2 import fs_tools

    monkeypatch.setattr(fs_tools, "ALLOWED_ROOTS", [tmp_path])
    tools.ensure_loaded()


MD = """# Staro Indian Group
## Company Overview
Staro Indian Group is a conglomerate with **three divisions**.
- Manufacturing
- Retail
- Logistics
1. First finding
2. Second finding
"""


class TestDocsCreate:
    def test_creates_real_docx(self, tmp_path):
        target = "report.docx"
        r = tools.call("docs_create", {"path": target, "content": MD})
        assert r["ok"] is True, r["speech"]
        path = r["data"]["path"]
        from pathlib import Path

        p = Path(path)
        assert p.exists() and p.stat().st_size > 1000  # real docx zip

        from mk2.tools.docs_tools import read_back

        text = read_back(p)
        assert "Staro Indian Group" in text
        assert "three divisions" in text      # bold run extracted as text
        assert "Manufacturing" in text        # bullet
        assert "Second finding" in text       # numbered list

    def test_append_extends_document(self, tmp_path):
        tools.call("docs_create", {"path": "rep.docx", "content": MD})
        r = tools.call("docs_append",
                       {"path": "rep.docx",
                        "content": "## Financials\nRevenue grew 40%."})
        assert r["ok"] is True
        from mk2.tools.docs_tools import read_back

        assert "Revenue grew" in read_back(tmp_path / "rep.docx")

    def test_fallback_to_word_html_without_docx_lib(self, tmp_path, monkeypatch):
        from mk2.tools import docs_tools as dt

        def no_docx(md, path):
            raise ImportError("No module named 'docx'")
        monkeypatch.setattr(dt, "_render_docx", no_docx)
        r = tools.call("docs_create", {"path": "fallback.docx", "content": MD})
        assert r["ok"] is True
        from pathlib import Path

        p = Path(r["data"]["path"])
        assert p.suffix == ".doc"
        body = p.read_text(encoding="utf-8")
        assert "<b>three divisions</b>" in body and "<h1>" in body

    def test_empty_content_rejected(self):
        r = tools.call("docs_create", {"path": "x.docx", "content": "hi"})
        assert r["ok"] is False


class TestAgentDeliversFiles:
    def test_report_request_ends_in_real_file(self, monkeypatch, tmp_path):
        """The '60-page report' failure mode: agent must DELIVER, not outline."""
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2 import fs_tools

        monkeypatch.setattr(fs_tools, "ALLOWED_ROOTS", [tmp_path])
        seq = iter([
            '{"tool":"web_search","args":{"query":"Staro Indian Group company"}}',
            '{"tool":"docs_create","args":{"path":"staro report.docx","content":"# Staro Indian Group\\n## Overview\\nA diversified group with three divisions and growing retail footprint."}}',
            '{"say":"Your report is ready at Documents."}',
        ])
        from mk2.tools import web_tools

        monkeypatch.setattr(web_tools, "ddg_results",
                            lambda q, max_results=5: [{"title": t, "url": "https://x.test"} for t in ["bg"]])
        monkeypatch.setattr("mk2.llm.chat_stream", lambda *a, **k: iter([next(seq)]))
        events = []
        reply = __import__("mk2.brain", fromlist=["handle_turn"]).handle_turn(
            "make a report on staro indian group", on_event=events.append)
        assert "ready" in reply.lower() or "created" in reply.lower()
        audits = [a["tool"] for a in db.recent_audit(10)]
        assert "docs_create" in audits
