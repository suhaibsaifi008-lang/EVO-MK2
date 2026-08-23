"""Filesystem tools with hard path-allowlist security.

Allowed roots: user Documents/Desktop/Downloads + EVO-MK2/data.
Everything else â€” denied and audited. Path traversal attempts are
resolved before checks so ../, absolute paths, and short-name tricks
cannot escape.
"""
from pathlib import Path

from .tools import tool
from .config import DATA

ALLOWED_ROOTS = [
    Path.home() / "Documents",
    Path.home() / "Desktop",
    Path.home() / "Downloads",
    DATA,
]


class OutsideRoots(PermissionError):
    pass


def _safe(p: str) -> Path:
    cand = Path(str(p)).expanduser()
    if not cand.is_absolute():
        cand = ALLOWED_ROOTS[-1] / cand
    resolved = cand.resolve()
    for root in ALLOWED_ROOTS:
        r = root.resolve()
        try:
            if resolved == r or r in resolved.parents or str(resolved).lower().startswith(str(r).lower() + "\\"):
                return resolved
        except OSError:
            continue
    raise OutsideRoots(f"outside allowed roots: {list(str(r) for r in ALLOWED_ROOTS)}")


@tool("fs_read", "Read a text file inside allowed folders (Documents/Desktop/Downloads/EVO data).",
      {"path": {"type": "string"}, "max_chars": {"type": "integer"}}, permission="read")
def fs_read(path: str, max_chars: int = 4000) -> dict:
    p = _safe(path)
    if not p.is_file():
        return {"ok": False, "speech": f"No file at {p.name}.", "data": {}}
    text = p.read_text(encoding="utf-8", errors="replace")[: int(max_chars)]
    return {"ok": True, "speech": text[:600] or "(empty file)", "data": {"path": str(p), "text": text}}


@tool("fs_write", "Write (or overwrite) a text file inside allowed folders.",
      {"path": {"type": "string"}, "content": {"type": "string"}}, permission="execute")
def fs_write(path: str, content: str) -> dict:
    p = _safe(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"ok": True, "speech": f"Saved {p.name} ({len(content)} chars).", "data": {"path": str(p)}}


@tool("fs_search", "Search filenames inside allowed folders.",
      {"pattern": {"type": "string"}, "root": {"type": "string"}}, permission="read")
def fs_search(pattern: str, root: str = "") -> dict:
    base = _safe(root) if root else ALLOWED_ROOTS[0]
    pat = f"*{pattern.strip().lower()}*"
    hits = []
    for p in sorted(base.rglob("*")):
        if p.is_file() and pattern.lower() in p.name.lower():
            hits.append({"name": p.name, "path": str(p), "size": p.stat().st_size})
        if len(hits) >= 20:
            break
    if not hits:
        return {"ok": False, "speech": f"Nothing matching '{pattern}' in {base.name}.", "data": {}}
    speech = "; ".join(h["name"] for h in hits[:6])
    return {"ok": True, "speech": f"Found: {speech}", "data": {"hits": hits}}

