from . import tool

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


def _try_ddg(query: str, max_results: int) -> list[dict]:
    raw = _get("https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query))
    out = []
    for m in re.finditer(r'(?is)<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>', raw):
        href = m.group(1)
        title = re.sub(r"(?s)<[^>]+>", "", htmllib.unescape(m.group(2))).strip()
        real_url = ""
        if "uddg=" in href:
            real_url = urllib.parse.unquote(href.split("uddg=")[-1].split("&")[0])
        elif href.startswith("http"):
            real_url = href
        if real_url and "duckduckgo" not in real_url and len(title) >= 4:
            out.append({"title": title[:120], "url": real_url})
    return out[:max_results]


def _try_bing(query: str, max_results: int) -> list[dict]:
    raw = _get("https://www.bing.com/search?q=" + urllib.parse.quote_plus(query))
    out = []
    # Bing changes HTML often — match ANY link that goes to a non-Bing domain
    for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', raw):
        url = m.group(1)
        if "bing.com" in url or "microsoft" in url or "msn.com" in url:
            continue
        title = re.sub(r"(?s)<[^>]+>", "", htmllib.unescape(m.group(2))).strip()
        if len(title) >= 10 and "." in url.split("//")[-1].split("/")[0]:
            out.append({"title": title[:120], "url": url})
        if len(out) >= max_results:
            break
    return out


def ddg_results(query: str, max_results: int = 5) -> list[dict]:
    """Multi-engine search with automatic fallback.
    Tries DDG lite, then Bing. Returns whatever it finds."""
    for engine_fn in (_try_ddg, _try_bing):
        try:
            results = engine_fn(query, max_results)
            if results:
                return results
        except Exception:
            continue
    return []




def _is_safe_url(url: str) -> bool:
    try:
        import ipaddress
        import socket
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        host = parsed.hostname or ""
        if not host or host.lower() in ("localhost", "127.0.0.1", "::1", "metadata.google.internal"):
            return False
        # Resolve IP to verify non-private / non-loopback
        addrs = socket.getaddrinfo(host, None)
        for family, _, _, _, sockaddr in addrs:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast:
                return False
        return True
    except Exception:
        return False


def fetch_page_text(url: str, max_chars: int = 3000) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not _is_safe_url(url):
        return "(blocked: internal or unsafe URL)"
    raw = _get(url)
    raw = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", raw)
    text = htmllib.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    body = "\n".join(ln for ln in lines if ln)[:max_chars]
    return body or "(empty page)"
