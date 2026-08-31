"""Skill Forge: teach EVO permanent new abilities as Python scripts.

Skills are stored in DATA/skills/<name>.py with a .json meta file.
They auto-register as tools and survive restarts. Hardened from MK1:
AST validation before save, optional test-run, never registers broken code.
"""
import ast
import json
import subprocess
import sys
import threading
from pathlib import Path

from .config import DATA

SKILLS_DIR = DATA / "skills"
_lock = threading.Lock()

CONTRACT = (
    "Script receives args as ONE JSON string in sys.argv[1]. "
    "Do the work, print result to stdout, exit non-zero on failure."
)

# --- Phase 5: AST security audit -------------------------------------------
# Skills are code EVO wrote for itself. Before anything is saved it must
# pass a static audit: no process spawning, no raw sockets, no dynamic
# execution, no filesystem escapes beyond the data dir. This is a
# blocklist, not a sandbox - it raises the bar sharply and pairs with the
# test-run gate below.
BANNED_MODULES = {"subprocess", "socket", "ctypes", "multiprocessing",
                  "http.server", "socketserver", "winreg", "os", "shutil"}
BANNED_NAMES = {"eval", "exec", "compile", "__import__", "system", "popen",
                "spawn", "fork", "kill", "getattr", "setattr", "globals", "locals", "delattr", "vars"}

def audit_code(code: str) -> None:
    """Raise ValueError if the code touches banned capabilities."""
    import ast as _ast

    try:
        tree = _ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"syntax error: {exc}") from exc
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in BANNED_MODULES or a.name in BANNED_MODULES:
                    raise ValueError(f"banned module '{a.name}'")
        elif isinstance(node, _ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_MODULES or (node.module or "") in BANNED_MODULES:
                raise ValueError(f"banned module '{node.module}'")
        elif isinstance(node, _ast.Attribute):
            if node.attr in BANNED_NAMES:
                raise ValueError(f"banned call '.{node.attr}()'")
            if node.attr in ('__class__', '__bases__', '__subclasses__', '__mro__', '__builtins__', '__globals__', '__code__'):
                raise ValueError(f"banned attribute '.{node.attr}'")
        elif isinstance(node, (_ast.Call,)):
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name in BANNED_NAMES:
                raise ValueError(f"banned call '{name}()'")


def validate_code(code: str) -> None:
    """Single gate used by save(): syntax + security audit."""
    try:
        ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"syntax error: {exc}") from exc
    audit_code(code)


def _paths(name: str) -> tuple:
    clean = name.strip().lower().replace(" ", "_")
    import re

    clean = re.sub(r"[^a-z0-9_]", "", clean)[:40]
    base = SKILLS_DIR / clean
    return base.with_suffix(".py"), base.with_suffix(".json"), clean


def _paths(name: str) -> tuple:
    clean = name.strip().lower().replace(" ", "_")
    import re

    clean = re.sub(r"[^a-z0-9_]", "", clean)[:40]
    base = SKILLS_DIR / clean
    return base.with_suffix(".py"), base.with_suffix(".json"), clean


def save(name: str, description: str, code: str) -> dict:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        validate_code(code)
    except ValueError as exc:
        return {"ok": False, "speech": f"Rejected: {exc}", "data": {}}

    py_path, json_path, clean = _paths(name)
    header = f'"""{description.strip()[:280]}\n\n{CONTRACT}"""\n'
    py_path.write_text(header + code.lstrip("\n"), encoding="utf-8")
    json_path.write_text(json.dumps({"description": description[:300]}), encoding="utf-8")

    # test-run
    r = subprocess.run(
        [sys.executable, "-I", str(py_path), "{}"],
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if r.returncode != 0:
        py_path.unlink(missing_ok=True)
        json_path.unlink(missing_ok=True)
        return {"ok": False,
                "speech": f"Test run FAILED (exit {r.returncode}): {r.stderr[-200:]}. Skill NOT saved.",
                "data": {}}

    _register(clean, str(py_path))
    return {"ok": True, "speech": f"Skill '{clean}' saved and armed.", "data": {}}


def delete(name: str) -> bool:
    py_path, json_path, clean = _paths(name)
    existed = False
    for p in (py_path, json_path):
        if p.exists():
            p.unlink()
            existed = True
    from .tools import _REGISTRY

    _REGISTRY.pop(f"skill_{clean}", None)
    return existed


def list_skills() -> list[dict]:
    out = []
    if not SKILLS_DIR.exists():
        return out
    for p in sorted(SKILLS_DIR.glob("*.py")):
        meta_p = p.with_suffix(".json")
        desc = ""
        if meta_p.exists():
            try:
                desc = json.loads(meta_p.read_text(encoding="utf-8")).get("description", "")
            except Exception:
                pass
        out.append({"name": p.stem, "description": desc})
    return out


def _register(clean: str, path: str) -> None:
    """Register a saved skill script as an invocable tool."""
    from .tools import Tool, _REGISTRY

    def invoke(**kwargs) -> dict:
        import json as _json

        payload = _json.dumps(kwargs, ensure_ascii=False)
        proc = subprocess.run(
            [sys.executable, "-I", path, payload],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            return {"ok": False,
                    "speech": f"Skill failed (exit {proc.returncode}): {proc.stderr[-200:]}",
                    "data": {}}
        return {"ok": True, "speech": proc.stdout.strip()[:600] or "(no output)",
                "data": {}}

    meta_p = Path(path).with_suffix(".json")
    desc = ""
    if meta_p.exists():
        try:
            desc = json.loads(meta_p.read_text(encoding="utf-8")).get("description", "")
        except Exception:
            pass

    t = Tool(
        name=f"skill_{clean}",
        description=f"[learned skill] {desc or clean}",
        args_schema={},
        permission="execute",
        fn=invoke,
    )
    with _lock:
        _REGISTRY[t.name] = t


def load_all() -> int:
    """Register all saved skills on boot."""
    if not SKILLS_DIR.exists():
        return 0
    count = 0
    import logging
    _log = logging.getLogger('mk2.skills')
    for py_path in sorted(SKILLS_DIR.glob("*.py")):
        try:
            code = py_path.read_text(encoding='utf-8')
            validate_code(code)
            _register(py_path.stem, str(py_path))
            count += 1
        except ValueError as ve:
            _log.warning('Skipping skill %s: validation failed: %s', py_path.stem, ve)
        except Exception as exc:
            _log.warning('Skipping skill %s: %s', py_path.stem, exc)
    return count


# tools ------------------------------------------------------------------

from .tools import tool  # noqa: E402


@tool("skill_save", "Teach a new permanent ability as Python code. Test-runs before saving.",
      {"name": {"type": "string"}, "description": {"type": "string"},
       "code": {"type": "string"}}, permission="execute")
def skill_save_tool(name: str, description: str, code: str) -> dict:
    return save(name, description, code)


@tool("skill_list", "List all learned skills.", {}, permission="read")
def skill_list() -> dict:
    rows = list_skills()
    if not rows:
        return {"ok": True, "speech": "No learned skills yet.", "data": {}}
    speech = "; ".join(r["name"] for r in rows[:8])
    return {"ok": True, "speech": f"Learned skills: {speech}", "data": {"skills": rows}}


@tool("skill_delete", "Delete a learned skill permanently.",
      {"name": {"type": "string"}}, permission="execute")
def skill_delete(name: str) -> dict:
    ok = delete(name)
    return {"ok": ok, "speech": f"'{name}' deleted." if ok else f"No skill '{name}'.",
            "data": {}}
