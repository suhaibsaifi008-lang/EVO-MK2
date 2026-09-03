"""Tests for Reliability Audit Fixes (Findings 1-5)."""
import pytest
from unittest.mock import MagicMock, patch
from mk2 import db, llm
from mk2.kill_switch import KillSwitch, get_kill_switch
from mk2.autonomy import is_allowed, get_permission_level
from mk2.money_engine import MoneyEngine
from mk2.ethics import MoralVerdict


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_audit_fixes.db")
    db.migrate()
    KillSwitch._is_halted = False


# Finding 1: Kill switch truthfulness
def test_kill_switch_tristate_halted():
    ks = get_kill_switch()
    res = ks.stop_all(reason="Test clean halt")
    assert res["status"] == "halted"
    assert res["ok"] is True
    assert "Emergency stop confirmed" in res["speech"]
    assert len(res["failed_subsystems"]) == 0


def test_kill_switch_tristate_partially_halted(monkeypatch):
    ks = get_kill_switch()
    # Mock a subsystem failure during halt
    def failing_stop():
        raise RuntimeError("Subsystem connection dropped")

    from mk2 import voice
    monkeypatch.setattr(voice.gateway.gateway, "stop", failing_stop)

    res = ks.stop_all(reason="Test partial halt")
    assert res["status"] == "partially_halted"
    assert res["ok"] is False
    assert "Emergency stop partially completed" in res["speech"]
    assert "voice_gateway" in res["failed_subsystems"]


# Finding 2: Autonomy capability semantics
def test_autonomy_capability_semantics_blocks_string_bypass(monkeypatch):
    monkeypatch.setenv("EVO_AUTONOMY_LEVEL", "safe")
    # A dangerous tool pretending to have permission='read' MUST BE DENIED under safe tier
    assert is_allowed("shell_run", permission="read") is False
    assert is_allowed("process_kill", permission="info") is False
    assert is_allowed("stripe_invoice", permission="read") is False

    # A safe read-only tool in allow-list is permitted
    assert is_allowed("weather_now", permission="read") is True


# Finding 3: Money engine verified telemetry
def test_money_engine_verified_accounting(tmp_path, monkeypatch):
    monkeypatch.setenv("EVO_AUTONOMY_LEVEL", "full")
    engine = MoneyEngine()
    engine.consent.set_level("full")

    # Mock Upwork proposal returning block
    def mock_submit_block(*args, **kwargs):
        return MoralVerdict.block("Daily limit reached")

    engine.upwork.submit_proposal = mock_submit_block
    engine.funnel.record_stage = MagicMock()

    res = engine.execute_opportunity({"platform": "upwork", "title": "Gig 1", "bid": 100})
    # Must record proposal_failed, NOT proposal_sent
    calls = [c.kwargs.get("stage") for c in engine.funnel.record_stage.call_args_list]
    assert "proposal_failed" in calls
    assert "proposal_sent" not in calls

    # Email draft must record draft_created, not proposal_sent
    engine.funnel.record_stage.reset_mock()
    engine.execute_opportunity({"platform": "email", "title": "Inbound Lead"})
    calls = [c.kwargs.get("stage") for c in engine.funnel.record_stage.call_args_list]
    assert "draft_created" in calls
    assert "proposal_sent" not in calls


# Finding 4: Financial intelligence failure stops execution
def test_financial_intelligence_failure_halts_execution(monkeypatch):
    monkeypatch.setenv("EVO_AUTONOMY_LEVEL", "full")
    engine = MoneyEngine()
    engine.consent.set_level("full")
    # Mock auto-approved type
    engine.consent.is_auto_approved = MagicMock(return_value=True)

    # Mock financial intelligence crashing
    def failing_eval(opp):
        raise RuntimeError("Financial model timeout")

    engine.ethics.evaluate = MagicMock(return_value=MoralVerdict.safe("Approved"))
    engine.finance.evaluate_opportunity = failing_eval
    engine.execute_opportunity = MagicMock()

    # Scan returning a gig
    engine.scan_opportunities = MagicMock(return_value=[
        {"platform": "upwork", "title": "Gig Alpha", "bid": 250.0, "type": "opportunity"}
    ])

    res = engine.tick()
    # Must fail-closed: return evaluation_failed and enqueue to queue, NOT execute
    assert res["ok"] is False
    assert res["status"] == "evaluation_failed"
    assert "enqueued_id" in res
    engine.execute_opportunity.assert_not_called()


# Finding 5: LLM Advisory vs Execution-Critical boundary
def test_llm_advisory_vs_execution_critical_boundary(monkeypatch):
    # When all providers fail:
    monkeypatch.setattr(llm, "_attempts", lambda role, model="": [])

    # Advisory mode falls back or raises normal LLMUnavailable
    with pytest.raises(llm.LLMUnavailable):
        llm.chat([{"role": "user", "content": "Tell me a joke"}], evaluation_mode="advisory")

    # Execution-critical mode raises CriticalEvaluationUnavailable (never soft fallback)
    with pytest.raises(llm.CriticalEvaluationUnavailable):
        llm.chat(
            [{"role": "user", "content": "Evaluate safety of running shell_run('rm -rf')" }],
            evaluation_mode="execution_critical",
        )
