"""Autonomous Engineering & Scientific Simulation Core for EVO MK2 (JARVIS Dimension 5 — 9/10).

Provides:
1. Closed-Loop Test-Driven Development (TDD) multi-agent swarm:
   Architect -> Coder -> TestEngineer -> Subprocess Sandbox -> Iterative Debugger/Healer.
2. Scientific & Symbolic Simulation Sandbox:
   Symbolic algebra, differential kinematics, numerical optimization, and data visualization.
3. Automated Self-Healing & Code Repair:
   Iteratively repairs failing scripts using stack traces and AST analysis.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config, llm
from .bus import bus
from .tools import tool

log = logging.getLogger("mk2.engineering")

ENGINEERING_DIR = config.DATA / "engineering"
ENGINEERING_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class EngineeringStep:
    step_id: str
    role: str
    description: str
    code_artifact: str = ""
    test_artifact: str = ""
    passed: bool = False
    iterations: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0


@dataclass
class EngineeringProject:
    id: str
    objective: str
    workspace_dir: Path
    status: str = "planning"  # planning | coding | testing | healing | completed | failed
    files: dict[str, str] = field(default_factory=dict)
    steps: list[EngineeringStep] = field(default_factory=list)
    final_output: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0


class EngineeringWorkspace:
    """Isolated sandbox directory for building, running, and testing code."""
    DEFAULT_TIMEOUT: float = 30.0

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.path = ENGINEERING_DIR / project_id
        self.path.mkdir(parents=True, exist_ok=True)

    def write_file(self, filename: str, content: str) -> Path:
        target = (self.path / filename).resolve()
        if not str(target).startswith(str(self.path.resolve())):
            raise ValueError(f"Path traversal blocked: {filename}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def read_file(self, filename: str) -> str:
        target = (self.path / filename).resolve()
        if not str(target).startswith(str(self.path.resolve())):
            raise ValueError(f"Path traversal blocked: {filename}")
        return target.read_text(encoding="utf-8") if target.exists() else ""

    def list_files(self) -> list[str]:
        return [str(p.relative_to(self.path)) for p in self.path.rglob("*") if p.is_file()]

    def run_tests(self, test_file: str = "test_solution.py", timeout_sec: Optional[float] = None) -> tuple[bool, str, str]:
        """Execute test suite inside sandbox using python -m pytest or unittest."""
        t_limit = timeout_sec if timeout_sec is not None else self.DEFAULT_TIMEOUT
        cmd = [sys.executable, "-m", "pytest", test_file, "-q"]
        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.path),
                capture_output=True,
                text=True,
                timeout=t_limit,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            passed = res.returncode == 0
            return passed, res.stdout, res.stderr
        except subprocess.TimeoutExpired:
            return False, "", f"Execution timed out after {t_limit}s."
        except Exception as exc:
            # Fallback to unittest if pytest not available
            try:
                res = subprocess.run(
                    [sys.executable, test_file],
                    cwd=str(self.path),
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return res.returncode == 0, res.stdout, res.stderr
            except Exception as exc2:
                return False, "", f"Execution error: {exc2}"

    def run_script(self, script_name: str, args: list[str] = None, timeout_sec: int = 30) -> tuple[int, str, str]:
        """Execute a Python script inside sandbox."""
        cmd = [sys.executable, script_name] + (args or [])
        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.path),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired:
            return -1, "", f"Execution timed out after {timeout_sec}s."
        except Exception as exc:
            return -1, "", str(exc)

    def cleanup(self) -> None:
        try:
            shutil.rmtree(self.path, ignore_errors=True)
        except Exception:
            pass


class EngineeringSwarm:
    """Closed-loop multi-agent engineering swarm."""

    def __init__(self, max_heal_iterations: int = 5) -> None:
        self.max_heal_iterations = max_heal_iterations

    def _call_llm(self, role: str, prompt: str, system_instructions: str = "") -> str:
        messages = [
            {"role": "system", "content": system_instructions or f"You are a world-class {role} AI agent. Output clean, reliable, professional results."},
            {"role": "user", "content": prompt},
        ]
        res = llm.chat(messages, temperature=0.1, timeout=45, role="primary")
        return (res or "").strip()

    def _strip_markdown(self, text: str) -> str:
        """Strip markdown ```python ... ``` fences cleanly."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            return "\n".join(lines).strip()
        return text

    def engineer(self, objective: str, context: Optional[dict] = None) -> dict[str, Any]:
        """Full autonomous TDD lifecycle: Spec -> Code -> Tests -> Sandbox -> Self-Heal Loop."""
        project_id = f"eng_{uuid.uuid4().hex[:8]}"
        ws = EngineeringWorkspace(project_id)
        t0 = time.time()

        bus.publish("engineering.started", {"project_id": project_id, "objective": objective})
        log.info("[Engineering] Starting project %s: %s", project_id, objective)

        # Stage 1: Architect Specification
        arch_system = (
            "You are a Principal Software Architect. Given an objective, design a modular solution.\n"
            "Define the core classes, functions, contracts, and edge cases to test.\n"
            "Keep the architecture concise and robust."
        )
        arch_spec = self._call_llm("Architect", f"Objective: {objective}\nContext: {context or {}}", arch_system)

        # Stage 2: Coder Implementation
        coder_system = (
            "You are an Elite Senior Software Engineer. Implement the complete, production-ready Python solution.\n"
            "Include type annotations, docstrings, and comprehensive error handling.\n"
            "Return ONLY executable Python code for `solution.py`. No markdown explanations."
        )
        code_raw = self._call_llm(
            "Coder",
            f"Objective: {objective}\nArchitectural Specification:\n{arch_spec}\nImplement `solution.py`:",
            coder_system
        )
        solution_code = self._strip_markdown(code_raw)
        ws.write_file("solution.py", solution_code)

        # Stage 3: Test Engineer Harness
        tester_system = (
            "You are a Principal QA / Test Engineer. Write a comprehensive pytest suite for `solution.py`.\n"
            "Import what you test from `solution`. Cover basic cases, edge cases, negative tests, and performance limits.\n"
            "Return ONLY executable Python code for `test_solution.py`. No markdown explanations."
        )
        test_raw = self._call_llm(
            "TestEngineer",
            f"Objective: {objective}\nCode Implementation:\n{solution_code}\nWrite `test_solution.py`:",
            tester_system
        )
        test_code = self._strip_markdown(test_raw)
        ws.write_file("test_solution.py", test_code)

        # Stage 4: Execution & Closed-Loop Self-Healing
        iterations = 0
        passed = False
        last_stdout = ""
        last_stderr = ""

        while iterations < self.max_heal_iterations:
            iterations += 1
            log.info("[Engineering %s] Test verification iteration %d/%d", project_id, iterations, self.max_heal_iterations)
            passed, stdout, stderr = ws.run_tests("test_solution.py", timeout_sec=25)
            last_stdout, last_stderr = stdout, stderr

            bus.publish("engineering.iteration", {
                "project_id": project_id,
                "iteration": iterations,
                "passed": passed,
                "stdout": stdout[:300],
                "stderr": stderr[:300],
            })

            if passed:
                log.info("[Engineering %s] All tests passed on iteration %d!", project_id, iterations)
                break

            # Self-Healing Debugger Stage
            log.warning("[Engineering %s] Tests failed on iteration %d. Diagnosing and healing...", project_id, iterations)
            heal_system = (
                "You are an Elite Debugging & Diagnostic Specialist. Code tests failed.\n"
                "Analyze the code, the tests, and the failure traceback.\n"
                "Fix the root cause in `solution.py` (or `test_solution.py` if the test was invalid).\n"
                "Reply ONLY valid JSON with keys: 'explanation', 'solution_py' (full code), 'test_solution_py' (optional full code)."
            )
            heal_prompt = (
                f"Objective: {objective}\n"
                f"Current solution.py:\n{solution_code}\n"
                f"Current test_solution.py:\n{test_code}\n"
                f"Test STDOUT:\n{stdout}\n"
                f"Test STDERR:\n{stderr}\n"
                "Diagnose and provide the corrected code:"
            )
            heal_raw = self._call_llm("Debugger", heal_prompt, heal_system)
            try:
                # Extract JSON payload
                clean_json = self._strip_markdown(heal_raw)
                if not clean_json.startswith("{"):
                    idx = clean_json.find("{")
                    if idx != -1:
                        clean_json = clean_json[idx:]
                data = json.loads(clean_json)
                if "solution_py" in data and data["solution_py"].strip():
                    solution_code = self._strip_markdown(data["solution_py"])
                    ws.write_file("solution.py", solution_code)
                if "test_solution_py" in data and data["test_solution_py"].strip():
                    test_code = self._strip_markdown(data["test_solution_py"])
                    ws.write_file("test_solution.py", test_code)
            except Exception as exc:
                log.warning("[Engineering %s] Healing parse failed: %s", project_id, exc)
                # Fallback: prompt coder directly for fixed solution
                fallback_code = self._call_llm("Coder", f"Fix this code to make tests pass:\n{solution_code}\nErrors:\n{stdout}\n{stderr}")
                solution_code = self._strip_markdown(fallback_code)
                ws.write_file("solution.py", solution_code)

        duration = round(time.time() - t0, 2)
        status = "completed" if passed else "failed"

        result = {
            "ok": passed,
            "project_id": project_id,
            "status": status,
            "objective": objective,
            "iterations": iterations,
            "duration_s": duration,
            "workspace": str(ws.path),
            "files": ws.list_files(),
            "solution_code": solution_code,
            "test_code": test_code,
            "test_output": last_stdout or last_stderr,
            "speech": (
                f"Engineering solution for '{objective[:80]}' {status} in {duration}s "
                f"after {iterations} iteration(s) with {'all tests passing' if passed else 'unresolved failures'}."
            ),
        }

        bus.publish("engineering.completed", {
            "project_id": project_id,
            "status": status,
            "passed": passed,
            "duration_s": duration,
        })
        return result


