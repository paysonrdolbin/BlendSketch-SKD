"""Zip the blendsketch package for the updater:  python packaging/make_code_zip.py OUTDIR

Writes OUTDIR/blendsketch-code-<commit>.zip and OUTDIR/code-manifest.json, which
.github/workflows/publish-code.yml attaches to the `code-latest` release.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import bootstrap  # noqa: E402


def package_version(pkg: str) -> str:
    """__version__ from the package's __init__.py, without importing it."""
    import ast
    with open(os.path.join(pkg, "__init__.py"), encoding="utf-8") as f:
        for node in ast.parse(f.read()).body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "__version__" for t in node.targets):
                return ast.literal_eval(node.value)
    return "0"


def commit() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def make(out: str, pkg: str | None = None, sha: str | None = None, built: float | None = None) -> dict:
    """Zip pkg (default: this checkout's blendsketch package) into out and write its manifest."""
    os.makedirs(out, exist_ok=True)
    pkg = pkg or os.path.join(ROOT, "blendsketch")
    sha = sha or commit()
    name = f"blendsketch-code-{sha[:12]}.zip"
    path = os.path.join(out, name)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder, dirs, files in os.walk(pkg):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                if f.endswith(".py"):
                    full = os.path.join(folder, f)
                    rel = "blendsketch/" + os.path.relpath(full, pkg).replace(os.sep, "/")
                    with open(full, encoding="utf-8") as fh:
                        compile(fh.read(), rel, "exec")  # fail here, not on someone's machine
                    z.write(full, rel)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    manifest = {"version": f"{package_version(pkg)}+{sha[:7]}", "commit": sha, "built": built or time.time(),
                "requirements": bootstrap.requirements_hash(ROOT), "zip": name, "sha256": digest}
    with open(os.path.join(out, "code-manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    return manifest


if __name__ == "__main__":
    print(json.dumps(make(sys.argv[1]), indent=1))
