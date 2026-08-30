"""Passive environmental perception loop for EVO MK2.

Tracks foreground active window, user activity kind, audio playback status,
and maintains an ambient context_state dict injected into conversation turns.
"""
import ctypes
import logging
import os
import threading
import time
from typing import Optional

import psutil

from .bus import bus
from .config import DATA

log = logging.getLogger("mk2.perception")

_context_state: dict = {
    "app": "desktop",
    "window_title": "Windows Desktop",
    "activity": "idle",
    "audio_playing": False,
    "last_app_change": time.time(),
    "duration_in_app_s": 0,
    "last_updated": time.time(),
}
_state_lock = threading.Lock()
_stop_event = threading.Event()


def _get_active_window_info() -> tuple[str, str]:
    """Retrieve foreground application name and window title via Win32 user32."""
    try:
        u32 = ctypes.windll.user32
        hwnd = u32.GetForegroundWindow()
        if not hwnd:
            return "desktop", "Desktop"
        buf = ctypes.create_unicode_buffer(512)
        u32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value.strip() or "Desktop"

        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app_name = "desktop"
        if pid.value > 0:
            try:
                proc = psutil.Process(pid.value)
                app_name = proc.name().replace(".exe", "").lower()
            except Exception:
                app_name = "app"
        return app_name, title
    except Exception:
        return "desktop", "Desktop"


def _infer_activity_kind(app: str, title: str) -> str:
    """Infer activity classification from active app and title."""
    app_low = app.lower()
    title_low = title.lower()

    if any(k in app_low for k in ("code", "pycharm", "cursor", "devenv", "sublime", "notepad++", "terminal", "powershell", "cmd")):
        return "coding"
    elif any(k in app_low for k in ("chrome", "msedge", "firefox", "brave")):
        if any(w in title_low for w in ("youtube", "netflix", "twitch", "spotify")):
            return "media_consumption"
        elif any(w in title_low for w in ("github", "gitlab", "stackoverflow", "docs")):
            return "research_and_development"
        return "web_browsing"
    elif any(k in app_low for k in ("excel", "word", "powerpnt", "slack", "teams", "outlook", "discord")):
        return "work_and_communication"
    elif any(k in app_low for k in ("spotify", "vlc", "wmplayer", "music")):
        return "listening_to_music"
    elif app_low in ("steam", "epicgames"):
        return "gaming"
    return "general_computing"


def get_context_state() -> dict:
    """Return a copy of the ambient perception context state."""
    with _state_lock:
        state = dict(_context_state)
        state["duration_in_app_s"] = int(time.time() - state.get("last_app_change", time.time()))
        return state


def format_perception_prompt() -> str:
    """Format perception state for injection into LLM system messages."""
    state = get_context_state()
    app = state.get("app", "desktop")
    title = state.get("window_title", "")
    activity = state.get("activity", "idle")
    dur_m = state.get("duration_in_app_s", 0) // 60

    if app == "desktop" and not title:
        return ""
    return (
        f"USER AMBIENT CONTEXT: Currently in {app} ('{title[:60]}'), "
        f"activity='{activity}', active for {dur_m}m."
    )


def _perception_worker(interval: float = 5.0) -> None:
    """Background polling loop tracking user window and activity changes."""
    global _context_state
    last_app = ""
    last_title = ""

    while not _stop_event.is_set():
        try:
            app, title = _get_active_window_info()
            activity = _infer_activity_kind(app, title)

            with _state_lock:
                if app != last_app or title != last_title:
                    _context_state["app"] = app
                    _context_state["window_title"] = title
                    _context_state["activity"] = activity
                    _context_state["last_app_change"] = time.time()
                    last_app = app
                    last_title = title
                    bus.publish("perception.window", {
                        "app": app,
                        "title": title,
                        "activity": activity,
                    })
                _context_state["last_updated"] = time.time()
        except Exception as exc:
            log.debug("Perception loop error: %s", exc)

        _stop_event.wait(timeout=interval)


_worker_thread = None


def start_perception_loop(interval: float = 5.0) -> None:
    """Start ambient perception polling loop."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_perception_worker, args=(interval,), daemon=True, name="evo-perception")
    _worker_thread.start()
    log.info("Passive perception loop started (polling every %.1fs)", interval)


def stop_perception_loop() -> None:
    _stop_event.set()


def get_ambient_context() -> dict:
    """Retrieve full ambient context: active window, app, duration, battery, network."""
    from datetime import datetime

    app, title = _get_active_window_info()
    activity = _infer_activity_kind(app, title)

    battery_pct = None
    try:
        batt = psutil.sensors_battery()
        if batt:
            battery_pct = round(batt.percent, 1)
    except Exception:
        pass

    with _state_lock:
        dur = int((time.time() - _context_state.get("last_app_change", time.time())) / 60)
        return {
            "active_app": app,
            "window_title": title,
            "activity": activity,
            "duration_minutes": dur,
            "battery_pct": battery_pct,
            "time": datetime.now().isoformat(),
        }
