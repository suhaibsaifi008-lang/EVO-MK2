import sqlite3
import os

db_path = os.path.join(os.environ["LOCALAPPDATA"], "Temp", "opencode", "freellmapi", "server", "data", "freeapi.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("tables:", tables)

for t in tables:
    if "key" in t.lower() or "provider" in t.lower() or "platform" in t.lower():
        rows = conn.execute(f"SELECT * FROM {t} LIMIT 5").fetchall()
        cols = rows[0].keys() if rows else []
        print(f"\n=== {t} ({len(rows)} rows shown) ===")
        for r in rows:
            safe = {}
            for k in cols:
                v = r[k]
                if isinstance(v, str) and len(v) > 30:
                    v = v[:15] + "..."
                safe[k] = v
            print(" ", dict(safe))
