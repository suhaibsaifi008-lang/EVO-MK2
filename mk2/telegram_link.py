"""Telegram link — EVO on your phone, same brain as the console.

Long-polling bot (stdlib only, no new deps). Security model:

  - UNPAIRED chat_ids get ZERO responses (not even an error message).
  - Pairing: a numeric code is generated and shown in the console
    (`/api/pairing`); the first `/start <code>` from Telegram locks that
    chat_id in. Everything else stays silent forever.
  - Messages run through brain.handle_turn(surface="telegram") — identical
    tools, memory and audit trail as the console.
  - Every notify.out event (reminders, finished research/jobs) is mirrored
    to the paired chat.
"""
import json
import logging
import secrets
import threading
import urllib.parse
import urllib.request

from . import db
from .bus import bus
from .config import settings

log = logging.getLogger("mk2.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
CHAT_KEY = "telegram_chat_id"
CODE_KEY = "telegram_pair_code"
MAXLEN = 3900  # telegram hard limit is 4096; leave headroom


# ---------------- transport (monkeypatched in tests) ----------------

def _http(url: str, timeout: int = 35):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _api(method: str, token: str = "", **params):
    token = token or settings.telegram_token
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = API.format(token=token, method=method) + ("?" + qs if qs else "")
    return _http(url)


# ---------------- pairing ----------------

def paired_chat() -> str:
    return db.get_setting(CHAT_KEY, "").strip()


def pairing_code() -> str:
    """Current pairing code; generated on first use, survives restarts."""
    code = db.get_setting(CODE_KEY, "")
    if code:
        return code
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.set_setting(CODE_KEY, code)
    return code


def pair(chat_id: str, code: str) -> bool:
    if str(code).strip() == pairing_code():
        db.set_setting(CHAT_KEY, str(chat_id))
        db.set_setting(CODE_KEY, "")  # single-use
        db.audit("telegram_pair", f"chat={chat_id}", True, "paired")
        return True
    db.audit("telegram_pair", f"chat={chat_id}", False, "bad code")
    return False


def unpair() -> None:
    db.set_setting(CHAT_KEY, "")


def status() -> dict:
    return {
        "configured": bool(settings.telegram_token),
        "paired": bool(paired_chat()),
        "pairing_code": pairing_code(),
    }


# ---------------- sending ----------------

def send_message(text: str, chat_id: str = "", chunk: int = MAXLEN) -> bool:
    cid = chat_id or paired_chat()
    if not cid or not settings.telegram_token:
        return False
    body = (text or "").strip() or "(empty)"
    ok = True
    for i in range(0, len(body), chunk):
        try:
            r = _api("sendMessage", chat_id=cid,
                     text=body[i:i + chunk])
            ok = ok and bool(r.get("ok"))
        except Exception as exc:
            log.warning("send failed: %s", exc)
            errlog_note("send", exc)
            ok = False
    return ok


def errlog_note(where: str, exc: Exception) -> None:
    try:
        from . import errlog

        errlog.log_error(f"telegram:{where}", str(exc))
    except Exception:
        pass


# ---------------- receiving ----------------

def handle_update(update: dict) -> None:
    """Process one Telegram update. Unpaired chats are ignored SILENTLY."""
    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (msg.get("text") or "").strip()
    if not chat_id or not text:
        return

    if not paired_chat():
        parts = text.split()
        if parts[0].startswith("/start") and len(parts) > 1 and pair(chat_id, parts[1]):
            send_message("Paired. This phone now talks to your PC.", chat_id)
            bus.publish("notify.out", {"kind": "telegram",
                                       "text": "Telegram paired successfully."})
        return  # unpaired + wrong/no code: total silence

    if chat_id != paired_chat():
        return  # a stranger after pairing: still zero responses

    if text.startswith("/start"):
        send_message("Already paired. Just talk to me like you do at the console.", chat_id)
        return
    if text.startswith("/status"):
        send_message("Online. This chat is linked to your PC.", chat_id)
        return

    # same brain, tools, memory, audit as the console
    try:
        reply = _brain_turn(text)
    except Exception as exc:
        reply = f"That blew up mid-turn: {str(exc)[:200]}"
        errlog_note("turn", exc)
    send_message(reply, chat_id)


def _brain_turn(text: str) -> str:
    from . import brain

    return brain.handle_turn(text, surface="telegram")


def _bridge_notify(ev) -> None:
    """Mirror notify.out events to the paired phone."""
    payload = ev.payload or {}
    kind = payload.get("kind", "event")
    text = payload.get("text", "")
    if text:
        send_message(f"[{kind}] {text}"[:MAXLEN])


# ---------------- polling loop ----------------

_bridge = {"on": False}


def ensure_bridge() -> None:
    """Subscribe notify.out -> phone mirror. Idempotent across restarts."""
    if _bridge["on"]:
        return
    bus.subscribe("notify.out", _bridge_notify)
    _bridge["on"] = True


def run_polling(stop_evt: threading.Event, poll_seconds: int = 25) -> None:
    """Blocking long-poll loop. The kernel runs it via to_thread + supervises."""
    if not settings.telegram_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    ensure_bridge()
    offset = 0
    backoff = 2
    log.info("telegram link online (polling)")
    while not stop_evt.is_set():
        try:
            r = _api("getUpdates", timeout=poll_seconds,
                     offset=offset, allowed_updates=json.dumps(["message"]))
            backoff = 2
            for upd in r.get("result", []):
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
                try:
                    handle_update(upd)
                except Exception as exc:
                    log.warning("update failed: %s", exc)
                    errlog_note("update", exc)
        except Exception as exc:
            errlog_note("poll", exc)
            stop_evt.wait(backoff)
            backoff = min(backoff * 2, 30)
