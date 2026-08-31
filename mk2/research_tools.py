"""Deep research pipeline: search → read → synthesize → vault report.

Runs as a TOOL so the orchestrator streams 'thinking' while it works.
Every network step is hard-bounded; failures degrade gracefully.
"""
import re
import time
from datetime import datetime

from .tools import tool
from .tools.web_tools import ddg_results, fetch_page_text


def _slug(topic: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")[:50]
    return s or "report"


def _emit(msg: str) -> None:
    from .tools import emit_progress

    emit_progress(msg)


def _page_quality(text: str) -> float:
    """Heuristic junk filter: nav menus / cookie walls score ~0, real
    articles score high. Keeps 'the excerpt was only navigation' reports
    from ever happening."""
    if len(text) < 400:
        return 0.0
    words = text.split()
    links = text.count("http")
    caps = sum(1 for w in words if w.isupper() and len(w) > 2)
    nav_words = ("menu", "login", "sign in", "subscribe", "cookie",
                 "download app", "follow us", "newsletter", "©")
    nav_hits = sum(text.lower().count(w) for w in nav_words)
    score = 1.0
    if links / max(1, len(words)) > 0.02:
        score -= 0.5
    if caps / max(1, len(words)) > 0.08:
        score -= 0.2
    score -= min(0.6, nav_hits * 0.04)
    return max(0.0, score)


def _gather_sources(topic: str, max_sources: int = 4) -> list[dict]:
    from .tools import emit_progress

    queries = [topic, f"{topic} explained", f"{topic} 2026 best picks"]
    urls: list[str] = []
    seen = set()
    for qi, q in enumerate(queries):
        _emit(f"searching ({qi + 1}/{len(queries)})...")
        try:
            for r in ddg_results(q, max_results=5):
                if r["url"] not in seen and "youtube.com" not in r["url"] \
                        and "91mobiles.com/list" not in r["url"]:
                    seen.add(r["url"])
                    urls.append(r["url"])
        except Exception:
            continue
        if len(urls) >= max_sources * 3:
            break
    sources = []
    budget = list(urls[:max_sources * 2])
    for i, u in enumerate(budget):
        if len(sources) >= max_sources:
            break
        _emit(f"reading source {i + 1}...")
        try:
            text = fetch_page_text(u, max_chars=3500)
            if len(text) > 150 and _page_quality(text) >= 0.5:
                sources.append({"url": u, "text": text})
        except Exception:
            continue
    return sources


@tool("deep_research", "Research a topic across multiple web sources and save a structured cited report to the vault. Slow (30-90s).",
      {"topic": {"type": "string"}}, permission="read", long_running=True)
def deep_research(topic: str) -> dict:
    topic = (topic or "").strip()[:160]
    if not topic:
        raise ValueError("empty topic")

    from .deep_research import DeepResearchEngine
    engine = DeepResearchEngine(emit_fn=_emit)
    report_obj = engine.research(topic)

    slug = _slug(topic)
    doc = report_obj.markdown_report
    sources = [s["url"] for s in report_obj.sources]

    from .vault import write_note
    path = write_note(f"research {slug}", doc, tags=["research"])

    # Grow the knowledge graph from the report
    try:
        from . import rag
        rag.kg_extract(doc, source="research")
    except Exception:
        pass

    # Persist key research facts to conversation memory for follow-ups
    try:
        from . import db
        key_fact = f"research:{_slug(topic)}"
        db.remember_fact(key_fact, doc[:500], source="research")
        db.remember_fact(f"research_topic:{_slug(topic)}", topic[:80], source="research")
    except Exception:
        pass

    speech_prefix = "Cached research" if report_obj.cached else f"Research complete on {topic} ({report_obj.confidence} confidence)"
    return {
        "ok": True,
        "speech": f"{speech_prefix} using {len(sources)} sources — saved to the vault.",
        "data": {
            "path": str(path),
            "sources": sources,
            "confidence": report_obj.confidence,
            "report": doc[:4000],
        },
    }


@tool("web_fetch", "Fetch a webpage and return its readable text.",
      {"url": {"type": "string"}}, permission="read")
def web_fetch(url: str) -> dict:
    text = fetch_page_text(url, max_chars=4000)
    return {"ok": bool(text), "speech": text[:600], "data": {"text": text}}
