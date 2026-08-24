"""Mission runner (Phase 3): long tasks execute step-by-step with checkpoints,
dependencies, strategy rotation and full-surface delivery.

Status flow:
    queued --(deps done, promotion tick)--> running --> done | failed | stopped
    running --(steps exhausted)--> paused --(task_resume)--> running
    running --(stop request)---> stopping --> stopped

Survival guarantees:
  - Checkpoints saved every step; kernel re-spawns running missions on boot.
  - Strategy rotation: a tool that keeps failing gets BLOCKED for the rest
    of the mission so the model is forced onto another route.
  - Completion fans out via notify.out -> console/TTS + Telegram + push.
"""
import json
import logging
import threading
import time

from . import db, llm, tools
from .bus import bus

log = logging.getLogger("mk2.jobs")

ALLOWED_JOB_TOOLS = {
    "web_search", "web_fetch", "vault_write", "fs_write",
    "docs_read", "deep_research", "push_send", "shell_run",
    "docs_create", "docs_append", "ask_documents",
    "code_read", "code_search", "code_edit", "code_write", "code_test",
}

JOB_SYSTEM = (
    "You are an autonomous mission executor. Complete the user's goal step "
    "by step.\nEach turn reply ONLY one JSON object:\n"
    '{{"action": {{"tool": "<name>", "args": {{...}}}}}}\n'
    "or when finished:\n"
    '{{"finish": "<summary of what you did>"}}\n'
    "If a tool keeps failing, switch strategy - do not repeat it."
)

MAX_TOOL_FAILS = 2  # same tool failing this often => blocked for the mission

_threads: dict[int, threading.Thread] = {}
_threads_lock = threading.Lock()


# ------------------------------------------------------------- lifecycle

def start(goal: str, depends_on: list[int] | None = None,
          max_steps: int = 20) -> int:
    """Create a mission. Runs immediately unless it has unfinished deps."""
    now = time.time()
    deps = []
    for d in (depends_on or []):
        try:
            deps.append(int(d))
        except (TypeError, ValueError):
            continue
    with db._lock, db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(goal,status,max_steps,checkpoint,depends_on,"
            "created,updated) VALUES(?,?,?,?,?,?,?)",
            (str(goal)[:500], "pending", max_steps, "[]",
             json.dumps(deps), now, now),
        )
        jid = cur.lastrowid
    if deps:
        dead = _deps_failed(jid)
        if dead:
            _finish(jid, "failed",
                    f"Dependency mission(s) {dead} did not complete.")
        elif _deps_resolved(jid):
            _set_status(jid, "running")
            _spawn(jid)
        else:
            _set_status(jid, "queued")
            log.info("mission #%d queued on deps %s", jid, deps)
    else:
        _set_status(jid, "running")
        _spawn(jid)
    return int(jid)


def _deps_of(jid: int) -> list[int]:
    with db._lock, db.connect() as c:
        row = c.execute("SELECT depends_on FROM jobs WHERE id=?", (jid,)).fetchone()
    try:
        return [int(d) for d in json.loads((row["depends_on"] if row else "") or "[]")]
    except Exception:
        return []


def _dep_statuses(jid: int) -> dict[int, str]:
    deps = _deps_of(jid)
    if not deps:
        return {}
    marks = ",".join("?" * len(deps))
    with db._lock, db.connect() as c:
        rows = c.execute(
            f"SELECT id,status FROM jobs WHERE id IN ({marks})", deps).fetchall()
    return {r["id"]: r["status"] for r in rows}


def _deps_resolved(jid: int) -> bool:
    """True when every dependency reached a terminal state AND none failed."""
    st = _dep_statuses(jid)
    if not st:
        return True
    for status in st.values():
        if status not in ("done", "failed", "stopped"):
            return False
    return True


def _deps_failed(jid: int) -> list[int]:
    st = _dep_statuses(jid)
    return [d for d, s in st.items() if s in ("failed", "stopped")]


def promote_queued() -> int:
    """Move eligible queued missions to running. Called by kernel tick."""
    with db._lock, db.connect() as c:
        rows = c.execute(
            "SELECT id FROM jobs WHERE status='queued'").fetchall()
    started = 0
    for row in rows:
        jid = row["id"]
        dead = _deps_failed(jid)
        if dead:
            _finish(jid, "failed",
                    f"Dependency mission(s) {dead} did not complete.")
            continue
        if _deps_resolved(jid):
            _set_status(jid, "running")
            _spawn(jid)
            started += 1
    return started


