"""Paths and user config. config.json (optional) can override the pipe names."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIPE_DIR = ROOT / "pipes"
STATE_FILE = ROOT / "state.json"
LOCK_FILE = ROOT / "overlayconsole.lock"
CONFIG_FILE = ROOT / "config.json"

DEFAULT_PIPE_NAMES = ["alpha", "bravo", "charlie", "delta", "epsilon"]


def pipe_names():
    try:
        names = json.loads(CONFIG_FILE.read_text()).get("pipes")
    except (OSError, ValueError):
        names = None
    if (isinstance(names, list) and names
            and all(isinstance(n, str) and n.isidentifier() for n in names)
            and len(set(names)) == len(names)):
        return names
    return list(DEFAULT_PIPE_NAMES)
