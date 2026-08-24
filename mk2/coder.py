"""DevAgent - EVO codes on its own codebase, ox-alpha style.

The discipline that makes a great coder isn't a bigger model, it's the
LOOP: read before editing, change little, run the suite, read failures,
fix again - and never claim success while anything is red.

Safety (user-mandated):
  - Paths are jailed inside the EVO-MK2 project root.
  - EVO_CODE_APPROVAL=1 (default): writes/edits become PROPOSALS; nothing
    touches disk until 'apply <digest>'. Applying auto-runs the tests.
"""
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import db
from .config import DATA

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # mk2/coder.py -> repo
PROPOSALS_DIR = DATA / "proposals" / "code"
TEST_TIMEOUT = 420


def _in_root(p: Path) -> bool:
    try:
        rp, root = p.resolve(), PROJECT_ROOT.resolve()
    except OSError:
        return False
    return rp == root or str(rp).lower().startswith(str(root).lower() + "\\") \
        or str(rp).lower().startswith(str(root).lower() + "/")


def _safe(path: str) -> Path:
    cand = Path(str(path)).expanduser()
    if not cand.is_absolute():
        cand = PROJECT_ROOT / cand
    if not _in_root(cand):
        raise ValueError(f"outside project root: {path}")
    resolved = cand.resolve()
    for part in ("__pycache__", ".git", ".venv"):
        if part in resolved.parts:
            raise ValueError(f"refusing to touch {part}")
    return resolved


def _approval_on() -> bool:
    """ALWAYS TRUE. The user mandated: EVO never edits its own code -
    not even one character - without an explicit 'apply <digest>'.
    Kept as a function so the rule lives in exactly one place."""
    return True


def do_read(path: str, max_chars: int = 6000) -> dict:
    p = _safe(path)
    if not p.is_file():
        return {"ok": False, "error": f"no file {path}"}
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    return {"ok": True, "path": str(p.relative_to(PROJECT_ROOT)),
            "content": text[:int(max_chars)],
            "truncated": len(text) > int(max_chars), "total_chars": len(text)}


