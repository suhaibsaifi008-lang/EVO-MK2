"""Add provider keys directly to FreeLLMAPI's SQLite database."""
import sqlite3
import os

db_path = os.path.join(os.environ["LOCALAPPDATA"], "Temp", "opencode", "freellmapi", "server", "data", "freeapi.db")

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# Show schema
print("=== api_keys schema ===")
cols = [r[1] for r in conn.execute("PRAGMA table_info(api_keys)").fetchall()]
print(cols)

# Show encryption key file
enc_key_path = os.path.join(os.environ["LOCALAPPDATA"], "Temp", "opencode", "freellmapi", "server", "data", ".encryption-key")
if os.path.exists(enc_key_path):
    enc_key = open(enc_key_key_path if False else enc_key_path, "rb").read()
    print(f"\nencryption key: {len(enc_key)} bytes")

# Check settings table for any relevant config
print("\n=== settings ===")
for r in conn.execute("SELECT * FROM settings LIMIT 10").fetchall():
    safe = {k: (str(v)[:20]+"..." if len(str(v))>30 else v) for k,v in dict(r).items()}
    print(safe)

# Check what platforms/providers are expected
print("\n=== existing fallback_config ===")
for r in conn.execute("SELECT * FROM fallback_config LIMIT 5").fetchall():
    print(dict(r))

conn.close()
