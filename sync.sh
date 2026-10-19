#!/usr/bin/env bash
# Bring BlendSketch's latest main into BlendSketch-SKD and publish it.
#
#   ./sync.sh             fetch upstream, merge, run the tests, push to origin
#   ./sync.sh --no-push   stop after the merge and tests
#
# Pushing to origin/main publishes the code that SKD's in-app updater installs
# (.github/workflows/publish-code.yml), so nothing is pushed if the tests fail.
# On a merge conflict the merge is left in progress for you to resolve.

set -euo pipefail
cd "$(dirname "$0")"

push=1
[ "${1:-}" = "--no-push" ] && push=0

if ! git remote get-url upstream >/dev/null 2>&1; then
    git remote add upstream git@github.com:paysonrdolbin/BlendSketch.git
fi
if [ "$(git branch --show-current)" != main ]; then
    echo "Switch to main first (on $(git branch --show-current))." >&2
    exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Commit or stash your changes first:" >&2
    git status --short --untracked-files=no >&2
    exit 1
fi

git fetch upstream
git fetch origin
if [ -n "$(git rev-list HEAD..origin/main)" ]; then
    echo "origin/main has commits this copy doesn't; run 'git pull' first." >&2
    exit 1
fi

new=$(git rev-list --count HEAD..upstream/main)
if [ "$new" = 0 ]; then
    echo "Already up to date with BlendSketch."
    exit 0
fi
echo "Merging $new new BlendSketch commit(s):"
git log --oneline HEAD..upstream/main

before=$(git rev-parse HEAD)
if ! git merge --no-edit upstream/main; then
    cat >&2 <<'EOF'

The merge has conflicts. Keep SKD's side of its own changes (bootstrap.py APP,
update.py REPO, the SKD imports in window.py), then:
    git add <files> && git commit && ./sync.sh
Or give up on this merge with:  git merge --abort
EOF
    exit 1
fi

if [ -x .venv/bin/python ]; then
    echo "Running tests..."
    if ! QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q tests; then
        echo "Tests failed; nothing pushed. Fix and commit, then run ./sync.sh again," >&2
        echo "or undo the merge with:  git reset --hard $before" >&2
        exit 1
    fi
else
    echo "No .venv here; skipping tests."
fi

if ! git diff --quiet "$before" HEAD -- requirements.txt; then
    echo
    echo "requirements.txt changed: the in-app updater can't install this one. Rebuild the app:"
    echo "    .venv/bin/python -m PyInstaller --noconfirm BlendSketch.spec"
fi

if [ "$push" = 1 ]; then
    git push origin main
    echo "Pushed. SKD's update is published in about a minute (Help > Check for Updates)."
else
    echo "Merged locally; push with:  git push origin main"
fi
