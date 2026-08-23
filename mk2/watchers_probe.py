"""Proactive watchers: monitor system state, pages, calendar — fire alerts.

Watchers run on a tick loop. Each check produces a candidate event.
The arbiter decides whether it's worth interrupting the user.
"""
import hashlib
import logging
import time

from . import db

log = logging.getLogger("mk2.watchers")

_lock = threading_lock = __import__("threading").Lock()


def _ps(script: str, timeout: int = 15) -> str:
    import subprocess
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return (r.stdout or "").strip()


def get_battery() -> dict | None:
    try:
        out = _ps("(Get-WmiObject Win32_Battery).EstimatedChargeRemaining")
        pct = int(float(out))
        charging = "Charging" in _ps("(Get-WmiObject Win32_Battery).BatteryStatus")
        return {"percent": pct, "charging": charging}
    except Exception:
        return None


def get_disk_free(drive: str = "C") -> float | None:
    try:
        out = _ps(f"(Get-PSDrive {drive}).Free / 1GB")
        return round(float(out), 1)
    except Exception:
        return None


def page_hash(url: str) -> str | None:
    try:
        from .tools.web_tools import fetch_page_text
        text = fetch_page_text(url, max_chars=5000)
        return __import__("hashlib").sha256(text.encode()).hexdigest()[:16]
    except Exception:
        return None
