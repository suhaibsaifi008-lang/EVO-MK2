"""Revenue and Outcome Tracker for EVO MK2 (JARVIS Phase 5).

Persists autonomous business actions, conversions, proposals, and revenue metrics in SQLite.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, Optional

from . import db

log = logging.getLogger("mk2.revenue")


class RevenueTracker:
    """Tracks actions, conversion funnels, and actual income generated."""

    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS autonomous_revenue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        source TEXT NOT NULL,
                        amount REAL NOT NULL DEFAULT 0.0,
                        action_type TEXT NOT NULL,
                        client_name TEXT,
                        status TEXT NOT NULL,
                        meta_json TEXT
                    )
                """)
                con.execute("CREATE INDEX IF NOT EXISTS idx_rev_ts ON autonomous_revenue(ts)")
        except Exception as exc:
            log.warning("Failed to init autonomous_revenue table: %s", exc)

    def record_action(self, source: str, action_type: str, client: str = "", amount: float = 0.0, status: str = "initiated", meta: dict | None = None) -> int:
        now = time.time()
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                cur = con.execute(
                    """
                    INSERT INTO autonomous_revenue (ts, source, amount, action_type, client_name, status, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now, source, amount, action_type, client, status, json.dumps(meta or {}, default=str)),
                )
                return cur.lastrowid or -1
        except Exception as exc:
            log.warning("Failed recording revenue action: %s", exc)
            return -1

    def record_payment(self, amount: float, source: str, client: str = "", note: str = "") -> int:
        log.info("Recording revenue received: $%.2f from %s (%s)", amount, source, client)
        return self.record_action(source, "payment_received", client=client, amount=amount, status="paid", meta={"note": note})

    def get_stats(self, days: int = 7) -> dict[str, Any]:
        cutoff = time.time() - (days * 86400)
        total_revenue = 0.0
        total_actions = 0
        by_source: dict[str, float] = {}

        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute("SELECT * FROM autonomous_revenue WHERE ts >= ?", (cutoff,)).fetchall()
                for r in rows:
                    total_actions += 1
                    amt = float(r["amount"] or 0.0)
                    src = str(r["source"] or "unknown")
                    if r["status"] == "paid":
                        total_revenue += amt
                        by_source[src] = by_source.get(src, 0.0) + amt
        except Exception as exc:
            log.warning("Failed querying revenue stats: %s", exc)

        return {
            "period_days": days,
            "total_revenue": round(total_revenue, 2),
            "actions_count": total_actions,
            "by_source": by_source,
        }

    def weekly_report(self) -> str:
        stats = self.get_stats(7)
        lines = [
            f"# EVO Weekly Revenue & Opportunity Report",
            f"**Total Revenue Paid:** ${stats['total_revenue']:.2f}",
            f"**Autonomous Actions Taken:** {stats['actions_count']}",
        ]
        if stats["by_source"]:
            lines.append("### Breakdown by Source:")
            for s, rev in stats["by_source"].items():
                lines.append(f"- **{s.capitalize()}**: ${rev:.2f}")
        else:
            lines.append("*No revenue deposits recorded this period.*")
        return "\n".join(lines)


_global_rev: Optional[RevenueTracker] = None


def get_revenue_tracker() -> RevenueTracker:
    global _global_rev
    if _global_rev is None:
        _global_rev = RevenueTracker()
    return _global_rev
