"""Phase 6: Life admin — expenses and subscriptions from your bank CSVs.

Drop bank exports (CSV) into data/expenses/ then call:
  ingest_expenses(file)   -> normalizes rows into the ledger
  expense_summary(month)  -> "2026-08" totals per category
  subscription_audit      -> recurring merchants across >= 2 months

Column detection is flexible (date/description/narration/details, amount,
debit, withdrawal, credit). UPI/NEFT/IMPS noise is stripped to get a
clean merchant name.
"""
import csv
import json
import re
from datetime import datetime
from pathlib import Path

from . import db
from .config import DATA
from .tools import tool

EXPENSES_DIR = DATA / "expenses"

CATEGORY_RULES = [
    ("food", ("swiggy", "zomato", "restaurant", "cafe", "domino", "mcdonald",
              "kfc", "starbucks", "blinkit", "zepto", "bigbasket", "grofer")),
    ("shopping", ("amazon", "flipkart", "myntra", "ajio", "meesho", "nykaa")),
    ("transport", ("uber", "ola", "rapido", "irctc", "metro", "petrol",
                   "indian oil", "hp petro", "bpcl", "fastag")),
    ("entertainment", ("netflix", "spotify", "prime video", "hotstar",
                       "youtube premium", "sony liv", "jiocinema")),
    ("utilities", ("electricity", "bescom", "water bill", "airtel", "jio fiber",
                   "act fibernet", "broadband", "gas", "mobile recharge")),
    ("health", ("pharmacy", "apollo", "hospital", "clinic", "1mg", "practo")),
    ("finance", ("insurance", "premium", "sip ", "mutual fund", "zerodha",
                 "groww", "emi")),
]


def _norm_merchant(desc: str) -> str:
    d = desc.lower()
    noise = (r"upi[/\-:]*\d*", r"\b(neft|imps|rtgs|ach|pos|emi)\b[\w/\-]*",
             r"\b\d{10,14}\b", r"ref\s*no\.?\s*\w+", r"@+(paytm|ybl|okaxis|"
             r"upi|ibl)\b")
    for pat in noise:
        d = re.sub(pat, " ", d)
    # first real word wins ('amazon.in purchase' -> amazon)
    for tok in re.findall(r"[a-z0-9.&']+", d):
        base = tok.split(".")[0]
        if re.fullmatch(r"[a-z][a-z&']{3,}", base):
            return base[:60]
    words = re.findall(r"[a-z][a-z0-9&' ]{2,}", d)
    name = max(words, key=len).strip() if words else desc.strip()[:30]
    return name.strip(" -")[:60] or "unknown"


def _category(merchant: str) -> str:
    m = merchant.lower()
    for cat, keys in CATEGORY_RULES:
        if any(k in m for k in keys):
            return cat
    return "other"


def _parse_amount(raw: str) -> float | None:
    s = re.sub(r"[^\d.\-]", "", str(raw or ""))
    try:
        return abs(float(s)) if s not in ("", "-", ".") else None
    except ValueError:
        return None


def _parse_date(raw: str) -> str | None:
    raw = str(raw or "").strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d %b %Y",
                "%d/%b/%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw.split()[0], fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    return None


