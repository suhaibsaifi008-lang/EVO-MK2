"""Push notifications via ntfy.sh — EVO pings your phone.

Set NTFY_TOPIC to a long random string (it IS the address), install ntfy
on the phone, subscribe to the same topic. When NTFY_TOPIC is configured
the kernel bridges notify.out events (reminders, finished jobs/research)
to the phone automatically; tools can also call push directly.
"""
import logging
import urllib.request

from .config import settings

log = logging.getLogger("mk2.push")


def _post(url: str, data: bytes, headers: dict, timeout: int = 10):
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def push(title: str, message: str, priority: str = "default") -> bool:
    topic = settings.ntfy_topic.strip()
    if not topic:
        return False

    try:
        from .idempotency import generate_idempotency_key, execute_exactly_once
        key = generate_idempotency_key("notification", {"topic": topic, "title": title, "msg": message}, time_window_s=120.0)

        def _do_push() -> bool:
            url = f"{settings.ntfy_server.rstrip('/')}/{topic}"
            headers = {
                "Title": title[:120].encode("utf-8", "ignore"),
                "Priority": priority,
                "Tags": "robot",
            }
            try:
                status = _post(url, (message or "").strip()[:4000].encode("utf-8"), headers=headers)
                return 200 <= status < 300
            except Exception as exc:
                log.warning("push failed: %s", exc)
                return False

        _, ok = execute_exactly_once(key, "notification", _do_push, lease_s=15.0)
        return bool(ok)
    except Exception as exc:
        log.warning("Idempotent push fallback: %s", exc)
        return False


def status() -> dict:
    return {"configured": bool(settings.ntfy_topic),
            "server": settings.ntfy_server}


# ---------------- tool ----------------

from .tools import tool  # noqa: E402


@tool("push_send", "Send a push notification to the user's phone (ntfy).",
      {"title": {"type": "string"}, "message": {"type": "string"}}, permission="comms")
def push_send(title: str, message: str = "") -> dict:
    if not settings.ntfy_topic:
        return {"ok": False,
                "speech": "Push isn't set up - add NTFY_TOPIC to .env.",
                "data": {}}
    ok = push(title or "EVO", message or "(no text)")
    if ok:
        return {"ok": True, "speech": "Pushed to your phone.", "data": {}}
    return {"ok": False, "speech": "The push didn't go through.", "data": {}}


# ---------------- notify.out bridge ----------------

def _bridge(ev) -> None:
    payload = ev.payload or {}
    kind = payload.get("kind", "event")
    text = payload.get("text", "")
    if text:
        push(f"EVO - {kind}", text[:500], priority="high"
             if kind in ("reminder",) else "default")


_sub = {"active": False}


def start_bridge() -> None:
    """Subscribe notify.out -> phone push. Idempotent."""
    if not settings.ntfy_topic or _sub["active"]:
        return
    from .bus import bus

    bus.subscribe("notify.out", _bridge)
    _sub["active"] = True
