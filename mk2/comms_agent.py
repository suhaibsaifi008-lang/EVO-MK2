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

    RATE_LIMITS = {
        "max_messages_per_hour": 10,
    }

    def __init__(self):
        self.vault = get_credential_vault()
        self.consent = get_consent_manager()
        self.ethics = get_moral_engine()
        self.audit = get_audit_logger()
        self.sent_history: list[dict[str, Any]] = []
        self._rate_limits: dict[str, list[float]] = {"telegram": [], "sms": []}

    def _check_rate_limit(self, channel: str) -> bool:
        now = time.time()
        recent = [t for t in self._rate_limits.get(channel, []) if now - t < 3600]
        self._rate_limits[channel] = recent
        if len(recent) >= self.RATE_LIMITS["max_messages_per_hour"]:
            log.warning("Rate limit exceeded for %s channel (%d msgs in past hour).", channel, len(recent))
            return False
        self._rate_limits[channel].append(now)
        return True

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

    def send_telegram(self, chat_id: str = "", message: str = "", user_approved: bool = False) -> MoralVerdict:
        """Send a Telegram message with rate limits, consent check, and audit trail."""
        # Resolve chat_id if not given
        if not chat_id:
            try:
                creds = self.vault.get("telegram")
                if creds and creds.get("chat_id"):
                    chat_id = str(creds["chat_id"])
            except Exception:
                pass
            if not chat_id:
                try:
                    from . import telegram_link
                    chat_id = telegram_link.paired_chat()
                except Exception:
                    pass

        action = {"action": "send_telegram", "channel": "telegram", "to": chat_id, "text": message}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("send_message") and not user_approved:
            return MoralVerdict.caution("Sending messaging alerts requires user approval.", action=action)

        if not self._check_rate_limit("telegram"):
            return MoralVerdict.block("Telegram rate limit reached (max 10 msgs/hour).")

        # Try telegram_link first
        try:
            from . import telegram_link
            sent = telegram_link.send_message(text=message, chat_id=chat_id)
            if sent:
                self.sent_history.append({"channel": "telegram", "to": chat_id, "text": message, "ts": time.time()})
                self.audit.log_action(action, v, {"ok": True})
                return MoralVerdict.safe("Telegram message delivered.")
        except Exception as exc:
            log.debug("telegram_link delivery note: %s", exc)

        # Fallback to direct Bot API from vault token
        try:
            creds = self.vault.get("telegram")
            token = creds.get("token") if creds else None
            if token and chat_id:
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
            log.warning("Telegram direct API delivery note: %s", exc)

        # Simulation fallback for development/offline
        log.info("[Telegram Outbound Simulated] -> %s: %s", chat_id, message)
        self.sent_history.append({"channel": "telegram", "to": chat_id, "text": message, "ts": time.time(), "simulated": True})
        self.audit.log_action(action, v, {"ok": True, "simulated": True})
        return MoralVerdict.safe("Telegram message dispatched (simulated).", action=action)

    def read_telegram(self, limit: int = 10) -> list[dict[str, Any]]:
        """Read recent incoming messages via Telegram Bot API."""
        try:
            from . import telegram_link
            updates = telegram_link._api("getUpdates", limit=limit)
            if updates and isinstance(updates, dict) and updates.get("ok"):
                res: list[dict[str, Any]] = []
                for u in updates.get("result", []):
                    msg = u.get("message") or u.get("edited_message") or {}
                    text = msg.get("text", "")
                    sender = msg.get("from", {}).get("first_name", "Telegram User")
                    cid = str(msg.get("chat", {}).get("id", ""))
                    if text:
                        res.append({
                            "id": f"tg_{u.get('update_id')}",
                            "from": sender,
                            "chat_id": cid,
                            "text": text,
                            "ts": msg.get("date", time.time()),
                            "channel": "telegram",
                        })
                return res
        except Exception as exc:
            log.debug("Telegram read updates note: %s", exc)
        return []

    def send_sms(self, to: str, message: str, user_approved: bool = False) -> MoralVerdict:
        """Send an SMS via Twilio with rate limits and consent validation."""
        action = {"action": "send_sms", "channel": "sms", "to": to, "text": message}
        v = self.ethics.evaluate(action)
        if v.verdict == "block":
            return v

        if not self.consent.has_consent("send_message") and not user_approved:
            return MoralVerdict.caution("Sending SMS alerts requires user approval.", action=action)

        if not self._check_rate_limit("sms"):
            return MoralVerdict.block("SMS rate limit reached (max 10 msgs/hour).")

        try:
            creds = self.vault.get("twilio")
            if not creds or not creds.get("account_sid") or not creds.get("auth_token"):
                log.info("[SMS Outbound Simulated] -> %s: %s", to, message)
                self.sent_history.append({"channel": "sms", "to": to, "text": message, "ts": time.time(), "simulated": True})
                self.audit.log_action(action, v, {"ok": True, "simulated": True})
                return MoralVerdict.safe("SMS sent (simulated mode).", action=action)

            import twilio.rest
            client = twilio.rest.Client(creds["account_sid"], creds["auth_token"])
            from_number = creds.get("from_number", "")
            sms = client.messages.create(body=message, from_=from_number, to=to)
            self.sent_history.append({"channel": "sms", "to": to, "text": message, "sid": sms.sid, "ts": time.time()})
            self.audit.log_action(action, v, {"ok": True, "sid": sms.sid})
            return MoralVerdict.safe(f"SMS delivered via Twilio (SID: {sms.sid}).", action=action)
        except ImportError:
            log.warning("Twilio package not installed. Simulating SMS dispatch.")
            self.sent_history.append({"channel": "sms", "to": to, "text": message, "ts": time.time(), "simulated": True})
            return MoralVerdict.safe("SMS simulated (twilio library not installed).", action=action)
        except Exception as exc:
            log.warning("Twilio SMS send error: %s", exc)
            return MoralVerdict.caution(f"Failed sending SMS: {exc}", action=action)

    def read_sms(self, limit: int = 10) -> list[dict[str, Any]]:
        """Read incoming SMS messages (pull-based retrieval not supported by Twilio REST API)."""
        return []


_global_comms: Optional[CommsAgent] = None


def get_comms_agent() -> CommsAgent:
    global _global_comms
    if _global_comms is None:
        _global_comms = CommsAgent()
    return _global_comms
