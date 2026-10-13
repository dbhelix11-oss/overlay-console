"""Persist window geometry, visibility and pane ratios to state.json."""
import json
import os
import re

_GEOM = re.compile(r"^(\d+)x(\d+)([+-]-?\d+)([+-]-?\d+)$")
SAVE_DELAY_MS = 500


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

    def entry(self, key):
        return self.data["windows"].setdefault(key, {})

    def track(self, key, win, default_geometry, default_visible=True):
        """Restore a Toplevel/Tk window's geometry and follow its changes."""
        e = self.entry(key)
        geom = clamp_geometry(e.get("geometry"), win.winfo_screenwidth(),
                              win.winfo_screenheight())
        win.geometry(geom or default_geometry)
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
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.data, indent=2, sort_keys=True))
            os.replace(tmp, self.path)
        except OSError:
            pass
