"""Consent Manager for EVO MK2 (JARVIS Foundation).

Enforces strict graduated consent tiers and manages auto-approval trust scoring.
Starts at 'assist' tier. Dangerous or external write/send operations require
explicit approval until 3 consecutive successful precedents are earned.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .config import DATA

log = logging.getLogger("mk2.consent")

CONSENT_FILE = DATA / "consent_state.json"

CONSENT_LEVELS = ["none", "read", "assist", "execute", "full"]

ACTIONS_BY_LEVEL = {
    "none": [],
    "read": [
        "web_search", "screen_read", "weather", "calendar_read", "vault_read",
        "doc_read", "reminder_list", "todo_list", "time", "date"
    ],
    "assist": [
        "fs_write", "docs_create", "deep_research", "translate", "screenshot",
        "vault_write", "doc_export", "note_create"
    ],
    "execute": [
        "browser_navigate", "browser_screenshot", "timer_set", "todo_add",
        "reminder_set", "app_open", "pc_control"
    ],
    "full": [
        "browser_click", "browser_type", "mail_send", "shell_run",
        "autonomy_execute", "proposal_submit", "stripe_invoice"
    ],
}


class ConsentManager:
    """Manages user consent levels, precedents, and trust scoring."""

    def __init__(self, state_path: Optional[Path] = None):
        self.state_path = state_path or CONSENT_FILE
        self.current_level = "assist"
        self.action_precedents: dict[str, dict[str, Any]] = {}
        self.audit_log: list[dict[str, Any]] = []
        self._load_state()

    def _load_state(self) -> None:
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.current_level = data.get("current_level", "assist")
                self.action_precedents = data.get("action_precedents", {})
                self.audit_log = data.get("audit_log", [])
            except Exception as exc:
                log.warning("Failed to load consent state: %s", exc)

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        out = {
            "current_level": self.current_level,
            "action_precedents": self.action_precedents,
            "audit_log": self.audit_log[-500:],  # keep last 500 audit events
            "updated_at": time.time(),
        }
        try:
            self.state_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Failed to save consent state: %s", exc)

    def get_level(self) -> str:
        return self.current_level

    def set_level(self, level: str) -> bool:
        lvl = level.strip().lower()
        if lvl not in CONSENT_LEVELS:
            return False
        old_level = self.current_level
        self.current_level = lvl
        self.audit_log.append({
            "ts": time.time(),
            "event": "level_changed",
            "from": old_level,
            "to": lvl,
        })
        self._save_state()
        log.info("Consent level updated: %s -> %s", old_level, lvl)
        return True

    def required_level_for(self, action: str) -> str:
        """Find the minimum consent level required for a given action."""
        act = action.strip().lower()
        for lvl in CONSENT_LEVELS:
            if act in ACTIONS_BY_LEVEL.get(lvl, []):
                return lvl
        # Default unlisted actions to 'full' for safety
        return "full"

    def has_consent(self, action: str) -> bool:
        """Check if current consent level covers this action, or if auto-approved by precedent."""
        act = action.strip().lower()
        # 1. Precedent auto-approval check (3 successful precedents)
        if self.is_auto_approved(act):
            return True

        req_level = self.required_level_for(act)
        req_idx = CONSENT_LEVELS.index(req_level)
        cur_idx = CONSENT_LEVELS.index(self.current_level)
        return cur_idx >= req_idx

    def is_auto_approved(self, action_type: str) -> bool:
        """Check if an action type has earned 3 consecutive successful precedents."""
        act = action_type.strip().lower()
        prec = self.action_precedents.get(act, {})
        consecutive_success = prec.get("consecutive_success", 0)
        return consecutive_success >= 3

    def record_outcome(self, action_type: str, success: bool, details: str = "") -> None:
        """Record outcome of an action to build or decrement trust."""
        act = action_type.strip().lower()
        if act not in self.action_precedents:
            self.action_precedents[act] = {
                "total_runs": 0,
                "success_count": 0,
                "failure_count": 0,
                "consecutive_success": 0,
            }
        p = self.action_precedents[act]
        p["total_runs"] += 1
        if success:
            p["success_count"] += 1
            p["consecutive_success"] += 1
        else:
            p["failure_count"] += 1
            p["consecutive_success"] = 0  # reset streak on failure

        self.audit_log.append({
            "ts": time.time(),
            "action": act,
            "success": success,
            "details": details[:200],
            "streak": p["consecutive_success"],
        })
        self._save_state()

    def trust_score(self) -> float:
        """Calculate overall operational trust score (0.0 to 1.0)."""
        total_runs = sum(p.get("total_runs", 0) for p in self.action_precedents.values())
        if total_runs == 0:
            return 0.5  # baseline neutral trust
        total_success = sum(p.get("success_count", 0) for p in self.action_precedents.values())
        return round(total_success / total_runs, 2)


_global_consent: Optional[ConsentManager] = None


def get_consent_manager() -> ConsentManager:
    global _global_consent
    if _global_consent is None:
        _global_consent = ConsentManager()
    return _global_consent
