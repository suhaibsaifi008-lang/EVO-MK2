"""Approval Queue for EVO MK2 (JARVIS Phase 6).

Holds pending autonomous actions requiring explicit user review before execution.
Provides approve, reject, edit, and auto-approve controls for human-in-the-loop governance.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from .audit import get_audit_logger
from .config import DATA
from .consent import get_consent_manager
from .ethics import MoralVerdict

log = logging.getLogger("mk2.approval_queue")

QUEUE_FILE = DATA / "approval_queue.json"


class ApprovalQueue:
    """Manages user approvals for autonomous actions."""

    def __init__(self, file_path: Optional[Path] = None):
        self.file_path = file_path or QUEUE_FILE
        self.pending: dict[str, dict[str, Any]] = {}
        self.history: list[dict[str, Any]] = []
        self.consent = get_consent_manager()
        self.audit = get_audit_logger()
        self._load()

    def _load(self) -> None:
        if self.file_path.exists():
            try:
                data = json.loads(self.file_path.read_text(encoding="utf-8"))
                self.pending = data.get("pending", {})
                self.history = data.get("history", [])
            except Exception as exc:
                log.warning("Failed loading approval queue: %s", exc)

    def _save(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.file_path.write_text(
                json.dumps({"pending": self.pending, "history": self.history[-100:]}, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("Failed saving approval queue: %s", exc)

    def enqueue(self, action: dict[str, Any], verdict: Optional[MoralVerdict] = None) -> str:
        """Add an action to the approval queue. Returns the approval ID."""
        item_id = str(uuid.uuid4())[:8]
        entry = {
            "id": item_id,
            "ts": time.time(),
            "action": action,
            "verdict": verdict.to_dict() if verdict else {},
            "status": "pending",
            "expires": time.time() + 3600,
        }
        self.pending[item_id] = entry
        self._save()
        log.info("Enqueued action #%s for user approval: %s", item_id, action.get("type", "unknown"))
        return item_id

    def get_pending(self) -> list[dict[str, Any]]:
        """List all actions currently awaiting user review."""
        now = time.time()
        # Filter out expired items
        valid_items = [v for v in self.pending.values() if v.get("expires", now + 1) > now]
        return sorted(valid_items, key=lambda x: x.get("ts", 0), reverse=True)

    def get_item(self, item_id: str) -> Optional[dict[str, Any]]:
        item = self.pending.get(item_id)
        if item and item.get("expires", time.time() + 1) < time.time():
            return None
        return item

    def approve(self, item_id: str) -> dict[str, Any]:
        """User approves the action. Marks it approved and triggers callback or execution."""
        item = self.pending.pop(item_id, None)
        if not item:
            return {"ok": False, "error": f"Item #{item_id} not found."}
        if item.get("expires", time.time() + 1) < time.time():
            return {"ok": False, "error": f"Item #{item_id} has expired."}

        item["status"] = "approved"
        item["resolved_ts"] = time.time()
        self.history.append(item)
        self._save()

        action = item.get("action", {})
        act_type = action.get("type", "action")
        # Record precedent
        self.consent.record_outcome(act_type, True, f"User approved #{item_id}")
        self.audit.log_action(action, None, {"ok": True, "approved_by_user": True})
        log.info("Action #%s approved by user.", item_id)
        return {"ok": True, "item": item}

    def reject(self, item_id: str, reason: str = "") -> dict[str, Any]:
        """User rejects the action. Logs reason to train future strategy."""
        item = self.pending.pop(item_id, None)
        if not item:
            return {"ok": False, "error": f"Item #{item_id} not found."}

        item["status"] = "rejected"
        item["rejection_reason"] = reason
        item["resolved_ts"] = time.time()
        self.history.append(item)
        self._save()

        action = item.get("action", {})
        act_type = action.get("type", "action")
        self.consent.record_outcome(act_type, False, f"User rejected: {reason}")
        self.audit.log_action(action, None, {"ok": False, "rejected_by_user": True, "reason": reason})
        log.info("Action #%s rejected by user: %s", item_id, reason)
        return {"ok": True, "item": item}


_global_queue: Optional[ApprovalQueue] = None


def get_approval_queue() -> ApprovalQueue:
    global _global_queue
    if _global_queue is None:
        _global_queue = ApprovalQueue()
    return _global_queue
