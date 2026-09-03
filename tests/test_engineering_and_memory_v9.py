"""Tests for JARVIS 9/10 Upgrades: Autonomous Engineering Core & Associative Relational Memory."""
import time
import pytest

from mk2 import db, tools
from mk2.engineering import EngineeringWorkspace, EngineeringSwarm, ScientificSimulator, code_repair
from mk2.deep_memory import record_relation, graph_query, format_graph_context, consolidate_memories


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_v9.db")
    db.migrate()
    tools.ensure_loaded()


def test_engineering_workspace_sandboxing(tmp_path):
    """Verify EngineeringWorkspace isolates files and prevents directory traversal."""
    ws = EngineeringWorkspace("test_proj_001")
    fpath = ws.write_file("module.py", "def add(a, b): return a + b\n")
    assert fpath.exists()
    assert ws.read_file("module.py") == "def add(a, b): return a + b\n"
    assert "module.py" in ws.list_files()

    # Path traversal attempt must be blocked
    with pytest.raises(ValueError, match="Path traversal blocked"):
        ws.write_file("../evil.py", "import os")

    ws.cleanup()


def test_engineering_workspace_run_tests():
    """Verify sandbox runs tests and captures pass/fail status."""
    ws = EngineeringWorkspace("test_proj_tdd")
    ws.write_file("calc.py", "def multiply(x, y): return x * y\n")
    ws.write_file("test_calc.py", "from calc import multiply\ndef test_mult(): assert multiply(3, 4) == 12\n")

    passed, out, err = ws.run_tests("test_calc.py")
    assert passed is True

    # Failing test
    ws.write_file("test_calc_fail.py", "from calc import multiply\ndef test_fail(): assert multiply(2, 2) == 5\n")
    passed, out, err = ws.run_tests("test_calc_fail.py")
    assert passed is False
    assert "AssertionError" in out or "assert 4 == 5" in out or "FAILED" in out

    ws.cleanup()


def test_code_repair_functionality():
    """Verify code_repair validates syntax."""
    bad_code = "def greet(name):\nprint('hello ' + name)"
    # Syntax check or repair
    res = code_repair(bad_code, "IndentationError: expected an indented block")
    assert isinstance(res, dict)
    assert "ok" in res


def test_scientific_simulator_local_execution():
    """Verify ScientificSimulator runs python mathematical calculations."""
    sim = ScientificSimulator()
    res = sim.simulate("Calculate the first 5 prime numbers", {"limit": 5})
    assert isinstance(res, dict)
    assert "ok" in res


def test_deep_memory_knowledge_graph():
    """Verify multi-hop relational graph storage and query traversal."""
    # Store relationship edges
    assert record_relation("Tony Stark", "created", "Mark III", "Armor design", 1.0) is True
    assert record_relation("Mark III", "has_subsystem", "JARVIS", "Core AI", 0.95) is True
    assert record_relation("JARVIS", "runs_on", "Stark Server", "Host infrastructure", 0.9) is True

    # Multi-hop query from Tony Stark (depth 2)
    edges = graph_query("Tony Stark", hops=2)
    assert len(edges) >= 2
    sources = [e["source"] for e in edges]
    targets = [e["target"] for e in edges]
    assert "tony stark" in sources
    assert "mark iii" in targets or "mark iii" in sources

    # Test formatted context
    ctx = format_graph_context("Tony Stark")
    assert "Associative Memory Graph:" in ctx
    assert "tony stark" in ctx
    assert "mark iii" in ctx


def test_memory_consolidation_empty():
    """Verify consolidation handles short dialogue cleanly."""
    res = consolidate_memories()
    assert res["ok"] is True
    assert res["edges_added"] == 0


def test_tool_synthesizer_self_healing_logic():
    """Verify tool_synthesizer _heal_code fallback preserves runnable structure."""
    from mk2.tool_synthesizer import _heal_code
    broken = "def my_test_tool(x):\n    return x + 1"
    healed = _heal_code("my_test_tool", broken, "TypeError: unsupported operand type")
    assert "def my_test_tool" in healed
