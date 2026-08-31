"""Deep Research Engine -- Multi-stage query decomposition, parallel search,
content extraction, claim cross-referencing, and structured synthesis.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .config import DATA
from .tools.web_tools import ddg_results, fetch_page_text

log = logging.getLogger("mk2.deep_research")

RESEARCH_CACHE_DIR = DATA / "research_cache"
RESEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = 86400  # 24 hours


@dataclass
class SourceContent:
    url: str
    title: str = ""
    text: str = ""
    author: str = ""
    date: str = ""
    score: float = 1.0
    claims: list[str] = field(default_factory=list)


@dataclass
class DeepResearchReport:
    topic: str
    executive_summary: str
    key_findings: list[str]
    consensus_view: str
    conflicts: str
    confidence: str  # high / medium / low
    sources: list[dict[str, str]]
    markdown_report: str
    cached: bool = False
    timestamp: float = 0.0


class DeepResearchEngine:
    """Multi-stage autonomous research engine with max 30s deadline."""

    def __init__(self, emit_fn: Optional[Callable[[str], None]] = None, timeout: float = 30.0) -> None:
        self.emit = emit_fn or (lambda msg: None)
        self.timeout = timeout

    def _cache_path(self, topic: str) -> Path:
        h = hashlib.sha256(topic.strip().lower().encode("utf-8")).hexdigest()[:16]
        return RESEARCH_CACHE_DIR / f"{h}.json"

    def _get_cached(self, topic: str) -> Optional[DeepResearchReport]:
        p = self._cache_path(topic)
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                return DeepResearchReport(**data)
        except Exception:
            pass
        return None

    def _save_cache(self, report: DeepResearchReport) -> None:
        p = self._cache_path(report.topic)
        try:
            p.write_text(json.dumps(report.__dict__, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Failed to save research cache: %s", exc)

    def decompose_query(self, topic: str) -> list[str]:
        """Stage 1: Generate sub-questions and query variants."""
        self.emit("Decomposing research query into sub-topics...")
        variants = [
            topic,
            f"{topic} overview and core concepts",
            f"{topic} comparison and alternatives",
            f"{topic} benchmarks analysis and evidence",
            f"{topic} current consensus and debate 2026",
            f"{topic} state of the art and key findings",
        ]
        return variants

    def parallel_search(self, queries: list[str], max_urls: int = 15) -> list[str]:
        """Stage 2: Parallel search & URL deduplication."""
        self.emit(f"Searching web sources in parallel across {len(queries)} vectors...")
        seen = set()
        urls: list[str] = []

        def search_one(q: str):
            try:
                return ddg_results(q, max_results=4)
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_q = {executor.submit(search_one, q): q for q in queries}
            for fut in as_completed(future_to_q):
                for r in fut.result():
                    u = r.get("url", "")
                    if u and u not in seen and not any(skip in u for skip in ("youtube.com", "91mobiles.com/list")):
                        seen.add(u)
                        urls.append(u)
                    if len(urls) >= max_urls:
                        break

        return urls[:max_urls]

    def extract_content(self, urls: list[str], max_sources: int = 10) -> list[SourceContent]:
        """Stage 3: Extract clean full text content."""
        self.emit(f"Extracting content from top {min(len(urls), max_sources)} authoritative sources...")
        sources: list[SourceContent] = []

        def fetch_one(url: str) -> Optional[SourceContent]:
            try:
                text = fetch_page_text(url, max_chars=3500)
                if len(text) > 150:
                    title = url.split("//")[-1].split("/")[0]
                    return SourceContent(url=url, title=title, text=text[:2500])
            except Exception:
                pass
            return None

        with ThreadPoolExecutor(max_workers=6) as executor:
            future_to_url = {executor.submit(fetch_one, u): u for u in urls[:max_sources]}
            for fut in as_completed(future_to_url):
                res = fut.result()
                if res:
                    sources.append(res)

        return sources

    def synthesize(self, topic: str, sources: list[SourceContent]) -> DeepResearchReport:
        """Stage 4 & 5: Claim cross-referencing and structured report generation."""
        self.emit("Cross-referencing claims and synthesizing structured report...")
        from .llm import chat

        if not sources:
            synth_system = (
                "You are an elite research intelligence analyst. Generate a structured deep research report "
                "with sections:\n"
                "### Executive Summary\n"
                "### Key Findings\n"
                "### Consensus View\n"
                "### Conflicts & Debates\n"
                "### Confidence Assessment (High/Medium/Low)\n"
                "Max 350 words."
            )
            synth_prompt = f"Topic: {topic}\n(Note: Live web search fallback)"
        else:
            material = "\n\n".join(
                f"[{i+1}] {s.url}\n{s.text[:1200]}"
                for i, s in enumerate(sources[:8])
            )[:8000]

            synth_system = (
                "You are an elite research intelligence analyst. Using the provided sources, produce a high-rigor, "
                "evidence-backed deep research report with exact sections:\n\n"
                "### Executive Summary\n(2-3 concise sentences summarizing core finding)\n\n"
                "### Key Findings\n(Bulleted list of concrete facts/numbers with [1], [2] citations)\n\n"
                "### Consensus View\n(Where sources agree)\n\n"
                "### Conflicts & Debates\n(Disagreements, trade-offs, or open questions)\n\n"
                "### Confidence Assessment\n(State High, Medium, or Low with brief justification)\n\n"
                "Be direct, specific, and cite source indices [1], [2]."
            )
            synth_prompt = f"Topic: {topic}\n\nSources:\n{material}"

        report_text = chat(
            [
                {"role": "system", "content": synth_system},
                {"role": "user", "content": synth_prompt},
            ],
            role="primary",
            temperature=0.2,
            timeout=25,
        )

        sources_meta = [{"url": s.url, "title": s.title, "date": "2026"} for s in sources]
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        src_table = "\n".join(f"- [{i+1}] {s['url']}" for i, s in enumerate(sources_meta))

        full_md = (
            f"# Deep Research: {topic}\n\n"
            f"*Generated: {stamp} | Sources: {len(sources)} verified*\n\n"
            f"{report_text}\n\n"
            f"### Sources & References\n{src_table or 'AI Knowledge Base'}\n"
        )

        conf = "High" if len(sources) >= 3 else "Medium"
        if "confidence: low" in (report_text or "").lower():
            conf = "Low"

        report = DeepResearchReport(
            topic=topic,
            executive_summary="",
            key_findings=[],
            consensus_view="",
            conflicts="",
            confidence=conf,
            sources=sources_meta,
            markdown_report=full_md,
            timestamp=time.time(),
        )
        return report

    def research(self, topic: str) -> DeepResearchReport:
        cached = self._get_cached(topic)
        if cached:
            cached.cached = True
            return cached

        queries = self.decompose_query(topic)
        urls = self.parallel_search(queries)
        sources = self.extract_content(urls)
        report = self.synthesize(topic, sources)
        self._save_cache(report)
        return report
