"""File Intelligence Agent for EVO MK2 (JARVIS Phase 6)."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from . import llm

log = logging.getLogger("mk2.file_agent")


class FileAgent:
    """Analyzes and summarizes project files and directories."""

    def summarize_batch(self, file_paths: list[str]) -> str:
        snippets = []
        for fp in file_paths[:5]:
            p = Path(fp)
            if p.exists() and p.is_file():
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")[:2000]
                    snippets.append(f"### File: {p.name}\n{content}")
                except Exception:
                    pass

        if not snippets:
            return "No readable files provided for batch summary."

        prompt = (
            "Create a consolidated executive summary connecting the key insights from these files:\n\n"
            + "\n\n".join(snippets)
            + "\n\nStructure with: 1. Core Synthesis, 2. Key Takeaways, 3. Notable Details."
        )

        try:
            res = llm.chat([
                {"role": "system", "content": "You are an expert technical analyst synthesizing multi-file data."},
                {"role": "user", "content": prompt},
            ], role="fast", temperature=0.2)
            return res.strip()
        except Exception as exc:
            return f"Batch summarization error: {exc}"


_global_file_agent: Optional[FileAgent] = None


def get_file_agent() -> FileAgent:
    global _global_file_agent
    if _global_file_agent is None:
        _global_file_agent = FileAgent()
    return _global_file_agent
