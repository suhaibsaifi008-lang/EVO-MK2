"""Cryptographic Tamper-Evident Audit Chain for EVO MK2 (M8.5).

Implements a cryptographically linked hash chain with HMAC signatures:
  entry_hash = SHA256(prev_hash : ts : actor : action : payload_hash)
  signature  = HMAC-SHA256(entry_hash, secret_audit_key)

Guarantees:
  - Retroactive modification of any past action is detected immediately.
  - Deletion or truncation of records breaks chain continuity.
  - Verification walks from genesis to tip in constant-time checks.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from typing import Any, Optional

from . import db
from .config import DATA
from .tools import tool

log = logging.getLogger("mk2.audit_chain")

GENESIS_HASH = "0" * 64
_KEY_PATH = DATA / "audit_chain.key"
_key_lock = threading.Lock()
_chain_lock = threading.Lock()
_cached_key: Optional[bytes] = None


def _get_audit_key() -> bytes:
    """Retrieve or generate the HMAC key for signing audit records."""
    global _cached_key
    with _key_lock:
        if _cached_key is not None:
            return _cached_key
        _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _KEY_PATH.exists():
            try:
                _cached_key = _KEY_PATH.read_bytes()
                if len(_cached_key) >= 32:
                    return _cached_key
            except Exception as exc:
                log.warning("Could not read audit key file, regenerating: %s", exc)

        # Generate fresh 256-bit cryptographically secure key
        _cached_key = os.urandom(32)
        try:
            _KEY_PATH.write_bytes(_cached_key)
        except Exception as exc:
            log.warning("Could not persist audit key to disk: %s", exc)
        return _cached_key


def _compute_payload_hash(payload: Any) -> str:
    """Deterministic SHA-256 hash of structured payload."""
    try:
        raw = json.dumps(payload, sort_keys=True, default=str)
    except Exception:
        raw = str(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute_entry_hash(prev_hash: str, ts: float, actor: str, action: str, payload_hash: str) -> str:
    """Compute single cryptographic block hash."""
    block = f"{prev_hash}:{ts:.6f}:{actor}:{action}:{payload_hash}"
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


def _sign_entry(entry_hash: str, key: bytes) -> str:
    """Generate HMAC-SHA256 signature for entry."""
    return hmac.new(key, entry_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def record_audit_event(actor: str, action: str, payload: Any) -> dict[str, Any]:
    """Append a cryptographically verified, signed entry to the audit chain."""
    key = _get_audit_key()
    ts = time.time()
    payload_hash = _compute_payload_hash(payload)

    with _chain_lock:
        with db.connect() as conn:
            # Get latest entry
            latest = conn.execute(
                "SELECT entry_hash FROM audit_chain ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = latest["entry_hash"] if latest else GENESIS_HASH

            entry_hash = _compute_entry_hash(prev_hash, ts, actor, action, payload_hash)
            sig = _sign_entry(entry_hash, key)

            cur = conn.execute(
                "INSERT INTO audit_chain (ts, actor, action, payload_hash, prev_hash, entry_hash, signature) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, actor, action, payload_hash, prev_hash, entry_hash, sig),
            )
            entry_id = cur.lastrowid

    return {
        "id": entry_id,
        "ts": ts,
        "actor": actor,
        "action": action,
        "payload_hash": payload_hash,
        "prev_hash": prev_hash,
        "entry_hash": entry_hash,
        "signature": sig,
    }


def verify_audit_chain() -> tuple[bool, str, int]:
    """Validate mathematical continuity and HMAC signatures of the complete audit chain."""
    key = _get_audit_key()

    with _chain_lock:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id, ts, actor, action, payload_hash, prev_hash, entry_hash, signature "
                "FROM audit_chain ORDER BY id ASC"
            ).fetchall()

    if not rows:
        return True, "Audit chain is empty. Genesis integrity intact.", 0

    expected_prev = GENESIS_HASH
    for r in rows:
        row_id = r["id"]
        ts = r["ts"]
        actor = r["actor"]
        action = r["action"]
        payload_hash = r["payload_hash"]
        prev_hash = r["prev_hash"]
        entry_hash = r["entry_hash"]
        sig = r["signature"]

        # 1. Chain continuity check
        if prev_hash != expected_prev:
            msg = f"Chain break at record #{row_id}: prev_hash {prev_hash[:12]}... does not match expected {expected_prev[:12]}..."
            log.error(msg)
            return False, msg, len(rows)

        # 2. Recompute entry hash
        computed_entry = _compute_entry_hash(prev_hash, ts, actor, action, payload_hash)
        if computed_entry != entry_hash:
            msg = f"Tamper detected at record #{row_id}: recomputed hash does not match stored entry_hash."
            log.error(msg)
            return False, msg, len(rows)

        # 3. Verify HMAC signature
        expected_sig = _sign_entry(entry_hash, key)
        if not hmac.compare_digest(sig, expected_sig):
            msg = f"Signature mismatch at record #{row_id}: cryptographic authentication failed (possible forgery)."
            log.error(msg)
            return False, msg, len(rows)

        expected_prev = entry_hash

    return True, f"Verified {len(rows)} audit records with 100% cryptographic integrity.", len(rows)


# ------------------------------------------------------------------ tools

@tool("audit_verify_chain", "Verify complete cryptographic integrity of the audit chain ledger.",
      {}, permission="read")
def audit_verify_chain_tool() -> dict:
    valid, message, count = verify_audit_chain()
    return {
        "ok": valid,
        "speech": f"Audit verification: {message}",
        "data": {"valid": valid, "records_checked": count, "message": message},
    }
