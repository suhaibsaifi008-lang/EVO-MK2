"""Knowledge Synthesizer & Cross-Domain Intelligence for EVO MK2.

Connects new research and documents to existing knowledge base concepts automatically.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .config import DATA
from .knowledge_agent import get_knowledge_agent

log = logging.getLogger("mk2.knowledge")

GRAPH_FILE = DATA / "knowledge_graph.json"


class KnowledgeSynthesizer:
    """Connects new knowledge to existing knowledge automatically."""

    def __init__(self, ka: Optional[Any] = None) -> None:
        self.ka = ka or get_knowledge_agent()
        self.graph_file = GRAPH_FILE
        self._graph: dict[str, list[dict[str, Any]]] = {}
        self._load_graph()

    def _load_graph(self) -> None:
        if self.graph_file.exists():
            try:
                self._graph = json.loads(self.graph_file.read_text(encoding="utf-8"))
            except Exception as exc:
                log.debug("Failed to load knowledge graph: %s", exc)

    def _save_graph(self) -> None:
        try:
            self.graph_file.parent.mkdir(parents=True, exist_ok=True)
            self.graph_file.write_text(json.dumps(self._graph, indent=2), encoding="utf-8")
        except Exception as exc:
            log.warning("Failed to save knowledge graph: %s", exc)

    def on_new_research(self, topic: str, content: str) -> dict[str, Any]:
        """Called after deep_research completes. Finds connections."""
        connections = []

        # 1. Search existing vault for related topics
        existing = self.ka.search(topic, limit=5)
        for ex in existing:
            if ex.get("title", "").lower() != topic.lower():
                connections.append({
                    "existing_topic": ex.get("title", ""),
                    "relevance": ex.get("score", 0.7),
                    "connection_type": "direct_match",
                })

        # 2. Ask LLM: what does this connect to?
        try:
            from . import llm
            prompt = (
                f'I just researched: "{topic}"\n\n'
                f"Here is a summary of what I learned:\n"
                f"{content[:2000]}\n\n"
                "Based on this, what OTHER topics or domains does this connect to? "
                "List 3-5 connection topics that might be in my existing knowledge base.\n"
                "Format as a simple list, one per line."
            )
            result = llm.chat([
                {"role": "system", "content": "You are a knowledge management assistant. List related topics concisely."},
                {"role": "user", "content": prompt},
            ], role="fast", timeout=10)

            for line in (result or "").split("\n"):
                line = line.strip("- •*0123456789. \t")
                if line and len(line) > 2 and line.lower() != topic.lower():
                    connections.append({
                        "existing_topic": line,
                        "relevance": 0.5,
                        "connection_type": "llm_inferred",
                    })
        except Exception as exc:
            log.debug("Knowledge graph LLM connection note: %s", exc)

        # 3. Save connections to knowledge graph
        self._graph[topic] = connections
        self._save_graph()

        return {
            "topic": topic,
            "connections": connections,
            "new_entries": len(connections),
        }

    def get_related(self, topic: str) -> list[dict[str, Any]]:
        """Get all knowledge connected to a topic from semantic search + graph."""
        related = []
        try:
            related = self.ka.search(topic, limit=5)
        except Exception:
            pass

        graph_links = self._graph.get(topic, [])
        for link in graph_links:
            related.append({
                "source": "knowledge_graph",
                "title": link.get("existing_topic", ""),
                "snippet": f"Connected topic via {link.get('connection_type', 'inferred')}",
                "score": link.get("relevance", 0.5),
            })
        return related


_global_synthesizer: Optional[KnowledgeSynthesizer] = None


def get_knowledge_synthesizer() -> KnowledgeSynthesizer:
    global _global_synthesizer
    if _global_synthesizer is None:
        _global_synthesizer = KnowledgeSynthesizer()
    return _global_synthesizer