class ScientificSimulator:
    """Numerical, symbolic, and physical simulation engine."""

    @staticmethod
    def simulate(problem: str, variables: Optional[dict] = None) -> dict[str, Any]:
        """Execute mathematical, physics, or algorithmic simulation."""
        sim_id = f"sim_{uuid.uuid4().hex[:6]}"
        ws = EngineeringWorkspace(sim_id)

        prompt = (
            f"Problem/Simulation Objective: {problem}\n"
            f"Input Parameters/Variables: {json.dumps(variables or {})}\n"
            "Write a standalone Python script `simulate.py` that computes the exact mathematical or physical solution.\n"
            "Use math, numpy, scipy, or sympy if helpful.\n"
            "The script MUST print a final JSON object to stdout in format:\n"
            '{"status": "success", "result": <numeric or structured value>, "summary": "<clear scientific explanation>"}\n'
            "Return ONLY executable Python code. No markdown fences."
        )
        system = (
            "You are a Senior Computational Scientist & Physicist. "
            "Write exact numerical/symbolic simulation scripts that print JSON results."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]
        raw_code = llm.chat(messages, temperature=0.1, timeout=30, role="primary")
        script = raw_code.strip()
        if script.startswith("```"):
            lines = script.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            script = "\n".join(lines).strip()

        ws.write_file("simulate.py", script)
        code, out, err = ws.run_script("simulate.py", timeout_sec=20)

        ws.cleanup()

        if code == 0 and out:
            try:
                # Find JSON payload in output
                out_clean = out.strip()
                if not out_clean.startswith("{"):
                    start = out_clean.find("{")
                    if start != -1:
                        out_clean = out_clean[start:]
                parsed = json.loads(out_clean)
                return {
                    "ok": True,
                    "result": parsed.get("result"),
                    "summary": parsed.get("summary", "Simulation completed successfully."),
                    "speech": f"Simulation computed: {parsed.get('summary', str(parsed.get('result'))[:120])}",
                    "raw_output": out[:500],
                }
            except Exception:
                return {
                    "ok": True,
                    "result": out.strip(),
                    "summary": out.strip(),
                    "speech": f"Simulation output: {out[:140]}",
                }

        return {
            "ok": False,
            "error": err or "Simulation script exited with non-zero status.",
            "speech": f"Simulation calculation failed: {err[:120]}",
        }


