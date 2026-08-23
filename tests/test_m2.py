import time

import pytest

from mk2 import db, tools
from mk2.reminders import tick
from mk2.timeparse import parse_when, strip_when


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    monkeypatch.setattr("mk2.fs_tools.ALLOWED_ROOTS",
                        [tmp_path / "docs", tmp_path / "data"])
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "data").mkdir(parents=True)


NOW = datetime_for_test = None


def dt(*a, **k):
    from datetime import datetime

    return datetime(*a, **k)


class TestTimeParse:
    def test_in_relative(self):
        now = dt(2026, 8, 23, 12, 0)
        d = parse_when("remind me in 10 minutes to stretch", now=now)
        assert (d - now).total_seconds() == 600

    def test_at_pm(self):
        now = dt(2026, 8, 23, 9, 0)
        d = parse_when("at 9pm call mum", now=now)
        assert d.hour == 21 and d.minute == 0

    def test_at_am_midnight_edge(self):
        now = dt(2026, 8, 23, 10, 0)
        d = parse_when("at 12:30am feed cat", now=now)
        assert d.hour == 0 and d.minute == 30 and d > now

    def test_tomorrow_morning(self):
        now = dt(2026, 8, 23, 22, 0)
        d = parse_when("tomorrow at 7am run", now=now)
        assert (d.date() - now.date()).days == 1 and d.hour == 7

    def test_plain_duration(self):
        now = dt(2026, 8, 23, 8, 0)
        assert parse_when("45 minutes", now=now) - now == __import__("datetime").timedelta(minutes=45)

    def test_garbage_returns_none(self):
        assert parse_when("sometime maybe") is None


class TestStripWhen:
    def test_strips_leading_and_time(self):
        assert "stretch" in strip_when("remind me in 10 minutes to stretch")

    def test_keeps_body_when_no_time_words(self):
        assert strip_when("call the bank").lower().startswith("call")


class TestReminderFlow:
    def test_tool_add_lists_pending(self):
        r = tools.call("reminder_add",
                       {"text": "tea time", "when": "in 5 minutes"})
        assert r["ok"]
        rows = db.reminders_pending()
        assert len(rows) == 1 and rows[0]["text"] == "tea time"

    def test_cancel(self):
        r = tools.call("reminder_add", {"text": "x", "when": "in 5 minutes"})
        rid = db.reminders_pending()[0]["id"]
        assert tools.call("reminder_cancel", {"id": rid})["ok"] is True
        r2 = tools.call("reminder_cancel", {"id": rid})
        assert r2["ok"] is False

    def test_dispatch_exactly_once(self):
        fired_events = []

        def publish(topic, payload):
            if topic == "notify.out":
                fired_events.append(payload)

        past = time.time() - 5
        db.reminder_add("old tea", past)
        db.reminder_add("old tea", past)
        for _ in range(3):  # multiple ticks
            n = tick(publish, now=time.time())
        assert len(fired_events) == 2  # two reminders, each once
        assert db.reminders_due() == []


class TestFsSecurityMatrix:
    def test_outside_denied_and_audited(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        monkeypatch.setattr("mk2.fs_tools.ALLOWED_ROOTS", [tmp_path])
        r = tools.call("fs_read", {"path": r"C:\Windows\System32\cmd.exe"})
        assert r["ok"] is False
        assert db.recent_audit()[0]["tool"] == "fs_read"

    def test_deep_traversal_denied(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        monkeypatch.setattr("mk2.fs_tools.ALLOWED_ROOTS", [tmp_path])
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        evil = str(deep / ".." / ".." / ".." / ".." / "Windows" / "win.ini")
        r = tools.call("fs_read", {"path": evil})
        assert r["ok"] is False

    def test_inside_allowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        docs = tmp_path / "docs"
        (docs / "hello.txt").write_text("world", encoding="utf-8")
        r = tools.call("fs_read", {"path": str(docs / "hello.txt")})
        assert r["ok"] and "world" in r["speech"]


class TestDiag:
    def test_diag_reports_components(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        d = client.get("/api/diag").json()
        comps = {c["component"] for c in d["checks"]}
        assert {"database", "vault", "screens_dir"} <= comps
        assert d["ok"] is True


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from mk2.server import app

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "srv.db")
    db.migrate()
    return TestClient(app)
