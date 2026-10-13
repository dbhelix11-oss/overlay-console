"""Standard POSIX named pipes (mkfifo) read by one thread each."""
import os
import queue
import select
import stat
import threading


def ensure_fifo(path):
    try:
        mode = os.stat(path).st_mode
        if stat.S_ISFIFO(mode):
            return
        raise FileExistsError(f"{path} exists and is not a FIFO")
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(path, 0o600)


class PipeReader(threading.Thread):
    """Reads lines from a FIFO and puts (name, line) on a queue.

    The FIFO is opened O_RDWR so the reader also holds a write end: writers
    that finish (`echo x > pipe`) never cause EOF and no reopen is needed.
    """

    def __init__(self, name, path, out_queue):
        super().__init__(daemon=True, name=f"pipe-{name}")
        self.pipe_name = name
        self.path = path
        self.out = out_queue
        self._halt = threading.Event()

    def run(self):
        fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        buf = b""
        try:
            while not self._halt.is_set():
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for line in lines:
                    self.out.put((self.pipe_name, line.decode("utf-8", "replace").rstrip("\r")))
        finally:
            os.close(fd)

    def stop(self):
        self._halt.set()


def start_readers(names, pipe_dir):
    q = queue.Queue()
    readers = []
    for n in names:
        p = pipe_dir / n
        ensure_fifo(p)
        r = PipeReader(n, p, q)
        r.start()
        readers.append(r)
    return q, readers
