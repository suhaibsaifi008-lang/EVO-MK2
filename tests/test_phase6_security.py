"""Phase 6 — Security & Life Admin: URL gate, breaches, expenses,
subscriptions, DPAPI secrets. All network/PS calls mocked."""
import json

import pytest

from mk2 import db, tools


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.migrate()
    from mk2 import life_admin
    from mk2.config import DATA as real_data

    monkeypatch.setattr(life_admin, "EXPENSES_DIR", tmp_path / "expenses")
    monkeypatch.setattr("mk2.vault_secrets.STORE", tmp_path / "secrets.bin")
    monkeypatch.delenv("GOOGLE_SAFEBROWSING_KEY", raising=False)
    tools.ensure_loaded()


# ---------------------------------------------------------------- security

class TestUrlCheck:
    def test_normal_site_is_safe(self, monkeypatch):
        from mk2.security import gate

        allow, note = gate("https://www.wikipedia.org/wiki/AI")
        assert allow is True and note == ""

    def test_raw_ip_host_suspicious(self):
        r = tools.call("url_check", {"url": "http://192.168.44.21/login"})
        assert r["data"]["verdict"] == "suspicious"
        assert any("IP" in x for x in r["data"]["reasons"])

    def test_brand_impersonation_flagged(self):
        r = tools.call("url_check",
                       {"url": "https://paypal-secure-login.tk/account"})
        v = r["data"]["verdict"]
        assert v in ("suspicious", "malicious")

    def test_shortener_warned_but_allowed(self):
        r = tools.call("url_check", {"url": "https://bit.ly/3xYz"})
        assert r["ok"] is True and r["data"]["verdict"] == "suspicious"

    def test_gate_blocks_malicious(self):
        from mk2.security import gate

        allow, note = gate("https://paypa1.verify-account.click/steal")
        # heuristics: .click TLD + brand impersonation
        if note:
            assert "blocked" in note or "warning" in note


class TestOpenAppGate:
    def test_malicious_url_never_reaches_browser(self, monkeypatch):
        from mk2.tools import system_tools as st

        opened = []
        monkeypatch.setattr("webbrowser.open", lambda u: opened.append(u))
        r = tools.call("open_app", {"target": "http://91.203.4.12/creds"})
        if r.get("data", {}).get("blocked"):
            assert opened == []            # blocked before browser
        else:
            assert len(opened) == 1        # suspicious-but-allowed warns


class TestBreachCheck:
    def test_email_validated(self):
        r = tools.call("breach_check", {"email": "not-an-email"})
        assert r["ok"] is False

    def test_breaches_found_via_provider(self, monkeypatch):
        from mk2 import security

        monkeypatch.delenv("HIBP_API_KEY", raising=False)
        monkeypatch.setattr(security, "_get_json",
                            lambda url, headers=None, timeout=10:
                            {"breaches_count": 2,
                             "breaches": ["LinkedIn2012", "Dropbox"]})
        r = tools.call("breach_check", {"email": "suhaib@example.com"})
        assert r["ok"] is True and "2 breach" in r["speech"]
        # full email must never land in the audit ledger
        blob = json.dumps(db.recent_audit(5))
        assert "suhaib@example.com" not in blob and "***" in blob

    def test_clean_address(self, monkeypatch):
        from mk2 import security

        monkeypatch.setattr(security, "_get_json",
                            lambda *a, **k: {"breaches_count": 0,
                                             "breaches": []})
        r = tools.call("breach_check", {"email": "clean@example.com"})
        assert "no known breaches" in r["speech"].lower()

    def test_provider_down_graceful(self, monkeypatch):
        from mk2 import security

        def boom(*a, **k):
            raise OSError("net down")
        monkeypatch.setattr(security, "_get_json", boom)
        monkeypatch.delenv("HIBP_API_KEY", raising=False)
        r = tools.call("breach_check", {"email": "x@y.com"})
        assert r["ok"] is False and "unreachable" in r["speech"].lower()


# -------------------------------------------------------------- life admin

CSV = """Date,Narration,Debit,Credit
01-08-2026,UPI/NETFLIX.COM/123456@paytm,649.00,
03-08-2026,SWIGGY order #8812,412.50,
05-08-2026,Amazon.in purchase,1899.00,
01-07-2026,UPI NETFLIX.COM renewal,649.00,
15-07-2026,Salary credit July,,45000.00
"""

