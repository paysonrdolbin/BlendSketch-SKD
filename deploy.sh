#!/usr/bin/env bash
# Build BlendSketch-SKD and publish it to the sandwich share for everyone in production.
#
#   ./deploy.sh              build, then copy to $SOFTWARE_DIR/BlendSketch-SKD
#   ./deploy.sh --no-build   copy the existing dist/BlendSketch as it is
#
# Also writes two launchers into 06_software: blendsketch.desktop (opens the app straight
# from the share) and install_blendsketch.desktop (adds it to the user's applications menu
# via packaging/install_launcher.sh). Artists never copy the app; they all run this one.

set -euo pipefail
cd "$(dirname "$0")"

SOFTWARE_DIR="${SOFTWARE_DIR:-/job/sandwich/06_software}"
DEST="$SOFTWARE_DIR/BlendSketch-SKD"
GROUP=sandwich

build=1
[ "${1:-}" = "--no-build" ] && build=0

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Commit your changes first, so the deployed build matches a commit:" >&2
    git status --short --untracked-files=no >&2
    exit 1
fi

if [ "$build" = 1 ]; then
    echo "Building $(git rev-parse --short HEAD)..."
    .venv/bin/python -m PyInstaller --noconfirm --log-level WARN BlendSketch.spec
fi
[ -x dist/BlendSketch/BlendSketch ] || { echo "No build in dist/BlendSketch." >&2; exit 1; }

# -a keeps the bundle's library symlinks; rsync swaps each file in whole, so anyone running
# the app while this runs keeps the old files until they restart.
mkdir -p "$DEST"
rsync -a --delete dist/BlendSketch/ "$DEST/"
install -m 775 packaging/install_launcher.sh "$DEST/install_launcher.sh"

cat > "$SOFTWARE_DIR/blendsketch.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=BlendSketch
GenericName=Sandwich Kwon Do Review Sketching
Exec=$DEST/BlendSketch %f
Icon=$DEST/_internal/packaging/icon.png
Categories=Graphics;
StartupWMClass=BlendSketch
StartupNotify=true
Terminal=false
EOF
cat > "$SOFTWARE_DIR/install_blendsketch.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Install BlendSketch
GenericName=Add BlendSketch to your applications menu
Exec=$DEST/install_launcher.sh
Icon=$DEST/_internal/packaging/icon.png
Terminal=true
StartupNotify=true
EOF

# Match the rest of 06_software: the sandwich group can run and redeploy everything.
chgrp -R "$GROUP" "$DEST" "$SOFTWARE_DIR/blendsketch.desktop" "$SOFTWARE_DIR/install_blendsketch.desktop"
chmod -R g+rwX "$DEST"
chmod 770 "$SOFTWARE_DIR/blendsketch.desktop" "$SOFTWARE_DIR/install_blendsketch.desktop"

echo "Deployed $(git rev-parse --short HEAD) to $DEST"
