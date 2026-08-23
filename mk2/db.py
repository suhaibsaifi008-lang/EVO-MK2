"""SQLite persistence. One file = one assistant. Migrations are idempotent."""
import sqlite3
import threading
import time

from .config import DATA

DB_PATH = DATA / "evo_mk2.db"
_lock = threading.Lock()

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
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts DESC);
CREATE INDEX IF NOT EXISTS idx_traces_turn ON traces(turn_id);
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
        c.execute("DELETE FROM messages WHERE id <= (SELECT MAX(id)-5000 FROM messages)")
        return int(cur.lastrowid or 0)


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
    with _lock, connect() as c:
        cur = c.execute("DELETE FROM facts WHERE key=?", (key.strip().lower(),))
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
