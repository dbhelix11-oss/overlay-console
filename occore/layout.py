"""Persist window geometry, visibility and pane ratios to state.json."""
import json
import os
import re
import shutil
import subprocess

_GEOM = re.compile(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$")
SAVE_DELAY_MS = 500
DESKTOP_POLL_MS = 2000
STICKY = 0xFFFFFFFF  # _NET_WM_DESKTOP value meaning "on all desktops"
_DESKTOP = re.compile(r"=\s*(\d+)")


_PARENT = re.compile(r"Parent window id:\s*(0x[0-9a-fA-F]+)")


def _client_id(win):
    """X id of the window the window manager manages. Tk wraps each toplevel in a
    wrapper window, which is the parent of the id winfo_id() reports."""
    own = hex(win.winfo_id())
    if not shutil.which("xwininfo"):
        return own
    try:
        out = subprocess.run(["xwininfo", "-id", own, "-tree"], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return own
    m = _PARENT.search(out)
    return m[1] if m else own


def _shown(win):
    """Shown (even if on another desktop, where X reports it unmapped) rather than withdrawn."""
    return win.state() != "withdrawn"


def get_desktop(win):
    """Virtual desktop a mapped window is on (X11 only), or None if unknown."""
    if not shutil.which("xprop"):
        return None
    try:
        out = subprocess.run(["xprop", "-id", _client_id(win), "_NET_WM_DESKTOP"],
                             capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = _DESKTOP.search(out)
    return int(m[1]) if m else None


def skip_taskbar(win):
    """Keep a secondary window off the taskbar/pager (X11, needs wmctrl)."""
    if not shutil.which("wmctrl"):
        return
    try:
        subprocess.run(["wmctrl", "-i", "-r", _client_id(win), "-b", "add,skip_taskbar,skip_pager"],
                       capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


def on_current_desktop(win):
    """True if the window lives on the desktop being shown (or on all of them, or unknown).
    A window on another desktop is unmapped by the window manager just like a minimized
    one, so callers use this to tell the two apart."""
    d = get_desktop(win)
    if d is None or d == STICKY or not shutil.which("xprop"):
        return True
    try:
        out = subprocess.run(["xprop", "-root", "_NET_CURRENT_DESKTOP"], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return True
    m = _DESKTOP.search(out)
    return m is None or int(m[1]) == d


def set_desktop(win, desktop):
    """Ask the window manager to move a mapped window to a virtual desktop (X11, needs wmctrl)."""
    if not shutil.which("wmctrl"):
        return
    wid = _client_id(win)
    args = (["-b", "add,sticky"] if desktop == STICKY else ["-t", str(desktop)])
    try:
        subprocess.run(["wmctrl", "-i", "-r", wid, *args], capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


def clamp_geometry(geom, screen_w, screen_h):
    """Keep a saved WxH+X+Y geometry on the current screen."""
    m = _GEOM.match(geom or "")
    if not m:
        return None
    w, h = int(m[1]), int(m[2])
    x, y = int(m[3]), int(m[4])
    w, h = min(w, screen_w), min(h, screen_h)
    x = max(0, min(x, screen_w - w))
    y = max(0, min(y, screen_h - h))
    return f"{w}x{h}+{x}+{y}"


class Layout:
    def __init__(self, path, tk_root):
        self.path = path
        self.root = tk_root
        self._after = None
        try:
            self.data = json.loads(path.read_text())
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("windows", {})
        self._wins = {}
        self.root.after(DESKTOP_POLL_MS, self._poll_desktops)

    def entry(self, key):
        return self.data["windows"].setdefault(key, {})

    def track(self, key, win, default_geometry, default_visible=True):
        """Restore a Toplevel/Tk window's geometry and follow its changes."""
        e = self.entry(key)
        geom = clamp_geometry(e.get("geometry"), win.winfo_screenwidth(),
                              win.winfo_screenheight())
        win.geometry(geom or default_geometry)
        self._wins[key] = win
        win.bind("<Configure>", lambda ev: self._on_configure(key, win, ev), add="+")
        return e.get("visible", default_visible)

    def _on_configure(self, key, win, event):
        if event.widget is not win:
            return
        self.entry(key)["geometry"] = win.geometry()
        self.schedule_save()

    def set_visible(self, key, visible):
        self.entry(key)["visible"] = visible
        self.schedule_save()
        if visible and key in self._wins:
            win = self._wins[key]
            win.after(200, lambda: self._restore_one(key, win))

    # -- virtual desktop memory (X11 only; silently does nothing elsewhere)
    def _restore_one(self, key, win):
        if not _shown(win):
            return
        if key != "control":  # the control window is the one taskbar entry
            skip_taskbar(win)
        d = self.entry(key).get("desktop")
        if isinstance(d, int):
            set_desktop(win, d)

    def restore_desktops(self):
        """Move every shown window to the desktop it was last on. Call once windows are mapped."""
        for key, win in self._wins.items():
            self._restore_one(key, win)

    def _record_desktops(self):
        changed = False
        for key, win in self._wins.items():
            if not _shown(win):
                continue
            d = get_desktop(win)
            if d is not None and self.entry(key).get("desktop") != d:
                self.entry(key)["desktop"] = d
                changed = True
        return changed

    def _poll_desktops(self):
        if self._record_desktops():
            self.schedule_save()
        self.root.after(DESKTOP_POLL_MS, self._poll_desktops)

    def track_paned(self, key, paned, default_ratios):
        """Store sash positions as fractions of the pane size so they survive resizes."""
        horizontal = str(paned.cget("orient")) == "horizontal"
        state = {"restoring": True}

        def size():
            return max(1, paned.winfo_width() if horizontal else paned.winfo_height())

        def apply(_ev=None):
            ratios = self.entry(key).get("sashes", default_ratios)
            for i, r in enumerate(ratios[:len(paned.panes()) - 1]):
                pos = int(r * size())
                if horizontal:
                    paned.sash_place(i, pos, 0)
                else:
                    paned.sash_place(i, 0, pos)

        def resized(_ev):
            # keep ratios on window resize; only user drags (below) change them
            state["restoring"] = True
            paned.after_idle(apply)
            paned.after(100, lambda: state.update(restoring=False))

        def released(_ev):
            n = len(paned.panes()) - 1
            ratios = []
            for i in range(n):
                c = paned.sash_coord(i)
                ratios.append(round((c[0] if horizontal else c[1]) / size(), 4))
            self.entry(key)["sashes"] = ratios
            self.schedule_save()

        paned.bind("<Configure>", resized, add="+")
        paned.bind("<ButtonRelease-1>", released, add="+")
        paned.after(50, apply)

    def schedule_save(self):
        if self._after is not None:
            self.root.after_cancel(self._after)
        self._after = self.root.after(SAVE_DELAY_MS, self.save)

    def save(self):
        self._after = None
        self._record_desktops()
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
            os.replace(tmp, self.path)
        except OSError:
            pass
