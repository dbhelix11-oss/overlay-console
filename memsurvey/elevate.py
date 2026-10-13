"""GUI-side client for the privileged helper (started via pkexec)."""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HELPER = Path(__file__).with_name("helper.py")
TIMEOUT = 60


class ElevateError(Exception):
    pass


class HelperClient:
    def __init__(self, argv=None):
        """argv overrides the launcher (tests); default is pkexec + system python."""
        if argv is None:
            pk = shutil.which("pkexec")
            if not pk:
                raise ElevateError("pkexec not found (install polkit)")
            argv = [pk, "/usr/bin/python3", str(HELPER)]
        self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self.lock = threading.Lock()  # one request at a time
        self.replies = queue.Queue()
        self.next_id = 1
        self.uid = None
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.replies.put(json.loads(line))
            except ValueError:
                pass
        self.replies.put(None)  # EOF

    def wait_ready(self, timeout=120):
        """Block until the helper says hello (user has authenticated) or exits."""
        try:
            msg = self.replies.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise ElevateError("timed out waiting for authentication")
        if msg is None:
            code = self.proc.wait()
            raise ElevateError("authentication cancelled or denied" if code in (126, 127)
                               else f"helper exited with status {code}")
        self.uid = msg.get("uid")
        return self.uid

    @property
    def alive(self):
        return self.proc.poll() is None

    def call(self, op, **args):
        """Return the list of text lines, or raise ElevateError."""
        with self.lock:
            if not self.alive:
                raise ElevateError("helper is not running")
            rid, self.next_id = self.next_id, self.next_id + 1
            try:
                self.proc.stdin.write(json.dumps({"id": rid, "op": op, **args}) + "\n")
                self.proc.stdin.flush()
                while True:
                    msg = self.replies.get(timeout=TIMEOUT)
                    if msg is None:
                        raise ElevateError("helper exited")
                    if msg.get("id") == rid:
                        break
            except (BrokenPipeError, queue.Empty) as e:
                raise ElevateError(f"helper not responding ({type(e).__name__})")
        if not msg.get("ok"):
            raise ElevateError(msg.get("error", "failed"))
        return msg["lines"]

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
