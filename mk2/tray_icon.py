"""EVO MK2 System Tray Presence.

Provides a permanent 'JARVIS is in the walls' desktop tray icon with status,
dashboard quick-launch, mute toggle, and clean exit controls.
"""
import logging
import threading
import webbrowser
from typing import Callable, Optional

from PIL import Image, ImageDraw

log = logging.getLogger("mk2.tray")

_tray_instance = None
_is_muted = False


def _create_icon_image(size: int = 64, active: bool = True) -> Image.Image:
    """Generate a high-tech glowing cyan arc-reactor tray icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    center = size // 2
    outer_r = size // 2 - 4
    mid_r = outer_r - 8
    inner_r = 8

    # Outer ring
    ring_color = (0, 220, 255, 240) if active else (120, 120, 120, 180)
    core_color = (180, 245, 255, 255) if active else (160, 160, 160, 200)

    draw.ellipse((center - outer_r, center - outer_r, center + outer_r, center + outer_r), outline=ring_color, width=3)
    draw.ellipse((center - mid_r, center - mid_r, center + mid_r, center + mid_r), outline=ring_color, width=2)
    # Inner glowing core
    draw.ellipse((center - inner_r, center - inner_r, center + inner_r, center + inner_r), fill=core_color)

    # 4 radial reactor spokes
    draw.line([(center, center - outer_r + 2), (center, center - mid_r - 2)], fill=ring_color, width=2)
    draw.line([(center, center + outer_r - 2), (center, center + mid_r + 2)], fill=ring_color, width=2)
    draw.line([(center - outer_r + 2, center), (center - mid_r - 2, center)], fill=ring_color, width=2)
    draw.line([(center + outer_r - 2, center), (center + mid_r + 2, center)], fill=ring_color, width=2)

    return img


def run_tray_icon(on_exit: Optional[Callable[[], None]] = None) -> None:
    """Start the system tray icon in the calling thread or loop."""
    global _tray_instance
    try:
        import pystray
        from pystray import MenuItem as item
    except ImportError:
        log.warning("pystray not installed; system tray presence disabled.")
        return

    def _open_dashboard():
        webbrowser.open("http://127.0.0.1:8421")

    def _toggle_mute(icon, item):
        global _is_muted
        _is_muted = not _is_muted
        from .bus import bus
        bus.publish("voice.mute.toggle", {"muted": _is_muted})
        icon.icon = _create_icon_image(64, active=not _is_muted)
        log.info("Voice mute toggled: %s", _is_muted)

    def _do_exit(icon, item):
        log.info("Exit requested from system tray")
        icon.stop()
        if on_exit:
            on_exit()

    menu = pystray.Menu(
        item("EVO MK2: Online", lambda: None, enabled=False),
        pystray.Menu.SEPARATOR,
        item("Open Dashboard", _open_dashboard, default=True),
        item("Mute Voice", _toggle_mute, checked=lambda item: _is_muted),
        pystray.Menu.SEPARATOR,
        item("Exit EVO", _do_exit),
    )

    icon_img = _create_icon_image(64, active=True)
    _tray_instance = pystray.Icon("EVO_MK2", icon_img, "EVO MK2 — JARVIS System", menu)
    log.info("EVO MK2 system tray icon ready")
    _tray_instance.run()


def launch_tray_in_background(on_exit: Optional[Callable[[], None]] = None) -> threading.Thread:
    """Launch system tray presence on a separate daemon thread."""
    t = threading.Thread(target=run_tray_icon, args=(on_exit,), daemon=True, name="evo-tray")
    t.start()
    return t
