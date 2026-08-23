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


def _gather_sources(topic: str, max_sources: int = 3) -> list[dict]:
    from .tools import emit_progress
    queries = [topic, f"{topic} explained", f"{topic} 2026"]
    urls: list[str] = []
    seen = set()
    for qi, q in enumerate(queries):
        _emit(f"searching ({qi + 1}/{len(queries)})...")
        try:
            for r in ddg_results(q, max_results=4):
                if r["url"] not in seen:
                    seen.add(r["url"])
                    urls.append(r["url"])
        except Exception:
            continue
        if len(urls) >= max_sources * 2:
            break
    sources = []
    for i, u in enumerate(urls[:max_sources]):
        _emit(f"reading source {i + 1}/{min(max_sources, len(urls))}...")
        try:
            text = fetch_page_text(u, max_chars=2200)
            if len(text) > 150:
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
            f"[{i+1}] {src['url']}\n{src['text'][:1200]}"
            for i, src in enumerate(sources))[:9000]
        synth_prompt = (
            f"Topic: {topic}\n\nMaterial:\n{material}"
        )
        synth_system = (
            "You are a research analyst. Using ONLY the material provided, "
            "write a concise briefing on the topic: key facts, notable "
            "disagreements between sources, and open questions. Cite sources "
            "as [1], [2]. Max 350 words."
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
    doc = (f"# Research: {topic}\n\n"
           f"*{stamp} &middot; {len(sources)} sources*" if has_sources else f"*{stamp} &middot; AI knowledge*"
           f"{report}\n\n## Sources\n{refs}\n")

    from .vault import write_note

    path = write_note(f"research {slug}", doc, tags=["research"])
    return {"ok": True,
            "speech": f"Research complete on {topic} using {len(sources)} sources — saved to the vault.",
            "data": {"path": str(path), "sources": [s["url"] for s in sources],
                     "report": doc[:4000]}}


@tool("web_fetch", "Fetch a webpage and return its readable text.",
      {"url": {"type": "string"}}, permission="read")
def web_fetch(url: str) -> dict:
    text = fetch_page_text(url, max_chars=4000)
    return {"ok": bool(text), "speech": text[:600], "data": {"text": text}}
