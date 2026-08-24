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
import math
import struct
import threading

from . import db
from .config import settings

_lock = threading.Lock()
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


def search(query: str, k: int = 4) -> list[dict]:
    """Semantic first; keyword overlap as tiebreaker/fallback."""
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
        if sem > 0.25 or kw > 0:
            scored.append((score, r))
    scored.sort(key=lambda t: (t[0][0], t[0][1]), reverse=True)
    return [{"summary": r["summary"], "ended_at": r["ended_at"],
             "importance": r["importance"],
             "semantic": round(max(s[0], 0.0), 3),
             "keywords": s[1]} for s, r in scored[:max(1, k)]]


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
