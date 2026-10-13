#!/usr/bin/env python3
"""Privileged read-only helper. Launched by the GUI through pkexec.

Protocol: one JSON object per line on stdin ({"id", "op", ...args}); one JSON
object per line on stdout ({"id", "ok", "lines"|"error"}). Only the operations in
OPS exist. None writes to memory or devices. The single exception is the
mmio_* trio, which writes fixed strings to the kernel tracer's own control
files (current_tracer / tracing_on) and always restores them to "nop".
"""
import atexit
import json
import os
import select
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from memsurvey import survey  # noqa: E402

MAX_READ = 4096
MAX_PAGES = 512


class OpError(Exception):
    pass


def _int(args, key, lo=0, hi=2**64 - 1):
    v = args.get(key)
    if isinstance(v, str):
        try:
            v = int(v, 0)
        except ValueError:
            raise OpError(f"{key}: not a number")
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise OpError(f"{key}: must be an integer in [{lo}, {hi}]")
    return v


def hexdump(data, base):
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hx = " ".join(f"{b:02x}" for b in chunk).ljust(47)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{base + i:016x}  {hx}  |{asc}|")
    return lines


def _read_at(path, addr, length):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as e:
        raise OpError(f"open {path}: {e.strerror}")
    try:
        os.lseek(fd, addr, os.SEEK_SET)
        return os.read(fd, length)
    except OSError as e:
        raise OpError(f"read at 0x{addr:x}: {e.strerror} (unmapped, protected or out of range)")
    finally:
        os.close(fd)


# ------------------------------------------------------------------------ ops
def op_ping(args):
    return [f"uid={os.geteuid()}"]


def op_section(args):
    name = args.get("name")
    fns = dict(survey.SECTIONS)
    if name not in fns:
        raise OpError("unknown section")
    return fns[name]()


def op_process_detail(args):
    return survey.process_detail(_int(args, "pid", 1, 2**22))


def op_read_process(args):
    pid, addr = _int(args, "pid", 1, 2**22), _int(args, "addr", 0, 2**63 - 1)
    n = _int(args, "length", 1, MAX_READ)
    data = _read_at(f"/proc/{pid}/mem", addr, n)
    return [f"pid {pid} virtual 0x{addr:x}, {len(data)} bytes"] + hexdump(data, addr)


def op_read_phys(args):
    addr = _int(args, "addr", 0, 2**52)
    n = _int(args, "length", 1, MAX_READ)
    data = _read_at("/dev/mem", addr, n)
    return [f"physical 0x{addr:x}, {len(data)} bytes (/dev/mem, STRICT_DEVMEM allows non-RAM + low 1 MiB only)"] \
        + hexdump(data, addr)


_PM_FLAGS = [(63, "present"), (62, "swapped"), (61, "file/shared"), (56, "exclusive"), (55, "soft-dirty")]


