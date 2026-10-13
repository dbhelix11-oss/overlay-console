"""Memory survey window: one tab per survey section, refreshed off the UI thread."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

from occore.windows import BG, NEW_FG, SEEN_FG
from . import survey

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

        bar = tk.Frame(self.top, bg=BG)
        bar.pack(fill="x")
        tk.Button(bar, text="refresh", command=self.refresh_current, **BTN).pack(side="left", padx=4, pady=3)
        tk.Label(bar, text="send tab to pipe:", bg=BG, fg=SEEN_FG).pack(side="left", padx=(12, 2))
        self.target = tk.StringVar(value=app.names[0])
        ttk.Combobox(bar, textvariable=self.target, values=app.names, width=9,
                     state="readonly").pack(side="left")
        tk.Button(bar, text="send", command=self.send_to_pipe, **BTN).pack(side="left", padx=4)
        self.status = tk.Label(bar, text="", bg=BG, fg=SEEN_FG)
        self.status.pack(side="right", padx=8)

        self.nb = ttk.Notebook(self.top)
        self.nb.pack(fill="both", expand=True)
        for name, fn in survey.SECTIONS:
            frame = tk.Frame(self.nb, bg=BG)
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
        fn = lines_fn or dict(survey.SECTIONS)[name]

        def work():
            try:
                lines = fn()
            except Exception as e:  # keep the UI alive whatever the kernel throws at us
                lines = [f"error while reading: {e!r}"]
            self.results.put((name, lines))

        threading.Thread(target=work, daemon=True).start()
        self.top.after(50, self._drain)

    def _drain(self):
        try:
            while True:
                self._show(*self.results.get_nowait())
        except queue.Empty:
            pass
        if self.busy:
            self.top.after(50, self._drain)

    def _show(self, name, lines):
        t = self.texts[name]
        y = t.yview()[0]
        t.configure(state="normal")
        t.delete("1.0", "end")
        for line in lines:
            t.insert("end", line + "\n", "head" if line.startswith("==") else ())
        t.configure(state="disabled")
        t.yview_moveto(y)
        self.busy.discard(name)
        self.status.configure(text="")

    def inspect_pid(self):
        s = self.pid_var.get().strip()
        if not s.isdigit():
            self.status.configure(text="enter a numeric PID")
            return
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
