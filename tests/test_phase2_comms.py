"""Phase 2 — Communication: telegram pairing/lock, mail draft-first gating,
push notifications, job stop-flag regression. All offline (no network)."""
import json

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    tools.ensure_loaded()


# ---------------------------------------------------------------- telegram

class TestTelegramPairingLock:
    def test_unpaired_chat_gets_zero_responses(self, monkeypatch):
        from mk2 import telegram_link as tg

        sends = []
        monkeypatch.setattr(tg, "send_message", lambda *a, **k: sends.append(a))
        tg.handle_update({"message": {"chat": {"id": 111},
                                      "text": "open youtube"}})
        assert sends == []  # total silence

    def test_pair_with_correct_code_then_reply(self, monkeypatch):
        from mk2 import telegram_link as tg

        code = tg.pairing_code()
        assert len(code) == 6
        sends = []
        monkeypatch.setattr(tg, "send_message",
                            lambda text, chat_id="": sends.append((text, chat_id)))
        tg.handle_update({"message": {"chat": {"id": 222},
                                      "text": f"/start {code}"}})
        assert tg.paired_chat() == "222"
        assert any("Paired" in t for t, _ in sends)

    def test_wrong_code_is_silence_and_does_not_pair(self, monkeypatch):
        from mk2 import telegram_link as tg

        tg.pairing_code()
        sends = []
        monkeypatch.setattr(tg, "send_message", lambda *a, **k: sends.append(a))
        tg.handle_update({"message": {"chat": {"id": 333},
                                      "text": "/start 000000"}})
        assert sends == [] and tg.paired_chat() == ""

    def test_paired_chat_runs_brain_stranger_ignored(self, monkeypatch):
        from mk2 import telegram_link as tg

        db.set_setting("telegram_chat_id", "555")
        turns = []
        monkeypatch.setattr(tg, "_brain_turn",
                            lambda text: turns.append(text) or "Done.")
        sends = []
        monkeypatch.setattr(tg, "send_message",
                            lambda text, chat_id="": sends.append((text, chat_id)))

        tg.handle_update({"message": {"chat": {"id": 555}, "text": "what time is it"}})
        assert turns == ["what time is it"]
        assert sends and sends[0] == ("Done.", "555")

        tg.handle_update({"message": {"chat": {"id": 999}, "text": "hack me"}})
        assert len(sends) == 1  # stranger got zero responses

    def test_notify_bridge_mirrors_to_phone(self):
        from mk2 import telegram_link as tg
        from mk2.bus import Event

        out = []
        orig = tg.send_message
        tg.send_message = lambda text, chat_id="": out.append(text)
        try:
            tg._bridge_notify(Event(topic="notify.out",
                                    payload={"kind": "reminder",
                                             "text": "Drink water"}))
        finally:
            tg.send_message = orig
        assert any("[reminder]" in t and "Drink water" in t for t in out)

    def test_status_shape(self):
        from mk2 import telegram_link as tg

        st = tg.status()
        assert set(st) == {"configured", "paired", "pairing_code"}


# -------------------------------------------------------------------- mail

class TestMailDraftFirstGating:
    def test_draft_creates_file_and_audits(self, tmp_path):
        from mk2 import mail_tools as mt

        r = mt.mail_draft("friend@example.com", "Lunch", "See you at 1.")
        assert r["ok"] is True
        draft_id = r["data"]["draft_id"]
        p = mt._draft_path(draft_id)
        assert p.exists()
        d = json.loads(p.read_text(encoding="utf-8"))
        assert d["sent"] is False and d["to"] == "friend@example.com"
        assert any(a["tool"] == "mail_draft" for a in db.recent_audit(5))

    def test_send_blocked_without_master_toggle(self, monkeypatch):
        from mk2 import config, mail_tools as mt

        monkeypatch.setattr(config.settings, "mail_send_enabled", False)
        did = mt.mail_draft("a@b.com", "S", "B")["data"]["draft_id"]
        r = tools.call("mail_send", {"draft_id": did})
        assert r["ok"] is False and "locked" in r["speech"].lower()

    def test_send_requires_existing_unsent_draft(self, monkeypatch):
        from mk2 import config, mail_tools as mt

        monkeypatch.setattr(config.settings, "mail_send_enabled", True)
        r = tools.call("mail_send", {"draft_id": "ghost"})
        assert r["ok"] is False and "no draft" in r["speech"].lower()

    def test_full_send_happy_path_and_single_use(self, monkeypatch):
        from mk2 import config, mail_tools as mt

        sent = []
        monkeypatch.setattr(mt, "_smtp_send",
                            lambda to, s, b: sent.append((to, s, b)))
        monkeypatch.setattr(config.settings, "mail_send_enabled", True)
        did = mt.mail_draft("x@y.com", "Report", "Attached.")["data"]["draft_id"]

        r = tools.call("mail_send", {"draft_id": did})
        assert r["ok"] is True and sent == [("x@y.com", "Report", "Attached.")]

        again = tools.call("mail_send", {"draft_id": did})
        assert again["ok"] is False and "already" in again["speech"].lower()
        assert len(sent) == 1  # single-use enforced

    def test_mail_unread_graceful_when_unconfigured(self):
        r = tools.call("mail_unread", {"limit": 3})
        assert r["ok"] is False and "not configured" in r["speech"].lower()


