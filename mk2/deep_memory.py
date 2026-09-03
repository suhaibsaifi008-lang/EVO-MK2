"""Phase 4: Deep memory — semantic recall over past episodes.

Embeddings: Gemini text-embedding when a key is configured; otherwise a
deterministic local hashing embedder (bag-of-ngrams -> 256-dim L2 vector).
Embedding BLOBs are tagged with their engine so mixed-era rows never get
compared across incompatible spaces.

Public surface:
    remember(text, importance)  -> store + embed an episode
    search(query, k)            -> semantic (or keyword) episode hits
    status()                    -> engine info for /api/diag
"""
import hashlib
import json
import logging
import math
import struct
import threading

from . import db
from .config import settings

log = logging.getLogger("mk2.deep_memory")

_lock = threading.RLock()
_engine = {"name": "", "dim": 0}

HASH_DIM = 256


# ---------------- embedding backends ----------------

def _gemini_embed(texts: list[str]) -> list[list[float]] | None:
    if not settings.gemini_key:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.gemini_key)
        resp = client.models.embed_content(
            model="gemini-embedding-001", contents=texts,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"))
        out = [e.values for e in resp.embeddings]
        return out if all(v for v in out) else None
    except Exception:
        return None


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * HASH_DIM
    words = "".join(c if c.isalnum() else " " for c in text.lower()).split()
    grams = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    for g in grams:
        h = int.from_bytes(hashlib.md5(g.encode()).digest()[:4], "little")
        vec[h % HASH_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def engine() -> tuple[str, int]:
    """(engine name, dim) for the CURRENT configuration; caches probe."""
    with _lock:
        if _engine["name"]:
            return _engine["name"], _engine["dim"]
        if settings.gemini_key:
            probe = _gemini_embed(["ping"])
            if probe and probe[0]:
                _engine["name"], _engine["dim"] = "gemini", len(probe[0])
                return _engine["name"], _engine["dim"]
        _engine["name"], _engine["dim"] = "hash", HASH_DIM
        return _engine["name"], _engine["dim"]


def embed(text: str) -> tuple[bytes, str]:
    """Returns (tagged blob, engine). Blob = tag byte + float32 array."""
    name, dim = engine()
    if name == "gemini":
        vecs = _gemini_embed([text[:8000]])
        if vecs and vecs[0] and len(vecs[0]) == dim:
            return b"G" + struct.pack(f"<{len(vecs[0])}f", *vecs[0]), name
    return b"H" + struct.pack(f"<{HASH_DIM}f", *_hash_embed(text)), "hash"


def _cosine(a: bytes | None, query_tagged: bytes) -> float:
    """Cosine between stored tagged-blob and tagged query. Incompatible
    engines or missing blobs -> -1 (never match semantically)."""
    if not a or not query_tagged or a[:1] != query_tagged[:1]:
        return -1.0
    n = (len(query_tagged) - 1) // 4
    try:
        qa = struct.unpack(f"<{n}f", query_tagged[1:])
        ba = struct.unpack(f"<{n}f", a[1:1 + n * 4])
    except struct.error:
        return -1.0
    dot = sum(x * y for x, y in zip(qa, ba))
    na = math.sqrt(sum(x * x for x in qa)) or 1.0
    nb = math.sqrt(sum(y * y for y in ba)) or 1.0
    return dot / (na * nb)


# ---------------- public API ----------------

def remember(text: str, importance: float = 1.0,
             started_at: float | None = None) -> int | None:
    text = (text or "").strip()
    if not text:
        return None
    started = started_at or __import__("time").time()
    with db._lock, db.connect() as c:
        cur = c.execute(
            "INSERT INTO episodes(summary,started_at,ended_at,importance) "
            "VALUES(?,?,?,?)",
            (text[:2000], started, started, max(0.5, min(float(importance), 3.0))))
        ep_id = int(cur.lastrowid or 0)
    try:
        blob, _eng = embed(text)
        db.set_episode_embedding(ep_id, blob)
    except Exception:
        pass
    return ep_id


def search(query: str, k: int = 4, min_similarity: float = 0.25) -> list[dict]:
    """Semantic first; keyword overlap as tiebreaker/fallback with relevance threshold."""
    query = (query or "").strip()
    if not query:
        return []
    rows = db.episodes_with_embeddings()
    if not rows:
        return []
    try:
        qblob, _ = embed(query)
    except Exception:
        qblob = None
    words = {w for w in query.lower().split() if len(w) > 3}
    scored = []
    for r in rows:
        sem = _cosine(r.get("embedding"), qblob) if qblob else -1.0
        low = r["summary"].lower()
        kw = sum(1 for w in words if w in low)
        score = (max(sem, 0.0), kw)
        # Relevance threshold: only include semantic matches >= min_similarity or high keyword matches
        if sem >= min_similarity or kw >= 1:
            scored.append((score, r))
    scored.sort(key=lambda t: (t[0][0], t[0][1]), reverse=True)
    return [{"summary": r["summary"], "ended_at": r["ended_at"],
             "importance": r["importance"],
             "semantic": round(max(s[0], 0.0), 3),
             "keywords": s[1]} for s, r in scored[:max(1, k)]]


def reindex_episodes() -> int:
    """Recompute embeddings for unindexed or stale stored episodes."""
    rows = db.episodes_with_embeddings()
    updated = 0
    for r in rows:
        summary = r.get("summary", "").strip()
        if summary and not r.get("embedding"):
            try:
                blob, _ = embed(summary)
                db.set_episode_embedding(r["id"], blob)
                updated += 1
            except Exception:
                pass
    return updated


def status() -> dict:
    name, dim = engine()
    rows = db.episodes_with_embeddings()
    embedded = sum(1 for r in rows if r.get("embedding"))
    return {"engine": name, "dim": dim, "episodes": len(rows),
            "embedded": embedded}


# ------------------------------------------------------------------ tools

from .tools import tool  # noqa: E402


@tool("remember_episode", "Store a durable memory with semantic indexing so it can be recalled weeks later by meaning, not just keywords.",
      {"text": {"type": "string"}, "importance": {"type": "number"}},
      permission="read")
def remember_episode_tool(text: str, importance: float = 1.0) -> dict:
    ep_id = remember(text, importance)
    if not ep_id:
        return {"ok": False, "speech": "Nothing to remember.", "data": {}}
    return {"ok": True,
            "speech": f"Committed to long-term memory.",
            "data": {"id": ep_id}}


@tool("search_episodes", "Semantic search over remembered past conversations and episodes.",
      {"query": {"type": "string"}, "limit": {"type": "integer"}}, permission="read")
def search_episodes_tool(query: str, limit: int = 4) -> dict:
    hits = search(query, max(1, min(int(limit), 10)))
    if not hits:
        return {"ok": False,
                "speech": "No memories match that yet.", "data": {}}
    speech = "; ".join(h["summary"][:80] for h in hits[:3])
    return {"ok": True, "speech": speech,
            "data": {"hits": hits}}


# ---------------- Knowledge Graph & Memory Consolidation ----------------

def record_relation(source: str, relation: str, target: str, context: str = "", confidence: float = 1.0) -> bool:
    """Store or update an associative edge in the memory graph."""
    import time
    s = (source or "").strip().lower()
    r = (relation or "").strip().lower()
    t = (target or "").strip().lower()
    if not (s and r and t):
        return False
    try:
        with db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memory_graph (source, relation, target, context, confidence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (s, r, t, str(context)[:300], float(confidence), time.time()),
            )
            return True
    except Exception as exc:
        log.warning("record_relation failed: %s", exc)
        return False


def graph_query(entity: str, hops: int = 2, max_edges: int = 25) -> list[dict]:
    """Traverse outbound and inbound relations up to N hops from an entity."""
    root = (entity or "").strip().lower()
    if not root:
        return []

    visited_entities = {root}
    frontier = {root}
    collected_edges = []

    try:
        with db.connect() as conn:
            for _ in range(max(1, min(int(hops), 3))):
                if not frontier or len(collected_edges) >= max_edges:
                    break
                placeholders = ",".join("?" * len(frontier))
                params = list(frontier) * 2
                query = f"""
                    SELECT source, relation, target, context, confidence
                    FROM memory_graph
                    WHERE source IN ({placeholders}) OR target IN ({placeholders})
                    LIMIT {max_edges}
                """
                rows = conn.execute(query, params).fetchall()
                next_frontier = set()
                for row in rows:
                    edge = {
                        "source": row["source"],
                        "relation": row["relation"],
                        "target": row["target"],
                        "context": row["context"],
                        "confidence": row["confidence"],
                    }
                    if edge not in collected_edges:
                        collected_edges.append(edge)
                    if row["source"] not in visited_entities:
                        visited_entities.add(row["source"])
                        next_frontier.add(row["source"])
                    if row["target"] not in visited_entities:
                        visited_entities.add(row["target"])
                        next_frontier.add(row["target"])
                frontier = next_frontier
        return collected_edges
    except Exception as exc:
        log.warning("graph_query failed: %s", exc)
        return []


def format_graph_context(topic: str) -> str:
    """Format relational graph context for prompt injection."""
    edges = graph_query(topic, hops=2, max_edges=10)
    if not edges:
        return ""
    lines = [f"- {e['source']} --[{e['relation']}]--> {e['target']}" + (f" ({e['context']})" if e['context'] else "") for e in edges]
    return "Associative Memory Graph:\n" + "\n".join(lines)


def consolidate_memories() -> dict:
    """Nightly / idle consolidation of recent turns into long-term graph relations."""
    try:
        from . import llm
        msgs = db.recent_messages(limit=30)
        if len(msgs) < 4:
            return {"ok": True, "speech": "Insufficient recent interactions to consolidate.", "edges_added": 0}

        dialogue = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in msgs[-20:])
        prompt = (
            "Analyze the recent interaction dialogue below and extract high-level durable entity-relationships.\n"
            "Identify user preferences, project details, decisions, habits, and key technical choices.\n"
            "Format your response ONLY as a JSON array of triples:\n"
            '[{"source": "user/entity", "relation": "likes/built/prefers/works_on", "target": "value/project", "context": "optional notes"}]\n\n'
            f"Dialogue:\n{dialogue}"
        )
        system = "You are a memory consolidation engine. Extract durable relational knowledge as JSON triples only."
        raw = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}], temperature=0.1, timeout=30, role="fast")
        clean = (raw or "").strip()
        if "[" in clean and "]" in clean:
            start = clean.find("[")
            end = clean.rfind("]") + 1
            clean = clean[start:end]
        triples = json.loads(clean)
        added = 0
        for t in triples:
            if isinstance(t, dict) and "source" in t and "relation" in t and "target" in t:
                if record_relation(t["source"], t["relation"], t["target"], t.get("context", "")):
                    added += 1
        return {
            "ok": True,
            "speech": f"Consolidated memories: extracted and indexed {added} new relationship(s).",
            "edges_added": added,
        }
    except Exception as exc:
        log.warning("Memory consolidation failed: %s", exc)
        return {"ok": False, "speech": f"Consolidation note: {exc}", "edges_added": 0}


@tool("query_knowledge_graph", "Query the multi-hop relational memory graph for connections to an entity or concept.",
      {"entity": {"type": "string"}, "hops": {"type": "integer"}}, permission="read")
def query_knowledge_graph_tool(entity: str, hops: int = 2) -> dict:
    edges = graph_query(entity, hops=hops)
    if not edges:
        return {"ok": False, "speech": f"No relational connections found for '{entity}'.", "data": {"edges": []}}
    summary = "; ".join(f"{e['source']} {e['relation']} {e['target']}" for e in edges[:4])
    return {"ok": True, "speech": summary, "data": {"edges": edges}}


@tool("consolidate_memory_now", "Trigger immediate memory consolidation, extracting entity triples and durable relationships from recent dialogue.",
      {}, permission="read")
def consolidate_memory_now_tool() -> dict:
    return consolidate_memories()