def stop(jid: int) -> bool:
    th = _threads.get(jid)
    alive = bool(th and th.is_alive())
    with db._lock, db.connect() as c:
        row = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    if not alive:
        if not row or row["status"] in ("done", "failed", "stopped", "stopping"):
            return False
        if row["status"] == "queued":
            # never started: go straight to terminal state
            c.execute("UPDATE jobs SET status='stopped',result=?,updated=? "
                      "WHERE id=?", ("Stopped before start.", time.time(), jid))
            return True
    with db._lock, db.connect() as c:
        c.execute("UPDATE jobs SET status='stopping', updated=? WHERE id=?",
                  (time.time(), jid))
    return True


def resume(jid: int) -> bool:
    """Manually resume a paused/queued mission (deps must be resolved)."""
    with db._lock, db.connect() as c:
        row = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row or row["status"] not in ("paused", "queued"):
        return False
    dead = _deps_failed(jid)
    if dead or not _deps_resolved(jid):
        return False
    _set_status(jid, "running")
    _spawn(jid)
    return True


def resume_running() -> int:
    """Re-spawn 'running' missions left over from a crash/restart, then
    promote anything queued whose dependencies are already satisfied."""
    n = 0
    with db._lock, db.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status='running'").fetchall()
    for row in rows:
        jid = row["id"]
        log.info("resuming mission %d after restart", jid)
        _spawn(jid)
        n += 1
    return n + promote_queued()


# ------------------------------------------------------------- internals

def _set_status(jid: int, status: str) -> None:
    with db._lock, db.connect() as c:
        c.execute("UPDATE jobs SET status=?, updated=? WHERE id=?",
                  (status, time.time(), jid))


def _spawn(jid: int) -> None:
    th = threading.Thread(target=_worker, args=(jid,), daemon=True,
                          name=f"mk2-job-{jid}")
    with _threads_lock:
        _threads[jid] = th
    th.start()


def _load_checkpoint(jid: int) -> list[dict]:
    with db._lock, db.connect() as c:
        row = c.execute("SELECT checkpoint FROM jobs WHERE id=?", (jid,)).fetchone()
    if not row or not row["checkpoint"]:
        return []
    try:
        return json.loads(row["checkpoint"])
    except Exception:
        return []


def _save_checkpoint(jid: int, transcript: list[dict]) -> None:
    with db._lock, db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET checkpoint=?, updated=? WHERE id=?",
            (json.dumps(transcript[-16:], ensure_ascii=False), time.time(), jid),
        )


def _bump_steps(jid: int, n: int) -> None:
    with db._lock, db.connect() as c:
        c.execute("UPDATE jobs SET steps_used=?, updated=? WHERE id=?",
                  (n, time.time(), jid))


# ---------------------------------------------------------------- worker

def _worker(jid: int) -> None:
    with db._lock, db.connect() as c:
        row = c.execute(
            "SELECT goal,max_steps,checkpoint FROM jobs WHERE id=?", (jid,)
        ).fetchone()
    if not row:
        return
    goal = row["goal"]
    max_steps = min(int(row["max_steps"] or 20), 50)
    transcript = []
    try:
        transcript = json.loads(row["checkpoint"] or "[]")
    except Exception:
        pass

    system = JOB_SYSTEM.format(max_steps=max_steps)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"Goal: {goal}"}] + transcript[-12:]

    tools_manifest = json.dumps(
        [{k: t[k] for k in ("name", "description", "args")}
         for t in tools.manifest()
         if t["name"] in ALLOWED_JOB_TOOLS]
    )
    system += f"\nAvailable TOOLS:\n{tools_manifest}"

    tool_fails: dict[str, int] = {}
    blocked: set[str] = set()

    for step in range(max_steps):
        if _should_stop(jid):
            _finish(jid, "stopped", "Stopped by user.")
            return
        bus.publish("job.progress", {
            "id": jid, "goal": goal[:80],
            "step": step + 1, "max_steps": max_steps,
        })
        hint = ""
        if blocked:
            hint = (f"\nBLOCKED tools (kept failing, pick ANOTHER strategy): "
                    f"{sorted(blocked)}.")
        try:
            raw = llm.chat(messages + [
                {"role": "system",
                 "content": f"Available TOOLS:\n{tools_manifest}{hint}\n"
                            'Reply ONLY JSON: {"action":{"tool":"...","args":{...}}} '
                            'or {"finish":"<summary>"}'}
            ], temperature=0.3, timeout=45)
        except Exception as exc:
            _save_checkpoint(jid, transcript)
            _finish(jid, "failed", f"LLM unreachable: {str(exc)[:120]}")
            return

        data = _parse_json(raw)
        if not data:
            continue
        if "finish" in data:
            summary = str(data["finish"])[:500]
            _finish(jid, "done", summary)
            bus.publish("notify.out", {"kind": "job",
                                       "text": f"Mission #{jid} finished: {summary}"})
            return
        action = data.get("action") if isinstance(data.get("action"), dict) else {}
        tool_name = str(action.get("tool", "")).strip()
        tool_args = action.get("args") if isinstance(action.get("args"), dict) else {}

        if tool_name in blocked:
            observation = (f"DENIED: '{tool_name}' kept failing and is blocked "
                           "for this mission. Choose a DIFFERENT approach.")
        elif tool_name not in ALLOWED_JOB_TOOLS:
            observation = f"DENIED: '{tool_name}' not allowed in missions."
        else:
            result = tools.call(tool_name, tool_args)
            if result.get("ok"):
                tool_fails.pop(tool_name, None)
                observation = json.dumps(result)[:2000]
            else:
                tool_fails[tool_name] = tool_fails.get(tool_name, 0) + 1
                if tool_fails[tool_name] >= MAX_TOOL_FAILS:
                    blocked.add(tool_name)
                    observation = (
                        json.dumps(result)[:800] +
                        f"\n'{tool_name}' has now failed {tool_fails[tool_name]}x "
                        "and is BLOCKED. Rotate strategy: different tool or "
                        "different arguments.")
                else:
                    observation = json.dumps(result)[:2000]

        transcript.append({"role": "assistant", "content": raw})
        transcript.append({"role": "user", "content": observation})
        _save_checkpoint(jid, transcript)
        _bump_steps(jid, step + 1)
        messages = messages[:2] + transcript[-10:]

    _finish(jid, "paused", f"Used {max_steps} steps without finishing. Resume to continue.")


