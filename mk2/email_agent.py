"""Autonomous Email Agent for EVO MK2 (JARVIS Phase 3).

Full email access: read inbox, identify money-making opportunities,
draft concise personalized proposals, require approval for first-time contacts,
and prevent duplicate outreach.
"""
from __future__ import annotations

import email
import imaplib
import json
import logging
import smtplib
import time
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

from . import llm
from .audit import get_audit_logger
from .consent import get_consent_manager
from .credential_vault import get_credential_vault
from .ethics import MoralVerdict, get_moral_engine

log = logging.getLogger("mk2.email_agent")


class EmailAgent:
    """Manages secure email communication, opportunity detection, and drafting."""

    OPPORTUNITY_KEYWORDS = [
        "proposal", "contract", "gig", "freelance", "job offer", "inquiry",
        "consulting", "project", "quote", "rate", "partnership", "hire",
        "invoice", "budget", "opportunity", "developer needed"
    ]

    def __init__(self):
        self.vault = get_credential_vault()
        self.consent = get_consent_manager()
        self.ethics = get_moral_engine()
        self.audit = get_audit_logger()
        self.sent_recipients: set[str] = set()

    def _get_creds(self, service: str = "gmail") -> dict[str, Any] | None:
        return self.vault.get(service) or self.vault.get("email")

    def connect_imap(self, service: str = "gmail") -> Optional[imaplib.IMAP4_SSL]:
        creds = self._get_creds(service)
        if not creds:
            return None
        host = creds.get("imap_host") or ("imap.gmail.com" if "gmail" in service else "")
        port = int(creds.get("imap_port", 993))
        user = creds.get("username") or creds.get("email")
        pw = creds.get("password")
        if not host or not user or not pw:
            return None
        try:
            client = imaplib.IMAP4_SSL(host, port)
            client.login(user, pw)
            return client
        except Exception as exc:
            log.warning("IMAP connection failed for %s: %s", service, exc)
            return None

    def read_inbox(self, service: str = "gmail", limit: int = 20, filter_opportunities: bool = True) -> list[dict[str, Any]]:
        """Read recent emails, optionally filtering for money-making opportunities."""
        if not self.consent.has_consent("mail_read") and not self.consent.has_consent("read"):
            return []

        client = self.connect_imap(service)
        if not client:
            return []

        messages: list[dict[str, Any]] = []
        try:
            client.select("INBOX", readonly=True)
            status, data = client.search(None, "ALL")
            if status != "OK" or not data or not data[0]:
                return []

            msg_ids = data[0].split()
            recent_ids = msg_ids[-limit:]

            for mid in reversed(recent_ids):
                try:
                    s, mdata = client.fetch(mid, "(RFC822)")
                    if s != "OK" or not mdata:
                        continue
                    raw_email = mdata[0][1]
                    msg = email.message_from_bytes(raw_email)

                    subj = ""
                    raw_subj = decode_header(msg.get("Subject", ""))[0]
                    if isinstance(raw_subj[0], bytes):
                        subj = raw_subj[0].decode(raw_subj[1] or "utf-8", "ignore")
                    else:
                        subj = str(raw_subj[0])

                    from_addr = msg.get("From", "")
                    date_str = msg.get("Date", "")

                    body_snippet = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body_snippet = part.get_payload(decode=True).decode("utf-8", "ignore")[:500]
                                break
                    else:
                        body_snippet = msg.get_payload(decode=True).decode("utf-8", "ignore")[:500]

                    item = {
                        "id": mid.decode("ascii", "ignore"),
                        "subject": subj,
                        "from": from_addr,
                        "date": date_str,
                        "snippet": body_snippet,
                    }

                    if filter_opportunities:
                        combined = f"{subj} {body_snippet}".lower()
                        if any(k in combined for k in self.OPPORTUNITY_KEYWORDS):
                            item["is_opportunity"] = True
                            messages.append(item)
                    else:
                        messages.append(item)
                except Exception as ex:
                    log.debug("Error parsing email %s: %s", mid, ex)
        finally:
            try:
                client.close()
                client.logout()
            except Exception:
                pass

        return messages

    def draft_email(self, to: str, subject: str, context: str, tone: str = "professional") -> MoralVerdict:
        """Draft a concise, personalized outreach email without sending."""
        action = {"action": "draft_email", "to": to, "subject": subject, "context": context}

        # 1. Moral pre-check
        verdict = self.ethics.evaluate(action)
        if verdict.verdict == "block":
            return verdict

        # 2. LLM Generation (3-4 sentences max, zero spam)
        prompt = (
            f"Draft a {tone} cold or warm outreach email to {to} regarding: {context}\n\n"
            "Rules:\n"
            "- Reference something specific about their situation or need.\n"
            "- Concise: 3-4 sentences maximum.\n"
            "- Propose a clear, low-friction next step (e.g. 10-minute chat or quick review).\n"
            "- NO spam phrasing, NO generic flattery, NO exclamation marks.\n"
            "- Professional closing."
        )

        try:
            body = llm.chat([
                {"role": "system", "content": "You are a master business communicator writing crisp, high-converting outreach."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.3)
        except Exception as exc:
            return MoralVerdict.caution(f"LLM drafting failed: {exc}")

        # 3. Post-generation content check
        content_action = {"action": "send_email", "to": to, "subject": subject, "body": body}
        content_verdict = self.ethics.evaluate(content_action)
        if content_verdict.verdict == "block":
            return content_verdict

        draft_payload = {
            "to": to,
            "subject": subject,
            "body": body.strip(),
            "status": "draft",
            "first_time_contact": to.lower() not in self.sent_recipients,
        }
        return MoralVerdict.safe("Draft created successfully.", action=draft_payload)

    def send(self, draft: dict[str, Any], user_approved: bool = False) -> MoralVerdict:
        """Send an email draft. Requires explicit user approval for first-time recipients."""
        to = draft.get("to", "").strip()
        subject = draft.get("subject", "").strip()
        body = draft.get("body", "").strip()

        if not to or not subject or not body:
            return MoralVerdict.block("Draft is missing required fields (to, subject, body).")

        # 1. Precedent & Approval gate
        is_first_time = to.lower() not in self.sent_recipients
        if is_first_time and not user_approved:
            return MoralVerdict.caution(
                f"First-time contact with {to} requires user approval.",
                risks=["first_time_recipient", "approval_required"],
                action=draft,
            )

        if not self.consent.has_consent("mail_send") and not user_approved:
            return MoralVerdict.caution("Sending email requires explicit consent.", action=draft)

        # 2. Final moral check
        v = self.ethics.evaluate({"action": "mail_send", "to": to, "subject": subject, "body": body})
        if v.verdict == "block":
            self.audit.log_action({"type": "mail_send", "to": to}, v, {"ok": False})
            return v

        # 3. SMTP Execution
        creds = self._get_creds()
        if not creds:
            return MoralVerdict.block("Email credentials not configured in CredentialVault.")

        host = creds.get("smtp_host") or "smtp.gmail.com"
        port = int(creds.get("smtp_port", 587))
        user = creds.get("username") or creds.get("email")
        pw = creds.get("password")

        try:
            msg = MIMEMultipart()
            msg["From"] = user
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls()
            server.login(user, pw)
            server.send_message(msg)
            server.quit()

            self.sent_recipients.add(to.lower())
            self.consent.record_outcome("mail_send", True, f"Sent to {to}")
            self.audit.log_action({"type": "mail_send", "to": to, "subject": subject}, v, {"ok": True})
            return MoralVerdict.safe(f"Email successfully delivered to {to}")
        except Exception as exc:
            log.warning("SMTP send failed to %s: %s", to, exc)
            self.consent.record_outcome("mail_send", False, str(exc))
            self.audit.log_action({"type": "mail_send", "to": to}, v, {"ok": False, "error": str(exc)})
            return MoralVerdict.caution(f"Failed to send email: {exc}", risks=["smtp_error"])


_global_email: Optional[EmailAgent] = None


def get_email_agent() -> EmailAgent:
    global _global_email
    if _global_email is None:
        _global_email = EmailAgent()
    return _global_email
