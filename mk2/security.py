"""Phase 6: Security — URL vetting, breach checks, system posture.

url_check combines offline heuristics (punycode, IP hosts, sketchy TLDs,
brand impersonation, link-shorteners) with Google Safe Browsing when a
key is configured. open_app consults this BEFORE any browser opens.
"""
import ipaddress
import json
import re
import urllib.parse
import urllib.request

from . import db
from .tools import tool

SUSPICIOUS_TLDS = {"zip", "mov", "tk", "ml", "ga", "cf", "gq", "top", "xyz",
                   "click", "country", "work", "link"}
BRANDS = ("google", "amazon", "netflix", "paypal", "microsoft", "apple",
          "sbi", "hdfc", "icici", "paytm", "flipkart", "instagram",
          "facebook", "whatsapp", "steam")
SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd", "cutt.ly",
              "rb.gy", "shorturl.at")


def _get_json(url: str, headers: dict | None = None, timeout: int = 10):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def _heuristics(host: str, path: str) -> tuple[str, list[str]]:
    reasons: list[str] = []
    verdict = "safe"
    bare = host.removeprefix("www.")
    try:
        ipaddress.ip_address(bare)
        reasons.append("host is a raw IP address")
        verdict = "suspicious"
    except ValueError:
        pass
    if "xn--" in bare.encode("idna").decode().lower():
        reasons.append("punycode lookalike domain")
        verdict = "malicious"
    tld = bare.rsplit(".", 1)[-1].lower()
    if tld in SUSPICIOUS_TLDS:
        reasons.append(f"suspicious TLD .{tld}")
        verdict = max(verdict, "suspicious", key=("safe", "suspicious",
                                                  "malicious").index)
    for b in BRANDS:
        if b in bare and not bare.endswith(
                tuple(f"{b}.{t}" for t in ("com", "in", "co.in", "org"))):
            reasons.append(f"possible '{b}' impersonation in domain")
            verdict = "malicious" if b in bare.split(".")[0] else "suspicious"
            break
    if len(bare.split(".")) > 4:
        reasons.append("excessive subdomain nesting")
        verdict = max(verdict, "suspicious",
                      key=("safe", "suspicious", "malicious").index)
    if bare in SHORTENERS:
        reasons.append("link shortener hides the real destination")
        verdict = max(verdict, "suspicious",
                      key=("safe", "suspicious", "malicious").index)
    return verdict, reasons


def _safe_browsing(url: str) -> str | None:
    key = __import__("os").environ.get("GOOGLE_SAFEBROWSING_KEY", "")
    if not key:
        return None
    try:
        import urllib.request as _u

        body = json.dumps({
            "client": {"clientId": "evo-mk2", "clientVersion": "1.0"},
            "threatInfo": {
                "threatTypes": ["MALWARE", "SOCIAL_ENGINEERING",
                                "UNWANTED_SOFTWARE"],
                "platformTypes": ["ANY_PLATFORM"],
                "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": url}]},
        }).encode()
        req = _u.Request(
            "https://safebrowsing.googleapis.com/v4/threatMatches:find?key="
            + key, data=body,
            headers={"Content-Type": "application/json"})
        with _u.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        return "malicious" if data.get("matches") else "safe"
    except Exception:
        return None


@tool("url_check", "Vet a URL for phishing/malware indicators before opening it.",
      {"url": {"type": "string"}}, permission="read")
def url_check(url: str) -> dict:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urllib.parse.urlparse(url)
    verdict, reasons = _heuristics(p.netloc.lower(), p.path)
    sb = _safe_browsing(url)
    if sb == "malicious":
        verdict, reasons = "malicious", reasons + ["Google Safe Browsing match"]
    elif sb == "safe" and verdict == "suspicious":
        verdict = "suspicious"  # keep heuristic warning even if SB silent
    return {"ok": True, "speech": f"{verdict.upper()}: " + ("; ".join(reasons) or "no red flags"),
            "data": {"verdict": verdict, "reasons": reasons}}


def gate(url: str) -> tuple[bool, str]:
    """Returns (allow, note) used by open_app before opening URLs."""
    try:
        r = url_check(url)
        v = r["data"]["verdict"]
        if v == "malicious":
            return False, "blocked: " + "; ".join(r["data"]["reasons"])
        if v == "suspicious":
            return True, "(warning: " + "; ".join(r["data"]["reasons"]) + ")"
        return True, ""
    except Exception:
        return True, ""


@tool("breach_check", "Check whether an email address appears in known data breaches.",
      {"email": {"type": "string"}}, permission="read")
def breach_check(email: str) -> dict:
    email = (email or "").strip()
    if "@" not in email:
        return {"ok": False, "speech": "That's not an email address.", "data": {}}
    import os

    hibp = os.environ.get("HIBP_API_KEY", "")
    try:
        if hibp:
            req = urllib.request.Request(
                f"https://haveibeenpwned.com/api/v3/breachedaccount/"
                f"{urllib.parse.quote(email)}?truncateResponse=false",
                headers={"hibp-api-key": hibp, "User-Agent": "EVO-MK2"})
            try:
                with urllib.request.urlopen(req, timeout=12) as resp:
                    breaches = json.loads(resp.read().decode())
                names = sorted(b["Name"] for b in breaches)[:10]
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    breaches, names = [], []
                else:
                    raise
        else:
            data = _get_json("https://api.xposedornot.com/v1/check-email/"
                             + urllib.parse.quote(email))
            names = sorted(set(data.get("breaches", []) or []))[:10]
            breaches = [{"Name": n} for n in names]
    except Exception as exc:
        return {"ok": False,
                "speech": f"Breach service unreachable: {str(exc)[:120]}",
                "data": {}}
    db.audit("breach_check", email[:3] + "***" + email[email.find("@"):], True,
             f"{len(breaches)} breaches")  # never audit the full address
    if not breaches:
        return {"ok": True,
                "speech": "Good news - no known breaches for that address.",
                "data": {"count": 0}}
    return {"ok": True,
            "speech": (f"That address appears in {len(breaches)} breach(es): "
                       + ", ".join(names[:6])
                       + ". Change that password everywhere it was reused."),
            "data": {"count": len(breaches), "breaches": names}}


@tool("security_scan", "Snapshot of this PC's security posture (Defender, firewall).",
      {}, permission="read")
def security_scan() -> dict:
    import subprocess

    def ps(script: str) -> str:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or "").strip()

    findings, oks = [], []
    try:
        mp = ps("(Get-MpComputerStatus | Select-Object "
                "RealTimeProtectionEnabled,AntivirusSignatureLastUpdated "
                "| ConvertTo-Json)")
        st = json.loads(mp or "{}")
        if st.get("RealTimeProtectionEnabled") is False:
            findings.append("Defender real-time protection is OFF")
        else:
            oks.append("Defender real-time protection on")
    except Exception:
        oks.append("Defender status unknown")
    try:
        fw = ps("(Get-NetFirewallProfile | Where-Object Enabled -eq $false "
                "| Measure-Object).Count")
        if int(fw or 0) > 0:
            findings.append(f"{fw} firewall profile(s) disabled")
        else:
            oks.append("All firewall profiles enabled")
    except Exception:
        oks.append("Firewall status unknown")
    speech = ("Issues: " + "; ".join(findings)) if findings else \
        "All clear: " + "; ".join(oks)
    return {"ok": True, "speech": speech[:500],
            "data": {"findings": findings, "ok": oks}}