def _should_stop(jid: int) -> bool:
    with db._lock, db.connect() as c:
        row = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    return bool(row and row["status"] == "stopping")


def _parse_json(raw: str):
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        m = __import__("re").search(r"\{.*\}", raw, __import__("re").DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return None


def _finish(jid: int, status: str, result: str) -> None:
    with db._lock, db.connect() as c:
        c.execute("UPDATE jobs SET status=?,result=?,updated=? WHERE id=?",
                  (status, result[:1000], time.time(), jid))
    with _threads_lock:
        _threads.pop(jid, None)


def list_jobs(limit: int = 8) -> list[dict]:
    with db._lock, db.connect() as c:
        rows = c.execute(
            "SELECT id,goal,status,steps_used,result,depends_on FROM jobs "
            "ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["depends_on"] = json.loads(d.get("depends_on") or "[]")
        except Exception:
            d["depends_on"] = []
        out.append(d)
    return out


# ------------------------------------------------------------------ tools

from .tools import tool  # noqa: E402


@tool("task_start", "Start an autonomous mission (multi-step goal). Optionally waits for other missions by id.",
      {"goal": {"type": "string"}, "depends_on": {"type": "array"}},
      permission="execute")
def task_start(goal: str, depends_on: list | None = None) -> dict:
    if not (goal or "").strip():
        return {"ok": False, "speech": "Give me an actual goal.", "data": {}}
    jid = start(goal.strip(), depends_on=depends_on)
    deps = _dep_statuses(jid)
    if deps:
        waiting = {d: s for d, s in deps.items()}
        return {"ok": True,
                "speech": (f"Mission #{jid} queued behind missions "
                           f"{waiting}. I'll start it automatically."),
                "data": {"id": jid, "waiting_on": waiting}}
    return {"ok": True,
            "speech": f"Mission #{jid} started. I'll report back when it finishes.",
            "data": {"id": jid}}


@tool("task_status", "List recent missions with their status.",
      {"limit": {"type": "integer"}}, permission="read")
def task_status(limit: int = 8) -> dict:
    rows = list_jobs(max(1, min(int(limit), 15)))
    if not rows:
        return {"ok": True, "speech": "No missions yet.", "data": {"missions": []}}
    lines = [f"#{r['id']} {r['status']}: {(r['result'] or r['goal'])[:60]}"
             for r in rows]
    return {"ok": True, "speech": "; ".join(lines),
            "data": {"missions": rows}}


@tool("task_stop", "Stop a running mission by id.",
      {"id": {"type": "integer"}}, permission="execute")
def task_stop(id: int) -> dict:
    ok = stop(int(id))
    return {"ok": ok,
            "speech": f"Stopping mission #{id}." if ok else f"Mission #{id} isn't running.",
            "data": {}}


@tool("task_resume", "Resume a paused or queued mission.",
      {"id": {"type": "integer"}}, permission="execute")
def task_resume(id: int) -> dict:
    ok = resume(int(id))
    return {"ok": ok,
            "speech": f"Mission #{id} resumed." if ok else f"Can't resume mission #{id} right now.",
            "data": {}}
