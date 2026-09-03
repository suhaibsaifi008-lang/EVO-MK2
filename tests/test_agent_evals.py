"""Tests for Agent Decision Evaluation Suite (M8.6)."""
import pytest
from mk2 import db
from mk2.evals import AgentEvalHarness, run_agent_evals


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_evals.db")
    db.migrate()


def test_agent_eval_harness_scenarios():
    """Verify all decision benchmarks execute and score above 85%."""
    harness = AgentEvalHarness()
    results = harness.run_all()
    assert len(results) >= 5

    for r in results:
        assert r.passed is True, f"Benchmark scenario failed: {r.scenario_name} - {r.verdict}"


def test_run_agent_evals_summary():
    """Verify evaluation summary structure and 100% score."""
    summary = run_agent_evals()
    assert summary["total"] >= 5
    assert summary["score_pct"] == 100.0
    assert summary["ok"] is True