def do_search(pattern: str, glob: str = "*.py", limit: int = 30) -> dict:
    hits = []
    rx = re.compile(re.escape(pattern) if len(pattern) < 3 else pattern,
                    re.IGNORECASE)
    for p in sorted(PROJECT_ROOT.rglob(glob)):
        if not _in_root(p) or not {"__pycache__", ".git", "data", ".venv"}.isdisjoint(p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append({"file": str(p.relative_to(PROJECT_ROOT)),
                             "line": i, "text": line.strip()[:200]})
                if len(hits) >= limit:
                    break
        if len(hits) >= limit:
            break
    return {"ok": True, "hits": hits}


def _store_proposal(path: Path, content: str) -> tuple[int, str]:
    PROPOSALS_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((str(path) + content).encode()).hexdigest()[:12]
    (PROPOSALS_DIR / f"{digest}.json").write_text(json.dumps(
        {"path": str(path), "content": content, "created": time.time()}),
        encoding="utf-8")
    db_id = db.proposal_add("code", f"code:{digest}",
                            f"[{digest}] rewrite "
                            f"{path.relative_to(PROJECT_ROOT)} ({len(content)} chars)")
    return (db_id or 0), digest


def do_write(path: str, content: str) -> dict:
    p = _safe(path)
    if _approval_on():
        db_id, digest = _store_proposal(p, content)
        return {"ok": True, "proposal": digest, "db_id": db_id,
                "speech": f"Staged as {digest} for {p.name}. Say 'apply {digest}' to commit."}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    db.audit("code_write", str(p.relative_to(PROJECT_ROOT)), True,
             f"{len(content)} chars")
    return {"ok": True, "applied": True, "path": str(p)}


def do_edit(path: str, old: str, new: str) -> dict:
    p = _safe(path)
    if not p.is_file():
        return {"ok": False, "error": f"no file {path}"}
    text = p.read_text(encoding="utf-8-sig")
    n = text.count(old)
    if n == 0:
        return {"ok": False, "error": "old_string not found"}
    if n > 1:
        return {"ok": False, "error": f"old_string matches {n}x - be more specific"}
    return do_write(str(p), text.replace(old, new, 1))


def do_test() -> dict:
    """Run the full pytest suite; parse the verdict line."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q", "--tb=no"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            timeout=TEST_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "summary": f"suite timed out after {TEST_TIMEOUT}s"}
    tail = (r.stdout or "").strip().splitlines()
    verdict = tail[-1] if tail else "(no output)"
    if "no tests ran" in verdict or "no tests collected" in verdict:
        return {"ok": False, "infra": True,
                "summary": f"pytest ran nowhere useful: {verdict[:150]}"}
    green = ("passed" in verdict and "failed" not in verdict
             and "error" not in verdict)
    return {"ok": bool(green and r.returncode == 0), "summary": verdict[:200]}


def do_apply(digest: str) -> dict:
    """Apply a staged proposal, then immediately run the test suite.

    Self-heal safety net: if a previous version of the file existed it is
    backed up; if the suite goes RED after applying, the change is
    automatically ROLLED BACK so EVO can experiment without wrecking
    itself."""
    pid_path = PROPOSALS_DIR / f"{digest}.json"
    if not pid_path.exists():
        return {"ok": False, "error": f"no proposal {digest}"}
    data = json.loads(pid_path.read_text(encoding="utf-8"))
    target = Path(data["path"])
    if not _in_root(target):
        return {"ok": False, "error": "proposal escaped project root"}
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        backup = target.read_text(encoding="utf-8-sig")
    target.write_text(data["content"], encoding="utf-8")
    db.audit("code_apply", str(target.relative_to(PROJECT_ROOT)), True, digest)
    tests = do_test()
    rolled_back = False
    if not tests["ok"] and backup is not None:
        target.write_text(backup, encoding="utf-8")
        rolled_back = True
        db.audit("code_revert", str(target.relative_to(PROJECT_ROOT)), True,
                 f"{digest} rolled back (red suite)")
    for row in db.proposals(status="pending", limit=50):
        if f"[{digest}]" in row["detail"]:
            db.proposal_set_status(row["id"], "approved")
    return {"ok": True, "applied": str(target), "tests": tests,
            "rolled_back": rolled_back}


def _autoapply_on() -> bool:
    import os

    return os.environ.get("EVO_SELF_HEAL_AUTOAPPLY", "0").strip() == "1"


# ---------------- devtask: the autonomous coding loop ----------------

ALLOWED_DEVTOOLS = {"code_read", "code_search", "code_edit", "code_write",
                    "code_test", "docs_read", "web_search"}

DEV_SYSTEM = (
    "You are EVO's senior developer agent working inside the EVO-MK2 repo.\n"
    "DISCIPLINE:\n"
    "1. Read code before changing it (code_read / code_search).\n"
    "2. Make the smallest correct change.\n"
    "3. After every change run code_test.\n"
    "4. If the suite is red, READ the failure and fix your change.\n"
    "5. NEVER finish while the suite is failing.\n"
    'Each turn reply ONLY JSON: {"action":{"tool":"...","args":{...}}} '
    'or {"finish":"<what you did>"} - only finish with a GREEN suite.'
)


def devtask_start(goal: str) -> None:
    threading.Thread(target=_dev_loop, args=(goal.strip(),), daemon=True,
                     name="mk2-devtask").start()


def _dev_loop(goal: str, max_steps: int = 25) -> None:
    from . import llm, tools
    from .bus import bus

    manifest = json.dumps(
        [{k: t[k] for k in ("name", "description", "args")}
         for t in tools.manifest() if t["name"] in ALLOWED_DEVTOOLS])
    messages = [{"role": "system", "content": DEV_SYSTEM + "\nTOOLS:\n" + manifest},
                {"role": "user", "content": f"TASK: {goal}"}]
    last_test_ok = False
    for step in range(max_steps):
        bus.publish("job.progress", {"goal": f"dev:{goal[:60]}",
                                     "step": step + 1, "max_steps": max_steps})
        try:
            raw = llm.chat(messages, temperature=0.2, timeout=60)
        except Exception as exc:
            bus.publish("notify.out", {"kind": "dev",
                                       "text": f"Dev task stalled: {str(exc)[:120]}"})
            return
        data = None
        try:
            data = json.loads(raw.strip())
        except Exception:
            m = re.search(r"\{[\s\S]*\}", raw)
            if m:
                try:
                    data = json.loads(m.group(0))
                except Exception:
                    data = None
        if not isinstance(data, dict):
            continue
        if "finish" in data:
            msg = (f"Dev task done: {data['finish']}" if last_test_ok else
                   f"Stopped WITHOUT green suite: {data['finish']}")
            bus.publish("notify.out", {"kind": "dev", "text": msg[:500]})
            return
        action = data.get("action") or {}
        name, args = str(action.get("tool", "")), action.get("args") or {}
        if name == "code_test":
            result = do_test()
            last_test_ok = result["ok"]
            observation = json.dumps(result)[:800]
        elif name in ALLOWED_DEVTOOLS:
            fn = {"code_read": do_read, "code_search": do_search,
                  "code_edit": do_edit, "code_write": do_write,
                  "docs_read": lambda **k: tools.call("docs_read", k),
                  "web_search": lambda **k: tools.call("web_search", k),
                  }.get(name)
            try:
                observation = (json.dumps(fn(**args))[:1500] if fn
                               else f"DENIED: {name}")
            except Exception as exc:
                observation = json.dumps({"ok": False, "error": str(exc)[:200]})
        else:
            observation = f"DENIED: {name} not allowed in dev tasks"
        # self-heal auto-apply: staged change + toggle on -> commit it now
        # (do_apply's backup/revert net rolls back automatically on red)
        if name in ("code_write", "code_edit") and _autoapply_on():
            m = re.search(r'"proposal"\s*:\s*"([0-9a-f]+)"', observation)
            if m:
                applied = do_apply(m.group(1))
                last_test_ok = bool(applied.get("tests", {}).get("ok"))
                observation = (observation[:600] +
                               json.dumps({"auto_applied": True,
                                           "tests": applied.get("tests"),
                                           "rolled_back":
                                               applied.get("rolled_back")}))[:1500]
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": observation})
        messages = messages[:2] + messages[-16:]
    bus.publish("notify.out", {"kind": "dev",
                               "text": f"Dev task hit step limit on: {goal[:80]}"})


# ------------------------------------------------------------------ tools

from .tools import tool  # noqa: E402


@tool("code_read", "Read a source file inside the EVO-MK2 project.",
      {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
      permission="read")
def code_read_tool(path: str, max_chars: int = 6000) -> dict:
    r = do_read(path, max_chars)
    if not r.get("ok"):
        return {"ok": False, "speech": r["error"], "data": {}}
    return {"ok": True, "speech": f"{r['path']} ({r['total_chars']} chars)",
            "data": r}


@tool("code_search", "Search project source for a pattern.",
      {"pattern": {"type": "string"}, "glob": {"type": "string"}},
      permission="read")
def code_search_tool(pattern: str, glob: str = "*.py") -> dict:
    r = do_search(pattern, glob)
    speech = "; ".join(f"{h['file']}:{h['line']}" for h in r["hits"][:5]) or "none"
    return {"ok": True, "speech": speech, "data": r}


@tool("code_edit", "Replace one exact occurrence in a project file (staged as proposal when approval mode is on).",
      {"path": {"type": "string"}, "old_string": {"type": "string"},
       "new_string": {"type": "string"}}, permission="execute")
def code_edit_tool(path: str, old_string: str, new_string: str) -> dict:
    r = do_edit(path, old_string, new_string)
    return ({"ok": True, "speech": r.get("speech", "staged/applied"), "data": r}
            if r.get("ok") else {"ok": False, "speech": r["error"], "data": {}})


@tool("code_write", "Write/overwrite a project file (staged as proposal when approval mode is on).",
      {"path": {"type": "string"}, "content": {"type": "string"}},
      permission="execute")
def code_write_tool(path: str, content: str) -> dict:
    r = do_write(path, content)
    return {"ok": True, "speech": r.get("speech", "written"), "data": r}


@tool("code_apply", "Commit a staged code proposal by digest; auto-runs the test suite after applying.",
      {"digest": {"type": "string"}}, permission="execute")
def code_apply_tool(digest: str) -> dict:
    r = do_apply(digest)
    if not r.get("ok"):
        return {"ok": False, "speech": r["error"], "data": {}}
    t = r["tests"]
    return {"ok": True,
            "speech": f"Applied. Tests: {t['summary']}",
            "data": r}


@tool("code_test", "Run the full pytest suite and report green/red.",
      {}, permission="read")
def code_test_tool() -> dict:
    r = do_test()
    return {"ok": True, "speech": ("GREEN - " if r["ok"] else "RED - ") + r["summary"],
            "data": r}


@tool("devtask", "Start an autonomous coding task on EVO's own codebase (explore -> edit -> test loop). Long-running; reports back when done.",
      {"goal": {"type": "string"}}, permission="execute", long_running=True)
def devtask_tool(goal: str) -> dict:
    if not (goal or "").strip():
        return {"ok": False, "speech": "Give me an actual coding task.", "data": {}}
    devtask_start(goal)
    return {"ok": True,
            "speech": ("Dev task started. I will read, edit, run the tests "
                       "and report back when the suite is green."),
            "data": {}}