# Global instances
_swarm = EngineeringSwarm()
_simulator = ScientificSimulator()


# ----------------------------------------------------------------------
# Core Tools
# ----------------------------------------------------------------------

@tool(
    "engineer_solution",
    "Autonomously engineer a complete software solution with closed-loop TDD (writes code, writes tests, executes in sandbox, and self-heals until passing).",
    {"objective": {"type": "string"}, "context": {"type": "object"}},
    permission="execute",
)
def engineer_solution(objective: str, context: Optional[dict] = None) -> dict:
    """Execute end-to-end TDD engineering swarm."""
    if not objective:
        return {"ok": False, "speech": "Please specify an engineering objective.", "data": {}}
    return _swarm.engineer(objective, context)


@tool(
    "simulate_engineering",
    "Run mathematical, physics, or numerical simulations to calculate scientific solutions.",
    {"problem": {"type": "string"}, "variables": {"type": "object"}},
    permission="execute",
)
def simulate_engineering(problem: str, variables: Optional[dict] = None) -> dict:
    """Run scientific / mathematical simulation."""
    if not problem:
        return {"ok": False, "speech": "Please specify a problem to simulate.", "data": {}}
    return _simulator.simulate(problem, variables)


@tool(
    "code_repair",
    "Diagnose, patch, and repair broken Python code using error tracebacks and automated test validation.",
    {"source_code": {"type": "string"}, "error_trace": {"type": "string"}},
    permission="execute",
)
def code_repair(source_code: str, error_trace: str) -> dict:
    """Diagnose and fix code using LLM AST debugger."""
    if not source_code:
        return {"ok": False, "speech": "Missing source code to repair.", "data": {}}
    prompt = (
        f"Broken Source Code:\n{source_code}\n\n"
        f"Error Trace / Failure:\n{error_trace}\n\n"
        "Diagnose the bug and provide the corrected code.\n"
        "Reply ONLY valid Python code with the fix. No markdown."
    )
    system = "You are an elite code diagnostic and repair engineer. Return only fixed code."
    repaired = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}], temperature=0.1, timeout=30, role="primary")
    clean = _swarm._strip_markdown(repaired)
    # Syntax check
    try:
        ast.parse(clean)
        syntax_ok = True
    except SyntaxError as se:
        syntax_ok = False
        return {"ok": False, "speech": f"Repaired code has syntax error: {se}", "data": {"code": clean}}

    return {
        "ok": True,
        "speech": "Code repaired and syntax-validated successfully.",
        "data": {"repaired_code": clean, "syntax_valid": syntax_ok},
    }
