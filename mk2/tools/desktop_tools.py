"""Desktop hands: gated mouse/keyboard/window control for EVO.

Safety architecture (do not remove lightly):
- Master switch EVO_DESKTOP_CONTROL=1 (set 0 to disarm everything here).
- PyAutoGUI FAILSAFE is always ON: slam the mouse into any screen corner
  and every action aborts instantly with FailSafeException.
- Actions are audited automatically by the tool registry, and each action
  saves a screenshot into data/vision/ so you can see what EVO did.
- Guidance for the brain baked into descriptions: focus a window before
  typing; use screen_read/browser_read to locate before clicking.
"""
import os
import threading
import time
from pathlib import Path

from ..config import DATA
from . import tool

VISION_DIR = DATA / "vision"

_pag_mod = None
_pag_lock = threading.Lock()
_last_focus = {"title": "", "ts": 0.0}


def enabled() -> bool:
    return os.environ.get("EVO_DESKTOP_CONTROL", "1") == "1"


def _pag():
    global _pag_mod
    with _pag_lock:
        if _pag_mod is None:
            import pyautogui as _p

            _p.FAILSAFE = True          # corner-slam aborts everything
            _p.PAUSE = 0.08             # human-ish pacing between actions
            _pag_mod = _p
    return _pag_mod


def _focus_recent(max_age: float = 180.0) -> bool:
    return (time.time() - _last_focus["ts"]) < max_age


def _release_modifiers() -> None:
    """Defensive: clear any stuck shift/ctrl/alt so typed text isn't
    case-mangled by a half-finished chord."""
    try:
        p = _pag()
        for k in ("shift", "ctrl", "alt"):
            p.keyUp(k)
    except Exception:
        pass


def _snap(action: str) -> str:
    """Screenshot after every action -> verifiable audit trail."""
    try:
        VISION_DIR.mkdir(parents=True, exist_ok=True)
        path = VISION_DIR / f"desktop_{action}_{int(time.time()*1000)}.png"
        _pag().screenshot(str(path))
        return str(path)
    except Exception:
        return ""


def _wrap(fn):
    def inner(*args, **kwargs):
        if not enabled():
            return {"ok": False,
                    "speech": "Desktop control is disarmed "
                              "(EVO_DESKTOP_CONTROL=0).",
                    "data": {}}
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if "FailSafe" in name:
                return {"ok": False,
                        "speech": "Failsafe tripped - mouse hit a screen "
                                  "corner. Aborting all input actions.",
                        "data": {"failsafe": True}}
            return {"ok": False, "speech": f"{name}: {str(exc)[:140]}",
                    "data": {}}
    return inner


@tool("desktop_windows",
      "List titles of open desktop windows. Use this to find the exact "
      "window name before window_focus.",
      {}, permission="read")
@_wrap
def desktop_windows() -> dict:
    import pygetwindow as pgw

    titles = [t.strip() for t in pgw.getAllTitles() if t and t.strip()]
    return {"ok": True, "speech": f"{len(titles)} windows open.",
            "data": {"titles": titles[:40]}}


@tool("window_focus",
      "Bring a desktop window to the front by (partial) title, so the next "
      "typed keys land in it. REQUIRED before type_text/press_key; waits "
      "up to 8s for slow-launching apps.",
      {"target": {"type": "string"}}, permission="execute")
@_wrap
def window_focus(target: str) -> dict:
    import pygetwindow as pgw

    want = (target or "").lower().strip()
    deadline = time.time() + 8.0
    win = None
    while time.time() < deadline and win is None:
        matches = [w for w in pgw.getAllWindows()
                   if want in (w.title or "").lower()]
        if matches:
            win = matches[0]
            break
        time.sleep(0.5)
    if win is None:
        return {"ok": False,
                "speech": f"No window matching '{target}' appeared within "
                          f"8s. Try desktop_windows first.", "data": {}}
    try:
        win.activate()
    except Exception:  # noqa: BLE001
        try:
            win.minimize()
            win.restore()
            win.activate()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "speech": f"Couldn't activate it: "
                                           f"{str(exc)[:100]}", "data": {}}
    time.sleep(0.4)
    _last_focus.update(title=win.title, ts=time.time())
    _release_modifiers()
    return {"ok": True, "speech": f"Focused '{win.title[:60]}'.",
            "data": {"title": win.title}}


