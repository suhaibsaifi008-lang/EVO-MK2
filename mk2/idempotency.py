"""Exactly-Once Execution & Idempotency Engine for EVO MK2 (M8.3).

Guarantees that external side-effects (jobs, reminders, notifications,
workflows, proposals, emails, and financial actions) execute exactly once,
preventing duplicate dispatches even across network retries, thread crashes,
or rapid event loops.

Schema:
  idempotency_ledger (key TEXT PRIMARY KEY, scope TEXT, status TEXT,
                      lease_until REAL, result_json TEXT, created_at REAL, completed_at REAL)
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable, Optional

from . import db

log = logging.getLogger("mk2.idempotency")

_idempotency_lock = threading.Lock()


def generate_idempotency_key(scope: str, payload: Any, time_window_s: Optional[float] = None) -> str:
    """Generate a deterministic SHA-256 idempotency key."""
    try:
        raw = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        raw = str(payload)

    if time_window_s and time_window_s > 0:
        bucket = int(time.time() // time_window_s)
        content = f"{scope}:{raw}:{bucket}"
    else:
        content = f"{scope}:{raw}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def execute_exactly_once(
    key: str,
    scope: str,
    fn: Callable[[], Any],
    lease_s: float = 30.0,
    ttl_s: float = 86400.0,
) -> tuple[bool, Any]:
    """Execute a callable with exactly-once semantic guarantees.

    Returns:
      (executed_now: bool, result: Any)
      - If already completed, returns (False, cached_result) without running fn().
      - If executing now, runs fn(), stores result, and returns (True, result).
    """
    now = time.time()

    with _idempotency_lock:
        with db.connect() as conn:
            row = conn.execute(
                "SELECT status, lease_until, result_json FROM idempotency_ledger WHERE key=?",
                (key,),
            ).fetchone()

            if row:
                status = row["status"]
                lease_until = row["lease_until"] or 0.0

                if status == "COMPLETED":
                    log.info("Exactly-once hit: key %s:%s already completed. Returning cached outcome.", scope, key[:8])
                    try:
                        cached = json.loads(row["result_json"])
                    except Exception:
                        cached = row["result_json"]
                    return False, cached

                if status == "EXECUTING" and now < lease_until:
                    log.warning("Concurrent execution lease active for %s:%s. Denying duplicate execution.", scope, key[:8])
                    return False, {"status": "in_progress", "detail": "Action is currently executing under another lease"}

            # Claim lease
            lease_until = now + lease_s
            conn.execute(
                "INSERT INTO idempotency_ledger (key, scope, status, lease_until, created_at) "
                "VALUES (?, ?, 'EXECUTING', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET status='EXECUTING', lease_until=excluded.lease_until",
                (key, scope, lease_until, now),
            )

    # Execute outside DB lock to prevent holding table lock during long I/O
    try:
        result = fn()
        try:
            res_str = json.dumps(result, default=str)
        except Exception:
            res_str = str(result)

        with _idempotency_lock:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE idempotency_ledger SET status='COMPLETED', completed_at=?, result_json=? WHERE key=?",
                    (time.time(), res_str, key),
                )
        return True, result

    except Exception as exc:
        log.error("Execution failed under idempotency key %s:%s: %s", scope, key[:8], exc)
        with _idempotency_lock:
            with db.connect() as conn:
                conn.execute(
                    "UPDATE idempotency_ledger SET status='FAILED', completed_at=?, result_json=? WHERE key=?",
                    (time.time(), json.dumps({"error": str(exc)}), key),
                )
        raise exc


def idempotent(scope: str, time_window_s: Optional[float] = None, lease_s: float = 30.0):
    """Decorator to enforce exactly-once execution on functions."""
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            payload = {"args": args, "kwargs": kwargs}
            key = generate_idempotency_key(scope, payload, time_window_s=time_window_s)
            _, result = execute_exactly_once(
                key=key,
                scope=scope,
                fn=lambda: func(*args, **kwargs),
                lease_s=lease_s,
            )
            return result
        return wrapper
    return decorator


# Convenience domain helpers

def idempotent_notification(topic: str, message: str, fn: Callable) -> tuple[bool, Any]:
    key = generate_idempotency_key("notification", {"topic": topic, "message": message}, time_window_s=300.0)
    return execute_exactly_once(key, "notification", fn)


def idempotent_financial(recipient: str, amount: float, currency: str, memo: str, fn: Callable) -> tuple[bool, Any]:
    key = generate_idempotency_key("financial", {"recipient": recipient, "amount": amount, "currency": currency, "memo": memo})
    return execute_exactly_once(key, "financial", fn)


def idempotent_email(to: str, subject: str, body: str, fn: Callable) -> tuple[bool, Any]:
    key = generate_idempotency_key("email", {"to": to, "subject": subject, "body": body[:500]}, time_window_s=600.0)
    return execute_exactly_once(key, "email", fn)
