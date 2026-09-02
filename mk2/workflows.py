"""Phase 5: Workflow chains — named, scheduled sequences of tool calls.

A workflow is a small YAML file in data/workflows/:

    name: morning_briefing
    description: Calendar + tech news to my phone
    schedule:
      daily: "08:00"        # or every_minutes: 120
    steps:
      - tool: calendar_today
      - tool: web_search
        args: {query: "today's top tech news"}
      - tool: push_send
        title: "Morning briefing"
        pass: results       # inject prior step outputs into 'message'

Sequential execution; a failed step aborts unless continue_on_error: true.
Everything runs through the normal permissioned/audited tools.call().
"""
import threading
import time
from pathlib import Path

import yaml

from . import db
from .config import DATA

WORKFLOWS_DIR = DATA / "workflows"
_lock = threading.Lock()
_last_run: dict[str, float] = {}


def _wf_path(name: str) -> Path:
    import re

    clean = re.sub(r"[^a-z0-9_]", "", name.strip().lower().replace(" ", "_"))[:40]
    return WORKFLOWS_DIR / f"{clean}.yaml"


def _load(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("steps") else None
    except Exception:
        return None


def _all() -> list[tuple[str, dict]]:
    if not WORKFLOWS_DIR.exists():
        return []
    out = []
    for p in sorted(WORKFLOWS_DIR.glob("*.yaml")):
        wf = _load(p)
        if wf:
            wf.setdefault("name", p.stem)
            out.append((p.stem, wf))
    return out


def create(spec_yaml: str) -> tuple[bool, str, str]:
    """Validate + save. Returns (ok, name, message)."""
    try:
        spec = yaml.safe_load(spec_yaml)
    except Exception as exc:
        return False, "", f"invalid YAML: {str(exc)[:120]}"
    if not isinstance(spec, dict) or not spec.get("name") or not spec.get("steps"):
        return False, "", "spec needs 'name' and a non-empty 'steps' list"
    name = str(spec["name"])
    for i, step in enumerate(spec["steps"]):
        if not isinstance(step, dict) or not step.get("tool"):
            return False, name, f"step {i} needs a 'tool'"
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    _wf_path(name).write_text(
        yaml.safe_dump(spec, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    return True, name, f"Workflow '{name}' saved ({len(spec['steps'])} steps)."


def delete(name: str) -> bool:
    p = _wf_path(name)
    if p.exists():
        p.unlink()
        return True
    return False


def run(name: str, publish_progress=None) -> dict:
    """Execute all steps sequentially via the audited registry."""
    path = _wf_path(name)
    wf = _load(path)
    if not wf:
        return {"ok": False, "error": f"no workflow '{name}'"}
    from . import tools

    results = []
    for i, step in enumerate(wf["steps"]):
        tool_name = str(step["tool"])
        args = dict(step.get("args") or {})
        if step.get("pass"):  # inject prior outputs for templating tools
            digest = "; ".join(
                f"{r['tool']}: {str(r.get('speech', ''))[:200]}" for r in results)
            key = step["pass"]
            args.setdefault(key, digest[:1500])
        if publish_progress:
            publish_progress(f"step {i + 1}/{len(wf['steps'])}: {tool_name}")
        r = tools.call(tool_name, args)
        results.append({"tool": tool_name, "ok": r.get("ok", False),
                        "speech": str(r.get("speech", ""))[:400]})
        if not r.get("ok", False) and not wf.get("continue_on_error"):
            break
    ok = all(r["ok"] for r in results)
    with _lock:
        _last_run[name] = time.time()
    db.audit("workflow_run", name, ok,
             "; ".join(f"{r['tool']}:{'ok' if r['ok'] else 'FAIL'}"
                       for r in results)[:250])
    return {"ok": ok, "results": results}


# ---------------- scheduling ----------------

def due_now() -> list[str]:
    """Names of workflows whose schedule is due (checked by kernel tick)."""
    import datetime as _dt

    now = _dt.datetime.now()
    due = []
    for name, wf in _all():
        sched = wf.get("schedule") or {}
        if "every_minutes" in sched:
            every = max(5, int(sched["every_minutes"]))
            with _lock:
                last = _last_run.get(name, 0)
            if time.time() - last >= every * 60:
                due.append(name)
        elif "daily" in sched:
            stamp = now.strftime("%Y%m%d")
            with _lock:
                last_day = _last_run.get(f"{name}@day", "")
            if last_day != stamp:
                due.append(name)
    return due


def mark_ran(name: str) -> None:
    import datetime as _dt

    with _lock:
        _last_run[name] = time.time()
        _last_run[f"{name}@day"] = _dt.datetime.now().strftime("%Y%m%d")


# ------------------------------------------------------------------ tools

from .tools import tool  # noqa: E402


@tool("workflow_create", "Create a reusable multi-step automation from a YAML spec (name, optional schedule daily/every_minutes, steps of tool+args).",
      {"spec_yaml": {"type": "string"}}, permission="execute")
def workflow_create_tool(spec_yaml: str) -> dict:
    ok, name, msg = create(spec_yaml)
    return {"ok": ok, "speech": msg, "data": {"name": name}}


@tool("workflow_run", "Run a saved workflow by name, executing its steps in order.",
      {"name": {"type": "string"}}, permission="execute")
def workflow_run_tool(name: str) -> dict:
    result = run(str(name))
    if not result.get("ok") and "error" in result:
        return {"ok": False, "speech": result["error"], "data": {}}
    lines = [f"{r['tool']}: {'OK' if r['ok'] else 'FAILED'}"
             for r in result.get("results", [])]
    return {"ok": result.get("ok", False),
            "speech": ("Workflow '" + str(name) + "' finished. "
                       + "; ".join(lines))[:600],
            "data": result}


@tool("workflow_list", "List saved workflows.", {}, permission="read")
def workflow_list() -> dict:
    items = [{"name": n, "description": w.get("description", ""),
              "schedule": w.get("schedule"),
              "steps": [s.get("tool") for s in w.get("steps", [])]}
             for n, w in _all()]
    if not items:
        return {"ok": True, "speech": "No workflows yet.", "data": {"workflows": []}}
    speech = "; ".join(i["name"] for i in items)
    return {"ok": True, "speech": f"Workflows: {speech}",
            "data": {"workflows": items}}


@tool("workflow_delete", "Delete a workflow by name.",
      {"name": {"type": "string"}}, permission="execute")
def workflow_delete(name: str) -> dict:
    ok = delete(str(name))
    return {"ok": ok,
            "speech": f"Deleted '{name}'." if ok else f"No workflow '{name}'.",
            "data": {}}
