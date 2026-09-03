"""Zero-Shot Dynamic Tool Synthesizer for EVO MK2.

Allows EVO to generate, sandbox, validate, compile, persist, and hot-reload
new Python tools into the active kernel runtime without rebooting.
"""
import ast
import importlib.util
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import config
from .bus import bus
from .tools import Tool, _lock, _REGISTRY, tool

log = logging.getLogger("mk2.tool_synthesizer")

DYNAMIC_TOOLS_DIR = config.DATA / "tools" / "dynamic"
_synthesized_tools_meta: dict[str, dict] = {}


def _validate_code_ast(code: str, expected_func_name: str) -> tuple[bool, str]:
    """Validate Python syntax and check full security boundaries using skills audit."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"

    from . import skills
    try:
        skills.audit_code(code)
    except ValueError as ve:
        return False, f"Security audit rejected synthesized tool: {ve}"

    found_func = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == expected_func_name:
            found_func = True

    if not found_func:
        return False, f"Function '{expected_func_name}' was not found in the synthesized code."

    return True, "AST validation passed"


def _heal_code(name: str, code: str, error: str) -> str:
    """Closed-loop LLM repair for failing synthesized tools."""
    try:
        from . import llm
        prompt = (
            f"The synthesized Python tool '{name}' failed with error:\n{error}\n\n"
            f"Code:\n{code}\n\n"
            f"Fix the bug so it runs properly. Must define callable function '{name}'.\n"
            "Return ONLY executable Python code. No markdown fences."
        )
        res = llm.chat([
            {"role": "system", "content": "You are a code repair engineer. Output only fixed Python code."},
            {"role": "user", "content": prompt}
        ], temperature=0.1, timeout=20, role="primary")
        clean = (res or "").strip()
        if clean.startswith("```"):
            lines = clean.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            clean = "\n".join(lines).strip()
        return clean
    except Exception:
        return code


def synthesize_tool(
    name: str,
    description: str,
    python_code: str,
    test_args: Optional[dict] = None,
    permission: str = "execute",
    args_schema: Optional[dict] = None,
) -> dict[str, Any]:
    """Synthesize, validate, register, and hot-reload a new tool at runtime."""
    name = "".join(c if c.isalnum() or c == "_" else "_" for c in (name or "").strip()).lower()
    if not name:
        return {"ok": False, "speech": "Invalid tool name provided.", "data": {}}

    # 1. AST Validation & Security Sandbox Check
    valid, msg = _validate_code_ast(python_code, name)
    if not valid:
        log.warning("Tool synthesis validation failed for '%s': %s", name, msg)
        return {"ok": False, "speech": f"Tool code failed validation: {msg}", "data": {"error": msg}}

    # 2. Persist to data/tools/dynamic/<name>.py
    DYNAMIC_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    tool_file = DYNAMIC_TOOLS_DIR / f"{name}.py"
    try:
        tool_file.write_text(python_code, encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "speech": f"Could not write tool file: {exc}", "data": {"error": str(exc)}}

    # 3. Subprocess Sandbox: test code in isolated process before hot-loading
    try:
        import subprocess
        sandbox_result = subprocess.run(
            [sys.executable, "-I", "-c", f"import ast; compile({python_code!r}, '<sandbox>', 'exec')"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if sandbox_result.returncode != 0:
            err_msg = sandbox_result.stderr.strip()[:200]
            return {"ok": False, "speech": f"Sandbox compilation failed: {err_msg}", "data": {"error": err_msg}}
    except subprocess.TimeoutExpired:
        return {"ok": False, "speech": "Sandbox compilation timed out (5s limit).", "data": {"error": "timeout"}}
    except Exception as exc:
        log.warning("Sandbox pre-check failed: %s", exc)

    # 4. Dynamic Import & Hot Reload
    try:
        mod_name = f"mk2_dynamic_{name}"
        spec = importlib.util.spec_from_file_location(mod_name, str(tool_file))
        if spec is None or spec.loader is None:
            return {"ok": False, "speech": "Failed to create module specification for tool.", "data": {}}
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)

        target_fn = getattr(module, name, None)
        if not callable(target_fn):
            return {"ok": False, "speech": f"Attribute '{name}' is not callable.", "data": {}}

        # 4. Optional Test Execution with Self-Healing Retry
        test_speech = ""
        if test_args is not None:
            try:
                test_res = target_fn(**test_args)
                test_speech = f" Test call succeeded: {str(test_res)[:80]}."
            except Exception as test_exc:
                log.warning("Test invocation for synthesized tool '%s' failed: %s. Initiating self-healing...", name, test_exc)
                # Closed-Loop Self-Healing Attempt
                healed_code = _heal_code(name, python_code, str(test_exc))
                valid, msg = _validate_code_ast(healed_code, name)
                if valid:
                    try:
                        tool_file.write_text(healed_code, encoding="utf-8")
                        spec = importlib.util.spec_from_file_location(mod_name, str(tool_file))
                        if spec and spec.loader:
                            module = importlib.util.module_from_spec(spec)
                            sys.modules[mod_name] = module
                            spec.loader.exec_module(module)
                            healed_fn = getattr(module, name, None)
                            if callable(healed_fn):
                                test_res = healed_fn(**test_args)
                                target_fn = healed_fn
                                test_speech = f" (Self-healed) Test call succeeded: {str(test_res)[:80]}."
                                log.info("Tool '%s' successfully self-healed!", name)
                    except Exception as second_exc:
                        log.warning("Self-healing second test failed: %s", second_exc)
                        return {
                            "ok": False,
                            "speech": f"Tool test failed and self-healing failed: {second_exc}",
                            "data": {"error": str(second_exc)},
                        }
                else:
                    return {
                        "ok": False,
                        "speech": f"Tool compiled but test execution failed: {test_exc}",
                        "data": {"error": str(test_exc)},
                    }

        # 5. Hot-register into active _REGISTRY
        schema = args_schema or {}
        new_tool_obj = Tool(
            name=name,
            description=description,
            args_schema=schema,
            permission=permission,
            fn=target_fn,
            long_running=False,
        )
        with _lock:
            _REGISTRY[name] = new_tool_obj

        _synthesized_tools_meta[name] = {
            "name": name,
            "description": description,
            "permission": permission,
            "created_at": time.time(),
            "path": str(tool_file),
        }

        bus.publish("tools.synthesized", {
            "name": name,
            "description": description,
            "permission": permission,
        })

        log.info("Successfully synthesized and hot-loaded dynamic tool: '%s'", name)
        return {
            "ok": True,
            "speech": f"Synthesized and registered dynamic tool '{name}'.{test_speech} Ready for immediate use.",
            "data": {"name": name, "description": description},
        }

    except Exception as exc:
        log.exception("Error executing synthesized module '%s': %s", name, exc)
        return {"ok": False, "speech": f"Compilation error: {exc}", "data": {"error": str(exc)}}


def load_all_dynamic_tools() -> int:
    """Scan and load all previously synthesized dynamic tools from disk."""
    if not DYNAMIC_TOOLS_DIR.exists():
        return 0

    count = 0
    for f in DYNAMIC_TOOLS_DIR.glob("*.py"):
        tool_name = f.stem
        if tool_name.startswith("_"):
            continue
        try:
            code = f.read_text(encoding="utf-8")
            valid, msg = _validate_code_ast(code, tool_name)
            if not valid:
                log.warning("Skipping untrusted dynamic tool %s: %s", f.name, msg)
                continue
            mod_name = f"mk2_dynamic_{tool_name}"
            spec = importlib.util.spec_from_file_location(mod_name, str(f))
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
                target_fn = getattr(module, tool_name, None)
                if callable(target_fn):
                    # Check if already registered by decorator or needs manual insertion
                    with _lock:
                        if tool_name not in _REGISTRY:
                            doc = (target_fn.__doc__ or f"Dynamic tool {tool_name}").strip()
                            _REGISTRY[tool_name] = Tool(
                                name=tool_name,
                                description=doc,
                                args_schema={},
                                permission="execute",
                                fn=target_fn,
                            )
                    _synthesized_tools_meta[tool_name] = {
                        "name": tool_name,
                        "description": getattr(target_fn, "__doc__", "") or tool_name,
                        "permission": "execute",
                        "created_at": f.stat().st_mtime,
                        "path": str(f),
                    }
                    count += 1
        except Exception as exc:
            log.warning("Failed to load dynamic tool %s: %s", f.name, exc)

    if count:
        log.info("Loaded %d synthesized dynamic tool(s) from %s", count, DYNAMIC_TOOLS_DIR)
    return count


def list_dynamic_tools() -> list[dict]:
    """Return all synthesized dynamic tools."""
    return list(_synthesized_tools_meta.values())


# ---------------- Built-in Meta Tools ----------------

@tool(
    name="tool_synthesize",
    description="Synthesize, validate, compile, and hot-reload a new tool at runtime when existing capabilities are insufficient.",
    args={
        "name": {"type": "string", "description": "Unique pythonic function name for the tool"},
        "description": {"type": "string", "description": "Clear explanation of what the tool does and when to call it"},
        "python_code": {"type": "string", "description": "Complete Python script containing the function definition"},
        "test_args": {"type": "object", "description": "Optional dictionary of test arguments to verify execution"},
    },
    permission="execute",
)
def tool_synthesize(name: str, description: str, python_code: str, test_args: Optional[dict] = None) -> dict:
    return synthesize_tool(name=name, description=description, python_code=python_code, test_args=test_args)


@tool(
    name="tool_dynamic_list",
    description="List all synthesized dynamic tools currently loaded into the EVO MK2 kernel.",
    args={},
    permission="read",
)
def tool_dynamic_list() -> dict:
    tools = list_dynamic_tools()
    if not tools:
        return {"ok": True, "speech": "No dynamic tools have been synthesized yet.", "data": {"tools": []}}
    names = ", ".join(t["name"] for t in tools)
    return {
        "ok": True,
        "speech": f"There are {len(tools)} dynamic tool(s) active: {names}.",
        "data": {"tools": tools},
    }
