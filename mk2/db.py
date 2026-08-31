"""SQLite persistence. One file = one assistant. Migrations are idempotent."""
import json
import sqlite3
import threading
import time
import logging

from .config import DATA

log = logging.getLogger('mk2.db')

DB_PATH = DATA / "evo_mk2.db"
# RLock allows reentrant calls within the same thread (e.g. migrations that read+write).
_lock = threading.RLock()

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    surface TEXT NOT NULL DEFAULT 'console',
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'explicit',
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary TEXT NOT NULL,
    started_at REAL,
    ended_at REAL,
    importance REAL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    ms REAL NOT NULL,
    detail TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    ok INTEGER NOT NULL,
    result_summary TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    steps_used INTEGER DEFAULT 0,
    max_steps INTEGER DEFAULT 30,
    checkpoint TEXT,
    result TEXT,
    created REAL,
    updated REAL
);
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    due_at REAL NOT NULL,
    fired INTEGER DEFAULT 0,
    created REAL
);
CREATE TABLE IF NOT EXISTS vec_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    ord INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON vec_chunks(source);
CREATE TABLE IF NOT EXISTS triples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    src TEXT DEFAULT 'inferred',
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS anecdotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    narrative TEXT NOT NULL,
    emotion TEXT,
    created_at REAL NOT NULL,
    referenced_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_anecdotes_emotion ON anecdotes(emotion);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    signature TEXT NOT NULL UNIQUE,
    detail TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant TEXT NOT NULL,
    category TEXT DEFAULT 'other',
    amount REAL NOT NULL,
    spent_on TEXT NOT NULL,
    source TEXT DEFAULT '',
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(spent_on);
CREATE INDEX IF NOT EXISTS idx_traces_turn ON traces(turn_id);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    val TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def migrate() -> None:
    with _lock, connect() as conn:
        conn.executescript(SCHEMA_V1)
        # idempotent column migrations (existing installs)
        jobs_cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "depends_on" not in jobs_cols:
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]'")
        ep_cols = {r[1] for r in conn.execute("PRAGMA table_info(episodes)")}
        if "embedding" not in ep_cols:
            conn.execute("ALTER TABLE episodes ADD COLUMN embedding BLOB")


def get_setting(key: str, default: str = "") -> str:
    with _lock, connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _lock, connect() as c:
        c.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value))
        )


def log_message(role: str, content: str, surface: str = "console") -> int:
    with _lock, connect() as c:
        cur = c.execute(
            "INSERT INTO messages(role,content,surface,ts) VALUES(?,?,?,?)",
            (role, content[:6000], surface, time.time()),
        )
        row_id = int(cur.lastrowid or 0)
        if row_id % 100 == 0:  # only check every 100 inserts
            c.execute("DELETE FROM messages WHERE id <= (SELECT MAX(id)-5000 FROM messages)")
        return row_id


def recent_messages(limit: int = 20) -> list[dict]:
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT id,role,content,surface,ts FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_messages() -> None:
    with _lock, connect() as c:
        c.execute("DELETE FROM messages")


def remember_fact(key: str, value: str, source: str = "explicit") -> None:
    key = key.strip().lower()[:80]
    if not key:
        return
    with _lock, connect() as c:
        c.execute(
            "INSERT INTO facts(key,value,source,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, source=excluded.source,"
            " updated_at=excluded.updated_at",
            (key, value[:400], source, time.time()),
        )


def forget_fact(key: str) -> bool:
    key = key.strip().lower()[:80]
    with _lock, connect() as c:
        cur = c.execute("DELETE FROM facts WHERE key=?", (key,))
        return cur.rowcount > 0


def all_facts(limit: int = 40) -> list[dict]:
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT key,value,updated_at FROM facts ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def search_facts(query: str, limit: int = 6) -> list[dict]:
    like = f"%{query.strip().lower()}%"
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT key,value FROM facts WHERE key LIKE ? OR value LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?", (like, like, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def add_episode(summary: str, started: float, importance: float = 1.0) -> None:
    with _lock, connect() as c:
        c.execute(
            "INSERT INTO episodes(summary,started_at,ended_at,importance) VALUES(?,?,?,?)",
            (summary[:2000], started, time.time(), importance),
        )


