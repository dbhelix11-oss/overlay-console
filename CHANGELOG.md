# Changelog

## 2026-10-12
- Initial version: multi-window Tk GUI with 5 named pipes (`pipes/alpha`..`echo`, plain POSIX FIFOs), window geometry/visibility/pane-ratio persistence (`state.json`), seen/new text coloring with divider.
- Memory survey window (unprivileged, read-only): summary + access report, physical map (e820, buddyinfo, memory blocks), kernel symbols/modules, per-process address-space categories and PID inspector, PCI/ioports/interrupts hardware view; any tab can be sent to a pipe window.
- Deferred: offline file logging, privileged (root) helper, I/O capture.
