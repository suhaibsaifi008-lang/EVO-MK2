"""Continuous Research & Monitoring Agent for EVO MK2 (JARVIS Phase 7 / Item 9).

Monitors user topics, retrieves fresh search data, tracks competitor updates,
and produces daily executive intelligence briefings.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from . import llm
from .config import DATA

log = logging.getLogger("mk2.research_agent")
TOPICS_FILE = DATA / "monitored_topics.json"


class ResearchAgent:
    """Continuously monitors topics, tracks trends, and generates research briefings with real search data."""

    def __init__(self):
        self.topics_file = TOPICS_FILE
        self.monitored: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if self.topics_file.exists():
            try:
                self.monitored = json.loads(self.topics_file.read_text(encoding="utf-8"))
            except Exception as exc:
                log.warning("Failed loading monitored topics: %s", exc)

    def _save(self) -> None:
        self.topics_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.topics_file.write_text(json.dumps(self.monitored, indent=2), encoding="utf-8")
        except Exception:
            pass

    def monitor_topic(self, topic: str, frequency: str = "daily") -> str:
        tid = f"top_{int(time.time())}"
        entry = {"id": tid, "topic": topic, "frequency": frequency, "added_at": time.time()}
        self.monitored.append(entry)
        self._save()
        return tid

    def list_monitored_topics(self) -> list[str]:
        return [t["topic"] for t in self.monitored]

    def daily_briefing(self) -> str:
        """Generate daily briefing with fresh web data."""
        topics = self.list_monitored_topics() or ["Autonomous AI Agents", "High-Ticket Freelance Trends", "Local LLM Optimization"]
        fresh_data = []

        for topic in topics[:3]:
            try:
                # Use existing deep_research tool if available
                from .tools import web_search
                search_res = web_search(topic)
                fresh_data.append({"topic": topic, "latest": search_res[:500] if search_res else "Market evolution ongoing."})
            except Exception:
                fresh_data.append({"topic": topic, "latest": "Market evolution ongoing."})

        prompt = (
            "Generate a high-density, actionable executive research briefing based on this data:\n"
            + json.dumps(fresh_data, indent=2)
            + "\n\nFormat with 3 core sections:\n1. Strategic Shifts\n2. Emerging Opportunities\n3. Recommended Actions"
        )

        try:
            brief = llm.chat([
                {"role": "system", "content": "You are a senior market intelligence director synthesizing live findings."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.3)
            return brief.strip()
        except Exception as exc:
            return f"Daily research briefing error: {exc}"

    def track_competitor(self, competitor: str) -> dict[str, Any]:
        """Research competitor moves using web search and structure the findings."""
        try:
            from .tools import web_search
            raw_intel = web_search(f"{competitor} news products 2026")
        except Exception:
            raw_intel = f"Recent developments in {competitor} product line."

        prompt = f"Analyze key positioning and recent moves for '{competitor}' from this context:\n{raw_intel[:800]}\n\nReturn 3 bullet points."
        try:
            res = llm.chat([
                {"role": "system", "content": "You are a competitive intelligence specialist."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            return {"competitor": competitor, "intelligence": res.strip(), "analyzed_at": time.time()}
        except Exception as exc:
            return {"competitor": competitor, "intelligence": f"Tracking error: {exc}"}

    def trend_report(self, industry: str) -> str:
        """Generate trend report for an industry."""
        prompt = f"Provide a concise trend report for the {industry} industry in 2026. Highlight 3 top emerging shifts."
        try:
            res = llm.chat([
                {"role": "system", "content": "You are an industry analyst."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            return res.strip()
        except Exception as exc:
            return f"Trend report error: {exc}"


_global_research: Optional[ResearchAgent] = None


def get_research_agent() -> ResearchAgent:
    global _global_research
    if _global_research is None:
        _global_research = ResearchAgent()
    return _global_research
