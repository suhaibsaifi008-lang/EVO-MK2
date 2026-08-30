"""Knowledge Intelligence Agent for EVO MK2 (JARVIS Phase 6)."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from . import db, llm
from .config import DATA

VAULT_DIR = DATA / "vault"

log = logging.getLogger("mk2.knowledge_agent")


class KnowledgeAgent:
    """Semantic search and contextual knowledge surfacing engine."""

    def __init__(self):
        self.vault_dir = VAULT_DIR

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        q_low = query.lower()

        if self.vault_dir.exists():
            for p in self.vault_dir.glob("*.md"):
                try:
                    txt = p.read_text(encoding="utf-8")
                    if q_low in p.name.lower() or q_low in txt.lower():
                        results.append({
                            "source": "vault",
                            "title": p.stem,
                            "path": str(p),
                            "snippet": txt[:300].strip(),
                            "score": 0.9 if q_low in p.name.lower() else 0.7,
                        })
                except Exception:
                    pass

        try:
            with sqlite3.connect(db.DB_PATH) as con:
                con.row_factory = sqlite3.Row
                rows = con.execute("SELECT key, val FROM facts").fetchall()
                for r in rows:
                    if q_low in r["key"].lower() or q_low in r["val"].lower():
                        results.append({
                            "source": "memory_fact",
                            "title": r["key"],
                            "snippet": r["val"],
                            "score": 0.85,
                        })
        except Exception:
            pass

        return sorted(results, key=lambda x: x.get("score", 0), reverse=True)[:limit]

    def proactive_surface(self, context_text: str) -> list[dict[str, Any]]:
        if not context_text:
            return []
        words = [w for w in context_text.lower().split() if len(w) > 4][:3]
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
