"""M2 tools registration: files, docs, vision, time/reminders, calendar."""
from .tools import tool  # noqa: F401
from .fs_tools import fs_read, fs_search, fs_write  # noqa: F401
from . import fs_tools as _fs  # noqa: F401


import subprocess
import time as t

from pathlib import Path

from .config import DATA


def _capture_png() -> Path:
    """Capture the whole screen to a PNG; returns the path."""
    shots = DATA / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    stamp = t.strftime("%Y%m%d_%H%M%S") + "_" + str(t.time_ns() % 1000)
    out = shots / f"cap_{stamp}.png"
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
        "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
        f"$bmp.Save('{out}');$g.Dispose();$bmp.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=25)
    return out


@tool("docs_read", "Read text from TXT/MD/CSV/JSON/PDF/DOCX files.",
      {"path": {"type": "string"}, "max_chars": {"type": "integer"}}, permission="read")
def docs_read(path: str, max_chars: int = 4000) -> dict:
    from .fs_tools import _safe

    p = _safe(path)
    if not p.is_file():
        return {"ok": False, "speech": f"No file {p.name}.", "data": {}}
    ext = p.suffix.lower()
    text = ""
    if ext in (".txt", ".md", ".csv", ".json", ".log"):
        text = p.read_text(encoding="utf-8", errors="replace")
    elif ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return {"ok": False, "speech": "PDF support needs: pip install pypdf", "data": {}}
        reader = PdfReader(str(p))
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:30])
    elif ext == ".docx":
        try:
            import docx
        except ImportError:
            return {"ok": False, "speech": "DOCX support needs: pip install python-docx", "data": {}}
        d = docx.Document(str(p))
        text = "\n".join(par.text for par in d.paragraphs)
    else:
        return {"ok": False, "speech": f"Unsupported type {ext}.", "data": {}}
    body = text[: int(max_chars)]
    return {"ok": bool(body), "speech": body[:600] or "(empty)", "data": {"text": body}}


@tool("screenshot", "Capture the screen to a PNG.", {}, permission="read")
def screenshot() -> dict:
    p = _capture_png()
    return {"ok": True, "speech": "Screenshot captured.",
            "data": {"path": str(p)}}


@tool("screen_read", "Look at the screen and answer a question about it.",
      {"question": {"type": "string"}}, permission="read")
def screen_read(question: str = "Describe what is on the screen.") -> dict:
    from .llm import LLMUnavailable, chat_vision

    png = _capture_png()
    try:
        answer = chat_vision(question.strip()[:300] or "Describe the screen.",
                             png.read_bytes(), timeout=40)
    except LLMUnavailable as exc:
        return {"ok": False,
                "speech": ("I captured the screen but my vision model is "
                           f"unreachable ({str(exc)[:100]})."),
                "data": {"path": str(png)}}
    return {"ok": True, "speech": answer[:600], "data": {}}


@tool("clipboard_get", "Read clipboard text.", {}, permission="read")
def clipboard_get() -> dict:
    import subprocess

    r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                       capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
    return {"ok": True, "speech": (r.stdout or "(empty)")[:400], "data": {}}


@tool("reminder_add", "Add a reminder. when examples: 'in 10 minutes', 'at 9pm', 'tomorrow at 7am'.",
      {"text": {"type": "string"}, "when": {"type": "string"}}, permission="read")
def reminder_add_tool(text: str, when: str) -> dict:
    from . import db
    from .timeparse import parse_when

    due = parse_when(when)
    if not due:
        return {"ok": False, "speech": "I couldn't understand that time. Try 'in 10 minutes' or 'at 9pm'.",
                "data": {}}
    rid = db.reminder_add(text.strip()[:300], due.timestamp())
    return {"ok": True, "speech": f"Reminder set for {due.strftime('%H:%M')} ({text.strip()[:60]}).",
            "data": {"id": rid, "due": due.isoformat()}}


@tool("reminder_list", "List pending reminders.", {}, permission="read")
def reminder_list() -> dict:
    from . import db

    rows = db.reminders_pending()
    if not rows:
        return {"ok": True, "speech": "No pending reminders.", "data": {"items": rows}}
    lines = [f"{r['id']}: {r['text'][:50]} @ "
             f"{__import__('datetime').datetime.fromtimestamp(r['due_at']).strftime('%a %H:%M')}"
             for r in rows]
    return {"ok": True, "speech": "; ".join(lines), "data": {"items": rows}}


@tool("reminder_cancel", "Cancel a reminder by id.", {"id": {"type": "integer"}}, permission="read")
def reminder_cancel(id: int) -> dict:
    from . import db

    ok = db.reminder_cancel(int(id))
    return {"ok": ok, "speech": "Cancelled." if ok else f"No pending reminder #{id}.", "data": {}}
