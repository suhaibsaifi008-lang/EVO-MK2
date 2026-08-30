"""Unified Communication Agent for EVO MK2 (JARVIS Phase 3).

Manages multi-channel messaging (Telegram, SMS, chat), message prioritization,
and draft reply generation with human-in-the-loop approval.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from . import llm
from .audit import get_audit_logger
from .consent import get_consent_manager
from .credential_vault import get_credential_vault
from .ethics import MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.comms_agent")


class CommsAgent:
    """Unified communication agent across SMS and Telegram channels."""

    def __init__(self):
        self.vault = get_credential_vault()
        self.consent = get_consent_manager()
        self.ethics = get_moral_engine()
        self.audit = get_audit_logger()
        self.sent_history: list[dict[str, Any]] = []

    def prioritize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Rank messages by urgency and importance (0-100 score)."""
        if not messages:
            return []

        prompt = (
            "Analyze these incoming messages and assign each an urgency score (0-100) and priority category ('urgent', 'normal', 'low'):\n\n"
            + json.dumps(messages, indent=2, default=str)
            + '\n\nReturn ONLY a JSON array of objects: [{"id": "<id>", "urgency": <0-100>, "category": "urgent"|"normal"|"low", "reason": "<short>"}]'
        )

        try:
            raw = llm.chat([
                {"role": "system", "content": "You are an executive triage assistant prioritizing communications."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.1)
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            scores = json.loads(clean.strip())
            score_map = {s["id"]: s for s in scores if "id" in s}

            for m in messages:
                sc = score_map.get(m.get("id"), {})
                m["urgency"] = sc.get("urgency", 50)
                m["category"] = sc.get("category", "normal")
                m["priority_reason"] = sc.get("reason", "")

            return sorted(messages, key=lambda x: x.get("urgency", 0), reverse=True)
        except Exception as exc:
            log.warning("Message prioritization failed: %s", exc)
            for m in messages:
                m["urgency"] = 50
                m["category"] = "normal"
            return messages

    def draft_reply(self, message: dict[str, Any], context: str = "") -> MoralVerdict:
        """Draft an appropriate, natural reply to a message without sending."""
        sender = message.get("from", "Contact")
        text = message.get("text") or message.get("body") or ""

        action = {"action": "draft_reply", "channel": message.get("channel", "telegram"), "to": sender, "in_reply_to": text}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        prompt = (
            f"Draft a concise, natural reply to this message from {sender}:\n"
            f"Received message: \"{text}\"\n"
            f"Context: {context or 'Direct message needing clear response'}\n\n"
            "Rules:\n"
            "- Concise, professional, and friendly.\n"
            "- 1-2 sentences maximum.\n"
            "- No corporate fluff."
        )

        try:
            reply = llm.chat([
                {"role": "system", "content": "You are EVO drafting an executive message reply on behalf of the user."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.3)
            draft = {
                "channel": message.get("channel", "telegram"),
                "to": sender,
                "reply_text": reply.strip(),
                "original_text": text,
                "drafted_at": time.time(),
            }
            return MoralVerdict.safe("Reply drafted successfully.", action=draft)
        except Exception as exc:
            return MoralVerdict.caution(f"Failed drafting reply: {exc}")

    def send_telegram(self, chat_id: str, message: str, user_approved: bool = False) -> MoralVerdict:
        """Send a Telegram message with consent verification."""
        action = {"action": "send_telegram", "channel": "telegram", "to": chat_id, "text": message}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("send_message") and not user_approved:
            return MoralVerdict.caution("Sending messaging alerts requires user approval.", action=action)

        creds = self.vault.get("telegram")
        token = creds.get("token") if creds else None
        if not token:
            # Fallback to local simulation / logging if token not configured yet
            log.info("[Telegram Outbound Simulation] -> %s: %s", chat_id, message)
            self.sent_history.append({"channel": "telegram", "to": chat_id, "text": message, "ts": time.time(), "simulated": True})
            return MoralVerdict.safe("Telegram message dispatched (simulated).", action=action)

        # Real Telegram Bot API call
        try:
            import urllib.request
            import urllib.parse
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": chat_id, "text": message}).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                if res_data.get("ok"):
                    self.sent_history.append({"channel": "telegram", "to": chat_id, "text": message, "ts": time.time()})
                    self.audit.log_action(action, v, {"ok": True})
                    return MoralVerdict.safe("Telegram message delivered.")
        except Exception as exc:
            log.warning("Telegram API delivery failed: %s", exc)

        return MoralVerdict.caution("Failed delivering Telegram message.")


_global_comms: Optional[CommsAgent] = None


def get_comms_agent() -> CommsAgent:
    global _global_comms
    if _global_comms is None:
        _global_comms = CommsAgent()
    return _global_comms
