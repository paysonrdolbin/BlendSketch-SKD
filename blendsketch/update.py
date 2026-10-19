"""Updating BlendSketch's code without downloading the whole app again.

Every push to main publishes the `blendsketch` package as a small zip on the repository's
`code-latest` release (.github/workflows/publish-code.yml), with a manifest saying which
commit it is, when it was built and which runtime (requirements.txt) it needs. The updater
downloads the zip into the user's data folder; bootstrap.py runs it from the next launch.

An update is refused, with a pointer to the full download, when it needs a different
runtime: changed requirements, or an import the installed app doesn't include.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import zipfile

import bootstrap

from . import __version__

REPO = "paysonrdolbin/BlendSketch-SKD"
CHANNEL = "code-latest"
MANIFEST = "code-manifest.json"
DOWNLOAD_PAGE = f"https://github.com/{REPO}/releases/latest"


# Runs under the pipeline's Python (see skd.py), so its imports aren't the app's to provide.
OUTSIDE_APP = {"sg_helper.py"}


class UpdateError(Exception):
    pass


class NotPublished(UpdateError):
    pass


def base_url() -> str:
    return os.environ.get("BLENDSKETCH_UPDATE_URL") or f"https://github.com/{REPO}/releases/download/{CHANNEL}"


def fetch(url: str, timeout_ms: int = 60000) -> bytes:
    """Download url with Qt's network stack (it uses the system's certificates on every OS).
    Needs a Q(Core)Application; works in any thread."""
    from PySide6.QtCore import QEventLoop, QTimer, QUrl
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

    nam = QNetworkAccessManager()
    req = QNetworkRequest(QUrl(url))
    req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                     QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    req.setAttribute(QNetworkRequest.Attribute.CacheLoadControlAttribute, QNetworkRequest.CacheLoadControl.AlwaysNetwork)
    req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f"BlendSketch/{__version__} updater")
    reply = nam.get(req)
    loop = QEventLoop()
    reply.finished.connect(loop.quit)
    QTimer.singleShot(timeout_ms, reply.abort)
    if not reply.isFinished():
        loop.exec()
    try:
        if reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute) == 404 \
                or reply.error() == QNetworkReply.NetworkError.ContentNotFoundError:
            raise NotPublished(f"{url.rsplit('/', 1)[-1]} isn't on the update server.")
        if reply.error() != QNetworkReply.NetworkError.NoError:
            raise UpdateError(f"Couldn't download {url.rsplit('/', 1)[-1]}: {reply.errorString()}")
        return bytes(reply.readAll().data())
    finally:
        reply.deleteLater()


# ---------------------------------------------------------------- what's installed

def running() -> dict:
    """The code this process is running: downloaded, built into the app, or a source checkout."""
    if bootstrap.ACTIVE:
        return dict(bootstrap.ACTIVE, source="update")
    rt = bootstrap.runtime()
    if rt:
        return dict(rt, source="app")
    return {"version": __version__, "source": "source"}


def installed() -> dict | None:
    """Downloaded code that will run at the next launch, if any (it may not be running yet)."""
    cur = bootstrap.read_json(os.path.join(bootstrap.code_dir(), "current.json"))
    return cur if bootstrap.usable(cur, bootstrap.runtime()) else None


def describe(info: dict) -> str:
    v = info.get("version", __version__)
    return {"update": f"{v} (updated)", "app": f"{v} (built in)", "source": f"{v} (from source)"}.get(info.get("source"), v)


# ---------------------------------------------------------------- checking

def check() -> dict:
    """Ask GitHub for the latest code. Returns {"state", "message", "manifest"}, where state is
    "source", "current", "available", "needs_full" or "bad"."""
    rt = bootstrap.runtime()
    if rt is None:
        return {"state": "source", "manifest": None,
                "message": "BlendSketch is running from source. Update it with git pull."}
    try:
        m = json.loads(fetch(f"{base_url()}/{MANIFEST}"))
    except NotPublished:
        return {"state": "current", "manifest": None,
                "message": f"No updates have been published yet. You're running {describe(running())}."}
    except ValueError as e:
        raise UpdateError(f"The update information couldn't be read: {e}") from e
    newest = max([running(), installed() or {}], key=lambda i: i.get("built", 0))
    if m["commit"] == newest.get("commit") or m["built"] <= newest.get("built", 0):
        nxt = installed()
        pending = nxt is not None and nxt.get("commit") != running().get("commit")
        msg = f"BlendSketch is up to date ({m['version']})."
        if pending:
            msg += " Restart BlendSketch to use it."
        return {"state": "current", "manifest": m, "message": msg}
    bad = bootstrap.read_json(os.path.join(bootstrap.code_dir(), "bad.json")) or {}
    if bad.get("commit") == m["commit"]:
        return {"state": "bad", "manifest": m,
                "message": f"Update {m['version']} didn't start on this computer, so BlendSketch is waiting for a newer one."}
    if m["requirements"] != rt["requirements"]:
        return {"state": "needs_full", "manifest": m,
                "message": f"Update {m['version']} needs a newer version of the app itself. Download it once from "
                           f"{DOWNLOAD_PAGE}; after that, updates install here again."}
    return {"state": "available", "manifest": m, "message": f"Update {m['version']} is available."}


# ---------------------------------------------------------------- installing

def code_problems(root: str) -> list[str]:
    """Reasons the code under root can't run in this runtime: syntax errors, or imports it can't find."""
    problems = []
    for folder, _dirs, files in os.walk(root):
        for f in files:
            if not f.endswith(".py") or f in OUTSIDE_APP:
                continue
            path = os.path.join(folder, f)
            rel = os.path.relpath(path, root)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            try:
                tree = ast.parse(src, rel)
                compile(tree, rel, "exec")
            except SyntaxError as e:
                problems.append(f"{rel}: {e.msg} (line {e.lineno})")
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                for name in names:
                    if name == "blendsketch" or name.startswith("blendsketch.") or name in sys.modules:
                        continue
                    try:
                        found = importlib.util.find_spec(name) is not None
                    except (ImportError, ValueError):
                        found = False
                    if not found:
                        problems.append(f"{rel}: needs {name}, which this version of the app doesn't include")
    return problems


def _safe_extract(z: zipfile.ZipFile, dest: str):
    for name in z.namelist():
        target = os.path.realpath(os.path.join(dest, name))
        if not target.startswith(os.path.realpath(dest) + os.sep):
            raise UpdateError(f"The update contains an unexpected path: {name}")
    z.extractall(dest)


def install(m: dict) -> dict:
    """Download, check and install the code described by manifest m. It runs from the next launch."""
    data = fetch(f"{base_url()}/{m['zip']}")
    if hashlib.sha256(data).hexdigest() != m["sha256"]:
        raise UpdateError("The update didn't download completely. Try again.")
    root = bootstrap.code_dir()
    os.makedirs(root, exist_ok=True)
    name = f"{m['commit'][:12]}-{int(m['built'])}"
    tmp = os.path.join(root, f".{name}.{os.getpid()}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            _safe_extract(z, tmp)
        if not os.path.isfile(os.path.join(tmp, "blendsketch", "__init__.py")):
            raise UpdateError("The update doesn't contain BlendSketch's code.")
        problems = code_problems(tmp)
        if problems:
            raise UpdateError(f"Update {m['version']} needs a newer version of the app itself. Download it once from "
                              f"{DOWNLOAD_PAGE}.\n\n" + "\n".join(problems[:5]))
        final = os.path.join(root, name)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    info = dict(m, dir=name)
    bootstrap.write_json(os.path.join(final, "manifest.json"), m)
    bootstrap.write_json(os.path.join(root, "current.json"), info)
    _prune(root, keep={name, (bootstrap.ACTIVE or {}).get("dir")})
    return info


def _prune(root: str, keep: set):
    """Remove older downloads, keeping the one installed and the one this process is running."""
    for d in os.listdir(root):
        path = os.path.join(root, d)
        if os.path.isdir(path) and d not in keep:
            shutil.rmtree(path, ignore_errors=True)  # a file in use on Windows stays until next time


def update() -> tuple[str, str]:
    """Check and, if there's an update, install it. Returns (state, message); state is as check()
    returns, or "installed"."""
    st = check()
    if st["state"] != "available":
        return st["state"], st["message"]
    info = install(st["manifest"])
    return "installed", f"Updated to {info['version']}. Restart BlendSketch to use it."


# ---------------------------------------------------------------- command line

def cli(args: list[str]) -> int:
    """`BlendSketch --update` and `BlendSketch --version`."""
    from PySide6.QtCore import QCoreApplication

    _app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])  # noqa: F841 (Qt networking needs one)
    if "--version" in args:
        print(f"BlendSketch {describe(running())}")
        nxt = installed()
        if nxt and nxt.get("commit") != running().get("commit"):
            print(f"Update {nxt['version']} is installed and runs from the next launch.")
        return 0
    if bootstrap.FALLBACK:
        print(bootstrap.FALLBACK)
    print(f"Running BlendSketch {describe(running())}. Checking for updates…")
    try:
        state, message = update()
    except (UpdateError, OSError) as e:
        print(f"Update failed: {e}")
        return 1
    print(message)
    return 0 if state in ("installed", "current", "source") else 2
