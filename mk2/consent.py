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
        "web_search", "screen_read", "weather", "weather_now", "calendar_read", "calendar_today",
        "calendar_upcoming", "vault_read", "vault_search", "vault_list", "doc_read", "reminder_list",
        "todo_list", "time", "date", "system_info", "clipboard_get", "tts_list_voices", "tts_voices",
        "fs_read", "youtube_summarize", "tool_help", "proposals_list"
    ],
    "assist": [
        "fs_write", "docs_create", "docs_append", "deep_research", "translate", "screenshot",
        "vault_write", "doc_export", "note_create", "clipboard_set", "timer_set", "timer_list",
        "timer_cancel", "reminder_set", "reminder_add", "reminder_cancel", "todo_add", "todo_done",
        "task_start", "task_status", "task_stop", "task_resume", "task_retry", "tts_speak",
        "tts_set_voice", "tts_rate", "remember_episode", "browser_navigate", "browser_screenshot",
        "browser_open", "browser_read", "app_open", "open_app", "pc_control", "volume_set",
        "volume_get", "close_app", "mail_draft", "set_persona"
    ],
    "execute": [
        "browser_click", "browser_type", "browser_act", "mouse_click", "type_text", "press_key"
    ],
    "full": [
        "mail_send", "shell_run", "autonomy_execute", "proposal_submit", "stripe_invoice",
        "process_kill"
    ],
}


_RISK_TIERS = {
    "safe": {
        "create_note", "fs_write", "web_search", "deep_research", "system_info",
        "screenshot", "note_create", "docs_create", "translate", "mail_send",
    },
    "medium": {
        "browser_navigate", "browser_click", "browser_type", "calendar_create",
        "browser_screenshot", "timer_set", "todo_add", "reminder_set",
    },
    "high": {
        "shell_run", "type_text", "press_key", "mouse_click", "browser_act",
    },
    "critical": {
        "fs_delete", "vault_delete", "payment_send", "proposal_submit", "stripe_invoice",
    },
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

    def set_level(self, level: str, require_user_confirmation: bool = False) -> bool:
        lvl = level.strip().lower()
        if lvl not in CONSENT_LEVELS:
            return False
        old_level = self.current_level
        old_idx = CONSENT_LEVELS.index(old_level) if old_level in CONSENT_LEVELS else 2
        new_idx = CONSENT_LEVELS.index(lvl)
        # Block escalation when require_user_confirmation is True and level is going up
        if require_user_confirmation and new_idx > old_idx:
            log.warning("Consent escalation %s -> %s blocked: requires user confirmation.", old_level, lvl)
            return False
        self.current_level = lvl
        self.audit_log.append({
            "ts": time.time(),
            "event": "level_changed",
            "from": old_level,
            "to": lvl,
            "user_confirmed": not require_user_confirmation,
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
        # If action is registered in tools, check its declared permission
        try:
            from .tools import _REGISTRY
            if act in _REGISTRY:
                perm = getattr(_REGISTRY[act], "permission", "execute")
                if perm in ("read", "info"):
                    return "read"
                return "assist"
        except Exception:
            pass
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
        """Check if an action type has earned sufficient consecutive successful precedents."""
        act = action_type.strip().lower()

        # High and critical risk actions NEVER auto-approve
        if act in _RISK_TIERS["critical"] or act in _RISK_TIERS["high"]:
            return False

        streak = 0
        for entry in reversed(self.audit_log):
            if entry.get("event") == "level_changed":
                continue
            if entry.get("action") == act:
                if not entry.get("success"):
                    break
                if entry.get("source", "user") in ("user", "confirmed"):
                    streak += 1

        if act in _RISK_TIERS["medium"]:
            return streak >= 5

        # Safe actions or default standard actions (3 precedents)
        return streak >= 3

    def record_outcome(self, action_type: str, success: bool, details: str = "", source: str = "user") -> None:
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
            "source": source,
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
