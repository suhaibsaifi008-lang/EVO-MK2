"""Opportunity Scorer & Win Probability Engine for EVO MK2.

Calculates win probabilities and Expected Value (EV) for freelance/sales leads
with online outcome learning.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .config import DATA

log = logging.getLogger("mk2.opportunity_scorer")

MODEL_FILE = DATA / "opportunity_weights.json"


@dataclass
class ScoredOpportunity:
    id: str
    title: str
    platform: str
    budget: float
    win_probability: float  # 0.0 to 1.0
    expected_value: float  # win_probability * budget
    score_breakdown: dict[str, float] = field(default_factory=dict)
    recommendation: str = "bid"  # bid, pass, high_priority


class OpportunityScorer:
    """Adaptive model scoring client jobs based on past pitch wins/losses."""

    def __init__(self) -> None:
        self.weights = {
            "budget_tier": 0.25,
            "skill_match": 0.35,
            "client_reputation": 0.20,
            "competition": 0.20,
        }
        self.outcomes: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if MODEL_FILE.exists():
            try:
                data = json.loads(MODEL_FILE.read_text(encoding="utf-8"))
                self.weights = data.get("weights", self.weights)
                self.outcomes = data.get("outcomes", [])
            except Exception as exc:
                log.debug("Opportunity weights load note: %s", exc)

    def _save(self) -> None:
        try:
            MODEL_FILE.write_text(json.dumps({"weights": self.weights, "outcomes": self.outcomes[-100:]}, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Failed to save opportunity model: %s", exc)

    def calculate_win_probability(self, opp: dict[str, Any]) -> tuple[float, dict[str, float]]:
        """Compute win probability (0.05 to 0.95) and factor breakdown."""
        title = opp.get("title", "").lower()
        desc = opp.get("description", "").lower()
        budget = float(opp.get("budget", opp.get("hourly_rate", 50) * 10))

        # 1. Skill match factor
        core_skills = ("python", "fastapi", "ai", "llm", "automation", "agent", "scraper", "react", "fullstack", "bot")
        hits = sum(1 for s in core_skills if s in title or s in desc)
        skill_score = min(1.0, hits * 0.3 + 0.2)

        # 2. Budget factor (sweet spot $200 - $3500)
        if 200 <= budget <= 3500:
            budget_score = 0.85
        elif budget > 3500:
            budget_score = 0.65  # Higher competition
        elif budget > 50:
            budget_score = 0.50
        else:
            budget_score = 0.20

        # 3. Client reputation factor
        client_spent = float(opp.get("client_spent", 0.0))
        payment_verified = bool(opp.get("payment_verified", True))
        reputation_score = 0.9 if client_spent > 5000 and payment_verified else (0.7 if payment_verified else 0.3)

        # 4. Competition factor (fewer proposals = higher win chance)
        proposals_count = int(opp.get("proposals_count", 10))
        if proposals_count < 5:
            comp_score = 0.90
        elif proposals_count <= 15:
            comp_score = 0.65
        else:
            comp_score = 0.35

        # Weighted aggregate
        prob = (
            skill_score * self.weights["skill_match"]
            + budget_score * self.weights["budget_tier"]
            + reputation_score * self.weights["client_reputation"]
            + comp_score * self.weights["competition"]
        )

        prob = min(0.95, max(0.05, prob))
        breakdown = {
            "skill_score": round(skill_score, 2),
            "budget_score": round(budget_score, 2),
            "reputation_score": round(reputation_score, 2),
            "competition_score": round(comp_score, 2),
        }
        return round(prob, 3), breakdown

    def score_opportunity(self, opp: dict[str, Any]) -> ScoredOpportunity:
        prob, breakdown = self.calculate_win_probability(opp)
        budget = float(opp.get("budget", opp.get("hourly_rate", 50) * 10))
        ev = round(prob * budget, 2)

        rec = "high_priority" if ev > 500 and prob >= 0.5 else ("bid" if prob >= 0.35 else "pass")
        return ScoredOpportunity(
            id=str(opp.get("id", f"opp_{int(time.time()*1000)}")),
            title=opp.get("title", "Untitled Opportunity"),
            platform=opp.get("platform", "upwork"),
            budget=budget,
            win_probability=prob,
            expected_value=ev,
            score_breakdown=breakdown,
            recommendation=rec,
        )

    def rank_opportunities(self, opportunities: list[dict[str, Any]]) -> list[ScoredOpportunity]:
        """Rank a batch of job leads descending by Expected Value."""
        scored = [self.score_opportunity(o) for o in opportunities]
        return sorted(scored, key=lambda s: s.expected_value, reverse=True)

    def record_outcome(self, opp_id: str, won: bool, actual_revenue: float = 0.0) -> None:
        """Update historical outcomes and fine-tune feature weights."""
        self.outcomes.append({
            "opp_id": opp_id,
            "won": won,
            "revenue": actual_revenue,
            "timestamp": time.time(),
        })
        # Simple adaptive update: reward weights of winning characteristics
        if len(self.outcomes) >= 5:
            win_rate = sum(1 for o in self.outcomes if o["won"]) / len(self.outcomes)
            if win_rate < 0.2:
                # Tighten skill match requirement
                self.weights["skill_match"] = min(0.45, self.weights["skill_match"] + 0.02)
                self.weights["competition"] = min(0.30, self.weights["competition"] + 0.01)
        self._save()


_global_scorer: Optional[OpportunityScorer] = None


def get_opportunity_scorer() -> OpportunityScorer:
    global _global_scorer
    if _global_scorer is None:
        _global_scorer = OpportunityScorer()
    return _global_scorer
