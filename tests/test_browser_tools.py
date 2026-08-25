"""Browser-hands tools: allowlist gating + action dispatch (mocked page)."""
import pytest

from mk2 import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "br.db")
    db.migrate()


class FakeLocator:
    def __init__(self, ok=True):
        self.ok = ok
        self.clicked = False
        self.filled = None

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        if not self.ok:
            raise TimeoutError("no such element")
        self.clicked = True

    def fill(self, text, timeout=None):
        if not self.ok:
            raise TimeoutError("no such field")
        self.filled = text

    def press(self, key):
        pass


class FakePage:
    def __init__(self):
        self.url = "https://www.canva.com/design"
        self._title = "Canva"
        self.buttons = {}
        self.fields = {}

    def title(self):
        return self._title

    def inner_text(self, sel):
        return "Welcome to Canva\nCreate a design"

    def screenshot(self, path=None):
        return path

    def goto(self, url, **kw):
        self.url = url
        return True

    def wait_for_load_state(self, state, timeout=None):
        pass

    def set_default_timeout(self, t):
        pass

    def get_by_role(self, role, name=None):
        return self.buttons.setdefault((role, name), FakeLocator(ok=False))

    def get_by_label(self, name):
        return self.fields.setdefault(("label", name), FakeLocator())

    def get_by_placeholder(self, name):
        return self.fields.setdefault(("ph", name), FakeLocator())

    def get_by_text(self, name, exact=False):
        return self.buttons.setdefault(("text", name), FakeLocator(ok=False))

    def locator(self, sel):
        return FakeLocator(ok=False)

    def keyboard(self):
        raise AssertionError


@pytest.fixture()
def fake_browser(monkeypatch):
    from mk2.tools import browser_tools as B

    page = FakePage()
    monkeypatch.setattr(B, "_ensure", lambda: page)
    yield B, page


def test_allowlist_blocks_foreign_domain(fake_browser):
    B, _ = fake_browser
    res = B.browser_open("https://evil-bank.example.com/login")
    assert res["ok"] is False and "allow-list" in res["speech"]


def test_open_allowed_domain(fake_browser):
    B, page = fake_browser
    res = B.browser_open("https://www.canva.com/some-design")
    assert res["ok"] is True
    assert page.url.endswith("/some-design")


def test_localhost_and_file_always_allowed(fake_browser):
    B, _page = fake_browser
    assert B._nav_allowed("http://127.0.0.1:8421/") is True
    assert B._nav_allowed("file:///C:/tmp/page.html") is True
    assert B._nav_allowed("ftp://x") is False


def test_click_dispatch_via_role(fake_browser):
    B, page = fake_browser
    page.buttons[("button", "Create a design")] = FakeLocator()
    res = B.browser_act("click", target="Create a design")
    assert res["ok"] is True
    assert page.buttons[("button", "Create a design")].clicked


def test_click_missing_target_reports_cleanly(fake_browser):
    B, page = fake_browser
    res = B.browser_act("click", target="nonexistent widget")
    assert res["ok"] is False and "browser_read" in res["speech"]


def test_type_and_submit(fake_browser):
    B, page = fake_browser
    loc = FakeLocator()
    page.fields[("label", "Search")] = loc
    res = B.browser_act("type", target="Search", value="minecraft",
                        submit=True)
    assert res["ok"] is True
    assert loc.filled == "minecraft"


def test_read_returns_text(fake_browser):
    B, _ = fake_browser
    res = B.browser_read()
    assert res["ok"] is True and "Canva" in res["data"]["title"]
