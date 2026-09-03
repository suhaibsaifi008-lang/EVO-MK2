"""Tests for Unified Authorization Pipeline (M8.1)."""
import pytest
from mk2 import db
from mk2.authz import check_authorization, AuthzRequest, get_authz_pipeline
from mk2.consent import get_consent_manager
from mk2.kill_switch import KillSwitch


@pytest.fixture(autouse=True)
def reset_state(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test_authz.db")
    db.migrate()
    KillSwitch._is_halted = False
    cm = get_consent_manager()
    cm.current_level = "assist"
    yield
    KillSwitch._is_halted = False


def test_authz_safe_action_allowed():
    """Verify safe read-only actions are permitted under assist consent."""
    dec = check_authorization("system_info", {}, actor="user")
    assert dec.allowed is True
    assert dec.risk_tier == "safe"


def test_authz_consent_denied():
    """Verify high-privilege actions are blocked when consent tier is insufficient."""
    cm = get_consent_manager()
    cm.current_level = "read"
    dec = check_authorization("fs_write", {"path": "test.txt", "content": "hi"}, actor="user")
    assert dec.allowed is False
    assert "Consent denied" in dec.reason


def test_authz_kill_switch_instant_halt():
    """Verify KillSwitch halts all incoming authorization requests."""
    KillSwitch._is_halted = True
    dec = check_authorization("system_info", {}, actor="user")
    assert dec.allowed is False
    assert "Kill switch active" in dec.reason


def test_authz_path_traversal_blocked():
    """Verify directory traversal patterns are blocked at authorization stage 2."""
    dec = check_authorization("fs_read", {"path": "../../windows/system32/cmd.exe"}, actor="user")
    assert dec.allowed is False
    assert "directory traversal" in dec.reason.lower()


def test_authz_autonomous_critical_enqueued():
    """Verify autonomous runner attempting critical tool escalates to approval queue."""
    cm = get_consent_manager()
    cm.current_level = "full"
    dec = check_authorization("shell_run", {"command": "git push origin main"}, actor="autonomous_runner")
    assert dec.allowed is False
    assert dec.needs_approval is True
    assert dec.approval_id is not None
