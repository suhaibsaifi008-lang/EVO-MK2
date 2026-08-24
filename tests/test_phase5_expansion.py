"""Phase 5 — Self-Expansion: hardened forge, workflows, habits, connectors."""
import json

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2 import skills
    import pathlib

    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")
    tools.ensure_loaded()


GOOD_SKILL = (
    "import json, sys\n"
    "args = json.loads(sys.argv[1])\n"
    "print('double=' + str(args.get('n', 0) * 2))\n"
)

BAD_SKILLS = [
    "import subprocess\nprint('pwned')",
    "from socket import socket\nprint('x')",
    "result = eval('1+1')",
    "import os\nos.system('rm -rf /')",
]


class TestForgeHardening:
    def test_working_skill_saves_and_runs(self, tmp_path):
        r = tools.call("skill_save", {"name": "doubler",
                                      "description": "doubles a number",
                                      "code": GOOD_SKILL})
        assert r["ok"] is True, r["speech"]
        out = tools.call("skill_doubler", {"n": 21})
        assert out["ok"] is True and "42" in out["speech"]

    def test_failing_code_rejected_not_registered(self):
        broken = "def oops(:\n  pass"
        r = tools.call("skill_save", {"name": "broken", "description": "b",
                                      "code": broken})
        assert r["ok"] is False and "Rejected" in r["speech"]
        assert "skill_broken" not in {t["name"] for t in tools.manifest()}

    @pytest.mark.parametrize("code", BAD_SKILLS)
    def test_dangerous_code_never_registers(self, code):
        r = tools.call("skill_save", {"name": "evil", "description": "e",
                                      "code": code})
        assert r["ok"] is False and "banned" in r["speech"].lower()
        assert "skill_evil" not in {t["name"] for t in tools.manifest()}


class TestWorkflows:
    YAML3 = """
name: triple_check
description: three sequential steps
steps:
  - tool: vault_write
    args: {topic: wf-test, content: step one}
  - tool: reminder_list
  - tool: workflow_list
"""

    def test_create_run_three_steps_sequentially(self, tmp_path, monkeypatch):
        from mk2 import workflows

        ok, name, msg = workflows.create(self.YAML3)
        assert ok, msg

        calls = []

        def spy(name_, args=None):
            calls.append(name_)
            return {"ok": True, "speech": f"{name_} done", "data": {}}
        monkeypatch.setattr("mk2.tools.call", spy)
        result = workflows.run("triple_check")
        assert result["ok"] is True
        assert len(result["results"]) == 3          # all three executed
        assert calls == ["vault_write", "reminder_list", "workflow_list"]
        # audit trail written for the run itself
        assert any(a["tool"] == "workflow_run" for a in db.recent_audit(5))

    def test_failure_aborts_unless_continue(self, tmp_path, monkeypatch):
        from mk2 import workflows

        workflows.create("""
name: aborting
steps:
  - tool: no_such_tool_xyz
  - tool: reminder_list
""")
        result = workflows.run("aborting")
        assert result["ok"] is False and len(result["results"]) == 1

        workflows.create("""
name: tolerant
continue_on_error: true
steps:
  - tool: no_such_tool_xyz
  - tool: reminder_list
""")
        result = workflows.run("tolerant")
        assert len(result["results"]) == 2

    def test_schedule_due_logic(self, monkeypatch, tmp_path):
        from mk2 import workflows

        workflows.create("""
name: nightly
schedule:
  daily: "03:00"
steps:
  - tool: reminder_list
""")
        assert workflows.due_now() == ["nightly"]   # never ran today
        workflows.mark_ran("nightly")
        assert workflows.due_now() == []            # marked -> not due

    def test_tools_wrappers_roundtrip(self):
        r = tools.call("workflow_create", {"spec_yaml": self.YAML3})
        assert r["ok"] is True
        lst = tools.call("workflow_list", {})
        assert any(w["name"] == "triple_check" for w in lst["data"]["workflows"])
        d = tools.call("workflow_delete", {"name": "triple_check"})
        assert d["ok"] is True


class TestHabits:
    def _seed_repeats(self, target="youtube"):
        for _ in range(3):
            db.audit("open_app", json.dumps({"target": target}), True, "opened")

    def test_three_repeats_propose_once(self, monkeypatch):
        from mk2 import habits

        self._seed_repeats()
        found = habits.scan()
        assert len(found) == 1
        again = habits.scan()
        assert again == []                          # deduped by signature

    def test_approve_turns_habit_into_workflow(self, monkeypatch, tmp_path):
        from mk2 import habits, workflows

        self._seed_repeats()
        p = habits.scan()[0]
        db.proposal_set_status(p["id"], "pending") if False else None
        r = tools.call("proposal_approve", {"id": p["id"]})
        assert r["ok"] is True, r["speech"]
        wf_name = r["data"]["workflow"]
        lst = tools.call("workflow_list", {})
        assert any(w["name"] == wf_name for w in lst["data"]["workflows"])
        # proposal consumed
        assert all(pr["id"] != p["id"] for pr in db.proposals())

    def test_reject_dismisses(self, monkeypatch):
        from mk2 import habits

        self._seed_repeats(target="netflix")
        p = habits.scan()[0]
        r = tools.call("proposal_reject", {"id": p["id"]})
        assert r["ok"] is True
        assert habits.scan() == []                  # stays dismissed


class TestConnectors:
    SPEC = {
        "name": "echo_test",
        "description": "test connector",
        "base_url": "https://api.echo.test",
        "method": "GET",
        "path": "/v1/echo/{city}",
        "args": {"city": {"in": "path", "type": "string"},
                 "units": {"in": "query", "type": "string"}},
    }

    def test_add_then_call_same_session(self, tmp_path, monkeypatch):
        from mk2.tools import connectors as conn

        r = tools.call("connector_add", {"spec_json": json.dumps(self.SPEC)})
        assert r["ok"] is True, r["speech"]

        seen = {}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            status = 200
            def read(self):
                return b'{"temp": 31}'
        def fake_urlopen(req, timeout=15):
            seen["url"] = req.full_url
            return FakeResp()
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        out = tools.call("api_echo_test", {"city": "delhi", "units": "metric"})
        assert out["ok"] is True and out["data"]["response"]["temp"] == 31
        assert "/v1/echo/delhi?" in seen["url"] and "units=metric" in seen["url"]

    def test_survives_restart_via_load_all(self, tmp_path):
        from mk2.tools import connectors as conn

        conn.CONNECTORS_DIR.mkdir(parents=True, exist_ok=True)
        (conn.CONNECTORS_DIR / "echo_test.json").write_text(
            json.dumps(self.SPEC), encoding="utf-8")
        n = conn.load_all()
        assert n >= 1
        assert "api_echo_test" in {t["name"] for t in tools.manifest()}

    def test_bad_specs_rejected(self):
        for bad in ['{"name":"x"}', '{"name":"a b","base_url":"ftp://y"}',
                    '{"name":"ok_name","base_url":"http://z","method":"DELETE"}']:
            r = tools.call("connector_add", {"spec_json": bad})
            assert r["ok"] is False
