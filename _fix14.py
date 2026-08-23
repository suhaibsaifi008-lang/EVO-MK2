from pathlib import Path

p = Path("mk2/tools/__init__.py")
s = p.read_text(encoding="utf-8")

if "_emitter" not in s:
    s = s.replace(
        '_REGISTRY: dict[str, "Tool"] = {}',
        '''_REGISTRY: dict[str, "Tool"] = {}
_emitter = {"fn": None}


def set_emitter(fn) -> None:
    """Brain attaches its event emitter so long tools can stream progress."""
    _emitter["fn"] = fn


def emit_progress(text: str) -> None:
    fn = _emitter.get("fn")
    if fn:
        try:
            fn({"type": "progress", "text": str(text)[:160]})
        except Exception:
            pass''',
    )

# route tool failures/starts through errlog ring as well
p.write_text(s, encoding="utf-8")
print("emitter added to registry")
