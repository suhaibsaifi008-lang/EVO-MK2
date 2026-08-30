"""Unit tests for JARVIS Autonomy Architecture in EVO MK2."""
from pathlib import Path
import tempfile
import time

from mk2.credential_vault import CredentialVault
from mk2.consent import ConsentManager
from mk2.ethics import MoralEngine, MoralVerdict
from mk2.audit import AuditLogger
from mk2.platforms.upwork import UpworkAgent
from mk2.revenue import RevenueTracker
from mk2.approval_queue import ApprovalQueue
from mk2.brain import _fast_path


def test_credential_vault():
    with tempfile.TemporaryDirectory() as td:
        v_path = Path(td) / "vault.enc"
        v = CredentialVault(vault_path=v_path, master_key="test-key-abc-123")
        v.store("upwork", {"username": "suhaib", "token": "secret_pass_999"})
        assert v.has("upwork")
        data = v.get("upwork")
        assert data["username"] == "suhaib"
        assert data["token"] == "secret_pass_999"
        assert "upwork" in v.list_services()

        # Verify disk payload is encrypted
        enc = v_path.read_bytes()
        assert b"secret_pass_999" not in enc

        # Remove
        assert v.remove("upwork")
        assert not v.has("upwork")


def test_consent_precedent_progression():
    with tempfile.TemporaryDirectory() as td:
        c_path = Path(td) / "consent.json"
        cm = ConsentManager(state_path=c_path)
        assert cm.get_level() == "assist"
        assert cm.has_consent("web_search")
        assert cm.has_consent("docs_create")
        assert not cm.has_consent("mail_send")

        # 3 successful precedents earn auto-approval
        assert not cm.is_auto_approved("mail_send")
        cm.record_outcome("mail_send", True)
        cm.record_outcome("mail_send", True)
        assert not cm.is_auto_approved("mail_send")
        cm.record_outcome("mail_send", True)
        assert cm.is_auto_approved("mail_send")
        assert cm.has_consent("mail_send")

        # Failure drops streak
        cm.record_outcome("mail_send", False)
        assert not cm.is_auto_approved("mail_send")
        assert not cm.has_consent("mail_send")


def test_moral_engine_verdicts():
    me = MoralEngine()
    # 1. Hard block
    v_block = me.evaluate({"action": "post_fake_review", "details": "fake review for product"})
    assert v_block.verdict == "block"
    assert "policy_violation" in v_block.risks

    # 2. Caution for first time / high stakes
    v_caution = me.evaluate({"action": "email_send", "to": "lead@test.com", "body": "Click here now for urgent business guarantee 100%"})
    assert v_caution.verdict == "caution"

    # 3. Safe for research
    v_safe = me.evaluate({"action": "web_search", "query": "python playwright"})
    assert v_safe.verdict == "safe"


def test_approval_queue():
    with tempfile.TemporaryDirectory() as td:
        q_path = Path(td) / "queue.json"
        q = ApprovalQueue(file_path=q_path)
        item_id = q.enqueue({"type": "proposal_submit", "target": "Acme"}, MoralVerdict.caution("Requires review"))
        assert q.get_item(item_id) is not None
        pending = q.get_pending()
        assert any(p["id"] == item_id for p in pending)

        # Approve
        res = q.approve(item_id)
        assert res["ok"]
        assert q.get_item(item_id) is None


def test_revenue_tracker():
    rev = RevenueTracker()
    rev.record_payment(200.0, "upwork", "ClientAlpha", "Phase 1 payment")
    stats = rev.get_stats(7)
    assert stats["total_revenue"] >= 200.0
    report = rev.weekly_report()
    assert "Revenue" in report


def test_fast_path_emergency_kill():
    reply = _fast_path("EVO, stop everything!")
    assert reply is not None
    assert "Emergency stop confirmed" in reply
