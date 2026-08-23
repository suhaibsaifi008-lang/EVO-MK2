"""Web fetch/search helpers (stdlib only)."""
import html as htmllib
import re
import urllib.parse
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EVO-MK2"}


def _get(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, "ignore")


def ddg_results(query: str, max_results: int = 5) -> list[dict]:
    raw = _get("https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query))
    out = []
    for m in re.finditer(r'(?is)<a[^>]+href="[^"]*uddg=([^&"\']+)[^"]*"[^>]*>(.*?)</a>', raw):
        url = urllib.parse.unquote(htmllib.unescape(m.group(1)))
        if "duckduckgo.com" in url:
            continue
        title = re.sub(r"(?s)<[^>]+>", "", htmllib.unescape(m.group(2))).strip()
        if len(title) >= 4:
            out.append({"title": title[:120], "url": url})
        if len(out) >= max_results:
            break
    return out


def fetch_page_text(url: str, max_chars: int = 3000) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    raw = _get(url)
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = htmllib.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    body = "\n".join(ln for ln in lines if ln)[:max_chars]
    return body or "(empty page)"
