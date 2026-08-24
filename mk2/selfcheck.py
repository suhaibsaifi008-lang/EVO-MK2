"""Phase 5.5: Self-check — EVO watches its own health and fixes itself.

Tick loop (kernel, every EVO_SELFCHECK_MINUTES, default 180):
  1. Run the full test suite (code_test).
  2. Scan the errlog ring for recurring runtime errors.
  3. Anything found -> notify.out summary.
  4. Red suite / recurring bug -> a dev task is spawned automatically to
     diagnose and stage a fix. Fixes are ALWAYS approval-gated proposals;
     with EVO_SELF_HEAL_AUTOAPPLY=1 the dev task may commit its own staged
     changes, protected by do_apply's backup/revert-on-red safety net.
"""
import os
import time
from collections import Counter

from . import db

_last_report = {"issues": []}
_healed_recent: dict[str, float] = {}
HEAL_COOLDOWN = 24 * 3600  # don't re-attempt the same issue within 24h


def _recurring_errors(threshold: int = 3) -> list[dict]:
    from . import errlog

    rows = errlog.recent(50)
    counts = Counter(r["component"] for r in rows)
    out = []
    for comp, n in counts.items():
        if n >= threshold:
            last = next((r for r in rows if r["component"] == comp), None)
            out.append({"type": "recurring_error", "component": comp,
                        "count": n,
                        "detail": f"{comp} failed {n}x - last: "
                                  f"{(last or {}).get('detail', '')[:150]}"})
    return out


def diagnose() -> dict:
    """Full self-inspection. Read-only; safe to run any time."""
    from . import coder

    issues = []
    tests = coder.do_test()
    if not tests["ok"]:
        if tests.get("infra"):
            issues.append({"type": "infra",
                           "detail": f"self-check could not run the suite: "
                                     f"{tests['summary']}"})
        else:
            issues.append({"type": "red_suite",
                           "detail": f"test suite RED: {tests['summary']}"})
    issues.extend(_recurring_errors())
    _last_report["issues"] = issues
    return {"healthy": not issues, "issues": issues, "tests": tests}


def heal(issue: dict) -> bool:
    """Spawn a dev task that stages (and per toggle, auto-applies) a fix.
    Same issue signature re-healed at most once per cooldown window."""
    from . import coder

    if issue["type"] == "infra":
        return False  # never auto-"fix" an environment problem
    sig = f"{issue['type']}:{issue.get('detail', '')[:80]}"
    last = _healed_recent.get(sig, 0)
    if time.time() - last < HEAL_COOLDOWN:
        return False
    goal = {
        "red_suite": "The pytest suite is failing. Diagnose the failures, "
                     "make the smallest correct fix, run code_test until GREEN.",
        "recurring_error": "A component keeps raising errors at runtime: "
                           + issue.get("detail", "")[:300]
                           + ". Find the root cause in the source and stage "
                             "a minimal fix; verify with code_test.",
    }.get(issue["type"], issue.get("detail", ""))
    if not goal:
        return False
    _healed_recent[sig] = time.time()
    coder.devtask_start(goal)
    return True


def tick() -> dict:
    """One self-check pass; heals what it can. Called by kernel tick."""
    report = diagnose()
    healed = []
    if os.environ.get("EVO_SELFCHECK_HEAL", "1") == "1":
        for issue in report["issues"]:
            if issue["type"] in ("red_suite", "recurring_error"):
                if heal(issue):
                    healed.append(issue["type"])
    if healed:
        db.audit("selfcheck_heal", ";".join(healed), True,
                 f"{len(report['issues'])} issue(s)")
    report["healed"] = healed
    return report


# ------------------------------------------------------------------ tools

from .tools import tool  # noqa: E402


@tool("selfcheck_now", "Inspect EVO's own health: full test suite + error ring. Read-only.",
      {}, permission="read")
def selfcheck_now() -> dict:
    r = diagnose()
    if r["healthy"]:
        return {"ok": True, "speech": "All clear: suite green, no recurring errors.",
                "data": r}
    lines = [f"{i['type']}: {i['detail'][:120]}" for i in r["issues"]]
    return {"ok": True,
            "speech": "Issues found: " + "; ".join(lines)[:500],
            "data": r}
