"""Background job runner: long tasks execute step-by-step with checkpoints.

Jobs survive restarts — the kernel re-spawns running jobs from their last
checkpoint on boot.
"""
import json
import logging
import threading
import time

from . import db, llm, tools

log = logging.getLogger("mk2.jobs")

ALLOWED_JOB_TOOLS = {
    "web_search", "web_fetch", "vault_write", "fs_write",
    "docs_read", "deep_research",
}

JOB_SYSTEM = (
    "You are an autonomous task executor. Complete the user's goal step by step.\n"
    "Each turn reply ONLY one JSON object:\n"
    '{{"action": {{"tool": "<name>", "args": {{...}}}}}}\n'
    "or when finished:\n"
    '{{"finish": "<summary of what you did>"}}\n'
    "Be efficient. Max {max_steps} steps."
)

_threads: dict[int, threading.Thread] = {}


def start(goal: str, max_steps: int = 20) -> int:
    now = time.time()
    with db._lock, db.connect() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(goal,status,max_steps,checkpoint,created,updated) "
            "VALUES(?,?,?,?,?,?)",
            (goal[:500], "running", max_steps, "[]", now, now),
        )
        jid = cur.lastrowid
    th = threading.Thread(target=_worker, args=(jid,), daemon=True,
                          name=f"mk2-job-{jid}")
    _threads[jid] = th
    th.start()
    return jid


def stop(jid: int) -> bool:
    th = _threads.get(jid)
    if not th or not th.is_alive():
        return False
    # mark stopped; worker checks between steps
    import json as _json
    ck = _load_checkpoint(jid)
    ck["stop"] = True
    _save_checkpoint(jid, ck)
    return True


def resume_running() -> int:
    """Re-spawn any 'running' jobs left over from a crash/restart."""
    n = 0
    with db._lock, db.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE status='running'"
        ).fetchall()
    for row in rows:
        jid = row["id"]
        log.info("resuming job %d after restart", jid)
        th = threading.Thread(target=_worker, args=(jid,), daemon=True,
                              name=f"mk2-job-{jid}-resume")
        _threads[jid] = th
        th.start()
        n += 1
    return n


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
    with db._lock, db.connect as conn:
        conn.execute(
            "UPDATE jobs SET checkpoint=?, updated=? WHERE id=?",
            (json.dumps(transcript[-16:], ensure_ascii=False), time.time(), jid),
        )


def _worker(jid: int) -> None:
    from .bus import bus

    with db._lock, db.connect() as c:
        row = c.execute("SELECT goal,max_steps,checkpoint FROM jobs WHERE id=?", (jid,)).fetchone()
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

    for step in range(max_steps):
        if _should_stop(jid):
            _finish(jid, "stopped", "Stopped by user.")
            return
        try:
            raw = llm.chat(messages + [
                {"role": "system",
                 "content": f"Available TOOLS:\n{tools_manifest}\n"
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
                                       "text": f"Job #{jid} finished: {summary}"})
            return
        action = data.get("action") if isinstance(data.get("action"), dict) else {}
        tool_name = str(action.get("tool", "")).strip()
        tool_args = action.get("args") if isinstance(action.get("args"), dict) else {}
        if tool_name not in ALLOWED_JOB_TOOLS:
            observation = f"DENIED: '{tool_name}' not allowed in jobs."
        else:
            observation = json.dumps(tools.call(tool_name, tool_args))[:2000]

        transcript.append({"role": "assistant", "content": raw})
        transcript.append({"role": "user", "content": observation})
        _save_checkpoint(jid, transcript)
        messages = messages[:2] + transcript[-10:]

    _finish(jid, "paused", f"Used {max_steps} steps without finishing. Resume to continue.")


def _should_stop(jid: int) -> bool:
    ck = _load_checkpoint(jid)
    return bool(ck.get("stop"))


def _parse_json(raw: str):
    import json as _json

    raw = raw.strip()
    try:
        return _json.loads(raw)
    except Exception:
        import re as _re

        m = _re.search(r"\{.*\}", raw, _re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(0))
            except Exception:
                pass
    return None


def _finish(jid: int, status: str, result: str) -> None:
    with db._lock, db.connect() as c:
        c.execute("UPDATE jobs SET status=?,result=?,updated=? WHERE id=?",
                  (status, result[:1000], time.time(), jid))
    _threads.pop(jid, None)


def list_jobs(limit: int = 8) -> list[dict]:
    with db._lock, db.connect() as c:
        rows = c.execute(
            "SELECT id,goal,status,steps_used,result FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