def _rows_from_csv(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") \
            if sample.count(",") > 1 or ";" in sample or "\t" in sample \
            else csv.excel
        reader = csv.DictReader(f)
        cols = {c.lower().strip(): c for c in (reader.fieldnames or [])}

        def pick(*names):
            for n in names:
                if n in cols:
                    return cols[n]
            return None

        c_date = pick("date", "transaction date", "txn date", "value date")
        c_desc = pick("description", "narration", "details", "remarks",
                      "particulars", "merchant")
        c_amt = pick("amount", "debit", "withdrawal", "withdrawal amt",
                     "debit amount")
        c_credit = pick("credit", "deposit", "credit amount")
        if not (c_date and c_desc):
            return out
        for row in reader:
            date = _parse_date(row.get(c_date))
            desc = str(row.get(c_desc) or "").strip()
            if not date or not desc:
                continue
            amt = _parse_amount(row.get(c_amt) or "")
            if amt is None:
                continue  # includes credit/income rows when no debit column
            out.append({"merchant": _norm_merchant(desc), "amount": round(amt, 2),
                        "spent_on": date, "raw": desc[:120],
                        "source": path.name})
    return out


@tool("ingest_expenses", "Import a bank CSV export from data/expenses into your expense ledger.",
      {"file": {"type": "string"}}, permission="read")
def ingest_expenses(file: str) -> dict:
    p = EXPENSES_DIR / Path(str(file)).name
    if not p.exists():
        p = EXPENSES_DIR / f"{file}.csv"
    if not p.exists():
        return {"ok": False,
                "speech": (f"No CSV named '{file}' in data/expenses. Drop "
                           "your bank export there first."),
                "data": {}}
    rows = _rows_from_csv(p)
    if not rows:
        return {"ok": False,
                "speech": ("Couldn't read any transactions - unrecognized "
                           "columns. Expected date + description + amount."),
                "data": {}}
    with db._lock, db.connect() as c:
        c.execute("DELETE FROM expenses WHERE source=?", (p.name,))
        for r in rows:
            c.execute(
                "INSERT INTO expenses(merchant,category,amount,spent_on,"
                "source,ts) VALUES(?,?,?,?,?,?)",
                (r["merchant"], _category(r["merchant"]), r["amount"],
                 r["spent_on"], r["source"], datetime.now().timestamp()))
    return {"ok": True,
            "speech": f"Imported {len(rows)} transactions from {p.name}.",
            "data": {"count": len(rows)}}


@tool("expense_summary", "Spending totals per category for a month ('2026-08').",
      {"month": {"type": "string"}}, permission="read")
def expense_summary(month: str) -> dict:
    month = (month or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        return {"ok": False,
                "speech": "Give me a month like 2026-08.", "data": {}}
    with db._lock, db.connect() as c:
        rows = c.execute(
            "SELECT category,SUM(amount) total,COUNT(*) n FROM expenses "
            "WHERE spent_on LIKE ? GROUP BY category ORDER BY total DESC",
            (month + "-%",)).fetchall()
    if not rows:
        return {"ok": True, "speech": f"No expenses recorded for {month}. "
                                      "Ingest a CSV first.",
                "data": {"categories": []}}
    total = sum(r["total"] for r in rows)
    lines = [f"{r['category']}: ₹{r['total']:.0f} ({r['n']})" for r in rows]
    return {"ok": True,
            "speech": f"{month} spending ₹{total:.0f} — " + "; ".join(lines),
            "data": {"month": month, "total": round(total, 2),
                     "categories": [dict(r) for r in rows]}}


@tool("subscription_audit", "Find recurring charges across months (subscriptions, EMIs).",
      {}, permission="read")
def subscription_audit() -> dict:
    with db._lock, db.connect() as c:
        rows = c.execute(
            "SELECT merchant,category,amount,substr(spent_on,1,7) ym "
            "FROM expenses").fetchall()
    by_merchant: dict[str, dict] = {}
    for r in rows:
        key = re.sub(r"\d+", "#", r["merchant"].lower())[:50]
        e = by_merchant.setdefault(key, {"months": set(), "amounts": [],
                                         "sample": r["merchant"],
                                         "category": r["category"]})
        e["months"].add(r["ym"])
        e["amounts"].append(r["amount"])
    subs = []
    for key, e in by_merchant.items():
        if len(e["months"]) < 2:
            continue
        avg = sum(e["amounts"]) / len(e["amounts"])
        spread = (max(e["amounts"]) - min(e["amounts"])) / max(avg, 1)
        if spread <= 0.15:  # similar charge each month => recurring
            subs.append({"merchant": e["sample"], "category": e["category"],
                         "monthly": round(sum(e["amounts"])
                                          / len(e["months"]), 2),
                         "months": sorted(e["months"])})
    subs.sort(key=lambda s: -s["monthly"])
    if not subs:
        return {"ok": True,
                "speech": ("No recurring charges detected yet - I need at "
                           "least two months of data. Ingest more CSVs."),
                "data": {"subscriptions": []}}
    lines = [f"{s['merchant']}: ~₹{s['monthly']:.0f}/mo ({s['category']})"
             for s in subs]
    monthly_total = sum(s["monthly"] for s in subs)
    return {"ok": True,
            "speech": (f"{len(subs)} recurring charge(s), ~₹{monthly_total:.0f}"
                       f"/mo total: " + "; ".join(lines))[:600],
            "data": {"subscriptions": subs,
                     "monthly_total": round(monthly_total, 2)}}
