"""Desktop-hands tools: dispatch, master switch, failsafe handling.
All input is mocked - the real mouse/keyboard is never touched in tests."""
import pytest

from mk2 import db


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "dt.db")
    db.migrate()


class FakePag:
    def __init__(self):
        self.FAILSAFE = False
        self.PAUSE = 0
        self.moves = []
        self.clicks = []
        self.scrolled = []
        self.typed = []
        self.pressed = []
        self.hotkeys = []

    def moveTo(self, x, y, duration=None):
        self.moves.append((x, y))

    def click(self, x=None, y=None, button="left", clicks=1):
        self.clicks.append((x, y, button, clicks))

    def scroll(self, amt):
        self.scrolled.append(("v", amt))

    def hscroll(self, amt):
        self.scrolled.append(("h", amt))

    def write(self, text, interval=0.0):
        self.typed.append(text)

    def press(self, key):
        self.pressed.append(key)

    def hotkey(self, *keys):
        self.hotkeys.append(keys)

    def screenshot(self, path=None):
        return path


class FailSafeExc(Exception):
    pass


@pytest.fixture()
def fake_pag(monkeypatch):
    from mk2.tools import desktop_tools as D

    fake = FakePag()
    fake.FailSafeException = FailSafeExc
    monkeypatch.setattr(D, "_pag", lambda: fake)
    yield D, fake


def test_master_switch_disarms(fake_pag, monkeypatch):
    D, _ = fake_pag
    monkeypatch.setenv("EVO_DESKTOP_CONTROL", "0")
    res = D.type_text(text="hi")
    assert res["ok"] is False and "disarmed" in res["speech"]


def test_type_text_paste_mode_hotkeys(fake_pag):
    D, fake = fake_pag
    D._last_focus.update(title="Notepad", ts=__import__("time").time())
    res = D.type_text(text="hello world")          # default = paste mode
    assert res["ok"] is True and res["data"]["mode"] == "paste"
    assert ("ctrl", "v") in fake.hotkeys


def test_type_text_keys_mode(fake_pag):
    D, fake = fake_pag
    D._last_focus.update(title="Notepad", ts=__import__("time").time())
    res = D.type_text(text="hello world", mode="keys")
    assert res["ok"] is True and fake.typed == ["hello world"]


def test_type_refuses_without_recent_focus(fake_pag):
    D, fake = fake_pag
    D._last_focus.update(title="", ts=0)
    res = D.type_text(text="hello")
    assert res["ok"] is False and res["data"].get("need_focus") is True
    assert fake.typed == []                      # nothing sprayed blindly
    # force override works
    assert D.type_text(text="x", force=True)["ok"] is True


def test_click_with_and_without_coords(fake_pag):
    D, fake = fake_pag
    r1 = D.mouse_click(x=100, y=200)
    r2 = D.mouse_click()
    assert r1["ok"] and r2["ok"]
    assert fake.moves == [(100, 200)]              # explicit move first
    assert fake.clicks == [(None, None, "left", 1),
                           (None, None, "left", 1)]


def test_scroll_directions(fake_pag):
    D, fake = fake_pag
    assert D.mouse_scroll("up")["ok"]
    assert D.mouse_scroll("left")["ok"]
    assert ("v", 600) in fake.scrolled and ("h", -600) in fake.scrolled
    assert D.mouse_scroll("sideways")["ok"] is False


def test_press_key_chord_and_single(fake_pag):
    D, fake = fake_pag
    D._last_focus.update(title="Notepad", ts=__import__("time").time())
    D.press_key(keys="ctrl+s")
    D.press_key(keys="enter")
    assert fake.hotkeys == [("ctrl", "s")]
    assert "enter" in fake.pressed


def test_failsafe_becomes_clean_refusal(fake_pag, monkeypatch):
    D, fake = fake_pag

    def boom(*a, **k):
        raise FailSafeExc("corner slammed")

    monkeypatch.setattr(fake, "click", boom)
    res = D.mouse_click(x=5, y=5)
    assert res["ok"] is False and res["data"].get("failsafe") is True
