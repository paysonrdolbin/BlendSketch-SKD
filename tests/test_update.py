"""The code updater, offline: releases are folders served over file://.  Run: .venv/bin/python -m pytest tests"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "packaging"))

import pytest  # noqa: E402
from PySide6.QtCore import QCoreApplication, QUrl  # noqa: E402

import bootstrap  # noqa: E402
import make_code_zip  # noqa: E402
from blendsketch import update  # noqa: E402

APP = QCoreApplication.instance() or QCoreApplication([])
REQS = bootstrap.requirements_hash(ROOT)


@pytest.fixture
def env(monkeypatch):
    """A packaged app built an hour ago, an empty data folder and an empty release."""
    tmp = tempfile.mkdtemp()
    data, release = os.path.join(tmp, "data"), os.path.join(tmp, "release")
    os.makedirs(release)
    rt = {"version": "0.1.0+app", "commit": "a" * 40, "built": time.time() - 3600, "requirements": REQS}
    monkeypatch.setenv("BLENDSKETCH_DATA", data)
    monkeypatch.setenv("BLENDSKETCH_UPDATE_URL", QUrl.fromLocalFile(release).toString())
    monkeypatch.setattr(bootstrap, "TEST_RUNTIME", rt)
    monkeypatch.setattr(bootstrap, "ACTIVE", None)
    yield {"tmp": tmp, "data": data, "release": release, "runtime": rt}
    shutil.rmtree(tmp, ignore_errors=True)


def publish(env, version="9.9.9", sha="b" * 40, built=None, extra="", requirements=None) -> dict:
    """Publish a copy of this checkout's package, with __version__ = version and `extra` appended to __init__."""
    pkg = os.path.join(env["tmp"], f"pkg-{sha[:6]}", "blendsketch")
    shutil.rmtree(os.path.dirname(pkg), ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "blendsketch"), pkg, ignore=shutil.ignore_patterns("__pycache__"))
    init = os.path.join(pkg, "__init__.py")
    with open(init) as f:
        src = f.read().replace('__version__ = "0.1.0"', f'__version__ = "{version}"')
    with open(init, "w") as f:
        f.write(src + extra)
    m = make_code_zip.make(env["release"], pkg=pkg, sha=sha, built=built or time.time())
    if requirements:
        m["requirements"] = requirements
        with open(os.path.join(env["release"], "code-manifest.json"), "w") as f:
            json.dump(m, f)
    return m


def launch(env, code="import blendsketch; print(blendsketch.__version__, blendsketch.__file__)") -> list[str]:
    """Start a fresh interpreter the way the packaged app starts, and run code after bootstrap.activate."""
    script = (f"import sys; sys.path.insert(0, {ROOT!r}); import bootstrap; "
              f"bootstrap.TEST_RUNTIME = {env['runtime']!r}; bootstrap.activate(force=True); "
              f"print('FALLBACK', bootstrap.FALLBACK); {code}")
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         env=dict(os.environ, BLENDSKETCH_DATA=env["data"]), cwd=env["tmp"])
    assert out.returncode == 0, out.stderr
    return out.stdout.strip().splitlines()


def test_installs_and_next_launch_runs_the_update(env):
    publish(env)
    assert update.check()["state"] == "available"
    state, msg = update.update()
    assert state == "installed" and "9.9.9" in msg
    fallback, line = launch(env)
    assert fallback == "FALLBACK None"
    version, path = line.split(" ", 1)
    assert version == "9.9.9" and path.startswith(os.path.realpath(env["data"])) or path.startswith(env["data"])
    assert update.check()["state"] == "current", "nothing newer after installing"


def test_without_updates_the_built_in_code_runs(env):
    _, line = launch(env)
    assert line.split()[0] == "0.1.0" and not line.split()[1].startswith(env["data"])


def test_older_code_than_the_app_is_ignored(env):
    publish(env, built=env["runtime"]["built"] - 60)
    assert update.check()["state"] == "current"


def test_new_requirements_need_the_full_app(env):
    publish(env, requirements="different")
    st = update.check()
    assert st["state"] == "needs_full" and update.DOWNLOAD_PAGE in st["message"]
    assert not os.path.exists(os.path.join(bootstrap.code_dir(), "current.json"))


def test_imports_the_app_lacks_are_refused(env):
    publish(env, extra="\nimport module_the_app_does_not_have\n")
    with pytest.raises(update.UpdateError, match="module_the_app_does_not_have"):
        update.update()
    assert not os.path.exists(os.path.join(bootstrap.code_dir(), "current.json"))


def test_corrupt_download_is_refused(env):
    m = publish(env)
    with open(os.path.join(env["release"], m["zip"]), "ab") as f:
        f.write(b"junk")
    with pytest.raises(update.UpdateError, match="didn't download completely"):
        update.update()


def test_code_that_fails_to_start_falls_back_and_is_not_reinstalled(env):
    publish(env, extra="\nraise RuntimeError('boom')\n")
    assert update.update()[0] == "installed"
    fallback, line = launch(env)
    assert "boom" in fallback and line.split()[0] == "0.1.0", "the built-in code runs instead"
    assert update.check()["state"] == "bad", "the same broken update isn't offered again"
    publish(env, version="9.9.10", sha="c" * 40)
    assert update.check()["state"] == "available", "a newer one is"


def test_nothing_published_yet(env):
    st = update.check()
    assert st["state"] == "current" and "No updates have been published yet" in st["message"]


def test_downloads_over_https_with_redirects():
    """github.com answers release and raw downloads with a redirect to another host."""
    if os.environ.get("BLENDSKETCH_OFFLINE"):
        pytest.skip("offline")
    data = update.fetch(f"https://github.com/{update.REPO}/raw/HEAD/requirements.txt")
    assert b"PySide6" in data


def test_source_checkouts_never_update(monkeypatch):
    monkeypatch.setattr(bootstrap, "TEST_RUNTIME", None)
    assert update.check()["state"] == "source"


def test_window_worker_checks_and_installs_off_the_ui_thread(env):
    """Help ▸ Check for Updates runs the same steps in a thread (its own Qt network manager and event loop)."""
    from PySide6.QtCore import QEventLoop, QTimer

    from blendsketch.window import UpdateWorker
    publish(env)
    results = []
    for install in (False, True):
        w = UpdateWorker(install=install)
        loop = QEventLoop()
        w.done.connect(lambda r: (results.append(r), loop.quit()))
        QTimer.singleShot(20000, loop.quit)
        w.start()
        loop.exec()
        w.wait()
    assert [r["state"] for r in results] == ["available", "installed"], results
