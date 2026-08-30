"""Autonomous Strategy Learner for EVO MK2 (JARVIS Phase 13)."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any, Optional

from . import db

log = logging.getLogger("mk2.strategy_learner")


class StrategyLearner:
    """Adapts business strategies based on empirical win/loss patterns."""

    def __init__(self):
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.execute("""
                    CREATE TABLE IF NOT EXISTS autonomous_strategy (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts REAL NOT NULL,
                        category TEXT NOT NULL,
                        strategy_key TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        reward REAL NOT NULL DEFAULT 0.0,
                        meta_json TEXT
                    )
                """)
                con.execute("CREATE INDEX IF NOT EXISTS idx_strat_cat ON autonomous_strategy(category)")
        except Exception as exc:
            log.warning("Strategy schema error: %s", exc)

    def record_outcome(self, category: str, strategy_key: str, success: bool, reward: float = 0.0, meta: dict | None = None) -> None:
        outcome = "win" if success else "loss"
        now = time.time()
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.execute(
                    "INSERT INTO autonomous_strategy (ts, category, strategy_key, outcome, reward, meta_json) VALUES (?, ?, ?, ?, ?, ?)",
                    (now, category, strategy_key, outcome, reward, json.dumps(meta or {})),
                )
        except Exception as exc:
            log.warning("Failed recording strategy outcome: %s", exc)

    def get_win_rate(self, category: str, strategy_key: str) -> float:
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                cur = con.cursor()
                total = cur.execute("SELECT COUNT(*) FROM autonomous_strategy WHERE category = ? AND strategy_key = ?", (category, strategy_key)).fetchone()[0]
                if not total:
                    return 0.5
                wins = cur.execute("SELECT COUNT(*) FROM autonomous_strategy WHERE category = ? AND strategy_key = ? AND outcome = 'win'", (category, strategy_key)).fetchone()[0]
                return round(wins / total, 2)
        except Exception:
            return 0.5

    def get_best_strategy(self, category: str) -> dict[str, Any]:
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute("""
                    SELECT strategy_key, 
                           SUM(CASE WHEN outcome = 'win' THEN 1 ELSE 0 END) as wins,
                           COUNT(*) as total,
                           SUM(reward) as total_reward
                    FROM autonomous_strategy 
                    WHERE category = ?
                    GROUP BY strategy_key
                    ORDER BY wins DESC, total_reward DESC
                    LIMIT 1
                """, (category,)).fetchall()
                if rows:
                    r = rows[0]
                    return {
                        "category": category,
                        "best_strategy": r["strategy_key"],
                        "wins": r["wins"],
                        "total_attempts": r["total"],
                        "win_rate": round(r["wins"] / r["total"], 2),
                    }
        except Exception:
            pass

        return {"category": category, "best_strategy": "default_personalized", "win_rate": 0.5}


_global_strat: Optional[StrategyLearner] = None


def get_strategy_learner() -> StrategyLearner:
    global _global_strat
    if _global_strat is None:
        _global_strat = StrategyLearner()
    return _global_strat
