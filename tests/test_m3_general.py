import pytest
from fastapi.testclient import TestClient

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2.tools import system_tools as st

    st._apps_cache["items"] = ["Valorant", "Spotify", "Notepad++"]  # hermetic
    st._apps_cache["ts"] = __import__("time").time()


class TestGeneralOpen:
    def test_typo_wikepedia_resolves_to_wikipedia(self, monkeypatch):
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
        r = tools.call("open_app", {"target": "wikepedia"})
        assert r["ok"] is True
        assert any("wikipedia.org" in u for u in opened)

    def test_unknown_gibberish_searches_instead_of_crashing(self, monkeypatch):
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
        r = tools.call("open_app", {"target": "zzqxv blorptastic"})
        assert r["ok"] is True
        assert opened and "google.com/search" in opened[0]  # EVO_SEARCH_ENGINE default
        assert "searched the web" in r["speech"]

    def test_any_installed_app_opens_via_lnk(self, monkeypatch, tmp_path):
        lnk = tmp_path / "MyCustomTool.lnk"
        lnk.write_bytes(b"\x00")
        seen = []
        monkeypatch.setattr("mk2.tools.system_tools._find_lnk", lambda n: str(lnk))
        monkeypatch.setattr("os.startfile", lambda p: seen.append(p))
        r = tools.call("open_app", {"target": "mycustomtool"})
        assert r["ok"] is True and seen
        assert "MyCustomTool" in r["speech"]

    def test_url_passthrough(self, monkeypatch):
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
        r = tools.call("open_app", {"target": "https://example.com/docs"})
        assert r["ok"] and "example.com" in opened[0]


class TestScreenReadHardening:
    def test_graceful_when_vision_down(self, monkeypatch, tmp_path):
        from mk2 import db as d
        from mk2.llm import LLMUnavailable

        fake_png = tmp_path / "cap.png"
        fake_png.write_bytes(b"\x89PNG fake")

        from mk2 import work_tools as wt

        monkeypatch.setattr(wt, "_capture_png", lambda: fake_png)
        def dead(*a, **k):
            raise LLMUnavailable("vision down")
        monkeypatch.setattr("mk2.llm.chat_vision", dead)
        r = wt.screen_read("what is this?")
        assert r["ok"] is False and "unreachable" in r["speech"].lower()

    def test_capture_produces_file(self, monkeypatch, tmp_path):
        """_capture_png runs real PowerShell; verify a PNG lands on disk."""
        from mk2.work_tools import _capture_png

        p = _capture_png()
        try:
            assert p.exists() and p.stat().st_size > 1000
        finally:
            p.unlink(missing_ok=True)


class TestAgentFailStreak:
    def test_stops_honestly_after_repeated_failures(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()

        seq = iter([
            '{"tool": "shell_run", "args": {"command": "boom"}}',
            '{"tool": "shell_run", "args": {"command": "boom"}}',
            '{"tool": "shell_run", "args": {"command": "boom"}}',
            '{"say": "I could not do that - the command kept failing."}',
        ])
        monkeypatch.setattr("mk2.llm.chat_stream", lambda *a, **k: iter([next(seq)]))
        events = []
        reply = brain_handle("run boom", events)
        assert "could not" in reply.lower() or "couldn't" in reply.lower() or "failed" in reply.lower()
        assert "could not" in reply.lower() or "couldn't" in reply.lower() or "failed" in reply.lower()
        tool_events = [e for e in events if e["type"] == "tool"]
        assert len(tool_events) <= 3  # no endless retry loop

def brain_handle(text, events):
    from mk2 import brain

    return brain.handle_turn(text, on_event=events.append)
