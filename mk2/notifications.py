"""Phase 7 / Tier 2: Windows toast notification bridge for EVO MK2.

Listens to notify.out, autonomy.confirm, and system.alert bus events,
displaying native Windows desktop notifications.
"""
import logging
import subprocess
import threading
from typing import Any

from .bus import bus

log = logging.getLogger("mk2.notifications")


def show_toast(title: str, message: str, urgency: str = "medium") -> bool:
    """Display a native Windows toast notification using PowerShell WinRT with injection-proof encoding."""
    import base64
    safe_title = (title or "EVO")[:64]
    safe_msg = (message or "")[:256]
    b64_title = base64.b64encode(safe_title.encode("utf-8")).decode("ascii")
    b64_msg = base64.b64encode(safe_msg.encode("utf-8")).decode("ascii")

    # Windows Notification XML via PowerShell using safe Base64 decoding
    ps_cmd = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template)
$title = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{b64_title}'))
$msg = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('{b64_msg}'))
$elements = $xml.GetElementsByTagName("text")
$elements.Item(0).AppendChild($xml.CreateTextNode($title)) > $null
$elements.Item(1).AppendChild($xml.CreateTextNode($msg)) > $null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("EVO Assistant").Show($toast)
"""

    def _run_ps():
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            log.debug("Toast dispatch failed: %s", exc)

    threading.Thread(target=_run_ps, daemon=True, name="toast-worker").start()
    return True


def _on_notify(topic: str, payload: dict[str, Any]) -> None:
    text = str(payload.get("text", payload.get("speech", ""))).strip()
    if not text:
        return
    kind = str(payload.get("kind", "notification"))
    urgency = str(payload.get("urgency", "medium"))
    title = f"EVO [{kind.capitalize()}]" if kind != "notification" else "EVO Assistant"
    show_toast(title, text, urgency)


def start_notification_bridge() -> None:
    """Wire bus subscriptions to desktop toasts."""
    bus.subscribe("notify.out", _on_notify)
    bus.subscribe("autonomy.confirm", lambda t, p: show_toast("EVO Confirmation Needed", p.get("text", "Confirm action?"), "high"))
    bus.subscribe("system.alert", lambda t, p: show_toast("EVO System Alert", str(p), "high"))
    log.info("Desktop toast notification bridge armed")
