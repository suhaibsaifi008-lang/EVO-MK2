"""Phase 4: RAG — ingest any folder of documents, then Q&A over them.

Chunk -> embed (deep_memory engine) -> sqlite -> retrieve top chunks ->
LLM synthesizes a cited answer. Only reads inside fs_tools.ALLOWED_ROOTS.
"""
import json
import re
from pathlib import Path

from . import db, deep_memory, llm
from .fs_tools import ALLOWED_ROOTS
from .tools import tool

READABLE = {".txt", ".md", ".csv", ".json", ".log"}
CHUNK_CHARS = 1400
CHUNK_OVERLAP = 150
TOP_K = 6


def _iter_files(folder: str, limit: int = 200):
    from pathlib import Path

    from .fs_tools import _safe

    base = _safe(folder)
    if base.is_file():
        yield base
        return
    n = 0
    for p in sorted(base.rglob("*")):
        if n >= limit:
            return
        if p.is_file() and p.suffix.lower() in READABLE:
            try:
                if p.stat().st_size < 2_000_000:  # 2 MB guard per file
                    yield p
                    n += 1
            except OSError:
                continue


def _doc_text(p) -> str:
    ext = p.suffix.lower()
    if ext in READABLE:
        return p.read_text(encoding="utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader

        return "\n".join((pg.extract_text() or "")
                         for pg in PdfReader(str(p)).pages[:40])
    if ext == ".docx":
        import docx

        d = docx.Document(str(p))
        return "\n".join(par.text for par in d.paragraphs)
    return ""


def _chunks(text: str):
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    step = CHUNK_CHARS - CHUNK_OVERLAP
    out, i = [], 0
    while i < len(text) and len(out) < 400:
        piece = text[i:i + CHUNK_CHARS].strip()
        if len(piece) > 80:
            out.append(piece)
        i += step
    return out


def _embed_batch(texts: list[str]) -> list[bytes | None]:
    """Embed many chunks; Gemini path batches, hash path is local anyway."""
    name, dim = deep_memory.engine()
    if name == "gemini" and settings_has_key():
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=_key())
            blobs = []
            for i in range(0, len(texts), 64):
                batch = [t[:8000] for t in texts[i:i + 64]]
                resp = client.models.embed_content(
                    model="gemini-embedding-001", contents=batch,
                    config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"))
                for e in resp.embeddings:
                    v = e.values if e and e.values else None
                    blobs.append(None if not v else
                                 b"G" + __import__("struct").pack(f"<{len(v)}f", *v))
            if all(blobs):
                return blobs
        except Exception:
            pass
    return [b"H" + __import__("struct").pack(
        f"<{deep_memory.HASH_DIM}f", *deep_memory._hash_embed(t)) for t in texts]


def _key() -> str:
    from .config import settings as s

    return s.gemini_key


def settings_has_key() -> bool:
    return bool(_key())


# ------------------------------------------------------------------ tools

@tool("ingest_documents", "Read every supported document in a folder (txt/md/csv/json/pdf/docx) so you can answer questions about them later.",
      {"folder": {"type": "string"}}, permission="read")
def ingest_documents(folder: str) -> dict:
    total_files = total_chunks = skipped = 0
    seen_sources = set()
    for p in _iter_files(folder):
        src = str(p)
        rel_ok = any(str(p.resolve()).startswith(str(r.resolve()))
                     for r in ALLOWED_ROOTS)
        if not rel_ok:
            skipped += 1
            continue
        try:
            text = _doc_text(p)
        except Exception as exc:
            skipped += 1
            db.chunk_delete_source(src)  # stale partial
            continue
        pieces = _chunks(text)
        if not pieces:
            skipped += 1
            continue
        db.chunk_delete_source(src)
        blobs = _embed_batch(pieces)
        for i, (piece, blob) in enumerate(zip(pieces, blobs)):
            db.chunk_add(src, i, piece, blob)
        seen_sources.add(src)
        total_files += 1
        total_chunks += len(pieces)
    if total_files == 0:
        return {"ok": False,
                "speech": ("I couldn't read any documents there "
                           f"({skipped} skipped). Supported: txt/md/csv/json/pdf/docx."),
                "data": {}}
    return {"ok": True,
            "speech": (f"Ingested {total_files} document(s) into "
                       f"{total_chunks} searchable chunks."
                       + (f" {skipped} skipped." if skipped else "")),
            "data": {"files": total_files, "chunks": total_chunks,
                     "skipped": skipped}}


@tool("ask_documents", "Answer a question using ONLY the ingested documents.",
      {"question": {"type": "string"}}, permission="read")
def ask_documents(question: str) -> dict:
    question = (question or "").strip()
    if not question:
        return {"ok": False, "speech": "Ask me something specific.", "data": {}}
    rows = db.all_chunks()
    if not rows:
        return {"ok": False,
                "speech": ("No documents ingested yet - give me a folder with "
                           "ingest_documents first."),
                "data": {}}
    qblob, _ = deep_memory.embed(question)
    scored = []
    words = {w for w in question.lower().split() if len(w) > 3}
    for r in rows:
        sem = deep_memory._cosine(r.get("embedding"), qblob)
        kw = sum(1 for w in words if w in r["text"].lower())
        scored.append((max(sem, 0.0), kw, r))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    top = [s for s in scored[:TOP_K] if s[0] > 0.05 or s[1] > 0]
    if not top:
        return {"ok": True,
                "speech": "Nothing in the ingested documents matches that.",
                "data": {}}
    context = "\n\n".join(
        f"[{i+1}] {_short(s[2]['source'])}\n{s[2]['text'][:900]}"
        for i, s in enumerate(top))
    answer = llm.chat(
        [{"role": "system",
          "content": ("Answer strictly from the provided excerpts of the "
                      "user's own documents. Cite as [1],[2]. If they don't "
                      "contain the answer say exactly what's missing. "
                      "Max 250 words.")},
         {"role": "user",
          "content": f"Question: {question}\n\nExcerpts:\n{context}"}],
        temperature=0.2, timeout=45)
    return {"ok": True, "speech": answer,
            "data": {"sources": [_short(s[2]["source"]) for s in top]}}


def _short(path: str, n: int = 48) -> str:
    import os

    return os.path.basename(path)[:n]


# ---------------- knowledge watcher (Phase 7.6) ----------------

def _watch_state() -> dict:
    import json as _json

    raw = db.get_setting("knowledge_watch_state", "{}")
    try:
        return _json.loads(raw)
    except Exception:
        return {}


def _save_watch_state(state: dict) -> None:
    import json as _json

    db.set_setting("knowledge_watch_state", _json.dumps(state))


def add_watch_dir(folder: str) -> dict:
    from .fs_tools import _safe

    p = _safe(folder)
    if not p.is_dir():
        return {"ok": False, "error": f"not a folder: {folder}"}
    dirs = [d for d in _watch_state().get("dirs", []) if d != str(p)]
    dirs.append(str(p))
    st = _watch_state()
    st["dirs"] = dirs
    _save_watch_state(st)
    return {"ok": True, "dir": str(p)}


def watch_scan(max_files: int = 25) -> dict:
    """Ingest new/changed documents in every watched dir. Called by the
    kernel tick; returns counts. Zero LLM cost unless kg_extract runs."""
    st = _watch_state()
    dirs = st.get("dirs", [])
    files_state = st.get("files", {})
    ingested = updated = removed = 0
    live = set()
    for d in dirs:
        base = Path(d)
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in READABLE:
                continue
            if "__pycache__" in p.parts or ".git" in p.parts:
                continue
            try:
                sig = f"{p.stat().st_mtime_ns}:{p.stat().st_size}"
            except OSError:
                continue
            live.add(str(p))
            if files_state.get(str(p)) == sig:
                continue
            try:
                text = _doc_text(p)
            except Exception:
                continue
            pieces = _chunks(text)
            if not pieces:
                continue
            db.chunk_delete_source(str(p))
            blobs = _embed_batch(pieces)
            for i, (piece, blob) in enumerate(zip(pieces, blobs)):
                db.chunk_add(str(p), i, piece, blob)
            files_state[str(p)] = sig
            ingested += 1
            if ingested + updated >= max_files * 2:
                break
        # removals: watched file deleted -> drop its chunks
    for src in list(files_state.keys()):
        if src in live:
            continue
        if any(src.startswith(d) for d in dirs):
            db.chunk_delete_source(src)
            files_state.pop(src, None)
            removed += 1
    st["files"] = {k: v for k, v in files_state.items()
                   if any(k.startswith(d) for d in dirs)}
    _save_watch_state(st)
    return {"ingested": ingested, "removed": removed}


@tool("knowledge_watch", "Auto-learn every supported document inside a folder - new/changed files are ingested into RAG automatically.",
      {"folder": {"type": "string"}}, permission="execute")
def knowledge_watch_tool(folder: str) -> dict:
    r = add_watch_dir(folder)
    if not r["ok"]:
        return {"ok": False, "speech": r["error"], "data": {}}
    scan = watch_scan()
    return {"ok": True,
            "speech": (f"Watching {r['dir']}. "
                       f"Initial ingest: {scan['ingested']} document(s)."),
            "data": r | scan}


def kg_extract(text: str, source: str = "docs") -> int:
    """Distill durable triples from a chunk of text via the fast model.
    Best-effort: failures are silent, count of stored triples returned."""
    from . import llm

    try:
        raw = llm.chat(
            [{"role": "system",
              "content": ('Extract durable facts as triples. Reply ONLY JSON '
                          '[["subject","predicate","object"], ...] max 8, '
                          "empty list if none.")},
             {"role": "user", "content": text[:4000]}],
            role="fast", temperature=0.0, timeout=20)
        m = re.search(r"\[[\s\S]*\]", raw)
        if not m:
            return 0
        tris = json.loads(m.group(0))
        n = 0
        for t in tris[:8]:
            if isinstance(t, list) and len(t) == 3 and all(
                    str(x).strip() for x in t):
                db.triple_add(str(t[0]), str(t[1]), str(t[2]), src=source)
                n += 1
        return n
    except Exception:
        return 0
