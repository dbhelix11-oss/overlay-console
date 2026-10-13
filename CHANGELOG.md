# Changelog

## 2026-10-13
- User guide: documented the docked grid and `setup-access.sh`. Commits now use the GitHub noreply address.
- First four pipes (alpha..delta) now snap into one resizable 2x2 grid window (`occore/dock.py`); column dividers move together, row/column ratios saved in `state.json`. Further pipes stay floating windows.
- Added `setup-access.sh`: menu to make the pipes reachable from anywhere (~/oc symlink, $OC variable, per-pipe shell functions, per-directory symlinks, SSH / socat instructions); can reload ~/.bashrc when sourced.

## 2026-10-12 (docs)
- Added `USERGUIDE.html`: self-contained user's guide covering pipes, seen/new text, memory survey, root helper, I/O capture, config, troubleshooting, glossary.

## 2026-10-12 (step 4)
- I/O tab in the Memory window. Stage A: per-second deltas of interrupt / disk / network counters (`memsurvey/iocapture.py`), optionally mirrored to a pipe window.
- Stage B (root): mmiotrace via helper ops `mmio_start/poll/stop` (writes only tracefs `current_tracer`/`tracing_on`, always restores `nop`, refuses if another tracer is active); trace lines decoded and annotated with the owning PCI device/BAR.

## 2026-10-12 (step 3)
- Privileged read-only helper (`memsurvey/helper.py`, launched via pkexec by `memsurvey/elevate.py`) with a fixed op whitelist over JSON lines; Elevate / Drop root button in the Memory window.
- Elevated mode re-reads all tabs as root; new Privileged tab: /proc/kcore virtual map, vmallocinfo, kallsyms search, process memory hexdump, pagemap virtual->physical, /dev/mem hexdump.

## 2026-10-12
- Initial version: multi-window Tk GUI with 5 named pipes (`pipes/alpha`..`echo`, plain POSIX FIFOs), window geometry/visibility/pane-ratio persistence (`state.json`), seen/new text coloring with divider.
- Memory survey window (unprivileged, read-only): summary + access report, physical map (e820, buddyinfo, memory blocks), kernel symbols/modules, per-process address-space categories and PID inspector, PCI/ioports/interrupts hardware view; any tab can be sent to a pipe window.
- Deferred: offline file logging, privileged (root) helper, I/O capture.
