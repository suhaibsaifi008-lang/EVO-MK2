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
            with db._lock:
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
            with db._lock:
                with sqlite3.connect(db.DB_PATH) as con:
                    cur = con.execute(
                        """
                        INSERT INTO autonomous_revenue (ts, source, amount, action_type, client_name, status, meta_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (now, source, amount, action_type, client, status, json.dumps(meta or {}, default=str)),
                    )
                    row_id = cur.lastrowid or -1
                    if row_id > 0 and row_id % 50 == 0:
                        con.execute("DELETE FROM autonomous_revenue WHERE id <= (SELECT MAX(id) - 5000 FROM autonomous_revenue)")
                    return row_id
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
            with db._lock:
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

    def get_funnel_metrics(self, days: int = 30) -> dict[str, Any]:
        """Query and compute metrics across all stages of the monetization funnel."""
        cutoff = time.time() - (days * 86400)
        stages = {
            "proposal_sent": 0,
            "client_viewed": 0,
            "client_responded": 0,
            "hired": 0,
            "delivered": 0,
            "paid": 0,
        }
        total_value = 0.0

        try:
            with db._lock:
                with sqlite3.connect(db.DB_PATH) as con:
                    con.row_factory = sqlite3.Row
                    rows = con.execute("SELECT action_type, status, amount FROM autonomous_revenue WHERE ts >= ?", (cutoff,)).fetchall()
                    for r in rows:
                        act = str(r["action_type"] or "")
                        st = str(r["status"] or "")
                        amt = float(r["amount"] or 0.0)

                        stage_matched = None
                        if act in stages:
                            stage_matched = act
                        elif st in stages:
                            stage_matched = st

                        if stage_matched:
                            stages[stage_matched] += 1

                        if st == "paid" or act in ("paid", "payment_received"):
                            total_value += amt
                            if stage_matched != "paid":
                                stages["paid"] = stages.get("paid", 0) + 1
        except Exception as exc:
            log.warning("Failed querying funnel metrics: %s", exc)

        proposals = max(1, stages.get("proposal_sent", 0))
        response_rate = round(stages.get("client_responded", 0) / proposals, 2)
        win_rate = round(stages.get("hired", 0) / proposals, 2)
        delivery_rate = round(stages.get("delivered", 0) / max(1, stages.get("hired", 1)), 2)
        payout_rate = round(stages.get("paid", 0) / max(1, stages.get("hired", 1)), 2)

        return {
            "period_days": days,
            "stages": stages,
            "response_rate": response_rate,
            "win_rate": win_rate,
            "delivery_rate": delivery_rate,
            "payout_rate": payout_rate,
            "total_paid": round(total_value, 2),
            "expected_value_per_hour": round(total_value / max(1, days * 8), 2),
        }


_global_rev: Optional[RevenueTracker] = None


def get_revenue_tracker() -> RevenueTracker:
    global _global_rev
    if _global_rev is None:
        _global_rev = RevenueTracker()
    return _global_rev
