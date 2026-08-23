"""Global error ring — the single place failures land.

Every caught exception worth knowing about flows through log_error().
/api/diag dumps this ring, so "something's wrong" becomes an instant,
specific answer instead of a guessing game.
"""
import threading
import time
from collections import deque

_ring = deque(maxlen=50)
_lock = threading.Lock()


def log_error(component: str, detail: str) -> None:
    with _lock:
        _ring.append({
            "ts": round(time.time(), 2),
            "component": component[:40],
            "detail": str(detail)[:300],
        })


def recent(limit: int = 20) -> list[dict]:
    with _lock:
        items = list(_ring)[-limit:]
    return list(reversed(items))


def clear() -> None:
    with _lock:
        _ring.clear()
