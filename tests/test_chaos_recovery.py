"""Tests for Chaos & Failure Injection Harness (M8.2)."""
import pytest
from mk2 import db, tools
from mk2.chaos import chaos


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_chaos.db")
    db.migrate()
    tools.ensure_loaded()


def test_chaos_kill_network():
    """Verify network drop is safely handled and recovered."""
    import urllib.request
    with chaos.kill_network():
        with pytest.raises(ConnectionRefusedError, match="Chaos: Network interface down"):
            urllib.request.urlopen("https://example.com")


def test_chaos_kill_model():
    """Verify model provider crash is caught."""
    from mk2 import llm
    with chaos.kill_model(503):
        with pytest.raises(RuntimeError, match="Chaos: LLM Provider HTTP 503"):
            llm.chat([{"role": "user", "content": "hi"}])


def test_chaos_kill_tool():
    """Verify crashed tool is caught by tools.call without crashing process."""
    with chaos.kill_tool("web_search"):
        res = tools.call("web_search", {"query": "python"})
        assert res["ok"] is False
        assert "error" in res["speech"].lower() or "crash" in res.get("data", {}).get("raw_error", "").lower()

    # Tool is restored afterwards
    assert tools._REGISTRY["web_search"].fn is not None


def test_chaos_kill_database():
    """Verify database locked exception is raised during chaos and recovered afterwards."""
    with chaos.kill_database():
        with pytest.raises(Exception, match="database is locked"):
            db.connect()

    # Recovered
    with db.connect() as conn:
        assert conn is not None


def test_chaos_kill_subprocess():
    """Verify engineering workspace respects reduced timeout under chaos."""
    from mk2.engineering import EngineeringWorkspace
    with chaos.kill_subprocess(timeout_s=0.2):
        ws = EngineeringWorkspace("chaos_hang_test")
        ws.write_file("test_hang.py", "import time\ndef test_slow(): time.sleep(10)\n")
        passed, out, err = ws.run_tests("test_hang.py")
        assert passed is False
        assert "timed out" in err.lower() or "timed out" in out.lower()
        ws.cleanup()
