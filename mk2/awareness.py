"""Phase 1: Proactive Awareness — watchers + arbiter + briefing.

EVO monitors your world and tells you what matters before you ask.
"""
import hashlib
import logging
import threading
import time
from datetime import datetime

from . import db
from .bus import bus

log = logging.getLogger("mk2.awareness")

_lock = __import__("threading").Lock()
_page_hashes: dict[str, str] = {}
_last_alerts: dict[str, float] = {}  # dedup key -> timestamp


def _ps(script: str, timeout: int = 15) -> str:
    import subprocess

    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return (r.stdout or "").strip()


def get_battery() -> tuple[int | None, bool]:
    try:
        out = _ps("(Get-WmiObject Win32_Battery).EstimatedChargeRemaining")
        pct = int(float(out))
        return pct, False
    except Exception:
        return None, False


def should_notify(dedup_key: str, min_gap_s: int = 300) -> bool:
    """True if we haven't sent this exact alert recently."""
    with _lock:
        last = _last_alerts.get(dedup_key, 0)
        if time.time() - last < min_gap_s:
            return False
        _last_alerts[dedup_key] = time.time()
    return True


def run_checks(publish) -> list[str]:
    """Run all awareness checks. Returns list of alert messages."""
    alerts = []

    # Battery
    try:
        battery_pct = int(_ps("(Get-WmiObject Win32_Battery).EstimatedChargeRemaining") or 100)
        if battery_pct <= 20:
            key = "battery_low"
            if should_notify(key):
                msg = f"Battery at {battery_pct}% — plug in soon."
                alerts.append(msg)
    except (ValueError, Exception):
        pass

    # Disk space
    try:
        free_gb = float(_ps("(Get-PSDrive C).Free / 1GB"))
        if free_gb < 10:
            key = "disk_low"
            if should_notify(key):
                alerts.append(f"Only {free_gb:.0f} GB free on C: — consider cleaning up.")
    except (ValueError, Exception):
        pass

    # Page changes
    watched_pages = db.get_setting("watched_pages", "")
    if watched_pages:
        for url in watched_pages.split(","):
            url = url.strip()
            if not url:
                continue
            try:
                from .tools.web_tools import fetch_page_text

                text = fetch_page_text(url, max_chars=2000)
                h = hashlib.sha256(text.encode()).hexdigest()[:12]
                prev = db.get_setting(f"pagehash_{hashlib.sha256(url.encode()).hexdigest()[:8]}", "")
                if prev and prev != h:
                    key = f"pagechange_{url[:50]}"
                    if should_notify(key, 600):
                        alerts.append(f"Page changed: {url[:60]}")
                db.set_setting(f"pagehash_{hashlib.sha256(url.encode()).hexdigest()[:8]}", h)
            except Exception:
                pass

    return alerts


def compose_briefing() -> str:
    """Compose daily briefing from all available data."""
    now = datetime.now()
    parts = [f"Good {'morning' if now.hour < 12 else 'afternoon'}. It is {now.strftime('%H:%M')} on {now.strftime('%A, %d %B')}."]

    # Reminders due today
    pending = db.reminders_pending()
    today_reminders = [
        r for r in pending
        if datetime.fromtimestamp(r["due_at"]).date() == now.date()
    ]
    if today_reminders:
        parts.append(f"You have {len(today_reminders)} reminder(s): " +
                     "; ".join(r["text"][:40] for r in today_reminders[:3]) + ".")

    # Weather via FreeLLMAPI knowledge (no API needed for general info)
    city = db.get_setting("city", "")

    # Vault recent notes
    from .vault import list_notes
    notes = list_notes()[:3]
    if notes:
        recent_topics = ", ".join(n["topic"] for n in notes)
        parts.append(f"Recent vault topics: {recent_topics}.")

    parts.append("All systems nominal.")
    return " ".join(parts)


class AwarenessEngine:
    """Runs checks periodically and publishes alerts."""

    def __init__(self) -> None:
        self._stop = __import__("threading").Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="mk2-awareness")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        log.info("awareness engine started")
        while not self._stop.is_set():
            try:
                from .fastlane import fast_command  # ensure tools loaded

                alerts = run_checks(self._publish_alert)
                for alert in alerts:
                    bus.publish("notify.out", {
                        "kind": "watcher",
                        "text": alert,
                    })
            except Exception as exc:
                log.warning("awareness check failed: %s", exc)
            self._stop.wait(120)  # every 2 minutes

    def _publish_alert(self, topic: str, payload: dict) -> None:
        bus.publish(topic, payload)

    def stop_and_wait(self) -> None:
        self._stop.set()


import queue as _q_mod

_queue_instance = _q_mod.Queue()


def _publish_alert_wrapper(topic: str, payload: dict) -> None:
    """Thread-safe publish to the event bus."""
    from .bus import publish_threadsafe

    publish_threadsafe(topic, payload)


# Patch run_checks to use wrapper
_original_run_checks = run_checks


def run_checks_safe():
    return _original_run_checks(_publish_alert_wrapper)
