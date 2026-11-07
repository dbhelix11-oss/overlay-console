"""DockWindow: the first four pipes snapped into one resizable 2x2 grid.

Outer vertical PanedWindow = rows (one draggable horizontal divider); each row is a
horizontal PanedWindow. The two column dividers are kept aligned, so the grid drags
like a single 2x2 table and the ratios are saved as fractions in state.json."""
import tkinter as tk

from occore.windows import BG, PipeWindow

KEY = "dock"
DOCK_SIZE = 4


class DockWindow:
    def __init__(self, app, names):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title("OverlayConsole — " + " / ".join(names))
        self.top.configure(bg=BG)
        self.top.protocol("WM_DELETE_WINDOW", self.hide)
        self.top.bind("<FocusOut>", self._focus_out)

        self.rows = tk.PanedWindow(self.top, orient="vertical", bg="#2a3440",
                                   sashwidth=5, borderwidth=0)
        self.rows.pack(fill="both", expand=True)
        self.cols = []  # horizontal PanedWindows, one per row
        self.panes = {}
        for chunk in (names[0:2], names[2:4]):
            if not chunk:
                continue
            row = tk.PanedWindow(self.rows, orient="horizontal", bg="#2a3440",
                                 sashwidth=5, borderwidth=0)
            for n in chunk:
                w = PipeWindow(app, n, parent=row)
                row.add(w.top, minsize=80, stretch="always")
                self.panes[n] = w
            self.rows.add(row, minsize=60, stretch="always")
            self.cols.append(row)

        layout = app.layout
        layout.track_paned(f"{KEY}:rows", self.rows, [0.5])
        if self.cols:
            layout.track_paned(f"{KEY}:cols", self.cols[0], [0.5])
        self._centre = False
        self.rows.bind("<Button-1>", self._centre_press, add="+")
        self.rows.bind("<B1-Motion>", self._centre_motion, add="+")
        self.rows.bind("<ButtonRelease-1>", self._centre_release, add="+")
        self.rows.bind("<Motion>", self._centre_hover, add="+")
        for row in self.cols:
            row.bind("<B1-Motion>", lambda ev, r=row: self._mirror(r), add="+")
            row.bind("<ButtonRelease-1>", lambda ev, r=row: self._mirror(r), add="+")
            row.bind("<Configure>", lambda ev, r=row: self.top.after(150, self._mirror, self.cols[0]),
                     add="+")

    # --- centre-point drag: the crossing of the row divider and the column divider ---
    GRAB = 10  # pixels either side of the column divider that count as "the centre"

    def _at_centre(self, ev):
        if len(self.cols) < 2 or len(self.cols[0].panes()) < 2:
            return False
        return abs(ev.x - self.cols[0].sash_coord(0)[0] - 2) <= self.GRAB and \
            len(self.rows.identify(ev.x, ev.y)) > 0  # y must be on the row divider

    def _centre_press(self, ev):
        self._centre = self._at_centre(ev)

    def _centre_motion(self, ev):
        if getattr(self, "_centre", False):
            lo = 80
            x = max(lo, min(ev.x, self.cols[0].winfo_width() - lo))
            self.cols[0].sash_place(0, x, 0)
            self._mirror(self.cols[0])  # vertical movement is done by the row divider itself

    def _centre_release(self, ev):
        if getattr(self, "_centre", False):
            self._centre = False
            x = self.cols[0].sash_coord(0)[0]
            self.app.layout.entry(f"{KEY}:cols")["sashes"] = [
                round(x / max(1, self.cols[0].winfo_width()), 4)]
            self.app.layout.schedule_save()

    def _centre_hover(self, ev):
        self.rows.configure(cursor="fleur" if self._at_centre(ev) else "")

    def _mirror(self, src):
        """Copy src's column divider to the other rows so the grid stays aligned."""
        if len(src.panes()) < 2:
            return
        x = src.sash_coord(0)[0]
        for row in self.cols:
            if row is not src and len(row.panes()) >= 2 and row.sash_coord(0)[0] != x:
                row.sash_place(0, x, 0)
        if src is not self.cols[0]:
            # keep the stored ratio (written from cols[0]) in step with a drag in another row
            self.app.layout.entry(f"{KEY}:cols")["sashes"] = [
                round(x / max(1, src.winfo_width()), 4)]
            self.app.layout.schedule_save()

    def _focus_out(self, ev):
        if ev.widget is self.top and self.top.focus_displayof() is None:
            for w in self.panes.values():
                w.mark_seen()

    def show(self):
        self.top.deiconify()
        self.app.layout.set_visible(KEY, True)
        self.app.refresh_buttons()

    def hide(self):
        self.top.withdraw()
        self.app.layout.set_visible(KEY, False)
        self.app.refresh_buttons()

    def visible(self):
        return self.top.state() != "withdrawn"
