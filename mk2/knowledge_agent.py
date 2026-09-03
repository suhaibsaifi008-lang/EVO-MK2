"""Knowledge Intelligence Agent for EVO MK2 (JARVIS Phase 6 / Task 5).

Semantic similarity search across markdown notes, SQLite memory facts, and turn history.
Auto-tags documents, builds relationship graphs, and proactively surfaces relevant context.
"""
from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from . import db, llm
from .config import DATA

log = logging.getLogger("mk2.knowledge_agent")
VAULT_DIR = DATA / "vault"

try:
    from sentence_transformers import SentenceTransformer
    _SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    _SENTENCE_TRANSFORMERS_AVAILABLE = False
    log.warning("sentence-transformers not installed. KnowledgeAgent will use token similarity fallback.")


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return dot / (norm1 * norm2)


class KnowledgeAgent:
    """Semantic search, contextual knowledge indexing, and relationship graph engine."""

    def __init__(self):
        self.vault_dir = VAULT_DIR
        self.model = None
        self._doc_embeddings: list[dict[str, Any]] = []
        if _SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self.model = SentenceTransformer("all-MiniLM-L6-v2")
                log.info("Loaded SentenceTransformer model for semantic knowledge search.")
            except Exception as exc:
                log.warning("Failed loading SentenceTransformer model: %s", exc)
                self.model = None
        self._index_documents()

    def _index_documents(self) -> None:
        """Index vault files and SQLite memory facts into search cache."""
        self._doc_embeddings = []
        # 1. Index Vault markdown notes
        if self.vault_dir.exists():
            for p in self.vault_dir.glob("*.md"):
                try:
                    txt = p.read_text(encoding="utf-8")
                    emb = self.model.encode(txt[:1000]).tolist() if self.model else None
                    self._doc_embeddings.append({
                        "source": "vault",
                        "id": p.name,
                        "title": p.stem,
                        "path": str(p),
                        "snippet": txt[:300].strip(),
                        "text": txt,
                        "embedding": emb,
                    })
                except Exception as exc:
                    log.debug("Note indexing error on %s: %s", p, exc)

        # 2. Index SQLite memory facts
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute("SELECT key, value FROM facts").fetchall()
                for r in rows:
                    fact_str = f"{r['key']}: {r['value']}"
                    emb = self.model.encode(fact_str).tolist() if self.model else None
                    self._doc_embeddings.append({
                        "source": "memory_fact",
                        "id": f"fact_{r['key']}",
                        "title": r["key"],
                        "snippet": r["value"],
                        "text": fact_str,
                        "embedding": emb,
                    })
        except Exception as exc:
            log.debug("Facts indexing note: %s", exc)

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search across vault notes and memory facts using semantic embeddings or token similarity."""
        results: list[dict[str, Any]] = []

        # If embeddings model is available and documents are indexed with vectors
        if self.model:
            try:
                q_emb = self.model.encode(query).tolist()
                for doc in self._doc_embeddings:
                    if doc.get("embedding"):
                        score = _cosine_similarity(q_emb, doc["embedding"])
                        if score > 0.15:
                            results.append({
                                "source": doc["source"],
                                "id": doc["id"],
                                "title": doc["title"],
                                "snippet": doc["snippet"],
                                "score": round(score, 3),
                            })
                if results:
                    return sorted(results, key=lambda x: x["score"], reverse=True)[:limit]
            except Exception as exc:
                log.warning("Semantic vector search error: %s. Falling back to keyword.", exc)

        # Graceful Keyword & Token Overlap Fallback
        q_low = query.lower()
        q_tokens = set(re.findall(r"\w+", q_low))

        # 1. Search Vault markdown notes
        if self.vault_dir.exists():
            for p in self.vault_dir.glob("*.md"):
                try:
                    txt = p.read_text(encoding="utf-8")
                    txt_tokens = set(re.findall(r"\w+", txt.lower()))
                    overlap = len(q_tokens & txt_tokens) / (len(q_tokens) or 1)
                    if overlap > 0 or q_low in txt.lower():
                        results.append({
                            "source": "vault",
                            "id": p.name,
                            "title": p.stem,
                            "path": str(p),
                            "snippet": txt[:300].strip(),
                            "score": round(0.5 + 0.5 * overlap, 2),
                        })
                except Exception:
                    pass

        # 2. Search SQLite memory facts
        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute("SELECT key, value FROM facts").fetchall()
                for r in rows:
                    fact_str = f"{r['key']} {r['value']}".lower()
                    fact_tokens = set(re.findall(r"\w+", fact_str))
                    overlap = len(q_tokens & fact_tokens) / (len(q_tokens) or 1)
                    if overlap > 0 or q_low in fact_str:
                        results.append({
                            "source": "memory_fact",
                            "id": f"fact_{r['key']}",
                            "title": r["key"],
                            "snippet": r["value"],
                            "score": round(0.5 + 0.4 * overlap, 2),
                        })
        except Exception:
            pass

        return sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:limit]

    def find_related(self, item_id: str) -> list[dict[str, Any]]:
        """Find semantically similar documents to a given document or topic."""
        return self.search(item_id, limit=3)

    def knowledge_graph(self, topic: str) -> dict[str, Any]:
        """Map connections across all data sources around a central topic."""
        topic_key = topic.strip().lower()
        hits = self.search(topic_key, limit=5)
        nodes = [{"id": topic_key, "type": "root", "label": topic}]
        edges = []
        for h in hits:
            nid = h.get("title", "node")
            nodes.append({"id": nid, "type": h.get("source"), "label": nid})
            edges.append({"source": topic_key, "target": nid, "weight": h.get("score", 0.5)})
        return {"topic": topic, "nodes": nodes, "edges": edges}

    def proactive_surface(self, context_text: str) -> list[dict[str, Any]]:
        """Surface relevant documents automatically based on active conversation context."""
        if not context_text:
            return []
        words = [w for w in re.findall(r"\w+", context_text.lower()) if len(w) > 4][:3]
        hits = []
        for w in words:
            hits.extend(self.search(w, limit=2))
        seen = set()
        unique = []
        for h in hits:
            key = (h.get("source"), h.get("title"))
            if key not in seen:
                seen.add(key)
                unique.append(h)
        return unique[:4]

    def auto_tag(self, text_or_path: str) -> list[str]:
        """Generate high-quality metadata tags for text using LLM."""
        sample = text_or_path[:1000]
        prompt = (
            f"Generate 3-5 concise tags (lowercase, hyphenated) for this content:\n\n{sample}\n\n"
            'Return ONLY JSON array of strings: ["tag-1", "tag-2", "tag-3"]'
        )
        try:
            raw = llm.chat([
                {"role": "system", "content": "You are a taxonomist generating clean metadata tags."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.1)
            clean = raw.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(clean.strip())
        except Exception:
            return ["general", "document"]


_global_knowledge: Optional[KnowledgeAgent] = None


def get_knowledge_agent() -> KnowledgeAgent:
    global _global_knowledge
    if _global_knowledge is None:
        _global_knowledge = KnowledgeAgent()
    return _global_knowledge
