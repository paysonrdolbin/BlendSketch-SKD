"""Starts BlendSketch, preferring app code downloaded by the updater over the copy built into the app.

The packaged app is a runtime (Python, Qt, FFmpeg, numpy) plus the `blendsketch` package.
`BlendSketch --update` (blendsketch/update.py) downloads newer `blendsketch` code into the
user's data folder; this module, which ships with the runtime and is never updated itself,
puts that code ahead of the built-in copy when the app starts. Downloaded code is used only
when it was made for this runtime and is newer than the built-in copy; if it fails to
import, the app starts with the built-in copy instead. `--bundled` skips downloaded code.

Running from source never uses downloaded code.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import os
import sys

APP = "BlendSketch-SKD"  # its own data folder, so plain BlendSketch updates never load in SKD
ACTIVE: dict | None = None   # info about the downloaded code in use, if any
FALLBACK: str | None = None  # why downloaded code wasn't used, when it should have been
TEST_RUNTIME: dict | None = None  # tests pretend to be a packaged app with this


def data_dir() -> str:
    if os.environ.get("BLENDSKETCH_DATA"):
        return os.environ["BLENDSKETCH_DATA"]
    if sys.platform == "darwin":
        return os.path.expanduser(f"~/Library/Application Support/{APP}")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP)
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), APP)


def code_dir() -> str:
    return os.path.join(data_dir(), "code")


def read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_json(path: str, data: dict):
    """Write atomically, so a crash never leaves a half-written file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def requirements_hash(root: str) -> str:
    """Identifies a runtime: downloaded code runs only on an app built from the same requirements.txt."""
    import hashlib
    with open(os.path.join(root, "requirements.txt"), encoding="utf-8") as f:
        lines = sorted(ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#"))
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def runtime() -> dict | None:
    """What the packaged app was built from (written by BlendSketch.spec); None when running from source."""
    if TEST_RUNTIME is not None:
        return TEST_RUNTIME
    if not getattr(sys, "frozen", False):
        return None
    try:
        from blendsketch_runtime import BUILD
    except ImportError:
        return None
    return BUILD


def usable(info: dict | None, rt: dict | None) -> bool:
    """Whether downloaded code described by info can run on runtime rt, and is newer than its built-in code."""
    return bool(info and rt and info.get("requirements") == rt.get("requirements")
                and info.get("built", 0) > rt.get("built", 0))


class _Finder(importlib.abc.MetaPathFinder):
    """Finds `blendsketch` and its submodules in one folder, ahead of the app's built-in copy."""

    def __init__(self, root: str):
        self.root = root

    def find_spec(self, name, path=None, target=None):
        if name == "blendsketch":
            return importlib.machinery.PathFinder.find_spec(name, [self.root], target)
        if name.startswith("blendsketch."):
            return importlib.machinery.PathFinder.find_spec(name, path, target)
        return None


def _purge():
    for name in [m for m in sys.modules if m == "blendsketch" or m.startswith("blendsketch.")]:
        del sys.modules[name]


def activate(force: bool = False) -> dict | None:
    """Put downloaded code first on the import path, if there's usable code. Returns its info."""
    global ACTIVE, FALLBACK
    if not (getattr(sys, "frozen", False) or force) or "--bundled" in sys.argv:
        return None
    cur = read_json(os.path.join(code_dir(), "current.json"))
    if not usable(cur, runtime()):
        return None
    root = os.path.join(code_dir(), cur["dir"])
    finder = _Finder(root)
    sys.meta_path.insert(0, finder)
    _purge()
    try:
        import blendsketch.app  # noqa: F401  (fails here, not mid-session, if the code is broken)
    except Exception as e:  # noqa: BLE001
        sys.meta_path.remove(finder)
        _purge()
        FALLBACK = f"The downloaded update ({cur.get('version', '?')}) couldn't start: {e}. Using the built-in version."
        write_json(os.path.join(code_dir(), "bad.json"), {"commit": cur.get("commit")})
        os.remove(os.path.join(code_dir(), "current.json"))
        return None
    ACTIVE = cur
    return cur


def start():
    activate()
    from blendsketch.app import main
    main()
