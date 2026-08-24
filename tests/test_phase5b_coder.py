"""DevAgent: path jail, approval-gated writes, apply+test, dev loop."""
import json

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2 import coder, errlog, skills
    import pathlib

    monkeypatch.setattr(skills, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(coder, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(coder, "PROPOSALS_DIR", tmp_path / "props")
    errlog.clear()                      # global ring must not leak between tests
    (tmp_path / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    tools.ensure_loaded()


class TestPathJail:
    def test_relative_inside_root_ok(self):
        from mk2 import coder

        r = coder.do_read("sample.py")
        assert r["ok"] is True and "VALUE" in r["content"]

    def test_escape_rejected(self):
        from mk2 import coder

        for bad in ("../outside.txt", "C:\\Windows\\win.ini",
                    "__pycache__/x.py", ".git/config"):
            with pytest.raises(ValueError):
                coder._safe(bad)

    def test_missing_file_clean_error(self):
        from mk2 import coder

        assert coder.do_read("nope.py")["ok"] is False


class TestApprovalGate:
    def test_write_always_stages_never_touches_disk(self, tmp_path, monkeypatch):
        from mk2 import coder

        monkeypatch.setenv("EVO_CODE_APPROVAL", "1")
        r = coder.do_write("new.py", "X = 2\n")
        assert r["ok"] is True and r.get("proposal")
        assert not (tmp_path / "new.py").exists()          # nothing on disk
        assert any(p["kind"] == "code" for p in db.proposals())

    def test_no_direct_mode_exists_anymore(self, monkeypatch):
        """User mandate: even 'small' changes require explicit apply."""
        from mk2 import coder

        monkeypatch.setenv("EVO_CODE_APPROVAL", "0")   # must be IGNORED
        r = coder.do_write("direct.py", "Y = 3\n")
        assert r.get("proposal") and not r.get("applied")

    def test_apply_writes_and_reports_tests(self, tmp_path, monkeypatch):
        from mk2 import coder

        staged = coder.do_write("apply_me.py", "Z = 9\n")
        digest = staged["proposal"]

        def fake_test():
            return {"ok": True, "summary": "5 passed in 0.01s"}
        monkeypatch.setattr(coder, "do_test", fake_test)
        (tmp_path / "apply_me.py").write_text("OLD = 0\n", encoding="utf-8")
        r = coder.do_apply(digest)
        assert r["ok"] is True
        assert (tmp_path / "apply_me.py").read_text() == "Z = 9\n"
        assert r["tests"]["summary"].startswith("5 passed")

    def test_red_suite_auto_reverts(self, tmp_path, monkeypatch):
        """Safety net: applied change that breaks the suite rolls back."""
        from mk2 import coder

        staged = coder.do_write("safe.py", "NEW = 1\n")
        (tmp_path / "safe.py").write_text("OLD = 0\n", encoding="utf-8")
        states = iter([{"ok": False, "summary": "3 failed"},
                       {"ok": True, "summary": "5 passed"}])
        monkeypatch.setattr(coder, "do_test", lambda: next(states))
        r = coder.do_apply(staged["proposal"])
        assert r["rolled_back"] is True
        assert (tmp_path / "safe.py").read_text() == "OLD = 0\n"  # restored

    def test_edit_requires_unique_match(self, tmp_path):
        from mk2 import coder

        (tmp_path / "dup.py").write_text("A = 1\nA = 1\n", encoding="utf-8")
        r = coder.do_edit("dup.py", "A = 1\n", "B = 1\n")
        assert r["ok"] is False and "2x" in r["error"]


class TestTestRunner:
    def test_parses_green_suite(self, monkeypatch):
        from mk2 import coder

        class R:
            returncode = 0
            stdout = "................\n142 passed in 12s\n"
        monkeypatch.setattr(coder.subprocess, "run",
                            lambda *a, **k: R())
        r = coder.do_test()
        assert r["ok"] is True and "142 passed" in r["summary"]

    def test_detects_red_suite(self, monkeypatch):
        from mk2 import coder

        class R:
            returncode = 1
            stdout = "..F..\n1 failed, 4 passed in 1s\n"
        monkeypatch.setattr(coder.subprocess, "run", lambda *a, **k: R())
        assert coder.do_test()["ok"] is False

    def test_tool_wrapper_green(self, monkeypatch):
        from mk2 import coder

        monkeypatch.setattr(coder, "do_test",
                            lambda: {"ok": True, "summary": "10 passed"})
        r = tools.call("code_test", {})
        assert r["speech"].startswith("GREEN")


class TestDevLoop:
    def _scripted_loop(self, monkeypatch, script):
        from mk2 import coder

        it = iter(script)

        def fake_chat(messages, **k):
            try:
                return next(it)
            except StopIteration:
                return '{"finish":"wrapped up"}'
        monkeypatch.setattr(coder.llm if hasattr(coder, "llm") else __import__("mk2.llm", fromlist=["chat"]),
                            "chat", fake_chat)

        notes = []
        monkeypatch.setattr("mk2.bus.bus.publish",
                            lambda topic, payload=None:
                            notes.append((topic, payload)) or None)
        return coder, notes

    def test_full_loop_write_then_test_then_finish(self, monkeypatch, tmp_path):
        coder, notes = self._scripted_loop(monkeypatch, [
            '{"action":{"tool":"code_read","args":{"path":"sample.py"}}}',
            '{"action":{"tool":"code_write","args":{"path":"made.py","content":"Q=1\\n"}}}',
            '{"action":{"tool":"code_test"}}',
            '{"finish":"added module"}',
        ])
        monkeypatch.setenv("EVO_SELF_HEAL_AUTOAPPLY", "1")  # commits its stages
        monkeypatch.setattr(coder, "do_test",
                            lambda: {"ok": True, "summary": "7 passed"})
        coder._dev_loop("add a module", max_steps=6)
        assert (tmp_path / "made.py").exists()      # committed via apply

        texts = [p["text"] for t, p in notes if t == "notify.out"]
        assert any("done" in t.lower() for t in texts)

    def test_never_finishes_on_red_suite(self, monkeypatch, tmp_path):
        coder, notes = self._scripted_loop(monkeypatch, [
            '{"finish":"I think I am done"}',
        ])
        monkeypatch.setattr(coder, "do_test",
                            lambda: {"ok": False, "summary": "1 failed"})
        # no code_test ran -> last_test_ok False -> finish must be flagged red
        coder._dev_loop("risky change", max_steps=3)
        texts = [p["text"] for t, p in notes if t == "notify.out"]
        assert any("WITHOUT green suite" in t for t in texts)

    def test_denies_non_dev_tools(self, monkeypatch, tmp_path):
        from mk2 import coder

        seen = []
        it = iter([
            '{"action":{"tool":"shell_run","args":{"command":"boom"}}}',
            '{"finish":"gave up"}',
        ])

        def fake_chat(messages, **k):
            try:
                return next(it)
            except StopIteration:
                return '{"finish":"gave up"}'
        monkeypatch.setattr(__import__("mk2.llm", fromlist=["chat"]), "chat", fake_chat)
        real_do_edit = coder.do_edit

        def guard(*a, **k):
            seen.append("edit-called")
            return real_do_edit(*a, **k)
        monkeypatch.setattr(coder, "do_edit", guard)
        monkeypatch.setattr(coder, "do_test", lambda: {"ok": True, "summary": "x passed"})
        coder._dev_loop("try something forbidden", max_steps=4)
        assert seen == []      # shell_run denied; edit never invoked


class TestRegistration:
    def test_dev_tools_registered(self):
        names = {t["name"] for t in tools.manifest()}
        for expected in ("code_read", "code_search", "code_edit",
                         "code_write", "code_apply", "code_test", "devtask"):
            assert expected in names, f"{expected} missing"

    def test_devtask_is_long_running(self):
        m = {t["name"]: t for t in tools.manifest()}["devtask"]
        assert m["long_running"] is True


class TestSelfCheck:
    def test_diagnose_green_when_all_well(self, monkeypatch, tmp_path):
        import mk2.selfcheck as sc
        from mk2 import coder

        (tmp_path / "sample.py").write_text("VALUE = 1\n", encoding="utf-8")
        monkeypatch.setattr(coder, "do_test",
                            lambda: {"ok": True, "summary": "157 passed"})
        r = sc.diagnose()
        assert r["healthy"] is True and r["issues"] == []

    def test_red_suite_detected_and_healed(self, monkeypatch):
        import mk2.selfcheck as sc
        from mk2 import coder

        monkeypatch.setattr(coder, "do_test",
                            lambda: {"ok": False, "summary": "2 failed"})
        started = []
        monkeypatch.setattr(coder, "devtask_start", lambda goal: started.append(goal))
        r = sc.tick()
        assert any(i["type"] == "red_suite" for i in r["issues"])
        assert r["healed"] == ["red_suite"]
        assert started and "pytest suite is failing" in started[0]

    def test_recurring_errors_detected(self, monkeypatch):
        import mk2.selfcheck as sc
        from mk2 import coder, errlog

        monkeypatch.setattr(coder, "do_test",
                            lambda: {"ok": True, "summary": "10 passed"})
        for _ in range(4):
            errlog.log_error("tool:screenshot", "gdi fail")
        r = sc.diagnose()
        kinds = [i["type"] for i in r["issues"]]
        assert "recurring_error" in kinds
        errlog.clear()

    def test_selfcheck_now_tool(self, monkeypatch):
        from mk2 import coder

        monkeypatch.setattr(coder, "do_test",
                            lambda: {"ok": True, "summary": "157 passed"})
        r = tools.call("selfcheck_now", {})
        assert r["ok"] is True and "All clear" in r["speech"]

    def test_autoapply_loop_commits_with_revert_net(self, monkeypatch, tmp_path):
        """Toggle ON: dev loop commits staged edits itself; red -> rollback."""
        from mk2 import coder

        monkeypatch.setenv("EVO_SELF_HEAL_AUTOAPPLY", "1")
        it = iter([
            '{"action":{"tool":"code_write","args":{"path":"auto.py","content":"K=1\\n"}}}',
            '{"finish":"committed"}',
        ])

        def fake_chat(messages, **k):
            try:
                return next(it)
            except StopIteration:
                return '{"finish":"done"}'
        monkeypatch.setattr(__import__("mk2.llm", fromlist=["chat"]), "chat", fake_chat)
        (tmp_path / "auto.py").write_text("K=0\n", encoding="utf-8")
        results = iter([{"ok": True, "summary": "6 passed"},
                        {"ok": True, "summary": "6 passed"}])
        monkeypatch.setattr(coder, "do_test", lambda: next(results))
        notes = []
        monkeypatch.setattr("mk2.bus.bus.publish",
                            lambda topic, payload=None:
                            notes.append((topic, payload)) or None)
        coder._dev_loop("self heal demo", max_steps=5)
        assert (tmp_path / "auto.py").read_text() == "K=1\n"  # committed


class TestRootAndFalseAlarm:
    def test_project_root_is_the_real_repo(self):
        """The bug that made selfcheck cry wolf: root must contain run.py.
        (Recomputed from the module file - the fixture patches the global.)"""
        from pathlib import Path

        real_root = Path(
            __import__("mk2.coder", fromlist=["PROJECT_ROOT"]).__file__
        ).resolve().parent.parent
        assert (real_root / "run.py").exists()
        assert (real_root / "mk2" / "__init__.py").exists()

    def test_no_tests_ran_flagged_as_infra_not_red(self, monkeypatch):
        from mk2 import coder, selfcheck

        class R:
            returncode = 5
            stdout = "no tests ran in 0.00s\n"
        monkeypatch.setattr(coder.subprocess, "run", lambda *a, **k: R())
        t = coder.do_test()
        assert t["ok"] is False and t.get("infra") is True
        r = selfcheck.diagnose.__wrapped__ if False else None
        # infra issues must never be auto-healed into dev tasks
        monkeypatch.setattr(coder, "do_test",
                            lambda: {"ok": False, "infra": True,
                                     "summary": "no tests ran"})
        started = []
        monkeypatch.setattr(coder, "devtask_start",
                            lambda g: started.append(g))
        rep = sc_tick(monkeypatch)
        assert rep["healed"] == [] and not any(
            i["type"] == "red_suite" for i in rep["issues"])


def sc_tick(monkeypatch):
    import mk2.selfcheck as sc

    sc._healed_recent.clear()
    return sc.tick()
