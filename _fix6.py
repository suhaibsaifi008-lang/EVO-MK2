from pathlib import Path

fixed = []
for p in Path(".").rglob("*.py"):
    if any(part in str(p) for part in (".venv", "__pycache__")):
        continue
    s = p.read_text(encoding="utf-8")
    if "\$" in s:
        n = s.count("\$")
        p.write_text(s.replace("\$", "$"), encoding="utf-8")
        fixed.append(f"{p} ({n})")
print("files repaired:", fixed or "none")