# -------------------------------------------------------------------- push

class TestPush:
    def test_push_tool_reports_unconfigured(self, monkeypatch):
        from mk2 import config

        monkeypatch.setattr(config.settings, "ntfy_topic", "")
        r = tools.call("push_send", {"title": "T"})
        assert r["ok"] is False and "NTFY_TOPIC" in r["speech"]

    def test_push_posts_within_hard_bound(self, monkeypatch):
        from mk2 import config, push_notify as pn

        posts = []
        monkeypatch.setattr(config.settings, "ntfy_topic", "evo-test-topic")
        monkeypatch.setattr(pn, "_post",
                            lambda url, data, headers, timeout=10:
                            posts.append((url, data, headers)) or 200)
        ok = pn.push("EVO", "hello phone")
        assert ok is True and len(posts) == 1
        url, data, headers = posts[0]
        assert url.endswith("/evo-test-topic") and b"hello phone" in data

    def test_push_failure_never_raises(self, monkeypatch):
        from mk2 import config, push_notify as pn

        monkeypatch.setattr(config.settings, "ntfy_topic", "evo-test-topic")

        def boom(*a, **k):
            raise OSError("net down")
        monkeypatch.setattr(pn, "_post", boom)
        assert pn.push("EVO", "x") is False


# ------------------------------------------------- regressions (bug fixes)

class TestPhase2Regressions:
    def test_job_checkpoint_saves_and_stop_flag_reads_status(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from mk2 import jobs

        with db._lock, db.connect() as c:
            cur = c.execute(
                "INSERT INTO jobs(goal,status,max_steps,checkpoint,created,updated) "
                "VALUES('g','running',20,'[]',0,0)")
            jid = cur.lastrowid
        jobs._save_checkpoint(jid, [{"role": "user", "content": "step"}])
        with db._lock, db.connect() as c:
            row = c.execute("SELECT checkpoint FROM jobs WHERE id=?", (jid,)).fetchone()
        assert "step" in row["checkpoint"]  # was silently failing before fix
        assert jobs._should_stop(jid) is False
        with db._lock, db.connect() as c:
            c.execute("UPDATE jobs SET status='stopping' WHERE id=?", (jid,))
        assert jobs._should_stop(jid) is True

    def test_research_report_doc_has_title_and_body(self):
        """The old ternary dropped either title or report body."""
        has_sources, no_sources = True, False
        build = lambda hs: (
            f"# Research: T\n\n"
            + (f"*stamp &middot; {3 if hs else 0} sources*"
               if hs else "*stamp &middot; AI knowledge*")
            + "\n\nBODY\n\n## Sources\n")
        doc_t = "# Research: T" in build(has_sources) and "BODY" in build(has_sources)
        doc_f = "# Research: T" in build(no_sources) and "AI knowledge" in build(no_sources)
        assert doc_t and doc_f

    def test_api_transcribe_no_longer_422s_on_missing_query_param(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from fastapi.testclient import TestClient
        from mk2.server import app

        client = TestClient(app, raise_server_exceptions=False)
        r = client.post("/api/transcribe", content=b"x" * 200)
        # Request annotation now resolves: body is parsed and vosk rejects junk
        # (500). The OLD bug answered 422 asking for a query param named
        # 'request' - that must never come back.
        assert '"loc":["query","request"]' not in r.text
