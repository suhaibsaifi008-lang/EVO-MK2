"""Unified Authorization Pipeline for EVO MK2 (M8.1).

Centralizes and guarantees all authorization decisions across surfaces:
  REST API, WebSocket, Voice, Subagent Swarms, and Autonomous Engines.

Pipeline Stages:
  1. Emergency Halt Check (KillSwitch)
  2. Input Sanitization & Anti-Traversal
  3. Action Risk Tiering & Required Consent Mapping
  4. User Consent Level Check
  5. Blast-Radius & Precedent-based Auto-Approval Check
  6. Cryptographic Audit Chain Dispatch
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from . import audit_chain
from .approval_queue import ApprovalQueue
from .consent import ACTIONS_BY_LEVEL, CONSENT_LEVELS, get_consent_manager
from .kill_switch import get_kill_switch

log = logging.getLogger("mk2.authz")


@dataclass
class AuthzRequest:
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    actor: str = "user"
    source: str = "console"
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthzDecision:
    allowed: bool
    reason: str
    risk_tier: str = "safe"
    required_consent: str = "read"
    needs_approval: bool = False
    approval_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "risk_tier": self.risk_tier,
            "required_consent": self.required_consent,
            "needs_approval": self.needs_approval,
            "approval_id": self.approval_id,
        }


class AuthorizationPipeline:
    """Unified policy evaluation pipeline for all actions and tool calls."""

    def __init__(self):
        self.consent = get_consent_manager()
        self.kill_switch = get_kill_switch()
        self.approval_queue = ApprovalQueue()

    def get_required_consent(self, action: str) -> str:
        """Find the minimum consent level required for an action."""
        act = (action or "").strip().lower()
        for level in ("read", "assist", "execute", "full"):
            if act in ACTIONS_BY_LEVEL.get(level, []):
                return level
        # Administrative / destructive defaults to full
        if any(d in act for d in ("kill", "admin", "delete", "format", "shutdown", "drop")):
            return "full"
        return "assist"

    def get_risk_tier(self, action: str) -> str:
        """Map action to a risk tier: safe, medium, high, critical."""
        act = (action or "").strip().lower()
        if act in ("shell_run", "process_kill", "stripe_invoice", "autonomy_permission", "delete_file"):
            return "critical"
        req = self.get_required_consent(act)
        if req == "full":
            return "high"
        elif req == "execute":
            return "medium"
        return "safe"

    def authorize(self, req: AuthzRequest) -> AuthzDecision:
        """Evaluate authorization for an incoming request through the unified pipeline."""
        act = (req.action or "").strip().lower()

        # Stage 1: Emergency Kill Switch Gate
        if self.kill_switch.is_active():
            decision = AuthzDecision(
                allowed=False,
                reason="Kill switch active — all autonomous and tool actions are halted.",
                risk_tier=self.get_risk_tier(act),
                required_consent=self.get_required_consent(act),
            )
            self._audit(req, decision)
            return decision

        # Context constraints (voice surface & quiet hours)
        ctx = req.context or {}
        if ctx.get("surface") == "voice":
            if act in ("shell_run", "mouse_click", "type_text", "press_key", "process_kill", "fs_delete"):
                decision = AuthzDecision(
                    allowed=False,
                    reason=f"Action '{act}' blocked over voice channel for safety.",
                    risk_tier="critical",
                    required_consent="full",
                )
                self._audit(req, decision)
                return decision
        if ctx.get("quiet_hours"):
            if act in ("shell_run", "fs_delete", "mail_send", "stripe_invoice"):
                decision = AuthzDecision(
                    allowed=False,
                    reason=f"Action '{act}' blocked during quiet hours.",
                    risk_tier="high",
                    required_consent="full",
                )
                self._audit(req, decision)
                return decision

        # Stage 2: Dangerous Argument, Path Traversal & Prompt Injection Validation
        args_str = json.dumps(req.args, default=str)
        if ".." in args_str and any(p in args_str for p in ("../", "..\\", "/..", "\\..")):
            # Suspicious path traversal attempt
            decision = AuthzDecision(
                allowed=False,
                reason="Action blocked: directory traversal pattern detected in arguments.",
                risk_tier="critical",
                required_consent="full",
            )
            self._audit(req, decision)
            return decision

        try:
            from . import firewall
            is_tainted, taint_reason = firewall.is_tainted_tool_call(act, req.args, [])
            if is_tainted:
                decision = AuthzDecision(
                    allowed=False,
                    reason=f"Action blocked by firewall: {taint_reason}",
                    risk_tier="critical",
                    required_consent="full",
                )
                self._audit(req, decision)
                return decision
        except Exception:
            pass

        risk_tier = self.get_risk_tier(act)
        req_consent = self.get_required_consent(act)

        # Stage 3: Consent Level Enforcement
        if not self.consent.has_consent(act):
            cur_level = getattr(self.consent, "current_level", "assist")
            decision = AuthzDecision(
                allowed=False,
                reason=f"Consent denied: '{act}' requires '{req_consent}' tier (current tier: '{cur_level}').",
                risk_tier=risk_tier,
                required_consent=req_consent,
            )
            self._audit(req, decision)
            return decision

        # Stage 4: Blast-Radius & Precedent Auto-Approval Check
        # For critical actions triggered autonomously, require user approval queue if no precedent
        if risk_tier == "critical" and req.actor in ("autonomous_runner", "subagent", "background"):
            if not self.consent.is_auto_approved(act):
                # Enqueue into approval queue
                enq_id = self.approval_queue.enqueue(
                    action={
                        "type": act,
                        "name": act,
                        "parameters": req.args,
                        "source": f"authz_pipeline:{req.actor}",
                        "risk": risk_tier,
                    }
                )
                decision = AuthzDecision(
                    allowed=False,
                    reason=f"Action '{act}' has critical blast-radius. Enqueued for user confirmation (Queue ID #{enq_id}).",
                    risk_tier=risk_tier,
                    required_consent=req_consent,
                    needs_approval=True,
                    approval_id=str(enq_id),
                )
                self._audit(req, decision)
                return decision

        # Stage 5: Granted
        decision = AuthzDecision(
            allowed=True,
            reason="Authorized by unified policy pipeline.",
            risk_tier=risk_tier,
            required_consent=req_consent,
        )
        self._audit(req, decision)
        return decision

    def _audit(self, req: AuthzRequest, decision: AuthzDecision) -> None:
        """Log decision to cryptographic tamper-evident audit chain."""
        try:
            audit_chain.record_audit_event(
                actor=req.actor,
                action=req.action,
                payload={
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "risk_tier": decision.risk_tier,
                    "source": req.source,
                    "args": {k: str(v)[:200] for k, v in req.args.items()} if isinstance(req.args, dict) else str(req.args)[:200],
                },
            )
        except Exception as exc:
            log.warning("Audit chain dispatch failed: %s", exc)


_pipeline: Optional[AuthorizationPipeline] = None


def get_authz_pipeline() -> AuthorizationPipeline:
    """Retrieve the global unified authorization pipeline singleton."""
    global _pipeline
    if _pipeline is None:
        _pipeline = AuthorizationPipeline()
    return _pipeline


def check_authorization(
    action: str,
    args: dict[str, Any] = None,
    actor: str = "user",
    source: str = "console",
    context: Optional[dict[str, Any]] = None,
) -> AuthzDecision:
    """Convenience functional interface for authorization check."""
    pipe = get_authz_pipeline()
    req = AuthzRequest(
        action=action,
        args=args or {},
        actor=actor,
        source=source,
        context=context or {},
    )
    return pipe.authorize(req)
