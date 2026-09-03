"""Autonomous Action Audit Logger for EVO MK2 (JARVIS Foundation).

Records full transparency trails of every autonomous attempt, moral evaluation,
precedent check, and execution outcome into SQLite and audit logs.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, Optional

from . import db
from .ethics import MoralVerdict

log = logging.getLogger("mk2.audit")


class AuditLogger:
    """Manages immutable audit logs for autonomous actions."""

    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS autonomous_audit (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        action_type TEXT NOT NULL,
                        action_json TEXT NOT NULL,
                        verdict TEXT NOT NULL,
                        reasoning TEXT,
                        risks_json TEXT,
                        outcome_json TEXT,
                        ok INTEGER NOT NULL DEFAULT 1
                    )
                """)
                con.execute("CREATE INDEX IF NOT EXISTS idx_auto_audit_ts ON autonomous_audit(ts)")
                con.execute("CREATE INDEX IF NOT EXISTS idx_auto_audit_type ON autonomous_audit(action_type)")
        except Exception as exc:
            log.warning("Failed to initialize autonomous_audit schema: %s", exc)

    def log_action(
        self,
        action: dict[str, Any],
        verdict: Optional[MoralVerdict] = None,
        outcome: Optional[dict[str, Any]] = None,
    ) -> int:
        """Log an action, its moral evaluation, and its outcome."""
        now = time.time()
        act_type = str(action.get("type") or action.get("action") or "generic_action")
        v_dict = verdict.to_dict() if verdict else {"verdict": "unverified", "reasoning": "", "risks": []}
        res = outcome or {}
        ok = 1 if res.get("ok", True) and v_dict.get("verdict") != "block" else 0

        row_id = -1
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                cur = con.execute(
                    """
                    INSERT INTO autonomous_audit
                    (ts, action_type, action_json, verdict, reasoning, risks_json, outcome_json, ok)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now,
                        act_type,
                        json.dumps(action, default=str),
                        v_dict.get("verdict", "safe"),
                        v_dict.get("reasoning", ""),
                        json.dumps(v_dict.get("risks", []), default=str),
                        json.dumps(res, default=str),
                        ok,
                    ),
                )
                row_id = cur.lastrowid or -1
        except Exception as exc:
            log.warning("Failed to write to autonomous_audit: %s", exc)

        # Wire into cryptographic tamper-evident audit chain (M8.5)
        try:
            from .audit_chain import record_audit_event
            record_audit_event(
                actor="autonomous_agent",
                action=act_type,
                payload={
                    "action": action,
                    "verdict": v_dict,
                    "outcome": res,
                    "ok": bool(ok),
                },
            )
        except Exception as chain_exc:
            log.debug("Cryptographic audit chain note: %s", chain_exc)

        log.info("Audited autonomous action #%s [%s]: verdict=%s ok=%s", row_id, act_type, v_dict.get("verdict"), ok)
        return row_id

    def get_history(self, action_type: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """Query recent autonomous action audit history."""
        query = "SELECT id, ts, action_type, action_json, verdict, reasoning, risks_json, outcome_json, ok FROM autonomous_audit"
        params: list[Any] = []
        if action_type:
            query += " WHERE action_type = ?"
            params.append(action_type)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        out = []
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.row_factory = sqlite3.Row
                for r in con.execute(query, params).fetchall():
                    out.append({
                        "id": r["id"],
                        "ts": r["ts"],
                        "action_type": r["action_type"],
                        "action": json.loads(r["action_json"] or "{}"),
                        "verdict": r["verdict"],
                        "reasoning": r["reasoning"],
                        "risks": json.loads(r["risks_json"] or "[]"),
                        "outcome": json.loads(r["outcome_json"] or "{}"),
                        "ok": bool(r["ok"]),
                    })
        except Exception as exc:
            log.warning("Failed to query autonomous_audit: %s", exc)
        return out

    def export_report(self, start_ts: float = 0.0, end_ts: float = 0.0) -> str:
        """Generate formatted transparency report for actions in a time window."""
        t_end = end_ts or time.time()
        t_start = start_ts or (t_end - 86400 * 7)  # default last 7 days

        history = []
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.row_factory = sqlite3.Row
                for r in con.execute(
                    "SELECT * FROM autonomous_audit WHERE ts BETWEEN ? AND ? ORDER BY id ASC",
                    (t_start, t_end),
                ).fetchall():
                    history.append(dict(r))
        except Exception:
            pass

        lines = [
            f"# EVO Autonomous Actions Audit Report",
            f"*Period: {time.strftime('%Y-%m-%d %H:%M', time.localtime(t_start))} to {time.strftime('%Y-%m-%d %H:%M', time.localtime(t_end))}*",
            f"**Total Actions Logged:** {len(history)}",
            "",
            "| ID | Time | Action Type | Verdict | Status | Summary |",
            "|---|---|---|---|---|---|",
        ]
        for h in history[-30:]:
            stamp = time.strftime("%m-%d %H:%M", time.localtime(h["ts"]))
            v = h["verdict"]
            status = "OK" if h["ok"] else "FAILED/BLOCKED"
            summary = (h["reasoning"] or "")[:60].replace("|", "/")
            lines.append(f"| {h['id']} | {stamp} | {h['action_type']} | {v} | {status} | {summary} |")

        return "\n".join(lines)


_global_audit: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _global_audit
    if _global_audit is None:
        _global_audit = AuditLogger()
    return _global_audit
