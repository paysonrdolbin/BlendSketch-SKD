# BlendSketch

Frame-accurate review for footage, with Procreate-style layers that blend (Multiply, Screen, Overlay and ten more), a smudge brush, and shared playlist files.

**Layers.** Each frame's drawing lives in layers. A stroke is baked into the selected layer when you finish it, so a frame with hundreds of strokes draws as fast as one with a few. The blend mode and opacity belong to the layer. Picking a different blend mode (the blend buttons, or 1 2 3) starts a new layer in that mode on top, unless your selected layer is still empty, in which case it just switches. To change a layer's mode afterwards, click the mode button on its row in the layer list. Starting to draw on a frame where you have no layer makes one; **+** under the layer list (or Edit ▸ New Layer, ⇧⌘/Ctrl+Shift+N) adds another on top. You draw on (and reorder) your own layers only; clicking someone else's selects it so you can hide it, and your next stroke then starts a new layer of yours on top.

The **eraser** (E) erases pixels from your selected layer. The **smudge** tool (S) picks up the selected layer's pixels under the brush and drags them along; the opacity slider becomes its strength, and pen pressure scales it. Neither touches the footage or other layers, and using either on an empty layer shows a reminder of which tool you're on.

- **Plays ProRes directly.** FFmpeg (through PyAV) is built in, so ProRes 422/4444, DNxHR, H.264 (including 10-bit and 4:2:2), HEVC, image sequences' single frames and most other formats play with no proxies or conversion.
- **Playlists are files.** A `.bsplaylist` holds the clip list plus every layer (as a PNG cropped to what's painted) and comment. Footage is referenced by path, not copied in. A lightly marked-up playlist is tens of kilobytes; each heavily painted full frame adds a megabyte or two.
- **You choose when to save.** Changes stay in the app until you press ⌘/Ctrl+S (File ▸ Save); the title bar shows when there are unsaved changes, and closing, quitting or opening another playlist asks whether to save first.
- **Notes are never overwritten.** Each save merges with what's on disk, so if two people do save the same playlist, both sets of notes survive. Every note carries its author, and you can show or hide each person's notes.

## Running from source

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

Check that a file decodes (no window opens):

```bash
.venv/bin/python run.py --check /path/to/clip.mov
```

The packaged app takes the same flag, e.g. `BlendSketch.app/Contents/MacOS/BlendSketch --check clip.mov`.

## Building the app

PyInstaller builds for the OS it runs on.

```bash
.venv/bin/pip install pyinstaller
QT_QPA_PLATFORM=offscreen .venv/bin/python packaging/make_icons.py   # once
.venv/bin/pyinstaller --noconfirm BlendSketch.spec
```

Output: `dist/BlendSketch.app` on macOS, `dist/BlendSketch/BlendSketch.exe` on Windows, `dist/BlendSketch/BlendSketch` on Linux.
`.github/workflows/build.yml` builds all three on GitHub Actions (run it from the Actions tab, or push a `v*` tag).

The macOS build is ad-hoc signed, not notarized. On another Mac, the first launch needs right-click ▸ Open (or `xattr -dr com.apple.quarantine BlendSketch.app`).

## Updating

You only need to download the app once. After that, Help ▸ Check for Updates (or `BlendSketch --update` from a terminal) downloads just BlendSketch's own code, a few hundred kilobytes, and uses it from the next launch. The app also checks quietly a few seconds after it starts and says in the status bar when there's an update.

```bash
BlendSketch.app/Contents/MacOS/BlendSketch --update      # macOS
BlendSketch/BlendSketch --update                         # Linux (BlendSketch.exe on Windows)
```

- Every push to `main` publishes the code to the repository's `code-latest` release (`.github/workflows/publish-code.yml`), after the tests pass.
- Downloaded code lives in your user data folder (`~/Library/Application Support/BlendSketch/code` on macOS, `~/.local/share/BlendSketch/code` on Linux, `%LOCALAPPDATA%\BlendSketch\code` on Windows), never inside the app, so the app's signature stays intact.
- When an update needs a newer app, because `requirements.txt` changed or the code imports something the app doesn't include, it isn't installed; the app sends you to the latest full release instead. Pushing a `v*` tag builds the apps and publishes that release.
- If downloaded code fails to start, the app starts with its built-in code instead, and that update isn't offered again. `--bundled` always starts the built-in code; `--version` shows which code is running.
- Running from source never updates itself; use `git pull`.

## Syncing with BlendSketch

BlendSketch-SKD is BlendSketch plus SKD Open Playlist. To bring in BlendSketch's latest main:

    ./sync.sh             # fetch, merge, run the tests, push to origin
    ./sync.sh --no-push   # same, without pushing

Pushing publishes the code SKD's in-app updater installs. On a conflict the script stops with
the merge in progress; keep SKD's side of its own changes, commit, and run it again.

## Deploying to the sandwich share

    ./deploy.sh

builds the app and copies it to `/job/sandwich/06_software/BlendSketch-SKD`, which everyone
runs from. Artists add it to their applications menu once, by double-clicking
"Install BlendSketch" in `06_software` (or running `install_launcher.sh` in the app folder);
after that each redeploy reaches them on their next launch.

## How footage is found

Each clip stores a path relative to the playlist file and the absolute path it was added from. When a playlist opens, BlendSketch tries, in order:

1. the relative path (works whenever footage and playlist sit on the same server, whatever it's mounted as),
2. the absolute path,
3. the absolute path rewritten through **Preferences ▸ Path mappings**, for servers mounted at different roots, e.g. `/Volumes/prod = P:/ = /mnt/prod`.

Clips that still can't be found show as **Missing**. Double-click one (or File ▸ Relink Footage) and pick the file; BlendSketch then looks in that folder and the folder above it for the other missing clips by name and offers to relink them too. Relinking updates the playlist for everyone.

## Shared editing

Every layer, comment and clip is a record with an id, an author and a modified time. Only a layer's author paints on it, so two people's pixels never need merging. Deleting writes a tombstone, so a merge can't bring a deleted note back. When two versions of the same record meet, the newer one wins. Saving is manual and atomic (write to a temp file, then rename), so a reader never sees a half-written file.

Your name comes from Preferences (it defaults to your login name).

The inspector lists the current frame's **Layers**, top first like Procreate, and its **Comments**, grouped by person:

```
Layers                         Comments
☑ ● Layer 2   Screen 100%      ☑ dana (you)   1
☑ ● Layer 1   Normal 100% lead     ☑ Shadow should pool tighter…
☐ ● Layer 1   Multiply 70%     ☑ lead   1
```

- Click one of your layers to draw on it; the selected layer is bold. Its row's mode button and the Opacity slider under the list change it.
- Drag your layers to reorder them, or right-click ▸ Move up / Move down (⇧⌘/Ctrl+Shift+↑ ↓ for the selected layer). You can put yours anywhere among other people's layers, but only move your own.
- A layer's checkbox shows or hides it. For comments, a person's checkbox hides all of theirs, on every frame. Hiding is saved per playlist on your machine and never changes the file, so you can hide anyone's.
- Select several (Shift- or ⌘/Ctrl-click). Clicking the checkbox of any selected row shows or hides all of them. Press Delete/Backspace, or right-click ▸ Delete. Only your own layers and comments are deleted; other people's are skipped and can only be hidden. ⌘/Ctrl+Z undoes a delete, a stroke, an erase or a smudge. Edit ▸ Delete My Layers on This Frame follows the same rule.

Playlists saved by earlier versions stored every stroke separately. Opening one converts them: on each frame, consecutive strokes by the same person with the same blend mode become one layer. Save to keep the result. Earlier versions of the app can't open a converted playlist, so update everyone's app.

## Shortcuts

| Key | Action | Key | Action |
| --- | --- | --- | --- |
| P B S L A R O T E | Pen, brush, smudge, line, arrow, rect, ellipse, text, eraser | Space | Play / pause |
| I, or hold Alt/Option | Eyedropper | ← → | Previous / next frame |
| 1 2 3 | Draw in Normal, Multiply, Screen (a new layer) | Shift+← → | Previous / next noted frame |
| [ ] | Brush size | ↑ ↓ | Previous / next clip |
| H | Show / hide marks | Home / End | First / last frame |
| F, ⌘/Ctrl+1 | Fit, actual pixels | M | Mute |
| ⌘/Ctrl+Z, ⇧⌘/Ctrl+Shift+Z | Undo, redo | ⌘/Ctrl+E | Export frame with marks |
| ⌘/Ctrl+S | Save | | |

Scroll wheel or pinch zooms; trackpad scroll or middle-drag pans. Shift constrains lines to 45° and shapes to squares and circles.

## Layout

```
blendsketch/
  app.py        entry point, theme, --check, --update, --version
  window.py     main window: playlist, undo, autosave, merge polling, relinking, export
  canvas.py     viewer: zoom/pan, tablet pressure, layer compositing, live strokes, text, eyedropper loupe
  layers.py     layer records, baking strokes into pixels, PNG storage, converting old playlists
  strokes.py    stroke rendering, blend modes, smudge
  media.py      PyAV decoding thread, frame cache, audio playback and clock
  playlist.py   file format, merging, path resolution
  timeline.py   scrub bar
  widgets.py    blend, color (RGB/HSV/HSL/CMYK sliders, recent colors) and brush controls
  panels.py     playlist, authors, this frame, comments, preferences
  update.py     downloading and installing newer app code
bootstrap.py    starts the app, preferring downloaded code over the built-in copy
tests/          merge and updater tests, and an offscreen end-to-end test
prototype/      the original web version, for reference
```
