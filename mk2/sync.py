"""Cross-device synchronization and context handoff protocol for EVO MK2.

Packages user profile, preferences, opinions, missions, and memory episodes
into lightweight sync bundles with last-write-wins conflict resolution.
"""
import hashlib
import hmac
import json
import logging
import os
import platform
import time
from pathlib import Path
from typing import Any, Optional

from . import config, db

log = logging.getLogger("mk2.sync")


def _get_sync_secret() -> bytes:
    """Resolve shared secret for HMAC bundle integrity verification."""
    key = os.environ.get("EVO_SYNC_SECRET", "").strip()
    if not key:
        sync_key_file = config.DATA / "sync_secret.key"
        if sync_key_file.exists():
            try:
                key = sync_key_file.read_text(encoding="utf-8").strip()
            except Exception:
                pass
        if not key:
            key = hashlib.sha256(f"evo-sync-{get_device_id()}".encode("utf-8")).hexdigest()
            try:
                sync_key_file.parent.mkdir(parents=True, exist_ok=True)
                sync_key_file.write_text(key, encoding="utf-8")
            except Exception:
                pass
    return key.encode("utf-8")


def _compute_bundle_signature(bundle: dict[str, Any], secret: bytes) -> str:
    """Compute HMAC-SHA256 signature over canonical JSON representation of bundle payload."""
    payload = {k: v for k, v in bundle.items() if k != "signature"}
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(secret, payload_bytes, hashlib.sha256).hexdigest()

DEVICE_ID_FILE = config.DATA / "device_id.txt"


def get_device_id() -> str:
    """Retrieve or generate a stable device identifier."""
    if DEVICE_ID_FILE.exists():
        try:
            val = DEVICE_ID_FILE.read_text(encoding="utf-8").strip()
            if val:
                return val
        except Exception:
            pass
    raw = f"{platform.node()}-{platform.machine()}-{time.time()}"
    did = hashlib.sha256(raw.encode()).hexdigest()[:12]
    try:
        DEVICE_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_ID_FILE.write_text(did, encoding="utf-8")
    except Exception:
        pass
    return did


def export_sync_bundle() -> dict[str, Any]:
    """Package current device state for export to peer devices."""
    from . import memory, patterns
    from .awareness import get_user_meta

    # 1. Profile and preferences
    profile = memory.get_user_profile()
    opinions = memory.get_user_opinions()
    meta = get_user_meta()

    # 2. Recent episodes & facts
    facts = []
    try:
        with db._lock, db.connect() as c:
            rows = c.execute("SELECT key, value, source, updated_at FROM facts ORDER BY updated_at DESC LIMIT 100").fetchall()
            facts = [dict(r) for r in rows]
    except Exception:
        pass

    # 3. Active missions
    missions_data = []
    try:
        from . import autonomy
        runner = autonomy.get_runner()
        missions_data = [
            {"id": m.id, "goal": m.goal, "status": m.status, "created": m.created_at}
            for m in runner.missions.values()
        ]
    except Exception:
        pass

    # 4. Learned patterns
    pat_list = patterns._load_patterns()

    bundle = {
        "version": "mk2-sync-1.0",
        "device_id": get_device_id(),
        "device_name": platform.node(),
        "timestamp": time.time(),
        "profile": profile,
        "opinions": opinions,
        "meta": meta,
        "facts": facts,
        "missions": missions_data,
        "patterns": pat_list,
    }
    secret = _get_sync_secret()
    bundle["signature"] = _compute_bundle_signature(bundle, secret)
    return bundle


def import_sync_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Incorporate and merge an incoming sync bundle from another device."""
    if not isinstance(bundle, dict) or bundle.get("version") != "mk2-sync-1.0":
        return {"ok": False, "error": "Invalid sync bundle format or version"}

    secret = _get_sync_secret()
    sig = bundle.get("signature")
    if sig:
        expected_sig = _compute_bundle_signature(bundle, secret)
        if not hmac.compare_digest(sig, expected_sig):
            log.warning("Sync bundle HMAC signature verification failed.")
            return {"ok": False, "error": "Invalid sync bundle signature (tampered or mismatched secret)"}

    sender_id = bundle.get("device_id", "unknown")
    sender_ts = bundle.get("timestamp", 0.0)

    # Replay window: reject bundles older than 5 minutes
    if abs(time.time() - sender_ts) > 300.0:
        log.warning("Sync bundle from %s rejected: timestamp too far from current time (replay window).", sender_id)
        return {"ok": False, "error": "Sync bundle timestamp outside 5-minute replay window"}

    merged_facts = 0
    merged_opinions = 0
    merged_patterns = 0

    # 1. Merge facts with true Last-Write-Wins (LWW) conflict resolution
    inbound_facts = bundle.get("facts", [])[:200]  # Bound inbound facts
    local_facts = {f["key"]: f for f in db.all_facts(200)}
    for f in inbound_facts:
        k = str(f.get("key") or "").strip()[:80]
        v = str(f.get("value") or "").strip()[:500]
        inbound_updated = float(f.get("updated_at") or sender_ts or 0.0)
        if k and v:
            if any(p in v.lower() for p in ("ignore previous", "system prompt", "you are now")):
                continue
            local_entry = local_facts.get(k)
            # If local fact exists and is strictly newer than incoming fact, keep local
            if local_entry and float(local_entry.get("updated_at", 0.0)) > inbound_updated:
                continue
            try:
                db.remember_fact(k, v, source=f"sync:{sender_id}")
                merged_facts += 1
            except Exception:
                pass

    # 2. Merge opinions with deduplication
    from . import memory
    inbound_opinions = bundle.get("opinions", {})
    # Bound inbound opinions to 50 topics
    if len(inbound_opinions) > 50:
        inbound_opinions = dict(list(inbound_opinions.items())[:50])
    existing_opinions = memory.get_user_opinions()
    for topic, data in inbound_opinions.items():
        if isinstance(data, dict):
            text = data.get("text", "")
            sent = data.get("sentiment", "neutral")
            if topic and text:
                local_op = existing_opinions.get(topic)
                # Deduplicate if sentiment and text are already identical to avoid count inflation (H11)
                if local_op and local_op.get("sentiment") == sent and text in local_op.get("text", ""):
                    continue
                memory.update_opinion(topic, text, sentiment=sent)
                merged_opinions += 1

    # 3. Merge patterns
    from . import patterns
    inbound_patterns = bundle.get("patterns", [])[:50]  # Bound inbound patterns
    local_patterns = patterns._load_patterns()
    local_ids = {p.get("id") for p in local_patterns}
    for p in inbound_patterns:
        if p.get("id") not in local_ids:
            local_patterns.append(p)
            merged_patterns += 1
    patterns._save_patterns(local_patterns)

    log.info(
        "Sync bundle from %s (%s) merged: %d facts, %d opinions, %d patterns",
        sender_id, bundle.get("device_name", ""), merged_facts, merged_opinions, merged_patterns,
    )

    return {
        "ok": True,
        "source_device": sender_id,
        "merged": {
            "facts": merged_facts,
            "opinions": merged_opinions,
            "patterns": merged_patterns,
        },
    }