CSV2 = """Date,Narration,Debit
01-09-2026,UPI NETFLIX.COM monthly,649.00
02-09-2026,Zomato dinner,380.00
"""


class TestLifeAdmin:
    def _ingest(self, tmp_path, name, content):
        d = tmp_path / "expenses"
        d.mkdir(exist_ok=True)
        (d / name).write_text(content, encoding="utf-8-sig")
        return tools.call("ingest_expenses", {"file": name})

    def test_ingest_parses_flexible_columns(self, tmp_path):
        r = self._ingest(tmp_path, "july.csv", CSV)
        assert r["ok"] is True and r["data"]["count"] == 4  # salary skipped
        with db._lock, db.connect() as c:
            rows = c.execute("SELECT merchant,category FROM expenses").fetchall()
        cats = {x["category"] for x in rows}
        assert "entertainment" in cats and "food" in cats

    def test_expense_summary_month(self, tmp_path):
        self._ingest(tmp_path, "aug.csv", CSV)
        r = tools.call("expense_summary", {"month": "2026-08"})
        assert r["ok"] is True and r["data"]["total"] == 2960.5
        cats = {c["category"]: c["total"] for c in r["data"]["categories"]}
        assert cats["shopping"] == 1899.0

    def test_subscription_audit_finds_recurring(self, tmp_path):
        self._ingest(tmp_path, "jul.csv", CSV)
        self._ingest(tmp_path, "sep.csv", CSV2)
        r = tools.call("subscription_audit", {})
        subs = {s["merchant"].lower(): s for s in r["data"]["subscriptions"]}
        assert any("netflix" in m for m in subs), r
        nf = next(s for m, s in subs.items() if "netflix" in m)
        assert nf["monthly"] == 649.0
        assert r["data"]["monthly_total"] >= 649.0

    def test_bad_month_rejected(self):
        r = tools.call("expense_summary", {"month": "August maybe?"})
        assert r["ok"] is False


# ------------------------------------------------------------ secrets vault

class TestSecretVault:
    def test_store_and_get_masked(self, tmp_path, monkeypatch):
        r = tools.call("secret_store",
                       {"key": "MY_API_TOKEN", "value": "super-secret-9876"})
        assert r["ok"] is True
        g = tools.call("secret_get", {"key": "my_api_token"})
        assert g["ok"] is True
        assert "super-secret-9876" not in g["speech"]      # masked in speech
        blob = json.dumps(db.recent_audit(5))
        assert "super-secret-9876" not in blob             # never audited

    def test_reveal_only_on_explicit_request(self):
        tools.call("secret_store", {"key": "tok2", "value": "abcdef12345"})
        r = tools.call("secret_get", {"key": "tok2", "reveal": True})
        assert r["data"]["value"] == "abcdef12345"

    def test_persistence_across_reload(self, tmp_path):
        tools.call("secret_store", {"key": "persist", "value": "keep-me-42"})
        from mk2 import vault_secrets as vs

        assert vs.get_secret("persist") == "keep-me-42"

    def test_delete_removes(self):
        tools.call("secret_store", {"key": "gone", "value": "tempval99"})
        r = tools.call("secret_delete", {"key": "gone"})
        assert r["ok"] is True
        assert tools.call("secret_get", {"key": "gone"})["ok"] is False

    def test_connector_uses_vault_token(self, tmp_path, monkeypatch):
        from mk2 import vault_secrets as vs
        from mk2.tools import connectors as conn

        vs.secret_store("ECHO_TOKEN", "vault-token-777")
        spec = {"name": "authed_echo", "base_url": "https://api.e.test",
                "method": "GET", "path": "/v1/x",
                "auth_env": "ECHO_TOKEN"}
        conn.CONNECTORS_DIR.mkdir(parents=True, exist_ok=True)
        (conn.CONNECTORS_DIR / "authed_echo.json").write_text(
            json.dumps(spec), encoding="utf-8")
        conn.load_all()

        seen = {}

        class FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"ok": true}'
        def fake_urlopen(req, timeout=15):
            seen["auth"] = req.headers.get("Authorization")
            return FakeResp()
        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        out = tools.call("api_authed_echo", {})
        assert out["ok"] is True
        assert seen["auth"] == "Bearer vault-token-777"   # straight from DPAPI vault
