#!/usr/bin/env python3
"""OverlayConsole: multi-window viewer for named pipes (and, later, memory survey)."""
import fcntl
import queue
import sys
import tkinter as tk

from occore import config
from occore.dock import DOCK_SIZE, KEY as DOCK_KEY, DockWindow
from occore.layout import Layout, on_current_desktop
from occore.pipes import start_readers
from occore.tray import Tray
from memsurvey.window import MemoryWindow
from occore.windows import ControlWindow, PipeWindow

POLL_MS = 50


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.names = config.pipe_names()
        self.pipe_dir = config.PIPE_DIR
        self.layout = Layout(config.STATE_FILE, self.root)
        self.queue, self.readers = start_readers(self.names, self.pipe_dir)

        self.tray = Tray()
        self.control = ControlWindow(self)
        self.layout.track("control", self.root, "260x320+40+40")
        self.layout.track_paned("control:paned", self.control.paned, [0.65])

        self.windows = {}
        self.dock = DockWindow(self, self.names[:DOCK_SIZE])
        self.windows.update(self.dock.panes)
        if not self.layout.track(DOCK_KEY, self.dock.top, "1000x640+320+40"):
            self.dock.top.withdraw()
        for i, n in enumerate(self.names[DOCK_SIZE:]):
            w = PipeWindow(self, n)
            visible = self.layout.track(f"pipe:{n}", w.top,
                                        f"520x300+{320 + 30 * i}+{40 + 30 * i}")
            self.windows[n] = w
            if not visible:
                w.top.withdraw()

        self.memory = MemoryWindow(self)
        if not self.layout.track(MemoryWindow.KEY, self.memory.top, "820x560+120+80",
                                 default_visible=False):
            self.memory.top.withdraw()

        # one taskbar entry: every other window is a transient child of the control window
        for top in (self.dock.top, self.memory.top,
                    *(w.top for w in self.windows.values() if not w.docked)):
            top.transient(self.root)
        if self.tray.available:
            self.root.bind("<Unmap>", self._on_unmap)  # minimizing sends everything to the tray
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.refresh_buttons()
        self.root.after(POLL_MS, self.poll)
        self.root.after(400, self.layout.restore_desktops)  # once the windows are mapped

    def toggle(self, name):
        w = self.windows[name]
        w.hide() if w.visible() else w.show()

    def toggle_memory(self):
        self.memory.hide() if self.memory.visible() else self.memory.show()

    def _on_unmap(self, ev):
        if ev.widget is self.root and self.root.state() == "iconic":
            self.root.after(150, self._maybe_tray)

    def _maybe_tray(self):
        # only a real minimize counts; a window on another desktop is unmapped too
        if self.root.state() == "iconic" and on_current_desktop(self.root):
            self.hide_to_tray()

    def hide_to_tray(self):
        if self.tray.available:
            self.root.withdraw()  # transient children go with it

    def show_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.after(200, self.layout.restore_desktops)

    def toggle_tray(self):
        if self.root.state() == "normal":
            self.hide_to_tray()
        else:
            self.show_from_tray()

    def refresh_buttons(self):
        self.control.refresh(self.windows, self.memory)

    def poll(self):
        changed = False
        try:
            while True:
                ev = self.tray.events.get_nowait()
                self.toggle_tray() if ev == "toggle" else self.quit()
                if ev == "quit":
                    return
        except queue.Empty:
            pass
        try:
            while True:
                name, line = self.queue.get_nowait()
                self.windows[name].append(line)
                changed = True
        except queue.Empty:
            pass
        if changed:
            self.refresh_buttons()
        self.root.after(POLL_MS, self.poll)

    def quit(self):
        self.tray.stop()
        if self.memory.helper:
            self.memory.helper.close()
        for r in self.readers:
            r.stop()
        self.layout.save()
        self.root.destroy()


def single_instance():
    fh = open(config.LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("OverlayConsole is already running.")
    return fh  # keep referenced for the life of the process


def main():
    lock = single_instance()  # noqa: F841
    App().root.mainloop()


if __name__ == "__main__":
    main()