def recall_episodes(query: str, limit: int = 4) -> list[dict]:
    words = [w for w in query.lower().split() if len(w) > 3][:5]
    if not words:
        return []
    like = " OR ".join(["summary LIKE ?"] * len(words))
    params = [f"%{w}%" for w in words] + [limit]
    with _lock, connect() as c:
        rows = c.execute(
            f"SELECT summary,ended_at FROM episodes WHERE {like} ORDER BY ended_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def audit(tool: str, args_json: str, ok: bool, summary: str) -> None:
    with _lock, connect() as c:
        c.execute(
            "INSERT INTO audit(tool,args_json,ok,result_summary,ts) VALUES(?,?,?,?,?)",
            (tool, args_json[:800], int(bool(ok)), summary[:300], time.time()),
        )


def recent_audit(limit: int = 12) -> list[dict]:
    with _lock, connect() as c:
        rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def trace(turn_id: str, stage: str, ms: float, detail: str = "") -> None:
    with _lock, connect() as c:
        c.execute(
            "INSERT INTO traces(turn_id,stage,ms,detail,ts) VALUES(?,?,?,?,?)",
            (turn_id[:40], stage[:40], round(ms, 1), detail[:300], time.time()),
        )


# ---------------- reminders ----------------

def reminder_add(text: str, due_at: float) -> int:
    with _lock, connect() as c:
        cur = c.execute(
            "INSERT INTO reminders(text,due_at,fired,created) VALUES(?,?,0,?)",
            (text[:400], due_at, time.time()),
        )
        return int(cur.lastrowid or 0)


def reminders_due(now: float | None = None) -> list[dict]:
    now = now if now is not None else time.time()
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT id,text,due_at FROM reminders WHERE fired=0 AND due_at<=? ORDER BY due_at",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def reminder_mark_fired(rid: int) -> None:
    with _lock, connect() as c:
        c.execute("UPDATE reminders SET fired=1 WHERE id=?", (rid,))


def reminders_pending() -> list[dict]:
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT id,text,due_at FROM reminders WHERE fired=0 ORDER BY due_at LIMIT 20"
        ).fetchall()
    return [dict(r) for r in rows]


def reminder_cancel(rid: int) -> bool:
    with _lock, connect() as c:
        cur = c.execute("DELETE FROM reminders WHERE id=? AND fired=0", (int(rid),))
        return cur.rowcount > 0


# ---------------- phase 4: vectors + knowledge graph ----------------

def chunk_add(source: str, ord_: int, text: str,
              embedding: bytes | None) -> int:
    with _lock, connect() as c:
        cur = c.execute(
            "INSERT INTO vec_chunks(source,ord,text,embedding,ts) VALUES(?,?,?,?,?)",
            (source[:200], int(ord_), text[:4000], embedding, time.time()),
        )
        return int(cur.lastrowid or 0)


def chunk_delete_source(source: str) -> int:
    with _lock, connect() as c:
        cur = c.execute("DELETE FROM vec_chunks WHERE source=?", (source,))
        return cur.rowcount


def all_chunks() -> list[dict]:
    """Every stored chunk with its embedding BLOB (small corpora fit RAM)."""
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT id,source,ord,text,embedding FROM vec_chunks ORDER BY id"
        ).fetchall()
    return [dict(r) for r in rows]


def triple_add(subject: str, predicate: str, obj: str,
               src: str = "inferred") -> None:
    s, p, o = subject.strip().lower()[:120], predicate.strip().lower()[:80], \
        obj.strip().lower()[:300]
    if not (s and p and o):
        return
    with _lock, connect() as c:
        dup = c.execute(
            "SELECT 1 FROM triples WHERE subject=? AND predicate=? AND object=?",
            (s, p, o)).fetchone()
        if not dup:
            c.execute(
                "INSERT INTO triples(subject,predicate,object,src,ts) "
                "VALUES(?,?,?,?,?)", (s, p, o, src[:40], time.time()))


def triples_all(limit: int = 100) -> list[dict]:
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT subject,predicate,object FROM triples "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def set_episode_embedding(ep_id: int, embedding: bytes) -> None:
    with _lock, connect() as c:
        c.execute("UPDATE episodes SET embedding=? WHERE id=?", (embedding, ep_id))


def episodes_with_embeddings(limit: int = 300) -> list[dict]:
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT id,summary,started_at,ended_at,importance,embedding "
            "FROM episodes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def proposal_add(kind: str, signature: str, detail: str) -> int | None:
    """Insert once per unique signature. Returns id the FIRST time only."""
    with _lock, connect() as c:
        dup = c.execute(
            "SELECT id FROM proposals WHERE signature=?",
            (signature,)).fetchone()
        if dup:
            return None
        cur = c.execute(
            "INSERT INTO proposals(kind,signature,detail,created) VALUES(?,?,?,?)",
            (kind[:40], signature[:200], detail[:600], time.time()))
        return int(cur.lastrowid or 0)


