"""I/O activity capture.

Stage A (unprivileged): once-a-second deltas of interrupt, disk and network
counters, each tied back to a device where the kernel gives us a name.
Stage B (root helper): mmiotrace lines (hardware register reads/writes) decoded
here and annotated with the PCI device whose BAR contains the address.
"""
import re
from pathlib import Path

from .survey import PROC, SYS, human, read, table

# ------------------------------------------------------------------ Stage A
def snapshot():
    """name -> counter, for every source. Names are prefixed by kind."""
    snap = {}
    text, _ = read(PROC / "interrupts")
    for line in (text or "").splitlines()[1:]:
        p = line.split()
        if not p:
            continue
        n, i = 0, 1
        while i < len(p) and p[i].isdigit():
            n += int(p[i]); i += 1
        snap[f"irq {p[0].rstrip(':')} {' '.join(p[i:])[:48]}"] = n
    text, _ = read(PROC / "diskstats")
    for line in (text or "").splitlines():
        p = line.split()
        if len(p) >= 10 and not p[2].startswith(("loop", "ram")) and (SYS / "block" / p[2]).exists():
            snap[f"disk {p[2]} read"] = int(p[5]) * 512     # sectors -> bytes
            snap[f"disk {p[2]} write"] = int(p[9]) * 512
    text, _ = read(PROC / "net/dev")
    for line in (text or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        p = rest.split()
        if len(p) >= 9:
            snap[f"net {name.strip()} rx"] = int(p[0])
            snap[f"net {name.strip()} tx"] = int(p[8])
    return snap


class Sampler:
    def __init__(self):
        self.prev = snapshot()
        self.total = {}

    def tick(self, interval=1.0, top=18):
        cur = snapshot()
        rows = []
        for k, v in cur.items():
            d = v - self.prev.get(k, v)
            if d > 0:
                self.total[k] = self.total.get(k, 0) + d
                rows.append((k, d / interval))
        self.prev = cur
        rows.sort(key=lambda r: -r[1])
        out = []
        for k, rate in rows[:top]:
            unit = "B/s" if k.startswith(("disk", "net")) else "irq/s"
            val = human(rate) + "/s" if unit == "B/s" else f"{rate:,.0f} irq/s"
            out.append((k, val, human(self.total[k]) if unit == "B/s" else f"{self.total[k]:,}"))
        return table(out, ["source", "rate", "total since start"]) if out else ["(no activity)"]


# ------------------------------------------------------------------ Stage B
def pci_ranges():
    """[(start, end, 'bdf driver BARn')] from sysfs resource files (readable without root)."""
    out = []
    base = SYS / "bus/pci/devices"
    if not base.is_dir():
        return out
    for d in sorted(base.glob("*")):
        t, _ = read(d / "resource")
        drv = (d / "driver").resolve().name if (d / "driver").exists() else "-"
        for i, line in enumerate((t or "").splitlines()[:6]):
            a, b, f = (int(x, 16) for x in line.split())
            if b > a and not f & 1:   # skip port-I/O BARs (bit0): mmiotrace sees memory only
                out.append((a, b, f"{d.name} {drv} BAR{i}"))
    return out


def annotate(phys, ranges):
    for a, b, label in ranges:
        if a <= phys <= b:
            return f"{label}+0x{phys - a:x}"
    return "?"


_RW = re.compile(r"^([RW]) (\d+) (\d+\.\d+) (\d+) ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+)")
_MAP = re.compile(r"^(MAP|UNMAP) (\d+\.\d+) (\d+) ([0-9a-f]+) ([0-9a-f]+) ([0-9a-f]+)")


def decode_mmio(line, ranges):
    """Format one mmiotrace line (fields per kernel trace_mmiotrace.c) for display."""
    m = _RW.match(line)
    if m:
        kind, width, ts, mid, phys, val, pc = m.groups()
        phys_i = int(phys, 16)
        op = "READ " if kind == "R" else "WRITE"
        return (f"{ts:>11} {op} {int(width)}B phys=0x{phys_i:x} value=0x{int(val, 16):0{int(width) * 2}x} "
                f"[{annotate(phys_i, ranges)}] map#{mid}")
    m = _MAP.match(line)
    if m:
        kind, ts, mid, phys, virt, ln = m.groups()
        phys_i = int(phys, 16)
        return (f"{ts:>11} {kind:<5} map#{mid} phys=0x{phys_i:x} len=0x{int(ln, 16):x} "
                f"[{annotate(phys_i, ranges)}]")
    return line


def selftest():
    r = [(0xf7c00000, 0xf7c1ffff, "0000:00:19.0 e1000e BAR0")]
    assert "e1000e BAR0+0x8" in decode_mmio("R 4 3.500000 1 f7c00008 1234 ffffffffa0123456 0", r)
    assert "MAP" in decode_mmio("MAP 0.047062 1 f7c00000 ffffc90000100000 20000 0 0", r)
    return True
