"""System tray icon (optional). Needs pystray + Pillow (apt: python3-pystray); without
them `available` is False and the app behaves as before."""
import os
import queue
import threading

# On X11 the plain (XEmbed) tray backend shows in every panel, including LXQt's; the
# AppIndicator backend pystray prefers needs a separate StatusNotifier host.
if os.environ.get("XDG_SESSION_TYPE") == "x11":
    os.environ.setdefault("PYSTRAY_BACKEND", "xorg")

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:  # ImportError, or a backend that cannot start
    pystray = None


def _icon_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 61, 61), radius=10, fill="#101418", outline="#7CFC9A", width=3)
    d.line((14, 22, 28, 32, 14, 42), fill="#7CFC9A", width=5)   # ">"
    d.line((32, 44, 50, 44), fill="#e0b040", width=5)           # "_"
    return img


class Tray:
    """Events ("toggle", "quit") arrive on .events; the Tk side polls it, because pystray
    calls back on its own thread and Tk must only be touched from the main thread."""

    def __init__(self):
        self.events = queue.SimpleQueue()
        self.icon = None
        self.available = False
        if pystray is None:
            return
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Show / hide", lambda *_: self.events.put("toggle"), default=True),
                pystray.MenuItem("Quit", lambda *_: self.events.put("quit")))
            self.icon = pystray.Icon("overlayconsole", _icon_image(), "OverlayConsole", menu)
            threading.Thread(target=self.icon.run, daemon=True).start()  # daemon: never blocks exit
            self.available = True
        except Exception:
            self.icon = None

    def stop(self):
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:
                pass
