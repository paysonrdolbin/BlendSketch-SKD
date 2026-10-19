#!/usr/bin/env bash
# Add BlendSketch to your applications menu, running the shared copy this script ships with.
#
# deploy.sh copies this next to the app in 06_software/BlendSketch-SKD; run it from there, or
# double-click "Install BlendSketch" in 06_software. Nothing is copied to your machine except
# the menu entry, so redeploying the shared copy updates everyone.

set -euo pipefail

APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPLICATIONS="$HOME/.local/share/applications"
TARGET="$APPLICATIONS/blendsketch.desktop"

if [ ! -x "$APP_DIR/BlendSketch" ]; then
    echo "BlendSketch isn't next to this script ($APP_DIR/BlendSketch is missing)." >&2
    exit 1
fi

mkdir -p "$APPLICATIONS"
# Replace any earlier launcher, including the one a local build installed under another name.
rm -f "$TARGET" "$APPLICATIONS/BlendSketch.desktop"
cat > "$TARGET" <<EOF
[Desktop Entry]
Type=Application
Name=BlendSketch
GenericName=Sandwich Kwon Do Review Sketching
Exec=$APP_DIR/BlendSketch %f
Icon=$APP_DIR/_internal/packaging/icon.png
Categories=Graphics;
StartupWMClass=BlendSketch
StartupNotify=true
Terminal=false
EOF
echo "Installed $TARGET"

update-desktop-database "$APPLICATIONS" 2>/dev/null || true
echo "BlendSketch is in your applications menu."
