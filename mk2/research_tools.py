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


@tool("deep_research", "Research a topic across multiple web sources and save a cited report to the vault. Slow (30-90s).",
      {"topic": {"type": "string"}}, permission="read", long_running=True)
def deep_research(topic: str) -> dict:
    topic = (topic or "").strip()[:160]
    if not topic:
        raise ValueError("empty topic")

    _emit("searching web sources...")
    sources = _gather_sources(topic)
    has_sources = len(sources) > 0
    if not has_sources:
        _emit("web search unavailable - using AI knowledge...")


    from .llm import chat

    if has_sources:
        material = "\n\n".join(
            f"[{i+1}] {src['url']}\n{src['text'][:1600]}"
            for i, src in enumerate(sources))[:11000]
        synth_prompt = (
            f"Topic: {topic}\n\nMaterial:\n{material}"
        )
        synth_system = (
            "You are a research analyst. Using ONLY the material provided, "
            "write a concrete, useful briefing on the topic: name actual "
            "products/models/options with numbers where available, key facts, "
            "notable disagreements between sources, and open questions. "
            "If the material lacks specifics, say exactly what is missing in "
            "one line - never write meta-commentary about the sources. "
            "Cite as [1], [2]. Max 400 words."
        )
    else:
        synth_prompt = topic
        synth_system = (
            "Write a concise research briefing on this topic from your knowledge. "
            "Include key facts, current state, and recommendations. "
            "Use markdown sections. Max 300 words. "
            "Note that you could not access live web sources."
        )

    report = chat(
        [
            {"role": "system", "content": synth_system},
            {"role": "user", "content": synth_prompt},
        ],
        role="primary", temperature=0.3, timeout=60,
    )

    slug = _slug(topic)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    refs = "\n".join(f"[{i+1}] {s['url']}" for i, s in enumerate(sources))
    meta = (f"*{stamp} &middot; {len(sources)} sources*" if has_sources
            else f"*{stamp} &middot; AI knowledge — live web unavailable*")
    doc = (f"# Research: {topic}\n\n"
           f"{meta}\n\n"
           f"{report}\n\n"
           f"## Sources\n{refs}\n")

    from .vault import write_note

    path = write_note(f"research {slug}", doc, tags=["research"])

    # Phase 7.6: grow the knowledge graph from the report
    try:
        from . import rag

        rag.kg_extract(report, source="research")
    except Exception:
        pass

    # Persist key research facts to conversation memory for follow-ups
    try:
        from . import db
        # Store the topic as a fact with the report summary as value
        key_fact = f"research:{_slug(topic)}"
        db.remember_fact(key_fact, doc[:500], source="research")
        # Also store the topic itself
        db.remember_fact(f"research_topic:{_slug(topic)}", topic[:80], source="research")
    except Exception:
        pass

    return {"ok": True,
            "speech": f"Research complete on {topic} using {len(sources)} sources — saved to the vault.",
            "data": {"path": str(path), "sources": [s["url"] for s in sources],
                     "report": doc[:4000]}}


@tool("web_fetch", "Fetch a webpage and return its readable text.",
      {"url": {"type": "string"}}, permission="read")
def web_fetch(url: str) -> dict:
    text = fetch_page_text(url, max_chars=4000)
    return {"ok": bool(text), "speech": text[:600], "data": {"text": text}}
