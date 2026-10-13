# Changelog

## 2026-10-12 (step 3)
- Privileged read-only helper (`memsurvey/helper.py`, launched via pkexec by `memsurvey/elevate.py`) with a fixed op whitelist over JSON lines; Elevate / Drop root button in the Memory window.
- Elevated mode re-reads all tabs as root; new Privileged tab: /proc/kcore virtual map, vmallocinfo, kallsyms search, process memory hexdump, pagemap virtual->physical, /dev/mem hexdump.

## 2026-10-12
- Initial version: multi-window Tk GUI with 5 named pipes (`pipes/alpha`..`echo`, plain POSIX FIFOs), window geometry/visibility/pane-ratio persistence (`state.json`), seen/new text coloring with divider.
- Memory survey window (unprivileged, read-only): summary + access report, physical map (e820, buddyinfo, memory blocks), kernel symbols/modules, per-process address-space categories and PID inspector, PCI/ioports/interrupts hardware view; any tab can be sent to a pipe window.
- Deferred: offline file logging, privileged (root) helper, I/O capture.
