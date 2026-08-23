from pathlib import Path

p = Path("mk2/voice/tts_best.py")
s = p.read_text(encoding="utf-8")
s = s.replace('if len(text) <= 90 or mode == "edge" and False:', "if len(text) <= 90:")
p.write_text(s, encoding="utf-8")

p2 = Path("mk2/server.py")
s2 = p2.read_text(encoding="utf-8")
s2 = s2.replace(
    "async def transcribe(request) -> dict:",
    "async def transcribe(request: Request) -> dict:",
)
s2 = s2.replace("from .voice import tts as tts_mod", "from .voice import tts_best")
s2 = s2.replace("path = tts_mod.synthesize_best", "path = tts_best.synthesize_best")
p2.write_text(s2, encoding="utf-8")
print("patched both files")