@tool("mouse_click",
      "Click the mouse at x,y screen coordinates (omit both to click where "
      "the cursor is). Use screen_read first to locate what to click.",
      {"x": {"type": "integer"}, "y": {"type": "integer"},
       "button": {"type": "string"}, "clicks": {"type": "integer"}},
      permission="execute", long_running=True)
@_wrap
def mouse_click(x: int = None, y: int = None, button: str = "left",
                clicks: int = 1) -> dict:  # noqa: ANN001
    p = _pag()
    moved = ""
    if x is not None and y is not None:
        p.moveTo(int(x), int(y), duration=0.15)
        moved = f" at {int(x)},{int(y)}"
    p.click(button=button or "left", clicks=int(clicks or 1))
    return {"ok": True,
            "speech": f"Clicked{moved}.",
            "data": {"screenshot": _snap("click")}}


@tool("mouse_scroll", "Scroll the mouse wheel: value=up|down|left|right.",
      {"value": {"type": "string"}}, permission="execute")
@_wrap
def mouse_scroll(value: str = "down") -> dict:
    p = _pag()
    v = (value or "down").lower()
    if v == "up":
        p.scroll(600)
    elif v == "down":
        p.scroll(-600)
    elif v == "left":
        p.hscroll(-600)
    elif v == "right":
        p.hscroll(600)
    else:
        return {"ok": False, "speech": "Scroll direction must be "
                "up/down/left/right.", "data": {}}
    return {"ok": True, "speech": f"Scrolled {v}.",
            "data": {"screenshot": _snap("scroll")}}


@tool("type_text",
      "Put text into the currently focused window. Default mode='paste' "
      "(clipboard -> ctrl+v: instant and layout-immune; clipboard restored "
      "after). mode='keys' types character-by-character like a human. "
      "Refuses unless window_focus succeeded recently (force=true overrides).",
      {"text": {"type": "string"}, "force": {"type": "boolean"},
       "mode": {"type": "string"}},
      permission="execute", long_running=True)
@_wrap
def type_text(text: str = "", force: bool = False, mode: str = "paste") -> dict:
    text = str(text or "")
    if not text:
        return {"ok": False, "speech": "Nothing to type.", "data": {}}
    if not force and not _focus_recent():
        return {"ok": False,
                "speech": "No window focused yet - I won't type blind. Use "
                          "window_focus first.",
                "data": {"need_focus": True}}
    p = _pag()
    _release_modifiers()
    text = text[:2000]
    if (mode or "paste").lower() == "paste":
        import pyperclip

        try:
            previous = pyperclip.paste()
        except Exception:
            previous = None
        pyperclip.copy(text)
        p.hotkey("ctrl", "v")
        time.sleep(0.4)
        try:
            if previous:
                pyperclip.copy(previous)
            else:
                pyperclip.copy("")
        except Exception:
            pass
        return {"ok": True, "speech": f"Pasted {len(text)} characters.",
                "data": {"mode": "paste", "screenshot": _snap("type")}}
    p.write(text, interval=0.012)
    return {"ok": True, "speech": f"Typed {len(text)} characters.",
            "data": {"mode": "keys", "screenshot": _snap("type")}}


@tool("press_key",
      "Press a key or chord in the focused window: 'enter', 'esc', 'tab', "
      "'ctrl+s', 'alt+f4', 'win' etc (+ separates chord keys). Refuses "
      "without a recent successful window_focus unless force=true.",
      {"keys": {"type": "string"}, "force": {"type": "boolean"}},
      permission="execute")
@_wrap
def press_key(keys: str = "", force: bool = False) -> dict:
    keys = (keys or "").strip()
    if not keys:
        return {"ok": False, "speech": "Which key?", "data": {}}
    if not force and not _focus_recent():
        return {"ok": False,
                "speech": "No window focused yet - use window_focus first.",
                "data": {"need_focus": True}}
    if "+" in keys:
        _pag().hotkey(*[k.strip() for k in keys.split("+") if k.strip()])
    else:
        _pag().press(keys)
    time.sleep(0.2)
    _release_modifiers()
    return {"ok": True, "speech": f"Pressed {keys}.",
            "data": {"screenshot": _snap("press")}}
