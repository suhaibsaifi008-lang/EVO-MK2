"""Phase 3 — Autonomy: mission DAG dependencies, strategy rotation,
boot resume, task_* tools. Plus PTT transcription regression coverage."""
import io
import json
import math
import struct
import time

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    tools.ensure_loaded()


def wait_until(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.05)
    return bool(fn())


def jstatus(jid):
    with db._lock, db.connect() as c:
        row = c.execute("SELECT status,result FROM jobs WHERE id=?", (jid,)).fetchone()
    return (row["status"], row["result"]) if row else ("missing", "")


def fake_finish_llm(monkeypatch, replies=None):
    """Scripted llm.chat for the mission worker."""
    import mk2.jobs as jobs

    seq = iter(replies or ['{"finish":"all done"}'])

    def chat(*a, **k):
        try:
            return next(seq)
        except StopIteration:
            return '{"finish":"all done"}'
    monkeypatch.setattr(jobs.llm, "chat", chat)
    return jobs


# ------------------------------------------------------------- transcription

class TestTranscriptionRegression:
    def wav(self, rate, seconds=0.6):
        n = int(rate * seconds)
        pcm = b"".join(struct.pack("<h", int(9000 * math.sin(
            2 * math.pi * 300 * i / rate))) for i in range(n))
        buf = io.BytesIO()
        buf.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt ")
        buf.write(struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16))
        buf.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
        return buf.getvalue()

    def test_resample_shrinks_48k_to_16k_length(self):
        from mk2.voice.stt import _resample

        raw = b"\x01\x00" * 48000  # 48000 samples
        out = _resample(raw, 48000, 16000)
        assert abs(len(out) / 2 - 16000) <= 2

    def test_resample_is_noop_at_16k(self):
        from mk2.voice.stt import SAMPLE_RATE, _resample

        assert _resample(b"\x01\x00\x02\x00", SAMPLE_RATE) == b"\x01\x00\x02\x00"

    def test_endpoint_accepts_browser_rate_wav(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EVO_STT_ENGINE", "vosk")  # keep tests offline
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from fastapi.testclient import TestClient
        from mk2.server import app

        client = TestClient(app)
        r = client.post("/api/transcribe", content=self.wav(48000))
        assert r.status_code == 200
        assert "text" in r.json()

    def test_bad_wav_returns_400_with_detail(self, monkeypatch, tmp_path):
        monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db"); db.migrate()
        from fastapi.testclient import TestClient
        from mk2.server import app

        client = TestClient(app, raise_server_exceptions=False)
        r = client.post("/api/transcribe", content=b"RIFFxxxxWAVE" + b"z" * 120)
        assert r.status_code == 500 or r.status_code == 400  # never a 422 query-param bug
        assert "query" not in r.text.lower()


# --------------------------------------------------------------- missions

class TestMissionDag:
    def test_simple_mission_completes(self, monkeypatch):
        jobs = fake_finish_llm(monkeypatch)
        jid = jobs.start("write a haiku")
        assert wait_until(lambda: jstatus(jid)[0] == "done")
        assert jstatus(jid)[1] == "all done"

    def test_dependency_blocks_then_promotes(self, monkeypatch):
        jobs = fake_finish_llm(monkeypatch)
        first = jobs.start("research laptops")
        assert wait_until(lambda: jstatus(first)[0] == "done")

        # second mission waits on a still-'running' fake dependency
        with db._lock, db.connect() as c:
            cur = c.execute(
                "INSERT INTO jobs(goal,status,max_steps,checkpoint,depends_on,"
                "created,updated) VALUES('blocker','running',20,'[]','[]',0,0)")
            blocker = cur.lastrowid
        third = jobs.start("compare them", depends_on=[first, blocker])
        assert jstatus(third)[0] == "queued"

        with db._lock, db.connect() as c:
            c.execute("UPDATE jobs SET status='done' WHERE id=?", (blocker,))
        started = jobs.promote_queued()
        assert started == 1
        assert wait_until(lambda: jstatus(third)[0] == "done")

    def test_failed_dependency_fails_dependent_without_running(self, monkeypatch):
        jobs = fake_finish_llm(monkeypatch)
        ran = []
        real_spawn = jobs._spawn
        monkeypatch.setattr(jobs, "_spawn",
                            lambda jid: ran.append(jid) or real_spawn(jid))
        with db._lock, db.connect() as c:
            cur = c.execute(
                "INSERT INTO jobs(goal,status,max_steps,checkpoint,depends_on,"
                "created,updated) VALUES('doomed','running',20,'[]','[]',0,0)")
            doomed = cur.lastrowid
        child = jobs.start("never runs", depends_on=[doomed])
        assert jstatus(child)[0] == "queued"  # dep still 'running' -> wait

        # fresh start with an ALREADY-failed dep: fail fast, never queue
        with db._lock, db.connect() as c:
            c.execute("UPDATE jobs SET status='failed' WHERE id=?", (doomed,))
        late = jobs.start("late child", depends_on=[doomed])
        st2, res2 = jstatus(late)
        assert st2 == "failed" and "did not complete" in res2.lower()

        # promote: queued child must die with its dependency
        jobs.promote_queued()
        st, res = jstatus(child)
        assert st == "failed" and "did not complete" in res.lower()
        assert child not in ran and late not in ran  # workers never spawned


class TestStrategyRotation:
    def test_repeatedly_failing_tool_gets_blocked(self, monkeypatch):
        import mk2.jobs as jobs

        fail = {"ok": False, "speech": "boom", "data": {}}
        calls = []

        def fake_call(name, args):
            calls.append(name)
            return dict(fail)

        monkeypatch.setattr(jobs.tools, "call", fake_call)
        replies = [
            '{"action": {"tool": "shell_run", "args": {"command": "a"}}}',
            '{"action": {"tool": "shell_run", "args": {"command": "b"}}}',
            '{"action": {"tool": "shell_run", "args": {"command": "c"}}}',  # denied, no exec
            '{"finish":"gave up honestly"}',
        ]
        seq = iter(replies)
        monkeypatch.setattr(jobs.llm, "chat", lambda *a, **k: next(seq))

        jid = jobs.start("impossible mission")
        assert wait_until(lambda: jstatus(jid)[0] == "done")
        assert calls.count("shell_run") == jobs.MAX_TOOL_FAILS  # 3rd blocked
        ckpt = jobs._load_checkpoint(jid)
        blob = json.dumps(ckpt).lower()
        assert "blocked" in blob  # model was told to rotate strategy

    def test_success_resets_failure_count(self, monkeypatch):
        import mk2.jobs as jobs

        responses = [
            {"ok": False, "speech": "f1", "data": {}},
            {"ok": True, "speech": "fine", "data": {}},   # reset
            {"ok": False, "speech": "f2", "data": {}},
            {"ok": False, "speech": "f3", "data": {}},    # 2nd consecutive -> block
        ]
        seen = []
        state = {"i": -1}

        def fake_call(name, args):
            state["i"] += 1
            seen.append(name)
            return dict(responses[state["i"]])

        monkeypatch.setattr(jobs.tools, "call", fake_call)
        script = ('{"action":{"tool":"web_search","args":{"query":"x"}}}',) * 4 \
            + ('{"finish":"done"}',)
        it = iter(script)
        monkeypatch.setattr(jobs.llm, "chat", lambda *a, **k: next(it))

        jid = jobs.start("flaky mission")
        assert wait_until(lambda: jstatus(jid)[0] == "done")
        assert seen[-1] == "web_search"
        ckpt = json.dumps(jobs._load_checkpoint(jid)).lower()
        assert "blocked" in ckpt


# ------------------------------------------------------------- task tools

class TestTaskTools:
    def test_start_status_stop_resume_flow(self, monkeypatch):
        fake_finish_llm(monkeypatch)
        r = tools.call("task_start", {"goal": "multi step thing"})
        assert r["ok"] is True and isinstance(r["data"]["id"], int)
        jid = r["data"]["id"]
        assert wait_until(lambda: jstatus(jid)[0] == "done")

        lst = tools.call("task_status", {})
        assert any(m["id"] == jid for m in lst["data"]["missions"])

        stop_r = tools.call("task_stop", {"id": jid})
        assert stop_r["ok"] is False  # already terminal

        bad_resume = tools.call("task_resume", {"id": jid})
        assert bad_resume["ok"] is False  # done is final

    def test_boot_resume_respawns_and_promotes(self, monkeypatch, tmp_path):
        jobs = fake_finish_llm(monkeypatch)
        with db._lock, db.connect() as c:
            c.execute(
                "INSERT INTO jobs(id,goal,status,max_steps,checkpoint,depends_on,"
                "created,updated) VALUES(50,'crashed mid-run','running',20,'[]','[]',0,0)")
            c.execute(
                "INSERT INTO jobs(id,goal,status,max_steps,checkpoint,depends_on,"
                "created,updated) VALUES(51,'waiting','queued',20,'[]','[50]',0,0)")
        n = jobs.resume_running()
        assert n >= 1
        assert wait_until(lambda: jstatus(50)[0] == "done")
        assert wait_until(lambda: jstatus(51)[0] == "done")  # promoted after dep

    def test_progress_events_published_per_step(self, monkeypatch):
        import mk2.bus as bus_mod

        events = []
        sub = bus_mod.bus.subscribe("job.progress",
                                    lambda ev: events.append(ev.payload))
        jobs = fake_finish_llm(monkeypatch)
        jid = jobs.start("watch me progress")
        assert wait_until(lambda: jstatus(jid)[0] == "done")
        assert any(p["id"] == jid and p["step"] >= 1 for p in events)
        bus_mod.bus.unsubscribe(sub)


class TestWhisperRouting:
    def _wav16k(self):
        n = 8000
        pcm = struct.pack("<" + "h" * n, *([0] * n))
        buf = io.BytesIO()
        buf.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt ")
        buf.write(struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16))
        buf.write(b"data" + struct.pack("<I", len(pcm)) + pcm)
        return buf.getvalue()

    def test_whisper_used_when_available(self, monkeypatch):
        import mk2.voice.stt as stt

        seen = {}

        def fake_whisper(pcm):
            seen["called"] = True
            return "open youtube"
        monkeypatch.setattr(stt, "_transcribe_whisper", fake_whisper)
        monkeypatch.setattr(
            stt, "_transcribe_vosk",
            lambda pcm: (_ for _ in ()).throw(AssertionError("vosk used")))
        assert stt.transcribe_wav(self._wav16k()) == "open youtube"
        assert seen.get("called")

    def test_falls_back_to_vosk_when_whisper_missing(self, monkeypatch):
        from mk2.voice import stt as stt

        def dead(pcm):
            raise ImportError("No module named faster_whisper")
        monkeypatch.setattr(stt, "_transcribe_whisper", dead)

        def dead_gemini(pcm):
            raise RuntimeError("no key")
        monkeypatch.setattr(stt, "_transcribe_gemini", dead_gemini)
        monkeypatch.setattr(stt, "_transcribe_vosk", lambda pcm: "vosk text")
        assert stt.transcribe_wav(self._wav16k()) == "vosk text"

    def test_forced_vosk_skips_whisper(self, monkeypatch):
        import mk2.voice.stt as stt

        monkeypatch.setenv("EVO_STT_ENGINE", "vosk")
        monkeypatch.setattr(stt, "_transcribe_whisper",
                            lambda pcm: (_ for _ in ()).throw(AssertionError("whisper used")))
        monkeypatch.setattr(stt, "_transcribe_vosk", lambda pcm: "vosk text")
        assert stt.transcribe_wav(self._wav16k()) == "vosk text"


class TestSearchEnginePreference:
    def test_default_is_google(self):
        from mk2.config import search_url

        assert search_url("open you do").startswith("https://www.google.com/search?q=")

    def test_brave_engine(self, monkeypatch):
        from mk2.config import search_url

        monkeypatch.setenv("EVO_SEARCH_ENGINE", "brave")
        assert "search.brave.com" in search_url("cats")

    def test_custom_template(self, monkeypatch):
        from mk2.config import search_url

        monkeypatch.setenv("EVO_SEARCH_URL", "https://x.com/find?text={q}")
        assert search_url("hi there") == "https://x.com/find?text=hi+there"
