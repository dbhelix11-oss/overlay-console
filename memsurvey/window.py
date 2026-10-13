"""Memory survey window: one tab per survey section, refreshed off the UI thread."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from occore.windows import BG, NEW_FG, SEEN_FG
from . import survey
from .elevate import ElevateError, HelperClient

TEXT_FG = "#c8d0d8"
BTN = dict(relief="flat", bg="#1c232b", fg="#ccc", activebackground="#2a3440")


class MemoryWindow:
    KEY = "memory"

    def __init__(self, app):
        self.app = app
        self.top = tk.Toplevel(app.root)
        self.top.title("OverlayConsole — memory survey")
        self.top.configure(bg=BG)
        self.texts = {}
        self.busy = set()
        self.results = queue.Queue()
        self.elev_results = queue.Queue()
        self.helper = None
        self.elevating = False
        self.launcher = None  # test hook: override pkexec argv

        bar = tk.Frame(self.top, bg=BG)
        bar.pack(fill="x")
        tk.Button(bar, text="refresh", command=self.refresh_current, **BTN).pack(side="left", padx=4, pady=3)
        self.elev_btn = tk.Button(bar, text="Elevate (root)…", command=self.toggle_elevate, **BTN)
        self.elev_btn.pack(side="left", padx=4, pady=3)
        self.mode = tk.Label(bar, text="user (uid %d)" % __import__("os").getuid(), bg=BG, fg=SEEN_FG)
        self.mode.pack(side="left")
        tk.Label(bar, text="send tab to pipe:", bg=BG, fg=SEEN_FG).pack(side="left", padx=(12, 2))
        self.target = tk.StringVar(value=app.names[0])
        ttk.Combobox(bar, textvariable=self.target, values=app.names, width=9,
                     state="readonly").pack(side="left")
        tk.Button(bar, text="send", command=self.send_to_pipe, **BTN).pack(side="left", padx=4)
        self.status = tk.Label(bar, text="", bg=BG, fg=SEEN_FG)
        self.status.pack(side="right", padx=8)

        self.nb = ttk.Notebook(self.top)
        self.nb.pack(fill="both", expand=True)
        for name in [n for n, _ in survey.SECTIONS] + ["Privileged"]:
            frame = tk.Frame(self.nb, bg=BG)
            if name == "Privileged":
                self._build_privileged_controls(frame)
            if name == "Processes":
                row = tk.Frame(frame, bg=BG)
                row.pack(fill="x")
                tk.Label(row, text="inspect PID:", bg=BG, fg=SEEN_FG).pack(side="left", padx=4)
                self.pid_var = tk.StringVar()
                e = tk.Entry(row, textvariable=self.pid_var, width=9, bg="#1c232b", fg=TEXT_FG,
                             insertbackground=NEW_FG, relief="flat")
                e.pack(side="left", pady=2)
                e.bind("<Return>", lambda ev: self.inspect_pid())
                tk.Button(row, text="show map", command=self.inspect_pid, **BTN).pack(side="left", padx=4)
                tk.Button(row, text="back to overview", command=lambda: self.refresh("Processes"),
                          **BTN).pack(side="left")
            t = tk.Text(frame, bg=BG, fg=TEXT_FG, font=("DejaVu Sans Mono", 10), wrap="none",
                        borderwidth=0, highlightthickness=0, state="disabled")
            ysb = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
            xsb = ttk.Scrollbar(frame, orient="horizontal", command=t.xview)
            t.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
            xsb.pack(side="bottom", fill="x")
            ysb.pack(side="right", fill="y")
            t.pack(fill="both", expand=True)
            t.tag_configure("head", foreground=NEW_FG)
            self.texts[name] = t
            self.nb.add(frame, text=name)

        saved = app.layout.entry(self.KEY).get("tab")
        if saved in self.texts:
            self.nb.select(list(self.texts).index(saved))
        self.nb.bind("<<NotebookTabChanged>>", self._tab_changed)
        self.top.protocol("WM_DELETE_WINDOW", self.hide)

    def current(self):
        return self.nb.tab(self.nb.select(), "text")

    def _tab_changed(self, _ev):
        name = self.current()
        self.app.layout.entry(self.KEY)["tab"] = name
        self.app.layout.schedule_save()
        t = self.texts[name]
        if not t.get("1.0", "end-1c"):
            self.refresh(name)

    def refresh_current(self):
        self.refresh(self.current())

    def refresh(self, name, lines_fn=None):
        if name in self.busy:
            return
        self.busy.add(name)
        self.status.configure(text=f"reading {name}…")
        fn = lines_fn or self._section_fn(name)

        def work():
            try:
                lines = fn()
            except Exception as e:  # keep the UI alive whatever the kernel throws at us
                lines = [f"error while reading: {e!r}"]
            self.results.put((name, lines))

        threading.Thread(target=work, daemon=True).start()
        self.top.after(50, self._drain)

    def _section_fn(self, name):
        if name == "Privileged":
            return lambda: ["Press Elevate (root)… to enable the tools above."] \
                if not self.helper else ["Choose a tool above."]
        if self.helper:
            h = self.helper
            return lambda: h.call("section", name=name)  # same readers, run as root
        return dict(survey.SECTIONS)[name]

    def _drain(self):
        try:
            while True:
                self._show(*self.results.get_nowait())
        except queue.Empty:
            pass
        if self.busy:
            self.top.after(50, self._drain)

    def _set_text(self, t, lines):
        t.configure(state="normal")
        t.delete("1.0", "end")
        for line in lines:
            t.insert("end", line + "\n", "head" if line.startswith("==") else ())
        t.configure(state="disabled")

    def _show(self, name, lines):
        t = self.texts[name]
        y = t.yview()[0]
        self._set_text(t, lines)
        t.yview_moveto(y)
        self.busy.discard(name)
        self.status.configure(text="")

    # ------------------------------------------------------------- elevation
    def toggle_elevate(self):
        if self.helper:
            self.drop_helper("back to user mode")
            return
        if self.elevating:
            return
        try:
            client = HelperClient(self.launcher)
        except (ElevateError, OSError) as e:
            self.status.configure(text=str(e))
            return
        self.elevating = True
        self.status.configure(text="waiting for authentication…")
        self.elev_btn.configure(state="disabled")

        def wait():
            try:
                uid = client.wait_ready()
                self.elev_results.put(("ok", (client, uid)))
            except ElevateError as e:
                self.elev_results.put(("fail", str(e)))
        threading.Thread(target=wait, daemon=True).start()
        self.top.after(100, self._poll_elevation)

    def _poll_elevation(self):
        try:
            name, payload = self.elev_results.get_nowait()
        except queue.Empty:
            self.top.after(100, self._poll_elevation)
            return
        if name == "ok":
            self.helper, uid = payload
            self.mode.configure(text=(f"ROOT helper (uid 0)" if uid == 0 else f"helper (uid {uid}, NOT root)"), fg="#ff7a59" if uid == 0 else SEEN_FG)
            self.elev_btn.configure(text="Drop root")
            self.status.configure(text="elevated — refreshing")
            for t in self.texts.values():
                self._set_text(t, [])
            self.refresh_current()
        else:
            self.status.configure(text=payload)
        self.elevating = False
        self.elev_btn.configure(state="normal")

    def drop_helper(self, why=""):
        if self.helper:
            self.helper.close()
            self.helper = None
        self.mode.configure(text="user (uid %d)" % __import__("os").getuid(), fg=SEEN_FG)
        self.elev_btn.configure(text="Elevate (root)…")
        self.status.configure(text=why)
        for t in self.texts.values():
            self._set_text(t, [])
        self.refresh_current()

    def _build_privileged_controls(self, frame):
        def row():
            r = tk.Frame(frame, bg=BG)
            r.pack(fill="x", pady=1)
            return r

        def entry(r, label, var, width):
            tk.Label(r, text=label, bg=BG, fg=SEEN_FG).pack(side="left", padx=(6, 2))
            tk.Entry(r, textvariable=var, width=width, bg="#1c232b", fg=TEXT_FG,
                     insertbackground=NEW_FG, relief="flat").pack(side="left")

        self.pv = {k: tk.StringVar(value=v) for k, v in
                   dict(pid="", addr="0x0", length="256", sym="", pages="16").items()}
        r = row()
        tk.Button(r, text="kernel virtual map (/proc/kcore)", command=lambda: self.priv("kcore_map"),
                  **BTN).pack(side="left", padx=4)
        tk.Button(r, text="vmallocinfo", command=lambda: self.priv("vmallocinfo"), **BTN).pack(side="left")
        entry(r, "kallsyms match:", self.pv["sym"], 14)
        tk.Button(r, text="find", command=lambda: self.priv("kallsyms", match=self.pv["sym"].get()),
                  **BTN).pack(side="left", padx=4)
        r = row()
        entry(r, "PID:", self.pv["pid"], 8)
        entry(r, "virtual addr:", self.pv["addr"], 16)
        entry(r, "bytes:", self.pv["length"], 6)
        tk.Button(r, text="hexdump process memory",
                  command=lambda: self.priv("read_process", pid=self.pv["pid"].get(),
                                            addr=self.pv["addr"].get(), length=self.pv["length"].get()),
                  **BTN).pack(side="left", padx=4)
        r = row()
        entry(r, "pages:", self.pv["pages"], 5)
        tk.Button(r, text="virtual → physical (pagemap)",
                  command=lambda: self.priv("pagemap", pid=self.pv["pid"].get(),
                                            vaddr=self.pv["addr"].get(), pages=self.pv["pages"].get()),
                  **BTN).pack(side="left", padx=4)
        entry(r, "   physical addr:", self.pv["addr"], 16)
        tk.Button(r, text="hexdump /dev/mem",
                  command=lambda: self.priv("read_phys", addr=self.pv["addr"].get(),
                                            length=self.pv["length"].get()),
                  **BTN).pack(side="left", padx=4)

    def priv(self, op, **args):
        if not self.helper:
            self.status.configure(text="elevate first")
            return
        h = self.helper
        try:
            args = {k: (int(v, 0) if k in ("pid", "addr", "vaddr", "length", "pages") else v)
                    for k, v in args.items()}
        except ValueError:
            self.status.configure(text="PID/address/bytes must be numbers (0x… ok)")
            return

        def call():
            try:
                return h.call(op, **args)
            except ElevateError as e:
                return [f"{op}: {e}"]
        self.refresh("Privileged", call)

    def inspect_pid(self):
        s = self.pid_var.get().strip()
        if not s.isdigit():
            self.status.configure(text="enter a numeric PID")
            return
        if self.helper:
            self.refresh("Processes", lambda: self.helper.call("process_detail", pid=int(s)))
        else:
            self.refresh("Processes", lambda: survey.process_detail(int(s)))

    def send_to_pipe(self):
        text = self.texts[self.current()].get("1.0", "end-1c")
        w = self.app.windows[self.target.get()]
        for line in text.splitlines():
            w.append(line)
        self.app.refresh_buttons()

    def show(self):
        self.top.deiconify()
        if not self.texts[self.current()].get("1.0", "end-1c"):
            self.refresh_current()
        self.app.layout.set_visible(self.KEY, True)
        self.app.refresh_buttons()

    def hide(self):
        self.top.withdraw()
        self.app.layout.set_visible(self.KEY, False)
        self.app.refresh_buttons()

    def visible(self):
        return self.top.state() != "withdrawn"