def op_pagemap(args):
    pid, vaddr = _int(args, "pid", 1, 2**22), _int(args, "vaddr", 0, 2**63 - 1)
    pages = _int(args, "pages", 1, MAX_PAGES)
    page = os.sysconf("SC_PAGE_SIZE")
    first = vaddr // page
    data = _read_at(f"/proc/{pid}/pagemap", first * 8, pages * 8)
    rows = [f"pid {pid}: virtual page -> physical frame (PFN). phys addr = PFN * {page}", "",
            f"{'virtual':<18} {'PFN':<12} {'physical':<18} flags", "-" * 70]
    for i in range(len(data) // 8):
        (v,) = struct.unpack_from("<Q", data, i * 8)
        flags = ",".join(n for bit, n in _PM_FLAGS if v >> bit & 1) or "-"
        pfn = v & ((1 << 55) - 1) if v >> 63 & 1 else 0
        phys = f"0x{pfn * page:012x}" if pfn else "-"
        rows.append(f"0x{(first + i) * page:014x}   {pfn:<12x} {phys:<18} {flags}")
    return rows


# ------------------------------------------------------- mmiotrace (Stage B)
_TRACEFS = ("/sys/kernel/tracing", "/sys/kernel/debug/tracing")
_mmio = {"fd": None, "dir": None, "buf": b""}


def _tracefs():
    for d in _TRACEFS:
        if os.path.exists(os.path.join(d, "current_tracer")):
            return d
    raise OpError("tracefs not available (is it mounted? try: mount -t tracefs nodev /sys/kernel/tracing)")


def _tw(d, name, value):
    try:
        with open(os.path.join(d, name), "w") as f:
            f.write(value)
    except OSError as e:
        raise OpError(f"write {name}: {e.strerror}")


def _mmio_off():
    d, fd = _mmio["dir"], _mmio["fd"]
    if fd is not None:
        os.close(fd)
    _mmio.update(fd=None, buf=b"")
    if d:
        try:
            _tw(d, "tracing_on", "0")
            _tw(d, "current_tracer", "nop")
        except OpError:
            pass
        _mmio["dir"] = None


atexit.register(_mmio_off)


def op_mmio_start(args):
    d = _tracefs()
    if _mmio["fd"] is not None:
        raise OpError("mmiotrace already running")
    tracers = survey.read(os.path.join(d, "available_tracers"))[0] or ""
    if "mmiotrace" not in tracers.split():
        raise OpError("kernel has no mmiotrace tracer")
    cur = (survey.read(os.path.join(d, "current_tracer"))[0] or "").strip()
    if cur not in ("nop", ""):
        raise OpError(f"another tracer is active ({cur}); refusing to replace it")
    _tw(d, "current_tracer", "mmiotrace")
    _mmio["dir"] = d
    try:
        _tw(d, "tracing_on", "1")
        _mmio["fd"] = os.open(os.path.join(d, "trace_pipe"), os.O_RDONLY | os.O_NONBLOCK)
    except (OpError, OSError) as e:
        _mmio_off()
        raise OpError(f"could not start: {e}")
    return ["mmiotrace on. It only sees registers of devices whose driver maps them AFTER this point "
            "(reload a driver, or plug a device in)."]


def op_mmio_poll(args):
    fd = _mmio["fd"]
    if fd is None:
        raise OpError("mmiotrace not running")
    for _ in range(64):
        if not select.select([fd], [], [], 0)[0]:
            break
        try:
            chunk = os.read(fd, 65536)
        except BlockingIOError:
            break
        if not chunk:
            break
        _mmio["buf"] += chunk
    *lines, _mmio["buf"] = _mmio["buf"].split(b"\n")
    lines = [l.decode("utf-8", "replace") for l in lines]
    if len(lines) > 1000:
        lines = lines[:1000] + [f"# dropped {len(lines) - 1000} lines"]
    return lines


def op_mmio_stop(args):
    if _mmio["dir"] is None:
        raise OpError("mmiotrace not running")
    _mmio_off()
    return ["mmiotrace off, tracer restored to nop"]


# x86-64 4-level layout, used only to label kcore segments
_REGIONS = [(0xffff800000000000, 0xffff887fffffffff, "guard hole"),
            (0xffff888000000000, 0xffffc87fffffffff, "direct map of physical RAM"),
            (0xffffc90000000000, 0xffffe8ffffffffff, "vmalloc/ioremap"),
            (0xffffea0000000000, 0xffffeaffffffffff, "vmemmap (struct page array)"),
            (0xffffffff80000000, 0xffffffff9fffffff, "kernel text/data"),
            (0xffffffffa0000000, 0xffffffffff5fffff, "modules")]


def op_kcore_map(args):
    try:
        f = open("/proc/kcore", "rb")
    except OSError as e:
        raise OpError(f"/proc/kcore: {e.strerror}")
    with f:
        eh = f.read(64)
        if eh[:4] != b"\x7fELF":
            raise OpError("/proc/kcore is not ELF")
        phoff, = struct.unpack_from("<Q", eh, 32)
        phentsize, phnum = struct.unpack_from("<HH", eh, 54)
        rows = []
        for i in range(phnum):
            f.seek(phoff + i * phentsize)
            ph = f.read(56)
            ptype, flags, off, vaddr, paddr, filesz, memsz, _ = struct.unpack("<IIQQQQQQ", ph)
            if ptype != 1:  # PT_LOAD
                continue
            label = next((n for lo, hi, n in _REGIONS if lo <= vaddr <= hi), "?")
            rows.append((vaddr, memsz, label))
    rows.sort()
    out = ["/proc/kcore: ELF view of kernel virtual memory. Each PT_LOAD segment is a mapped range.", "",
           f"{'virtual start':<20}{'virtual end':<20}{'size':<12}region", "-" * 76]
    for v, sz, label in rows:
        out.append(f"0x{v:016x}  0x{v + sz - 1:016x}  {survey.human(sz):<10}  {label}")
    out.append(f"\n{len(rows)} segments")
    dm = [r for r in rows if r[2] == "direct map of physical RAM"]
    if dm:
        out.append(f"Direct-map base (KASLR page_offset_base guess): 0x{dm[0][0]:x}")
    return out


def op_vmallocinfo(args):
    text, err = survey.read("/proc/vmallocinfo")
    if err:
        raise OpError(err)
    lines = text.splitlines()
    return lines[:400] + ([f"... {len(lines) - 400} more"] if len(lines) > 400 else [])


def op_kallsyms(args):
    q = args.get("match", "")
    if not isinstance(q, str) or len(q) > 64:
        raise OpError("match: string up to 64 chars")
    text, err = survey.read("/proc/kallsyms")
    if err:
        raise OpError(err)
    hits = [l for l in text.splitlines() if q in l.split()[2]] if q else text.splitlines()[:50]
    return hits[:200] + ([f"... {len(hits) - 200} more"] if len(hits) > 200 else [])


OPS = {"ping": op_ping, "section": op_section, "process_detail": op_process_detail,
       "read_process": op_read_process, "read_phys": op_read_phys, "pagemap": op_pagemap,
       "kcore_map": op_kcore_map, "mmio_start": op_mmio_start,
       "mmio_poll": op_mmio_poll, "mmio_stop": op_mmio_stop, "vmallocinfo": op_vmallocinfo, "kallsyms": op_kallsyms}


def handle(req):
    rid = req.get("id") if isinstance(req, dict) else None
    try:
        fn = OPS.get(req.get("op")) if isinstance(req, dict) else None
        if fn is None:
            raise OpError("unknown op")
        return {"id": rid, "ok": True, "lines": fn(req)}
    except OpError as e:
        return {"id": rid, "ok": False, "error": str(e)}
    except Exception as e:  # never die on one bad request
        return {"id": rid, "ok": False, "error": f"internal error: {e!r}"}


def main():
    out = sys.stdout
    out.write(json.dumps({"ready": True, "uid": os.geteuid()}) + "\n")
    out.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            resp = {"id": None, "ok": False, "error": "bad json"}
        else:
            resp = handle(req)
        out.write(json.dumps(resp) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
