"""Minimal stdlib HTTP server for EVO MK2."""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, "/sessions/gracious-laughing-tesla/mnt/EVO-MK2")
UI_DIR = Path("/sessions/gracious-laughing-tesla/mnt/EVO-MK2/mk2/ui")

def _build_response(status, ctype, body):
 if isinstance(body, str):
 body = body.encode("utf-8")
 header = "HTTP/1.1 " + status + "\r\n"
 header += "Content-Type: " + ctype + "\r\n"
 header += "Content-Length: " + str(len(body)) + "\r\n"
 header += "Connection: close\r\n"
 header += "Access-Control-Allow-Origin: *\r\n\r\n"
 return header.encode("utf-8") + body

def _json_ok(data):
 return _build_response("200 OK", "application/json", json.dumps(data, ensure_ascii=False))

def _json_err(code, msg):
 return _build_response(str(code) + " Error", "application/json", json.dumps({"error": msg}, ensure_ascii=False))

_modules = {}

def _get(key):
 global _modules
 if not _modules:
 try:
 from mk2 import config as c
 _modules["config"] = c
 except Exception:
 pass
 try:
 from mk2 import tools as t
 _modules["tools"] = t
 except Exception:
 pass
 try:
 from mk2 import brain as b
 _modules["brain"] = b
 except Exception:
 pass
 try:
 from mk2 import llm as l
 _modules["llm"] = l
 except Exception:
 pass
 return _modules.get(key)

async def _handle(r, w):
 try:
 data = await r.read
 if not data:
 w.close()
 return
 text = data.decode("utf-8", errors="replace")
 req_lines = text.split("\r\n")
 parts = req_lines[0].split(" ", 2) if req_lines else ["GET", "/", ""]
 method = parts[0]
 raw_path = parts[1] if len(parts) > 1 else "/"
 parsed = urlparse(raw_path)
 path = parsed.path
 body = {}
 if method == "POST":
 idx = text.find("\r\n\r\n")
 if idx >= 0:
 try:
 body = json.loads(text[idx + 4:])
 except Exception:
 body = {}
 print(" " + method + " " + path)
 settings = _get("config").settings
 brain_mod = _get("brain")
 tools_mod = _get("tools")
 llm_mod = _get("llm")
 if path == "/api/health":
 provs = []
 n_tools = 0
 try:
 if llm_mod is not None and hasattr(llm_mod, "_providers"):
 provs = [p["name"] for p in llm_mod._providers()]
 if tools_mod is not None and hasattr(tools_mod, "manifest"):
 n_tools = len(tools_mod.manifest())
 except Exception:
 pass
 w.write(_json_ok({"ok": True, "name": settings.name, "llm_online": True, "providers": provs, "tools": n_tools, "voice": "stdlib"}))
 elif path == "/api/chat" and method == "POST":
 try:
 reply = brain_mod.handle_turn(body.get("text", "")) if brain_mod is not None else "brain not loaded"
 w.write(_json_ok({"reply": reply}))
 except Exception as e:
 w.write(_json_err(500, str(e)[:200]))
 elif path == "/" or path == "/face":
 idx = UI_DIR / "index.html"
 if idx.exists():
 w.write(_build_response("200 OK", "text/html; charset=utf-8", idx.read_bytes()))
 else:
 w.write(_json_ok({"name": settings.name, "status": "running"}))
 elif path.startswith("/ui/"):
 safe = Path(path.split("/")[-1]).name
 fp = UI_DIR / safe
 if fp.exists():
 ext = Path(safe).suffix.lower()
 ctype_map = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}
 ctype = ctype_map.get(ext, "text/plain")
 w.write(_build_response("200 OK", ctype, fp.read_bytes()))
 else:
 w.write(_json_err(404, "not found"))
 else:
 w.write(_json_err(404, "unknown route: " + path))
 except Exception as e:
 print(" handler error: " + str(e))
 try:
 w.write(_json_err(500, str(e)[:200]))
 except Exception:
 pass
 finally:
 try:
 await w.drain()
 except Exception:
 pass
 w.close()


async def _server_loop(host="127.0.0.1", port=8421):
 srv = await asyncio.start_server(_handle, host, port)
 print(" EVO MK2 Jarvis stdlib server: http://" + host + ":" + str(port))
 async with srv:
 await srv.serve_forever()


if __name__ == "__main__":
 import argparse
 ap = argparse.ArgumentParser()
 ap.add_argument("--host", default="127.0.0.1")
 ap.add_argument("--port", type=int, default=8421)
 args = ap.parse_args()
 asyncio.run(_server_loop(args.host, args.port))