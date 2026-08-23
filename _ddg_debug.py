import sys
sys.path.insert(0, ".")
from mk2.tools.web_tools import _get
import re

raw = _get("https://lite.duckduckgo.com/lite/?q=best+laptop+under+50000")
print(f"total: {len(raw)} chars")

# find all <a> tags with href
links = re.findall(r'<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', raw, re.DOTALL)
print(f"all <a> tags: {len(links)}")

# find links that go to external sites
for href, text in links[:20]:
    clean_text = re.sub(r"<[^>]+>", "", text).strip()
    if href.startswith("http") and "duckduckgo" not in href:
        print(f"  EXTERNAL: {href[:80]} -> {clean_text[:60]}")
    elif "uddg=" in href:
        import urllib.parse
        real_url = urllib.parse.unquote(href.split("uddg=")[-1].split("&")[0])
        print(f"  UDDG: {real_url[:80]} -> {clean_text[:60]}")

# check if there are result snippets
snippets = re.findall(r'class="result-snippet"[^>]*>(.*?)</', raw, re.DOTALL)
print(f"\nsnippets: {len(snippets)}")

# check for table structure (old lite format)
rows = re.findall(r"<tr>", raw)
print(f"table rows: {len(rows)}")
