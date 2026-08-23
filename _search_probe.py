import sys
sys.path.insert(0, ".")
from mk2.tools.web_tools import _get

print("=== DDG raw ===")
try:
    r = _get("https://lite.duckduckgo.com/lite/?q=test")
    print(f"{len(r)} chars")
    print(r[:300])
except Exception as e:
    print(f"DDG error: {e}")

print("\n=== Bing raw ===")
try:
    r2 = _get("https://www.bing.com/search?q=test")
    print(f"{len(r2)} chars")
    import re
    links = re.findall(r'href="(https?://[^"]+)"', r2)
    real = [l for l in links if "bing" not in l and "microsoft" not in l][:5]
    print(f"links: {len(real)}")
except Exception as e:
    print(f"Bing error: {e}")
