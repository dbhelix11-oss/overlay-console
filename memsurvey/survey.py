"""Unprivileged, read-only survey of Linux memory and hardware address space.

Each section function returns a list of text lines. Every source is probed and
reported as ok / masked (readable but addresses zeroed) / denied, so the output
doubles as a map of what needs root.
"""
import os
import re
from collections import defaultdict
from pathlib import Path

PROC = Path("/proc")
SYS = Path("/sys")


def read(path):
    """Return (text, error). error is None on success, else a short reason."""
    try:
        return Path(path).read_text(errors="replace"), None
    except PermissionError:
        return None, "denied (permission)"
    except FileNotFoundError:
        return None, "not present"
    except OSError as e:
        return None, f"error ({e.strerror})"


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def table(rows, header):
    rows = [header] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    out = []
    for i, r in enumerate(rows):
        out.append("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())
        if i == 0:
            out.append("  ".join("-" * w for w in widths))
    return out


# ---------------------------------------------------------------- access probes
_ZERO_ADDR = re.compile(r"^(0+)([-\s])")


def probe_sources():
    """Status of each address-bearing source for the current user."""
    res = []

    def status_of(path, masked_test=None):
        text, err = read(path)
        if err:
            return err
        if masked_test and masked_test(text):
            return "masked (addresses zeroed)"
        return "ok"

    res.append(("/proc/meminfo", status_of(PROC / "meminfo")))
    res.append(("/sys/firmware/memmap (e820 map)", status_of(SYS / "firmware/memmap/0/start")))
    res.append(("/proc/iomem", status_of(PROC / "iomem", lambda t: t.startswith("00000000-00000000"))))
    res.append(("/proc/ioports", status_of(PROC / "ioports", lambda t: t.startswith("0000-0000"))))
    res.append(("/proc/kallsyms", status_of(PROC / "kallsyms", lambda t: t.startswith("0000000000000000"))))
    res.append(("/proc/modules", status_of(PROC / "modules", lambda t: "0x0000000000000000" in t)))
    res.append(("/proc/vmallocinfo", status_of(PROC / "vmallocinfo")))
    res.append(("/proc/zoneinfo", status_of(PROC / "zoneinfo")))
    res.append(("/proc/kcore", "denied (permission)" if not os.access(PROC / "kcore", os.R_OK) else "ok"))
    res.append(("/dev/mem", "denied (permission)" if not os.access("/dev/mem", os.R_OK) else "ok"))
    bar_state = "not present"
    for d in sorted((SYS / "bus/pci/devices").glob("*")):
        t, err = read(d / "resource")
        if err:
            bar_state = err
            break
        if any(int(x, 16) for x in t.split()):
            bar_state = "ok"
            break
        bar_state = "masked (addresses zeroed)"
    res.append(("PCI BAR addresses (sysfs resource)", bar_state))
    pm, err = None, None
    try:
        with open("/proc/self/pagemap", "rb") as f:
            f.seek((int("0x" + "1000", 16) >> 12) * 8)
            pm = f.read(8)
        # PFN field (bits 0-54) is zeroed without CAP_SYS_ADMIN
        pfn = int.from_bytes(pm, "little") & ((1 << 55) - 1)
        res.append(("/proc/self/pagemap (physical frame numbers)",
                    "ok" if pfn else "masked (PFNs zeroed)"))
    except OSError as e:
        res.append(("/proc/self/pagemap (physical frame numbers)", f"error ({e.strerror})"))
    ptrace = read(PROC / "sys/kernel/yama/ptrace_scope")[0]
    res.append(("/proc/<pid>/mem of other processes",
                f"limited (Yama ptrace_scope={ptrace.strip()})" if ptrace else "own-uid only"))
    kptr = read(PROC / "sys/kernel/kptr_restrict")[0]
    return res, (kptr or "?").strip()


# ---------------------------------------------------------------------- summary
MEMINFO_KEYS = ["MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapTotal",
                "SwapFree", "Slab", "KernelStack", "PageTables", "AnonPages", "Mapped",
                "Shmem", "HugePages_Total", "Hugepagesize", "DirectMap4k", "DirectMap2M",
                "DirectMap1G"]


def summary():
    out = ["== Memory totals (/proc/meminfo) =="]
    text, err = read(PROC / "meminfo")
    if err:
        return out + [err]
    info = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        parts = v.split()
        if parts:
            info[k] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
    out += table([(k, human(info[k]), f"{info[k]:,}") for k in MEMINFO_KEYS if k in info],
                 ["field", "size", "bytes"])
    page = os.sysconf("SC_PAGE_SIZE")
    out += ["", f"Page size: {page} bytes   (page frames in MemTotal: {info.get('MemTotal', 0) // page:,})"]
    bs = read(SYS / "devices/system/memory/block_size_bytes")[0]
    if bs:
        out.append(f"Memory hotplug block size: {human(int(bs, 16))}")
    out += ["", "== What can this user see? =="]
    probes, kptr = probe_sources()
    out += table(probes, ["source", "status"])
    out += ["", f"kptr_restrict={kptr}  (kernel pointer hiding: 0=off, 1=hide from non-root, 2=hide always)"]
    out.append(f"Running as uid {os.getuid()}")
    return out


# --------------------------------------------------------------------- physical
def _memmap():
    base = SYS / "firmware/memmap"
    ents = []
    if not base.is_dir():
        return None
    for d in base.iterdir():
        s, _ = read(d / "start")
        e, _ = read(d / "end")
        t, _ = read(d / "type")
        if s and e and t:
            ents.append((int(s, 16), int(e, 16), t.strip()))
    return sorted(ents)


def physical():
    out = ["== Physical address map: firmware e820 table (/sys/firmware/memmap) ==",
           "These are real physical addresses handed over by the BIOS/UEFI at boot.",
           "Unlike /proc/iomem they are readable without root.", ""]
    ents = _memmap()
    if ents is None:
        out.append("not present on this system")
    else:
        rows, totals = [], defaultdict(int)
        for s, e, t in ents:
            size = e - s + 1
            totals[t] += size
            rows.append((f"0x{s:012x}", f"0x{e:012x}", human(size), t))
        out += table(rows, ["start", "end", "size", "type"])
        out += ["", "Totals by type:"]
        out += table([(t, human(n)) for t, n in sorted(totals.items(), key=lambda x: -x[1])],
                     ["type", "size"])
        top = max((e for _, e, _ in ents)) + 1
        out.append(f"\nHighest physical address mapped: 0x{top:x} ({human(top)}) "
                   f"= {top.bit_length()} bits of address space used")

    out += ["", "== Kernel's resource tree (/proc/iomem) =="]
    text, err = read(PROC / "iomem")
    if err:
        out.append(err)
    else:
        masked = text.startswith("00000000-00000000")
        if masked:
            out.append("Addresses are zeroed for non-root; the nesting and names are still shown "
                       "(indent = parent/child ranges).")
        counts = defaultdict(int)
        for line in text.splitlines():
            counts[line.split(" : ", 1)[-1].strip()] += 1
        shown = text.splitlines()
        out += shown[:150]
        if len(shown) > 150:
            out.append(f"... {len(shown) - 150} more lines")

    out += ["", "== Memory blocks (/sys/devices/system/memory) =="]
    bdir = SYS / "devices/system/memory"
    blocks = sorted((p for p in bdir.glob("memory*") if p.name[6:].isdigit()),
                    key=lambda p: int(p.name[6:]))
    bs = read(bdir / "block_size_bytes")[0]
    if blocks and bs:
        bsz = int(bs, 16)
        online = sum(1 for b in blocks if (read(b / "online")[0] or "").strip() == "1")
        out.append(f"{len(blocks)} blocks of {human(bsz)} each; {online} online "
                   f"(block N covers physical 0x{bsz:x}*N upward)")
    else:
        out.append("not present")

    out += ["", "== Zones (/proc/buddyinfo): free blocks by order (order n = 2^n pages) =="]
    text, err = read(PROC / "buddyinfo")
    if err:
        out.append(err)
    else:
        page = os.sysconf("SC_PAGE_SIZE")
        rows = []
        for line in text.splitlines():
            m = re.match(r"Node (\d+), zone\s+(\S+)\s+(.*)", line)
            if m:
                counts = [int(x) for x in m[3].split()]
                free = sum(c << i for i, c in enumerate(counts)) * page
                rows.append((m[1], m[2], human(free), " ".join(str(c) for c in counts)))
        out += table(rows, ["node", "zone", "free", "free blocks per order 0..10"])
        out.append("DMA = below 16 MiB, DMA32 = below 4 GiB, Normal = the rest.")
    return out


# ----------------------------------------------------------------------- kernel
def kernel():
    out = ["== Kernel symbols (/proc/kallsyms) =="]
    text, err = read(PROC / "kallsyms")
    if err:
        out.append(err)
    else:
        lines = text.splitlines()
        masked = lines and lines[0].startswith("0000000000000000")
        out.append(f"{len(lines):,} symbols. "
                   + ("Addresses masked to zero (kptr_restrict); names/types still visible."
                      if masked else "Addresses visible."))
        types = defaultdict(int)
        for line in lines:
            p = line.split()
            if len(p) >= 2:
                types[p[1]] += 1
        out += table([(t, n, _KSYM_TYPES.get(t.lower(), "")) for t, n in
                      sorted(types.items(), key=lambda x: -x[1])[:12]], ["type", "count", "meaning"])
        if not masked:
            out += ["", "First symbols:"] + lines[:5]

    out += ["", "== Loaded modules (/proc/modules) =="]
    text, err = read(PROC / "modules")
    if err:
        out.append(err)
    else:
        rows = []
        for line in text.splitlines():
            p = line.split()
            rows.append((p[0], human(int(p[1])), p[2], p[5] if len(p) > 5 else ""))
        rows.sort(key=lambda r: -float(r[1].split()[0]) * (1024 ** ["B", "KiB", "MiB", "GiB", "TiB"].index(r[1].split()[1])))
        out.append(f"{len(rows)} modules (load addresses masked unless root); largest:")
        out += table(rows[:15], ["module", "size", "used by #", "load address"])
    out += ["", "== Kernel virtual allocations (/proc/vmallocinfo) =="]
    text, err = read(PROC / "vmallocinfo")
    out.append(err if err else f"{len(text.splitlines()):,} allocations")
    out += ["", "== Reference: x86-64 kernel virtual layout (4-level paging, from Documentation/arch/x86/x86_64/mm.rst) ==",
            "0000000000000000 - 00007fffffffffff  user space (128 TiB)",
            "ffff800000000000 - ffff87ffffffffff  guard hole (hypervisor)",
            "ffff888000000000 - ffffc87fffffffff  direct map of ALL physical RAM (64 TiB, KASLR-shifted)",
            "ffffc90000000000 - ffffe8ffffffffff  vmalloc / ioremap space (32 TiB)",
            "ffffea0000000000 - ffffeaffffffffff  vmemmap: the struct page array (1 TiB)",
            "ffffffff80000000 - ffffffff9fffffff  kernel text mapping (512 MiB)",
            "ffffffffa0000000 - fffffffffeffffff  modules",
            "(Exact bases shift with KASLR; this is the 4-level layout. 5-level paging moves some.)"]
    return out


_KSYM_TYPES = {"t": "text (code)", "d": "initialised data", "b": "BSS (zero-init data)",
               "r": "read-only data", "a": "absolute", "w": "weak", "v": "weak object",
               "u": "unique global", "g": "small initialised data", "n": "debug",
               "s": "small BSS", "i": "indirect", "?": "unknown"}


# -------------------------------------------------------------------- processes
_MAPS = re.compile(r"^([0-9a-f]+)-([0-9a-f]+)\s+(\S+)\s+\S+\s+\S+\s+\S+\s*(.*)$")


def categorize(perms, path):
    if path == "[heap]":
        return "heap"
    if path.startswith("[stack"):
        return "stack"
    if path in ("[vdso]", "[vvar]", "[vvar_vclock]", "[vsyscall]"):
        return "vdso/vvar/vsyscall"
    if path.startswith(("/dev/shm", "/SYSV", "/memfd:", "/dev/zero")) or "(deleted)" in path and "shm" in path:
        return "shared memory"
    if path.startswith("/dev/") or path.startswith("/sys/") or path.startswith("/proc/"):
        return "device/special mapping"
    if path.startswith("/"):
        base = path.rsplit("/", 1)[-1]
        if ".so" in base:
            return "shared library (file)"
        return "executable/data file"
    if path.startswith("[anon"):
        return "named anonymous (runtime allocators)"
    if path.startswith("["):
        return "other " + path
    return "anonymous (malloc/mmap)"


def parse_maps(pid):
    text, err = read(PROC / str(pid) / "maps")
    if err:
        return None, err
    regions = []
    for line in text.splitlines():
        m = _MAPS.match(line)
        if m:
            s, e = int(m[1], 16), int(m[2], 16)
            regions.append((s, e, m[3], m[4].strip()))
    return regions, None


def _rollup(pid):
    text, err = read(PROC / str(pid) / "smaps_rollup")
    if err:
        return None
    d = {}
    for line in text.splitlines()[1:]:
        k, _, v = line.partition(":")
        p = v.split()
        if p:
            d[k] = int(p[0]) * 1024
    return d


def processes(top=20):
    out = ["== Per-process virtual memory (/proc/<pid>/maps) =="]
    cat_size = defaultdict(int)
    cat_count = defaultdict(int)
    perm_size = defaultdict(int)
    readable, denied, rows = 0, 0, []
    for p in PROC.iterdir():
        if not p.name.isdigit():
            continue
        regions, err = parse_maps(p.name)
        if err:
            if "denied" in err:
                denied += 1
            continue
        readable += 1
        for s, e, perms, path in regions:
            c = categorize(perms, path)
            cat_size[c] += e - s
            cat_count[c] += 1
            perm_size[perms[:3]] += e - s
        roll = _rollup(p.name)
        comm = (read(p / "comm")[0] or "?").strip()
        if roll:
            rows.append((p.name, comm, human(roll.get("Rss", 0)), human(roll.get("Pss", 0)),
                         human(sum(e - s for s, e, *_ in regions)), len(regions), roll.get("Pss", 0)))
    out.append(f"{readable} processes readable by uid {os.getuid()}, {denied} denied "
               "(other users; need root or matching uid).")
    out += ["", "Virtual address space by category (readable processes; virtual size, not RAM used):"]
    out += table([(c, cat_count[c], human(cat_size[c])) for c in
                  sorted(cat_size, key=lambda c: -cat_size[c])], ["category", "regions", "virtual size"])
    out += ["", "By permission (r/w/x):"]
    out += table([(k, human(v)) for k, v in sorted(perm_size.items(), key=lambda x: -x[1])],
                 ["perms", "virtual size"])
    rows.sort(key=lambda r: -r[6])
    out += ["", f"Top {top} by PSS (proportional set size: RAM actually used, shared pages split among sharers):"]
    out += table([r[:6] for r in rows[:top]], ["pid", "name", "RSS", "PSS", "virtual", "regions"])
    out += ["", "Note: virtual >> RSS is normal; most virtual space is reserved but never touched."]
    return out


def process_detail(pid):
    """Memory map of one process, for the 'inspect PID' box."""
    regions, err = parse_maps(pid)
    comm = (read(PROC / str(pid) / "comm")[0] or "?").strip()
    if err:
        return [f"pid {pid} ({comm}): {err}"]
    out = [f"pid {pid} ({comm}): {len(regions)} regions", ""]
    rows = [(f"{s:012x}-{e:012x}", perms, human(e - s), categorize(perms, path), path)
            for s, e, perms, path in regions]
    out += table(rows, ["address range", "perms", "size", "category", "mapped file"])
    return out


# --------------------------------------------------------------------- hardware
def hardware():
    out = ["== PCI devices (/sys/bus/pci/devices) =="]
    base = SYS / "bus/pci/devices"
    devs = sorted(base.glob("*")) if base.is_dir() else []
    if not devs:
        out.append("none found")
    rows, any_bar = [], False
    for d in devs:
        g = lambda n: (read(d / n)[0] or "").strip()
        drv = (d / "driver").resolve().name if (d / "driver").exists() else "-"
        bars = []
        res, _ = read(d / "resource")
        for i, line in enumerate((res or "").splitlines()[:6]):
            a, b, f = (int(x, 16) for x in line.split())
            if a == 0 and b == 0:
                continue
            bars.append(f"BAR{i} 0x{a:x}-0x{b:x}")
        any_bar = any_bar or bool(bars)
        rows.append((d.name, f"{g('vendor')[2:]}:{g('device')[2:]}", g("class")[:6], drv,
                     "; ".join(bars) or "(none)"))
    out += table(rows, ["address", "ven:dev", "class", "driver", "memory/IO BARs"])
    if devs and not any_bar:
        out.append("\nBAR addresses are zeroed for non-root (they show where each device's registers sit "
                   "in physical address space). Use Elevate to see them.")

    out += ["", "== I/O port ranges (/proc/ioports, legacy x86 port space 0x0000-0xffff) =="]
    text, err = read(PROC / "ioports")
    if err:
        out.append(err)
    else:
        lines = text.splitlines()
        out += lines[:60]
        if len(lines) > 60:
            out.append(f"... {len(lines) - 60} more lines")

    out += ["", "== Interrupt lines (/proc/interrupts), busiest first =="]
    text, err = read(PROC / "interrupts")
    if err:
        out.append(err)
    else:
        rows = []
        for line in text.splitlines()[1:]:
            parts = line.split()
            if not parts:
                continue
            counts = []
            i = 1
            while i < len(parts) and parts[i].isdigit():
                counts.append(int(parts[i])); i += 1
            rows.append((parts[0].rstrip(":"), sum(counts), " ".join(parts[i:])[:60]))
        rows.sort(key=lambda r: -r[1])
        out += table([(a, f"{n:,}", d) for a, n, d in rows[:25]], ["irq", "count", "type / device"])
    return out


SECTIONS = [("Summary", summary), ("Physical", physical), ("Kernel", kernel),
            ("Processes", processes), ("Hardware", hardware)]

if __name__ == "__main__":
    for name, fn in SECTIONS:
        print(f"\n######## {name}\n")
        print("\n".join(fn()))
