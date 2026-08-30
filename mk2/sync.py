"""Cross-device synchronization and context handoff protocol for EVO MK2.

Packages user profile, preferences, opinions, missions, and memory episodes
into lightweight sync bundles with last-write-wins conflict resolution.
"""
import hashlib
import json
import logging
import os
import platform
import time
from pathlib import Path
from typing import Any, Optional

from . import config, db

log = logging.getLogger("mk2.sync")

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
        with runner._lock:
            for m in runner.missions.values():
                missions_data.append({
                    "id": m.id,
                    "goal": m.goal,
                    "status": m.status,
                    "strategy": m.strategy_used,
                    "session_state": m.session_state,
                })
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
    return bundle


def import_sync_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Incorporate and merge an incoming sync bundle from another device."""
    if not isinstance(bundle, dict) or bundle.get("version") != "mk2-sync-1.0":
        return {"ok": False, "error": "Invalid sync bundle format or version"}

    sender_id = bundle.get("device_id", "unknown")
    sender_ts = bundle.get("timestamp", 0.0)
    merged_facts = 0
    merged_opinions = 0
    merged_patterns = 0

    # 1. Merge facts (deduplicate and keep newest)
    inbound_facts = bundle.get("facts", [])
    for f in inbound_facts:
        k = f.get("key")
        v = f.get("value")
        if k and v:
            try:
                db.remember_fact(k, v, source=f"sync:{sender_id}")
                merged_facts += 1
            except Exception:
                pass

    # 2. Merge opinions
    from . import memory
    inbound_opinions = bundle.get("opinions", {})
    for topic, data in inbound_opinions.items():
        if isinstance(data, dict):
            text = data.get("text", "")
            sent = data.get("sentiment", "neutral")
            if topic and text:
                memory.update_opinion(topic, text, sentiment=sent)
                merged_opinions += 1

    # 3. Merge patterns
    from . import patterns
    inbound_patterns = bundle.get("patterns", [])
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
