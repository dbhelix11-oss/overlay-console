"""Tk windows: one PipeWindow per FIFO plus the control window."""
import tkinter as tk
from tkinter import ttk

BG = "#101418"
SEEN_FG = "#6b7580"
NEW_FG = "#7CFC9A"
DIVIDER_FG = "#e0b040"
MAX_LINES = 20000


class PipeWindow:
    """Text view of one pipe. Lines are 'new' (bright) until the window is left
    or 'mark seen' is pressed; a divider marks where the unread block begins."""

    def __init__(self, app, name):
        self.app = app
        self.name = name
        self.top = tk.Toplevel(app.root)
        self.top.title(f"OverlayConsole — {name}")
        self.top.configure(bg=BG)
        self.unseen = 0
        self.divider = False

        bar = tk.Frame(self.top, bg=BG)
        bar.pack(fill="x")
        self.status = tk.Label(bar, text="", bg=BG, fg=SEEN_FG, anchor="w")
        self.status.pack(side="left", padx=6)
        tk.Button(bar, text="mark seen", command=self.mark_seen, relief="flat",
                  bg="#1c232b", fg="#ccc", activebackground="#2a3440").pack(side="right", padx=4, pady=2)
        tk.Button(bar, text="clear", command=self.clear, relief="flat",
                  bg="#1c232b", fg="#ccc", activebackground="#2a3440").pack(side="right", pady=2)

        self.text = tk.Text(self.top, bg=BG, fg=SEEN_FG, insertbackground=NEW_FG,
                            font=("DejaVu Sans Mono", 10), wrap="none", state="disabled",
                            borderwidth=0, highlightthickness=0)
        ysb = ttk.Scrollbar(self.top, orient="vertical", command=self.text.yview)
        xsb = ttk.Scrollbar(self.top, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        xsb.pack(side="bottom", fill="x")
        ysb.pack(side="right", fill="y")
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("seen", foreground=SEEN_FG)
        self.text.tag_configure("new", foreground=NEW_FG)
        self.text.tag_configure("divider", foreground=DIVIDER_FG, justify="center")

        self.top.bind("<FocusOut>", self._focus_out)
        self.top.protocol("WM_DELETE_WINDOW", self.hide)
        self._update_status()

    # -- single entry point for incoming lines (the seam for a future log sink)
    def append(self, line):
        t = self.text
        at_bottom = self.text.yview()[1] >= 0.999
        t.configure(state="normal")
        if not self.divider:
            t.insert("end", "─── new ───\n", "divider")
            self.divider = True
        t.insert("end", line + "\n", "new")
        self.unseen += 1
        total = int(t.index("end-1c").split(".")[0])
        if total > MAX_LINES:
            t.delete("1.0", f"{total - MAX_LINES}.0")
        t.configure(state="disabled")
        if at_bottom:
            t.see("end")
        self._update_status()

    def mark_seen(self):
        t = self.text
        t.configure(state="normal")
        if self.divider:
            r = t.tag_ranges("divider")
            if r:
                t.delete(r[0], r[1])
        t.tag_remove("new", "1.0", "end")
        # everything that is not the (removed) divider is seen
        t.tag_add("seen", "1.0", "end")
        t.configure(state="disabled")
        self.divider = False
        self.unseen = 0
        self._update_status()

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.divider = False
        self.unseen = 0
        self._update_status()

    def _focus_out(self, ev):
        # Toplevel gets FocusOut for child widgets too; only act when focus really left
        if ev.widget is self.top and self.top.focus_displayof() is None:
            self.mark_seen()

    def _update_status(self):
        pending = f"{self.unseen} new" if self.unseen else "all seen"
        self.status.configure(text=f"pipes/{self.name}   {pending}")

    def show(self):
        self.top.deiconify()
        self.app.layout.set_visible(f"pipe:{self.name}", True)
        self.app.refresh_buttons()

    def hide(self):
        self.top.withdraw()
        self.app.layout.set_visible(f"pipe:{self.name}", False)
        self.app.refresh_buttons()

    def visible(self):
        return self.top.state() != "withdrawn"


class ControlWindow:
    """Main window: pipe toggles in the top pane, usage notes in the bottom pane."""

    def __init__(self, app):
        self.app = app
        root = app.root
        root.title("OverlayConsole")
        root.configure(bg=BG)
        self.paned = tk.PanedWindow(root, orient="vertical", bg="#2a3440",
                                    sashwidth=5, borderwidth=0)
        self.paned.pack(fill="both", expand=True)

        top = tk.Frame(self.paned, bg=BG)
        tk.Label(top, text="Pipes (click to show/hide a window)", bg=BG, fg="#ccc",
                 anchor="w").pack(fill="x", padx=6, pady=(6, 2))
        self.buttons = {}
        for name in app.names:
            b = tk.Button(top, text=name, anchor="w", relief="flat", bg="#1c232b",
                          fg="#ccc", activebackground="#2a3440",
                          command=lambda n=name: app.toggle(n))
            b.pack(fill="x", padx=6, pady=1)
            self.buttons[name] = b
        self.mem_button = tk.Button(top, text="Memory survey", anchor="w", relief="flat",
                                    bg="#1c232b", fg="#ccc", activebackground="#2a3440",
                                    command=lambda: app.toggle_memory())
        self.mem_button.pack(fill="x", padx=6, pady=(8, 1))
        self.paned.add(top, minsize=80)

        info = tk.Text(self.paned, bg=BG, fg=SEEN_FG, borderwidth=0, highlightthickness=0,
                       font=("DejaVu Sans Mono", 9), wrap="word")
        usage = ["Write into a window with ordinary shell pipes:\n"]
        for n in app.names[:2]:
            usage.append(f"  echo hello > {app.pipe_dir}/{n}\n")
        usage.append(f"  some_cmd | tee {app.pipe_dir}/{app.names[-1]}\n")
        usage.append("\nBright text = new since you last left the window.\n")
        info.insert("1.0", "".join(usage))
        info.configure(state="disabled")
        self.paned.add(info, minsize=60)

    def refresh(self, windows, memory=None):
        if memory is not None:
            self.mem_button.configure(text=f"{'●' if memory.visible() else '○'}  Memory survey")
        for n, b in self.buttons.items():
            w = windows[n]
            mark = "●" if w.visible() else "○"
            unseen = f"  ({w.unseen} new)" if w.unseen else ""
            b.configure(text=f"{mark}  {n}{unseen}",
                        fg=NEW_FG if w.unseen else "#ccc")
