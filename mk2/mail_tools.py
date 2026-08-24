"""Email tools — IMAP read + SMTP send. Draft-first, double-gated.

Reading is a `read` permission tool family. SENDING is deliberately
two-gated (roadmap acceptance):

  Gate 1  mail_send_enabled setup toggle must be ON (env MAIL_SEND_ENABLED=1)
  Gate 2  mail_send requires the exact draft id returned by mail_draft —
          no draft id, no send; drafts are single-use and audited.

Drafts live in data/mail/drafts/<id>.json so you can inspect every word
your assistant almost sent.
"""
import json
import re
import time
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.parser import BytesParser
from email import policy
from pathlib import Path

from . import db
from .config import DATA
from .tools import tool

DRAFTS_DIR = DATA / "mail" / "drafts"


def _draft_path(draft_id: str) -> Path:
    safe = re.sub(r"[^a-z0-9-]", "", str(draft_id).lower())[:40]
    return DRAFTS_DIR / f"{safe}.json"


def _save_draft(to: str, subject: str, body: str) -> tuple[str, Path]:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    draft_id = uuid.uuid4().hex[:12]
    p = _draft_path(draft_id)
    p.write_text(json.dumps({
        "id": draft_id, "to": to.strip(), "subject": subject.strip(),
        "body": body, "created": time.time(), "sent": False,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return draft_id, p


def _load_draft(draft_id: str) -> dict | None:
    p = _draft_path(draft_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ---------------- IMAP ----------------

def _imap():
    import imaplib

    from .config import settings as s

    if not (s.mail_user and s.mail_password and s.mail_imap_host):
        raise RuntimeError(
            "email not configured - set MAIL_ADDRESS / MAIL_PASSWORD / "
            "MAIL_IMAP_HOST in .env")
    conn = imaplib.IMAP4_SSL(s.mail_imap_host, s.mail_imap_port)
    conn.login(s.mail_user, s.mail_password)
    conn.select("INBOX", readonly=True)
    return conn


def _msg_brief(num: int, data: list) -> dict:
    msg = BytesParser(policy=policy.default).parsebytes(data[0][1])
    return {
        "num": num,
        "from": str(msg.get("From", ""))[:120],
        "subject": str(msg.get("Subject", "(no subject)"))[:160],
        "date": str(msg.get("Date", ""))[:40],
    }


def _fetch(limit: int, since_days: int = 7) -> list[dict]:
    conn = _imap()
    try:
        since = (datetime.now() - timedelta(days=max(1, since_days))
                 ).strftime("%d-%b-%Y")
        status, ids = conn.search(None, f'(SINCE "{since}")')
        if status != "OK":
            return []
        nums = ids[0].split()[-limit:]
        out = []
        for n in reversed(nums):  # newest first
            st, data = conn.fetch(n, "(BODY.PEEK[])")
            if st == "OK" and data and data[0]:
                out.append(_msg_brief(n.decode(), data))
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _fetch_one(num: str, max_chars: int = 3000) -> dict:
    conn = _imap()
    try:
        st, data = conn.fetch(str(num).encode(), "(BODY.PEEK[])")
        if st != "OK" or not data or not data[0]:
            raise RuntimeError(f"no message #{num}")
        msg = BytesParser(policy=policy.default).parsebytes(data[0][1])
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    body = part.get_content()
                    break
        else:
            body = msg.get_content()
        body = re.sub(r"\n{3,}", "\n\n", str(body)).strip()
        return {
            "num": str(num),
            "from": str(msg.get("From", ""))[:120],
            "subject": str(msg.get("Subject", "(no subject)"))[:160],
            "date": str(msg.get("Date", ""))[:40],
            "body": body[:max_chars],
        }
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ---------------- tools ----------------

@tool("mail_unread", "List recent inbox emails (newest first).",
      {"limit": {"type": "integer"}, "days": {"type": "integer"}}, permission="read")
def mail_unread(limit: int = 5, days: int = 7) -> dict:
    rows = _fetch(max(1, min(int(limit), 15)), since_days=int(days))
    if not rows:
        return {"ok": True, "speech": "No recent mail in that window.",
                "data": {"messages": []}}
    lines = [f"#{r['num']} {r['from']}: {r['subject']}" for r in rows]
    return {"ok": True, "speech": "; ".join(lines)[:600],
            "data": {"messages": rows}}


@tool("mail_read", "Read one email by its number from mail_unread.",
      {"num": {"type": "string"}}, permission="read")
def mail_read(num: str) -> dict:
    m = _fetch_one(num)
    return {"ok": True,
            "speech": f"From {m['from']}: {m['subject']}. {m['body'][:300]}",
            "data": {"message": m}}


@tool("mail_draft", "Compose an email draft. Nothing is sent until mail_send approves it.",
      {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
      permission="comms")
def mail_draft(to: str, subject: str, body: str) -> dict:
    if "@" not in to:
        return {"ok": False, "speech": "That doesn't look like an email address.", "data": {}}
    draft_id, path = _save_draft(to, subject, body)
    db.audit("mail_draft", json.dumps({"to": to, "subject": subject}, default=str),
             True, f"draft={draft_id}")
    return {
        "ok": True,
        "speech": (f"DRAFT ready (not sent). To: {to.strip()} | Subject: {subject.strip()} "
                   f"| First line: {body.strip().splitlines()[0][:80] if body.strip() else ''} "
                   f"| Send it with mail_send draft={draft_id}."),
        "data": {"draft_id": draft_id, "path": str(path)},
    }


@tool("mail_send", "Send a previously created draft by id. Refuses without MAIL_SEND_ENABLED=1.",
      {"draft_id": {"type": "string"}}, permission="comms")
def mail_send(draft_id: str) -> dict:
    from .config import settings as s

    # Gate 1: setup toggle
    if not s.mail_send_enabled:
        db.audit("mail_send", f"draft={draft_id}", False, "send disabled")
        return {
            "ok": False,
            "speech": ("I have the draft ready but sending is locked. Turn on "
                       "MAIL_SEND_ENABLED=1 in .env first - I will never email "
                       "anyone without that switch."),
            "data": {},
        }
    # Gate 2: exact existing unsent draft
    d = _load_draft(draft_id)
    if not d:
        db.audit("mail_send", f"draft={draft_id}", False, "unknown draft")
        return {"ok": False, "speech": f"No draft '{draft_id}'. Create one with mail_draft first.", "data": {}}
    if d.get("sent"):
        db.audit("mail_send", f"draft={draft_id}", False, "already sent")
        return {"ok": False, "speech": "That draft was already sent.", "data": {}}

    try:
        _smtp_send(d["to"], d["subject"], d["body"])
    except Exception as exc:
        db.audit("mail_send", f"draft={draft_id}", False, str(exc))
        return {"ok": False, "speech": f"Send failed: {str(exc)[:200]}", "data": {}}
    d["sent"] = True
    d["sent_at"] = time.time()
    _draft_path(draft_id).write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    db.audit("mail_send", json.dumps({"to": d["to"], "subject": d["subject"]}),
             True, f"draft={draft_id}")
    return {"ok": True, "speech": f"Sent to {d['to']}.", "data": {"draft_id": draft_id}}


def _smtp_send(to: str, subject: str, body: str) -> None:
    import smtplib

    from .config import settings as s

    msg = EmailMessage()
    msg["From"] = s.mail_user
    msg["To"] = to.strip()
    msg["Subject"] = subject.strip()[:200]
    msg.set_content(body[:20000])
    with smtplib.SMTP_SSL(s.mail_smtp_host, s.mail_smtp_port, timeout=30) as srv:
        srv.login(s.mail_user, s.mail_password)
        srv.send_message(msg)
