"""Phase 5: Habit detection — mine the audit ledger for repeated intents.

When the SAME tool+target repeats >= 3 times, propose an automation ONCE
(proposals table dedupes). Proposals surface as a notify.out toast and
wait for the user's explicit approval - nothing auto-runs.
"""
import json

from . import db

MIN_REPEATS = 3


def _signature(tool: str, target: str) -> str:
    return f"{tool}:{(target or '').strip().lower()[:80]}"


def scan() -> list[dict]:
    """Find repeated intents in recent audit rows; return new proposals."""
    rows = db.recent_audit(200)
    counts: dict[str, dict] = {}
    for r in rows:
        if r["tool"] not in ("open_app", "web_search", "close_app",
                             "screenshot", "deep_research"):
            continue
        try:
            args = json.loads(r["args_json"] or "{}")
        except Exception:
            continue
        target = str(args.get("target") or args.get("query")
                     or args.get("topic") or "")
        if not target or len(target) < 3:
            continue
        sig = _signature(r["tool"], target)
        entry = counts.setdefault(sig, {"tool": r["tool"], "target": target,
                                        "count": 0})
        entry["count"] += 1
    proposals = []
    for sig, e in counts.items():
        if e["count"] < MIN_REPEATS:
            continue
        detail = (f"You have used '{e['tool']}' with '{e['target']}' "
                  f"{e['count']}x recently.")
        pid = db.proposal_add("habit", sig, detail)
        if pid:
            proposals.append({"id": pid, "detail": detail,
                              "tool": e["tool"], "target": e["target"]})
    return proposals


def propose_habit(pattern: dict) -> str:
    """Turn a detected pattern into a specific, actionable proposal."""
    ptype = pattern.get("type", "")
    if ptype == "app_launch" or pattern.get("tool") == "open_app":
        app = pattern.get("app") or pattern.get("target", "this application")
        count = pattern.get("count", 3)
        usual_time = pattern.get("usual_time", "this time")
        return f"You have launched {app} {count} times recently around {usual_time}. Shall I schedule it to open automatically?"
    elif ptype == "tool_sequence":
        tools_seq = pattern.get("tools", "")
        name = pattern.get("suggested_name", "daily_routine")
        return f"I noticed you run [{tools_seq}] in sequence frequently. Would you like me to save this as a '{name}' workflow?"
    return f"I noticed a repeating pattern: {pattern.get('detail', '')}. Would you like me to automate it?"


def _workflow_yaml_for(tool: str, target: str, name_hint: str) -> str:
    step = {"tool": tool}
    if tool == "open_app":
        step["args"] = {"target": target}
    elif tool in ("web_search", "deep_research"):
        key = "query" if tool == "web_search" else "topic"
        step["args"] = {key: target}
    import yaml

    return yaml.safe_dump({
        "name": name_hint,
        "description": f"Habit automation: {tool} {target}",
        "steps": [step],
    }, sort_keys=False)


# ------------------------------------------------------------------ tools

from .tools import tool  # noqa: E402


@tool("proposals_list", "Show pending automation suggestions EVO noticed.", {},
      permission="read")
def proposals_list() -> dict:
    rows = db.proposals()
    if not rows:
        return {"ok": True, "speech": "No pending suggestions.",
                "data": {"proposals": []}}
    lines = [f"#{r['id']}: {r['detail']}" for r in rows]
    return {"ok": True,
            "speech": ("Suggestions waiting for your yes: "
                       + "; ".join(lines))[:600],
            "data": {"proposals": rows}}


@tool("proposal_approve", "Approve a suggestion by id - turns it into a scheduled workflow.",
      {"id": {"type": "integer"}}, permission="execute")
def proposal_approve(id: int) -> dict:
    rows = [p for p in db.proposals(status="pending", limit=50)
            if p["id"] == int(id)]
    if not rows:
        return {"ok": False, "speech": f"No pending proposal #{id}.", "data": {}}
    p = rows[0]
    # recover tool/target from the audit signature embedded in detail
    try:
        sig_tool = p["detail"].split("'")[1]
        sig_target = p["detail"].split("'")[3]
    except Exception:
        return {"ok": False, "speech": "Proposal is malformed.", "data": {}}
    from . import workflows

    yaml_spec = _workflow_yaml_for(sig_tool, sig_target,
                                   f"habit_{sig_tool}_{abs(hash(sig_target)) % 10000}")
    ok, name, msg = workflows.create(yaml_spec)
    if not ok:
        return {"ok": False, "speech": msg, "data": {}}
    db.proposal_set_status(int(id), "approved")
    return {"ok": True,
            "speech": f"Approved. Workflow '{name}' created from that habit.",
            "data": {"workflow": name}}


@tool("proposal_reject", "Dismiss a suggestion permanently.",
      {"id": {"type": "integer"}}, permission="execute")
def proposal_reject(id: int) -> dict:
    ok = db.proposal_set_status(int(id), "rejected")
    return {"ok": ok,
            "speech": "Dismissed." if ok else f"No proposal #{id}.",
            "data": {}}