def proposals(status: str = "pending", limit: int = 10) -> list[dict]:
    with _lock, connect() as c:
        rows = c.execute(
            "SELECT id,kind,detail,status,created FROM proposals "
            "WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
    return [dict(r) for r in rows]


def proposal_set_status(pid: int, status: str) -> bool:
    with _lock, connect() as c:
        cur = c.execute("UPDATE proposals SET status=? WHERE id=?",
                        (status, int(pid)))
        return cur.rowcount > 0


def record_event(topic: str, payload: dict | str) -> None:
    """Journal an event into SQLite for diagnostics and crash recovery."""
    payload_str = payload if isinstance(payload, str) else json.dumps(payload)
    try:
        with _lock, connect() as c:
            c.execute(
                "INSERT INTO events(topic, payload, created_at) VALUES(?, ?, ?)",
                (topic, payload_str, time.time())
            )
    except Exception as exc:
        log.warning('db.record_event: %s', exc)
        pass


def get_recent_events(limit: int = 50, topic: str = "") -> list[dict]:
    """Retrieve recent journaled events."""
    try:
        with _lock, connect() as c:
            if topic:
                rows = c.execute(
                    "SELECT id, topic, payload, created_at FROM events WHERE topic=? ORDER BY id DESC LIMIT ?",
                    (topic, limit)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT id, topic, payload, created_at FROM events ORDER BY id DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        log.warning('db.get_recent_events: %s', exc)
        return []


def replay_events(since_ts: float, limit: int = 200) -> list[dict]:
    """Replay events occurred since a given timestamp for crash recovery."""
    try:
        with _lock, connect() as c:
            rows = c.execute(
                "SELECT id, topic, payload, created_at FROM events WHERE created_at >= ? ORDER BY created_at ASC LIMIT ?",
                (float(since_ts), limit)
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d["payload"])
                except Exception as exc:
                    log.warning('db.replay_events: %s', exc)
                    pass
                out.append(d)
            return out
    except Exception as exc:
        log.warning('db.replay_events: %s', exc)
        return []


def get_last_shutdown_ts() -> float:
    """Retrieve last recorded shutdown timestamp for replay recovery."""
    try:
        with _lock, connect() as c:
            row = c.execute("SELECT val FROM kv WHERE key='system:last_shutdown'").fetchone()
            if row:
                return float(row["val"])
    except Exception as exc:
        log.warning('db.get_last_shutdown_ts: %s', exc)
        pass
    return 0.0


def set_last_shutdown_ts(ts: float | None = None) -> None:
    """Record current shutdown timestamp for replay on next start."""
    if ts is None:
        ts = time.time()
    try:
        with _lock, connect() as c:
            c.execute(
                "INSERT INTO kv(key, val, updated_at) VALUES('system:last_shutdown', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET val=excluded.val, updated_at=excluded.updated_at",
                (str(float(ts)), time.time())
            )
    except Exception as exc:
        log.warning('db.set_last_shutdown_ts: %s', exc)
        pass


def save_anecdote(name: str, narrative: str, emotion: str = "general") -> int:
    """Save a named memorable moment or anecdote for personality depth."""
    try:
        with _lock, connect() as c:
            cur = c.execute(
                "INSERT INTO anecdotes (name, narrative, emotion, created_at, referenced_count) VALUES (?, ?, ?, ?, 0)",
                (name, narrative, emotion, time.time()),
            )
            return cur.lastrowid
    except Exception as exc:
        log.warning('db.save_anecdote: %s', exc)
        return 0


def anecdotes_by_emotion(emotion: str, limit: int = 5) -> list[dict]:
    """Retrieve recent anecdotes filtered by emotion (e.g. 'funny', 'proud')."""
    try:
        with _lock, connect() as c:
            cur = c.execute(
                "SELECT id, name, narrative, emotion, created_at, referenced_count FROM anecdotes WHERE emotion = ? ORDER BY created_at DESC LIMIT ?",
                (emotion, limit),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        log.warning('db.anecdotes_by_emotion: %s', exc)
        return []


def all_anecdotes(limit: int = 20) -> list[dict]:
    """Retrieve all recent anecdotes."""
    try:
        with _lock, connect() as c:
            cur = c.execute(
                "SELECT id, name, narrative, emotion, created_at, referenced_count FROM anecdotes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        log.warning('db.all_anecdotes: %s', exc)
        return []


def import_messages(messages_list: list[dict]) -> int:
    """Bulk import messages for cross-device state sync."""
    imported = 0
    try:
        with _lock, connect() as c:
            for m in messages_list:
                role = m.get("role", "user")
                content = m.get("content", "")
                surface = m.get("surface", "sync")
                ts = m.get("ts", time.time())
                if content:
                    c.execute(
                        "INSERT INTO messages (role, content, surface, ts) VALUES (?, ?, ?, ?)",
                        (role, content, surface, ts),
                    )
                    imported += 1
    except Exception as exc:
        log.warning('db.import_messages: %s', exc)
        pass
    return imported
