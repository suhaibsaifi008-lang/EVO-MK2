"""Memory Vault â€” human-readable markdown memory files.

Inspired by jaredrhod/ai-memory-vault (AGPL): your memory lives as plain
markdown you can open, edit, and version yourself. MK2 stores durable notes,
preferences and a daily journal here; retrieval feeds straight into context.
"""
import re
import threading
import time
from pathlib import Path

from .config import DATA

VAULT_DIR = DATA / "vault"
_vault_lock = threading.Lock()


def _slug(topic: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")[:60]
    return s or "untitled"


def _ensure() -> Path:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    return VAULT_DIR


def _path(topic: str) -> Path:
    return _ensure() / f"{_slug(topic)}.md"


def write_note(topic: str, content: str, tags: list[str] | None = None) -> Path:
    """Create or UPDATE a note. Frontmatter keeps it greppable."""
    with _vault_lock:
        p = _path(topic)
        fm = (
            "---\n"
            f"topic: {_slug(topic)}\n"
            f"updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime())}\n"
            f"tags: {', '.join(tags or [])}\n"
            "---\n\n"
        )
        body = (content or "").strip() + "\n"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(fm + body, encoding="utf-8")
        tmp.replace(p)  # atomic on NTFS and ext4
        return p


def append_note(topic: str, line: str) -> Path:
    with _vault_lock:
        p = _path(topic)
        if not p.exists():
            fm = (
                "---\n"
                f"topic: {_slug(topic)}\n"
                f"updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime())}\n"
                f"tags: \n"
                "---\n\n"
            )
            body = (line or "").strip() + "\n"
            tmp = p.with_suffix(".tmp")
            tmp.write_text(fm + body, encoding="utf-8")
            tmp.replace(p)
            return p
        text = p.read_text(encoding="utf-8").rstrip() + f"\n- {line.strip()} [{time.strftime('%Y-%m-%d')}]\n"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(p)
        return p


def read_note(topic: str) -> str:
    p = _path(topic)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def list_notes() -> list[dict]:
    out = []
    if not VAULT_DIR.exists():
        return out
    for p in sorted(VAULT_DIR.glob("*.md")):
        head = p.read_text(encoding="utf-8", errors="ignore")[:400]
        m_topic = re.search(r"^topic:\s*(.+)$", head, re.MULTILINE)
        out.append({
            "file": p.name,
            "topic": (m_topic.group(1).strip() if m_topic else p.stem),
            "size": p.stat().st_size,
            "updated": p.stat().st_mtime,
        })
    return out


def search_vault(query: str, limit: int = 5) -> list[dict]:
    """Case-insensitive multi-word grep across the vault."""
    words = [w for w in re.findall(r"[a-z0-9]{3,}", query.lower())][:6]
    if not words:
        return []
    hits = []
    if not VAULT_DIR.exists():
        return hits
    for p in VAULT_DIR.glob("*.md"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        low = text.lower()
        score = sum(low.count(w) for w in words)
        if score:
            snippet = text
            for w in words:
                i = low.find(w)
                if i >= 0:
                    snippet = text[max(0, i - 60): i + 140]
                    break
            hits.append({"file": p.name, "score": score, "snippet": snippet.replace("\n", " ")[:240]})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:limit]


def journal(line: str) -> None:
    """Append to today's daily journal note."""
    day = time.strftime("%Y-%m-%d")
    append_note(f"journal {day}", line)


# ---------------- tools ----------------

from .tools import tool  # noqa: E402


@tool("vault_write", "Write/update a markdown memory note by topic.",
      {"topic": {"type": "string"}, "content": {"type": "string"}}, permission="execute")
def vault_write(topic: str, content: str) -> dict:
    p = write_note(topic, content)
    return {"ok": True, "speech": f"Noted under '{topic}'.", "data": {"file": p.name}}


@tool("vault_read", "Read a memory note by topic.",
      {"topic": {"type": "string"}}, permission="read")
def vault_read(topic: str) -> dict:
    text = read_note(topic)
    if not text:
        return {"ok": False, "speech": f"No note called '{topic}'.", "data": {}}
    return {"ok": True, "speech": text[:600], "data": {"text": text}}


@tool("vault_search", "Search all memory notes.", {"query": {"type": "string"}}, permission="read")
def vault_search(query: str) -> dict:
    rows = search_vault(query)
    if not rows:
        return {"ok": False, "speech": "Nothing in the vault about that.", "data": {}}
    speech = "; ".join(r["file"].replace(".md", "") for r in rows[:3])
    return {"ok": True, "speech": f"Found: {speech}", "data": {"hits": rows}}

