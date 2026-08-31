"""Skill Forge & Actionable Skill Distillation for EVO MK2.

1. Skill Forge: teach EVO permanent new abilities as Python scripts with AST validation.
2. Skill Distillation: extract tested, actionable procedures from deep research and conversation.
"""
from __future__ import annotations

import ast
import json
import logging
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .config import DATA

log = logging.getLogger("mk2.skills")

SKILLS_DIR = DATA / "skills"
SKILLS_DIR.mkdir(parents=True, exist_ok=True)
EXTRACTED_SKILLS_DIR = DATA / "extracted_skills"
EXTRACTED_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()

CONTRACT = (
    "Script receives args as ONE JSON string in sys.argv[1]. "
    "Do the work, print result to stdout, exit non-zero on failure."
)

# --- Phase 5: AST security audit -------------------------------------------
BANNED_MODULES = {
    "subprocess", "socket", "ctypes", "multiprocessing",
    "http.server", "socketserver", "winreg"
}
BANNED_NAMES = {
    "eval", "exec", "compile", "__import__", "system", "popen",
    "spawn", "fork", "kill"
}


def audit_code(code: str) -> None:
    """Raise ValueError if the code touches banned capabilities."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"syntax error: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if root in BANNED_MODULES or a.name in BANNED_MODULES:
                    raise ValueError(f"banned module '{a.name}'")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_MODULES or (node.module or "") in BANNED_MODULES:
                raise ValueError(f"banned module '{node.module}'")
        elif isinstance(node, ast.Attribute):
            if node.attr in BANNED_NAMES:
                raise ValueError(f"banned call '.{node.attr}()'")
        elif isinstance(node, (ast.Call,)):
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


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", text.lower().strip())[:40]


def _paths(name: str) -> tuple:
    clean = _slug(name)
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
        return {
            "ok": False,
            "speech": f"Test run FAILED (exit {r.returncode}): {r.stderr[-200:]}. Skill NOT saved.",
            "data": {},
        }

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
        payload = json.dumps(kwargs, ensure_ascii=False)
        proc = subprocess.run(
            [sys.executable, "-I", path, payload],
            capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            return {
                "ok": False,
                "speech": f"Skill failed (exit {proc.returncode}): {proc.stderr[-200:]}",
                "data": {},
            }
        return {"ok": True, "speech": proc.stdout.strip()[:600] or "(no output)", "data": {}}

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
    for py_path in sorted(SKILLS_DIR.glob("*.py")):
        try:
            _register(py_path.stem, str(py_path))
            count += 1
        except Exception:
            pass
    return count


# ---------------- Extracted Actionable Procedures (Skill Distillation) -----

class SkillExtractor:
    """Extracts actionable procedures from research and stores them for future proactive execution."""

    def __init__(self, skills_dir: Optional[Path] = None) -> None:
        self.skills_dir = skills_dir or EXTRACTED_SKILLS_DIR
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def extract_from_research(self, topic: str, content: str) -> list[dict[str, Any]]:
        """After deep research, extract 3-5 verified actionable procedures."""
        try:
            from . import llm
            prompt = (
                f'From this research on "{topic}":\n\n'
                f"{content[:3000]}\n\n"
                "Extract 3-5 ACTIONABLE PROCEDURES — things the user can actually DO.\n"
                "Each procedure should be:\n"
                "1. A clear action ('When X happens, do Y' or 'Step 1: ...')\n"
                "2. Specific enough to follow without additional research\n"
                "3. Tested/verified by sources\n\n"
                'Format as a numbered list. If no actionable procedures exist, say "None identified".'
            )
            result = llm.chat([
                {"role": "system", "content": "You are a skill extraction specialist. Extract only actionable, verified procedures."},
                {"role": "user", "content": prompt},
            ], role="fast", timeout=15)

            skills = []
            for line in (result or "").split("\n"):
                line = line.strip()
                if line and not line.lower().startswith("none"):
                    cleaned = re.sub(r"^[\d\.\-\*\•\s]+", "", line).strip()
                    if len(cleaned) > 10:
                        skills.append({
                            "topic": topic,
                            "procedure": cleaned,
                            "source": "deep_research",
                            "confidence": "high",
                            "uses": 0,
                            "created_at": time.time(),
                        })

            if skills:
                path = self.skills_dir / f"{_slug(topic)}.json"
                path.write_text(json.dumps(skills, indent=2), encoding="utf-8")

            return skills
        except Exception as exc:
            log.warning("Skill extraction failed: %s", exc)
            return []

    def get_relevant_skills(self, context: str) -> list[dict[str, Any]]:
        """Find skills relevant to current query or task using token overlap and difflib fuzzy matching."""
        relevant = []
        try:
            import difflib
            ctx_tokens = set(re.findall(r"\w{3,}", context.lower()))
            for f in self.skills_dir.glob("*.json"):
                try:
                    skills_list = json.loads(f.read_text(encoding="utf-8"))
                    for skill in skills_list:
                        proc = skill.get("procedure", "").lower()
                        topic = skill.get("topic", "").lower()
                        combined_text = f"{topic} {proc}"
                        skill_tokens = set(re.findall(r"\w{3,}", combined_text))

                        # 1. Exact token overlap
                        exact = len(ctx_tokens & skill_tokens)

                        # 2. Fuzzy match: check if any context token is close to any skill token
                        fuzzy = 0
                        for ct in ctx_tokens:
                            for st in skill_tokens:
                                if len(ct) > 3 and len(st) > 3:
                                    if difflib.SequenceMatcher(None, ct, st).ratio() > 0.8:
                                        fuzzy += 1

                        score = exact + fuzzy * 0.5
                        if score > 0:
                            entry = dict(skill)
                            entry["_match_score"] = score
                            relevant.append(entry)
                except Exception:
                    pass
            # Sort by match score descending
            relevant.sort(key=lambda x: x.get("_match_score", 0), reverse=True)
        except Exception as exc:
            log.debug("Skill query note: %s", exc)
        return relevant[:5]


_global_skill_extractor: Optional[SkillExtractor] = None


def get_skill_extractor() -> SkillExtractor:
    global _global_skill_extractor
    if _global_skill_extractor is None:
        _global_skill_extractor = SkillExtractor()
    return _global_skill_extractor


# ---------------- Tools Registration ---------------------------------------

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

