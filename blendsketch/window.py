"""Main window: playlist, viewer, transport and inspector, plus saving and merging."""

from __future__ import annotations

import copy
import os
import sys

from PySide6.QtCore import QEvent, QObject, QProcess, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressDialog, QPushButton, QSizePolicy, QSplitter, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
    QAbstractSpinBox,
)

import bootstrap

from . import APP_NAME, skd, update
from .canvas import Canvas
from .colors import author_color
from .icons import icon
from .media import MediaError, Player, VideoReader, frame_to_qimage, probe
from .layers import LayerStore, layer_order, make_layer, migrate
from .panels import ComposeBox, LayersPanel, NotesTree, PlaylistPanel, PrefsDialog, scroll_wrap
from .playlist import EXT, Playlist, PlaylistError, find_by_name, new_id, now
from .prefs import Prefs
from .skd_dialog import SKDOpenDialog, Worker
from .strokes import MAIN_BLENDS
from .timeline import Timeline
from .widgets import BlendPanel, BrushPanel, ColorPanel, section_label

HIDDEN_MARK = "#4a5060"
MEDIA_FILTER = "Footage (*.mov *.mp4 *.m4v *.mxf *.mkv *.avi *.webm *.png *.jpg *.jpeg *.tif *.tiff *.exr *.dpx);;All files (*)"
PLAYLIST_FILTER = f"BlendSketch playlist (*{EXT})"
UNDO_LIMIT = 100  # pixel undo keeps the changed area of each stroke, so keep the history bounded
TOOLS = [("pen", "Pen", "P"), ("brush", "Soft brush: best for Multiply shading and Screen glows", "B"),
         ("smudge", "Smudge: drag the pixels of your selected layer", "S"),
         ("line", "Line", "L"), ("arrow", "Arrow", "A"), ("rect", "Rectangle", "R"), ("ellipse", "Ellipse", "O"),
         ("text", "Text", "T"), ("eraser", "Eraser: erases from your selected layer", "E"), ("picker", "Eyedropper (or hold Alt/Option)", "I")]


def is_text_input(w) -> bool:
    return isinstance(w, (QLineEdit, QPlainTextEdit, QAbstractSpinBox))


class AltPicker(QObject):
    """Hold Alt/Option for a temporary eyedropper, like Photoshop."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win

    def eventFilter(self, obj, e):
        t = e.type()
        if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and e.key() == Qt.Key.Key_Alt and not e.isAutoRepeat():
            if is_text_input(QApplication.focusWidget()):
                return False
            if t == QEvent.Type.KeyPress:
                self.win.begin_temp_pick()
            else:
                self.win.end_temp_pick()
        elif t == QEvent.Type.ApplicationDeactivate:
            self.win.end_temp_pick()
        return False


class UpdateWorker(QThread):
    """Checks for (and optionally installs) an update off the UI thread."""

    done = Signal(dict)  # {"state", "message"}

    def __init__(self, install: bool, parent=None):
        super().__init__(parent)
        self.install = install

    def run(self):
        try:
            if self.install:
                state, message = update.update()
                r = {"state": state, "message": message}
            else:
                r = update.check()
        except (update.UpdateError, OSError, KeyError, ValueError) as e:
            r = {"state": "error", "message": str(e)}
        self.done.emit(r)


class MainWindow(QMainWindow):
    def __init__(self, prefs: Prefs):
        super().__init__()
        self.prefs = prefs
        self.settings = {
            "tool": "brush", "color": "#6f7fb8", "blend": "multiply", "opacity": 1.0, "pressure": True, "fill": False,
            "sizes": {"pen": 6, "brush": 80, "smudge": 60, "line": 5, "arrow": 5, "rect": 5, "ellipse": 5, "text": 8, "eraser": 30},
        }
        self.prev_tool = "brush"
        self.temp_pick = False
        self.pl: Playlist | None = None
        self.cur: str | None = None
        self.resolved: dict[str, str | None] = {}
        self.infos: dict[str, dict] = {}
        self.last_frame: dict[str, int] = {}
        # What this machine hides. View-only: never written to the playlist.
        self.hidden_layers: set[str] = set()
        self.hidden_comment_authors: set[str] = set()
        self.hidden_comments: set[str] = set()
        self.undo_stack: list = []
        self.redo_stack: list = []
        self.dirty = False
        self.store = LayerStore()                # every layer's pixels
        self.picked_layer: dict[tuple, str] = {}  # (item, frame, you) -> the layer you last clicked there
        self._pending_layer: dict | None = None   # made when a stroke started; recorded when it's finished
        self._opacity_before: dict | None = None
        self.by_frame: dict[int, list] = {}      # visible layers, bottom to top
        self.all_by_frame: dict[int, list] = {}  # every live layer, hidden or not
        self.player = Player(prefs.cache_mb)
        self._build_ui()
        self._build_menus()
        self._wire()
        self.set_tool("brush")
        self.color.set_hex(self.settings["color"])
        self._update_brush_ui()
        self.poll_timer = QTimer(self, interval=2000)
        self.poll_timer.timeout.connect(self._poll)
        self.poll_timer.start()
        self.cache_timer = QTimer(self, interval=400)
        self.cache_timer.timeout.connect(lambda: self.timeline.set_cached(self.player.cached_frames()))
        self.cache_timer.start()
        self.alt = AltPicker(self)
        QApplication.instance().installEventFilter(self.alt)
        g = prefs.geometry()
        if g is not None:
            self.restoreGeometry(g)
        else:
            self.resize(1480, 900)
        self._show_welcome()
        self._update_worker = None
        if bootstrap.FALLBACK:
            QTimer.singleShot(0, lambda: self.status.showMessage(bootstrap.FALLBACK, 15000))
        elif bootstrap.runtime() is not None:  # the packaged app: look for updates quietly
            QTimer.singleShot(5000, lambda: self.check_updates(quiet=True))

    # ================================================================ UI

    def _build_ui(self):
        self.setWindowTitle(APP_NAME)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

        self.playlist_panel = PlaylistPanel()
        self.playlist_panel.setMinimumWidth(230)
        split.addWidget(self.playlist_panel)

        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(8, 10, 8, 10)
        cl.setSpacing(8)
        mid = QHBoxLayout()
        mid.setSpacing(8)
        rail = QWidget()
        rail.setObjectName("Rail")
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(4, 6, 4, 6)
        rl.setSpacing(3)
        self.tool_buttons = {}
        group = QButtonGroup(self)
        for key, tip, sc in TOOLS:
            b = QToolButton()
            b.setIcon(icon(key))
            b.setIconSize(b.iconSize() * 1.15)
            b.setCheckable(True)
            b.setToolTip(f"{tip}  ({sc})")
            b.setObjectName("ToolButton")
            b.clicked.connect(lambda _=False, k=key: self.set_tool(k))
            group.addButton(b)
            rl.addWidget(b)
            self.tool_buttons[key] = b
            if key in ("smudge", "text", "eraser"):
                sep = QWidget()
                sep.setFixedHeight(1)
                sep.setObjectName("RailSep")
                rl.addWidget(sep)
        rl.addStretch(1)
        mid.addWidget(rail, 0, Qt.AlignmentFlag.AlignTop)

        self.stack = QStackedWidget()
        self.welcome = self._make_welcome()
        self.canvas = Canvas(self.settings)
        self.canvas.author = self.prefs.author
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(self.canvas)
        mid.addWidget(self.stack, 1)
        cl.addLayout(mid, 1)

        bar = QWidget()
        bar.setObjectName("Transport")
        tl = QVBoxLayout(bar)
        tl.setContentsMargins(10, 8, 10, 10)
        tl.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(2)

        def tbtn(name, tip, fn):
            b = QToolButton()
            b.setIcon(icon(name))
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
            return b

        self.transport_buttons = [
            tbtn("first", "First frame (Home)", lambda: self.goto(0)),
            tbtn("prevmark", "Previous noted frame (Shift+←)", lambda: self.jump_mark(-1)),
            tbtn("prev", "Previous frame (←)", lambda: self.step(-1)),
        ]
        self.play_btn = tbtn("play", "Play / pause (Space)", self.toggle_play)
        self.transport_buttons += [
            self.play_btn,
            tbtn("next", "Next frame (→)", lambda: self.step(1)),
            tbtn("nextmark", "Next noted frame (Shift+→)", lambda: self.jump_mark(1)),
            tbtn("last", "Last frame (End)", lambda: self.goto(self.player.frames - 1)),
        ]
        # Labels whose text changes during playback get fixed or ignored widths, so they can
        # never resize the viewer (which would also force a new decode size mid-shot).
        self.tc = QLabel("00:00:00:00")
        self.tc.setObjectName("Timecode")
        self.tc.ensurePolished()
        self.tc.setFixedWidth(self.tc.fontMetrics().horizontalAdvance("00:00:00:00   00000 / 00000") + 22)
        row.addSpacing(8)
        row.addWidget(self.tc)
        self.clip_info = QLabel("")
        self.clip_info.setObjectName("Muted")
        self.clip_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addSpacing(10)
        row.addWidget(self.clip_info, 1)
        self.zoom_label = QLabel("Fit")
        self.zoom_label.setObjectName("Muted")
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.zoom_label.ensurePolished()
        self.zoom_label.setFixedWidth(self.zoom_label.fontMetrics().horizontalAdvance("6400% of fit") + 8)
        row.addWidget(self.zoom_label)
        tbtn("fit", "Fit to view (F)", self.canvas.fit)
        self.mute_btn = QToolButton()
        self.mute_btn.setCheckable(True)
        self.mute_btn.setIcon(icon("sound"))
        self.mute_btn.setToolTip("Mute (M)")
        self.mute_btn.toggled.connect(self.set_muted)
        row.addWidget(self.mute_btn)
        tbtn("camera", "Export frame with marks (⌘/Ctrl+E)", self.export_snapshot)
        tl.addLayout(row)
        self.timeline = Timeline()
        tl.addWidget(self.timeline)
        cl.addWidget(bar)
        split.addWidget(center)

        insp = QWidget()
        insp.setObjectName("Inspector")
        il = QVBoxLayout(insp)
        il.setContentsMargins(14, 12, 14, 12)
        il.setSpacing(10)
        self.blend = BlendPanel()
        self.color = ColorPanel(self.prefs)
        self.brush = BrushPanel()
        self.layers_panel = LayersPanel()
        self.comment_tree = NotesTree("comments")
        self.compose = ComposeBox()
        comments_box = QWidget()
        cbl = QVBoxLayout(comments_box)
        cbl.setContentsMargins(0, 0, 0, 0)
        cbl.setSpacing(6)
        cbl.addWidget(self.comment_tree)
        cbl.addWidget(self.compose)
        for title, w in (("Layer blend mode", self.blend), ("Color", self.color), ("Brush", self.brush),
                         ("Layers on this frame", self.layers_panel), ("Comments on this frame", comments_box)):
            il.addWidget(section_label(title))
            il.addWidget(w)
            sep = QWidget()
            sep.setObjectName("Sep")
            sep.setFixedHeight(1)
            il.addWidget(sep)
        il.addStretch(1)
        scroll = scroll_wrap(insp)
        scroll.setMinimumWidth(320)
        split.addWidget(scroll)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([260, 900, 340])

        self.status = self.statusBar()
        self.author_btn = QPushButton()
        self.author_btn.setObjectName("AuthorButton")
        self.author_btn.setToolTip("Change your name in Preferences")
        self.author_btn.clicked.connect(self.open_prefs)
        self.status.addPermanentWidget(self.author_btn)
        self._update_author_btn()

    def _make_welcome(self) -> QWidget:
        w = QWidget()
        w.setObjectName("Welcome")
        lay = QVBoxLayout(w)
        lay.addStretch(1)
        title = QLabel(APP_NAME)
        title.setObjectName("WelcomeTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("Review footage frame by frame and draw with blend modes.\nPlaylists keep your notes; footage stays where it is.")
        sub.setObjectName("Muted")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        lay.addWidget(sub)
        row = QHBoxLayout()
        row.addStretch(1)
        sg = QPushButton("SKD Open Playlist…")
        sg.setObjectName("Primary")
        sg.clicked.connect(self.skd_open_dialog)
        new = QPushButton("New playlist…")
        new.clicked.connect(self.new_playlist)
        op = QPushButton("Open playlist…")
        op.clicked.connect(self.open_dialog)
        row.addWidget(sg)
        row.addWidget(new)
        row.addWidget(op)
        row.addStretch(1)
        lay.addSpacing(14)
        lay.addLayout(row)
        self.recent_box = QVBoxLayout()
        self.recent_box.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(18)
        lay.addLayout(self.recent_box)
        lay.addStretch(2)
        return w

    def _fill_recent(self):
        while self.recent_box.count():
            it = self.recent_box.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        files = [p for p in self.prefs.recent_files]
        if not files:
            return
        lab = QLabel("RECENT")
        lab.setObjectName("SectionLabel")
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.recent_box.addWidget(lab)
        for p in files[:8]:
            b = QPushButton(os.path.basename(p))
            b.setObjectName("RecentLink")
            b.setToolTip(p)
            b.clicked.connect(lambda _=False, pp=p: self.open_playlist(pp))
            self.recent_box.addWidget(b)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")

        def act(menu, text, fn, sc=None):
            a = QAction(text, self)
            if sc:
                a.setShortcut(QKeySequence(sc))
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        act(m, "SKD Open Playlist…", self.skd_open_dialog, "Ctrl+Shift+O")
        m.addSeparator()
        act(m, "New Playlist…", self.new_playlist, QKeySequence.StandardKey.New)
        act(m, "Open Playlist…", self.open_dialog, QKeySequence.StandardKey.Open)
        self.recent_menu = m.addMenu("Open Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        m.addSeparator()
        self.a_add = act(m, "Add Footage…", self.add_media_dialog, "Ctrl+Shift+A")
        self.a_relink = act(m, "Relink Footage…", lambda: self.cur and self.relink(self.cur), "Ctrl+Shift+L")
        m.addSeparator()
        self.a_save = act(m, "Save", self.save, QKeySequence.StandardKey.Save)
        act(m, "Export Frame with Marks…", self.export_snapshot, "Ctrl+E")
        act(m, "Copy Frame with Marks", self.copy_snapshot, "Ctrl+Shift+C")
        m.addSeparator()
        pref = act(m, "Preferences…", self.open_prefs, QKeySequence.StandardKey.Preferences)
        pref.setMenuRole(QAction.MenuRole.PreferencesRole)

        e = mb.addMenu("&Edit")
        self.a_undo = act(e, "Undo", self.undo, QKeySequence.StandardKey.Undo)
        self.a_redo = act(e, "Redo", self.redo, QKeySequence.StandardKey.Redo)
        e.addSeparator()
        act(e, "New Layer", self.new_layer, "Ctrl+Shift+N")
        act(e, "Move Layer Up", lambda: self._step_active(-1), "Ctrl+Shift+Up")
        act(e, "Move Layer Down", lambda: self._step_active(1), "Ctrl+Shift+Down")
        act(e, "Delete My Layers on This Frame", self.clear_mine, None)

        t = mb.addMenu("&Tools")
        for key, tip, sc in TOOLS:
            act(t, tip.split(":")[0].split(" (")[0], lambda _=False, k=key: self.set_tool(k), sc)
        t.addSeparator()
        for i, key in enumerate(MAIN_BLENDS):
            act(t, f"Blend: {key.title()}", lambda _=False, k=key: self.set_blend(k), str(i + 1))
        act(t, "Smaller Brush", lambda: self.nudge_size(1 / 1.2), "[")
        act(t, "Larger Brush", lambda: self.nudge_size(1.2), "]")

        v = mb.addMenu("&View")
        self.a_marks = act(v, "Show Marks", self.toggle_marks, "H")
        self.a_marks.setCheckable(True)
        self.a_marks.setChecked(True)
        act(v, "Fit to View", self.canvas.fit, "F")
        act(v, "Actual Pixels", self.canvas.set_actual_pixels, "Ctrl+1")
        act(v, "Zoom In", lambda: self.canvas.zoom_at(1.25, self.canvas.rect().center().toPointF()), QKeySequence.StandardKey.ZoomIn)
        act(v, "Zoom Out", lambda: self.canvas.zoom_at(0.8, self.canvas.rect().center().toPointF()), QKeySequence.StandardKey.ZoomOut)

        p = mb.addMenu("&Playback")
        act(p, "Play / Pause", self.toggle_play, "Space")
        act(p, "Next Frame", lambda: self.step(1), "Right")
        act(p, "Previous Frame", lambda: self.step(-1), "Left")
        act(p, "Next Noted Frame", lambda: self.jump_mark(1), "Shift+Right")
        act(p, "Previous Noted Frame", lambda: self.jump_mark(-1), "Shift+Left")
        act(p, "First Frame", lambda: self.goto(0), "Home")
        act(p, "Last Frame", lambda: self.goto(self.player.frames - 1), "End")
        act(p, "Next Clip", lambda: self.step_clip(1), "Down")
        act(p, "Previous Clip", lambda: self.step_clip(-1), "Up")
        self.a_mute = act(p, "Mute", lambda: self.mute_btn.toggle(), "M")

        h = mb.addMenu("&Help")
        act(h, "Check for Updates…", lambda: self.check_updates(quiet=False)).setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
        act(h, f"About {APP_NAME}", self.about).setMenuRole(QAction.MenuRole.AboutRole)

    def _fill_recent_menu(self):
        self.recent_menu.clear()
        for path in self.prefs.recent_files:
            a = self.recent_menu.addAction(path)
            a.triggered.connect(lambda _=False, pp=path: self.open_playlist(pp))
        if not self.prefs.recent_files:
            self.recent_menu.addAction("No recent playlists").setEnabled(False)

    def _wire(self):
        self.player.frameChanged.connect(self._on_frame)
        self.player.imageReady.connect(self._on_image)
        self.player.playingChanged.connect(self._on_playing)
        self.player.error.connect(lambda m: self.status.showMessage(m, 6000))
        self.canvas.decodeSizeWanted.connect(self.player.set_size)
        self.canvas.strokeCommitted.connect(self.add_stroke)
        self.canvas.layerWanted.connect(self._begin_layer)
        self.canvas.strokeCancelled.connect(self._drop_pending_layer)
        self.canvas.colorPicked.connect(self._picked)
        self.canvas.zoomChanged.connect(lambda z: self.zoom_label.setText("Fit" if abs(z - 1) < 1e-3 else f"{z * 100:.0f}% of fit"))
        self.timeline.seek.connect(self.goto)
        self.blend.changed.connect(self.set_blend)
        self.color.colorChanged.connect(self._color_changed)
        self.color.pickToggled.connect(lambda on: self.set_tool("picker") if on else (self.settings["tool"] == "picker" and self.set_tool(self.prev_tool)))
        self.brush.sizeChanged.connect(self._size_changed)
        self.brush.opacityChanged.connect(self._opacity_changed)
        self.brush.pressureChanged.connect(lambda v: self.settings.__setitem__("pressure", v))
        self.brush.fillChanged.connect(lambda v: self.settings.__setitem__("fill", v))
        lp = self.layers_panel
        lp.tree.layerPicked.connect(self.pick_layer)
        lp.tree.itemToggled.connect(lambda ids, on: self._items_toggled("layers", ids, on))
        lp.tree.deleteRequested.connect(lambda ids: self.delete_records("layers", ids))
        lp.tree.moveRequested.connect(self.move_layers)
        lp.tree.stepRequested.connect(self.step_layers)
        lp.tree.blendRequested.connect(self.set_layer_blend)
        lp.add_btn.clicked.connect(self.new_layer)
        lp.opacity.sliderPressed.connect(self._opacity_pressed)
        lp.opacity.valueChanged.connect(self._opacity_value)
        lp.opacity.sliderReleased.connect(self._opacity_released)
        self.comment_tree.authorToggled.connect(self._author_toggled)
        self.comment_tree.itemToggled.connect(lambda ids, on: self._items_toggled("comments", ids, on))
        self.comment_tree.deleteRequested.connect(lambda ids: self.delete_records("comments", ids))
        self.compose.addRequested.connect(self.add_comment)
        pp = self.playlist_panel
        pp.itemSelected.connect(self.open_item)
        pp.orderChanged.connect(self._reordered)
        pp.addRequested.connect(self.add_media_dialog)
        pp.removeRequested.connect(self.remove_item)
        pp.relinkRequested.connect(self.relink)
        pp.renameRequested.connect(self._rename)
        pp.filesDropped.connect(self.add_media)
        pp.thumbReady.connect(self._refresh_playlist)

    # ================================================================ tools & settings

    def set_tool(self, key: str):
        if key == "picker" and self.settings["tool"] != "picker":
            self.prev_tool = self.settings["tool"]
        if key != "picker":
            self.temp_pick = False
        if self.settings["tool"] == "text" and key != "text":
            self.canvas._commit_text()
        self.settings["tool"] = key
        for k, b in self.tool_buttons.items():
            b.setChecked(k == key)
        self.color.pick_btn.blockSignals(True)
        self.color.pick_btn.setChecked(key == "picker")
        self.color.pick_btn.blockSignals(False)
        cursors = {"text": Qt.CursorShape.IBeamCursor, "pen": Qt.CursorShape.BlankCursor, "brush": Qt.CursorShape.BlankCursor,
                   "smudge": Qt.CursorShape.BlankCursor,
                   "eraser": Qt.CursorShape.BlankCursor}
        self.canvas.setCursor(cursors.get(key, Qt.CursorShape.CrossCursor))
        self.canvas.refresh_pick()
        self._update_brush_ui()

    def begin_temp_pick(self):
        if self.settings["tool"] != "picker" and self.canvas.live is None and self.stack.currentWidget() is self.canvas:
            self.temp_pick = True
            self.set_tool("picker")

    def end_temp_pick(self):
        if self.temp_pick:
            self.temp_pick = False
            self.set_tool(self.prev_tool)

    def _size_tool(self):
        t = self.settings["tool"]
        return self.prev_tool if t == "picker" else t

    def _update_brush_ui(self):
        t = self._size_tool()
        self.brush.set_strength_label(t == "smudge")
        self.brush.set_values(self.settings["sizes"][t], self.settings["opacity"], self.settings["pressure"], self.settings["fill"])
        self.blend.set_preview(self.settings["color"], self.settings["opacity"])

    def _size_changed(self, v):
        self.settings["sizes"][self._size_tool()] = v
        self.canvas.update()

    def nudge_size(self, k):
        t = self._size_tool()
        v = self.settings["sizes"][t]
        self.settings["sizes"][t] = max(1, min(300, round(v / 1.2) if k < 1 else round(v * 1.2 + 0.5)))
        self._update_brush_ui()
        self.canvas.update()

    def _opacity_changed(self, v):
        self.settings["opacity"] = v
        self.blend.set_preview(self.settings["color"], v)

    def set_blend(self, key):
        """Draw in another blend mode. Like Procreate, a different mode means a new layer on top;
        an empty selected layer just switches. A layer's own mode button changes the whole layer."""
        self.settings["blend"] = key
        self.blend.set_mode(key)
        active = self._active_layer()
        if active is None or active["blend"] == key:
            return  # with no layer of yours here, the next stroke makes one in this mode
        if self.store.is_empty(active, *self._size()):
            self._set_layer(active, blend=key)
        else:
            self.new_layer()

    def set_layer_blend(self, lid: str, key: str):
        """Change one of your layers' blend mode (the mode button on its row)."""
        rec = next((r for r in self.all_by_frame.get(self._frame(), []) if r["id"] == lid), None)
        if rec is None or rec["blend"] == key:
            return
        if rec["author"] != self.prefs.author:
            self.status.showMessage("Only your own layers' blend modes can be changed.", 4000)
            return
        self._set_layer(rec, blend=key)

    def _color_changed(self, hex_):
        self.settings["color"] = hex_
        self.blend.set_preview(hex_, self.settings["opacity"])

    def _picked(self, hex_):
        self.color.set_hex(hex_, emit=True)
        self.color.render_recents(self.prefs.push_color(hex_))
        self.status.showMessage(f"Picked {hex_}", 2500)
        self.temp_pick = False
        self.set_tool(self.prev_tool)

    def toggle_marks(self):
        self.canvas.show_marks = self.a_marks.isChecked()
        self.canvas.invalidate()

    def set_muted(self, on):
        self.mute_btn.setIcon(icon("mute" if on else "sound"))
        self.player.set_muted(on)

    # ================================================================ playlists

    def _show_welcome(self):
        self._fill_recent()
        self.stack.setCurrentWidget(self.welcome)
        self.playlist_panel.setEnabled(False)
        self._set_enabled(False)

    def _set_enabled(self, on: bool):
        for w in (self.a_add, self.a_relink, self.a_save):
            w.setEnabled(on)
        for b in self.transport_buttons:
            b.setEnabled(on and bool(self.player.info) and self.player.frames > 1)

    def new_playlist(self):
        start = self.pl.folder if self.pl else os.path.expanduser("~")
        path, _ = QFileDialog.getSaveFileName(self, "New playlist", os.path.join(start, f"Review{EXT}"), PLAYLIST_FILTER)
        if not path:
            return
        if not path.endswith(EXT):
            path += EXT
        if not self.maybe_save():  # asked only once a new file is chosen
            return
        try:
            pl = Playlist.create(path, os.path.splitext(os.path.basename(path))[0], self.prefs.author)
        except PlaylistError as e:
            QMessageBox.warning(self, "Couldn't create playlist", str(e))
            return
        self._load_playlist(pl)

    def open_dialog(self):
        start = self.pl.folder if self.pl else os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, "Open playlist", start, PLAYLIST_FILTER + ";;All files (*)")
        if path:
            self.open_playlist(path)

    def open_playlist(self, path: str):
        if not self.maybe_save():
            return
        try:
            pl = Playlist.open(path)
        except PlaylistError as e:
            QMessageBox.warning(self, "Couldn't open playlist", str(e))
            if not os.path.exists(path):
                self.prefs.forget_file(path)
                self._fill_recent()
            return
        self._load_playlist(pl)

    # ---------------------------------------------------------------- SKD (ShotGrid)

    def skd_open_dialog(self):
        dlg = SKDOpenDialog(self.prefs, self)
        if dlg.exec() == SKDOpenDialog.DialogCode.Accepted and dlg.chosen:
            self.open_skd_playlist(dlg.chosen.id)

    def open_skd_playlist(self, playlist_id: int):
        """Fetch a ShotGrid playlist's footage in the background, then open its shared notes file."""
        if not self.maybe_save():
            return
        progress = QProgressDialog("Reading the playlist from ShotGrid…", "Cancel", 0, 0, self)
        progress.setWindowTitle("SKD Open Playlist")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setAutoReset(False)
        progress.setAutoClose(False)
        progress.setMinimumDuration(0)
        progress.setMinimumWidth(460)
        worker = Worker(lambda report, cancelled: skd.prepare(playlist_id, report, cancelled), self)

        def report(text, done, total):
            progress.setLabelText(text)
            progress.setMaximum(1000 if total else 0)
            if total:
                progress.setValue(min(1000, int(done * 1000 / total)))

        worker.progress.connect(report)
        worker.finished.connect(worker.deleteLater)  # only once the thread has stopped
        progress.canceled.connect(worker.cancel)
        worker.succeeded.connect(lambda r: (progress.close(), self._skd_loaded(r)))
        worker.failed.connect(lambda msg: (progress.close(), QMessageBox.warning(self, "Couldn't open the playlist", msg)))
        worker.start()
        progress.show()

    def _skd_loaded(self, r: dict | None):
        if r is None:
            self.status.showMessage("Cancelled", 3000)
            return
        sg_pl = r["playlist"]
        path = skd.notes_path(sg_pl["id"])
        try:
            skd.ensure_review_dir()
            if os.path.exists(path):
                pl = Playlist.open(path)
            else:
                pl = Playlist.create(path, sg_pl["code"], self.prefs.author, pid=f"sg_playlist_{sg_pl['id']}")
        except (PlaylistError, OSError) as e:
            QMessageBox.warning(self, "Couldn't open the review notes", f"{e}\n\nNotes for this playlist live in {path}.")
            return
        changed = skd.sync(pl, r["versions"], r["media"], self.prefs.mappings, self.prefs.author)
        if sg_pl["code"] and pl.name != sg_pl["code"]:
            pl.rename(sg_pl["code"])
            changed = True
        if changed:
            try:
                pl.save()  # the clip list is shared too, so the next person opens the same clips
            except PlaylistError as e:
                QMessageBox.warning(self, "Couldn't save the clip list", str(e))
        self._load_playlist(pl)
        if r["skipped"]:
            n = len(r["skipped"])
            QMessageBox.information(self, "Some Versions have no footage",
                                    f"{n} Version{'s' if n != 1 else ''} couldn't be added:\n\n" + "\n".join(r["skipped"]))

    def _load_playlist(self, pl: Playlist):
        self.player.unload()
        self.pl = pl
        self.cur = None
        self.undo_stack, self.redo_stack = [], []
        self.dirty = False
        self.store = LayerStore()
        self.canvas.set_store(self.store)
        self.picked_layer = {}
        view = self.prefs.view_state(pl.data["id"])
        self.hidden_layers = set(view.get("layers", []))
        self.hidden_comment_authors = set(view.get("comment_authors", []))
        self.hidden_comments = set(view.get("comments", []))
        self.prefs.push_file(pl.path)
        self.playlist_panel.setEnabled(True)
        self.playlist_panel.set_playlist(pl.name, pl.path)
        self._resolve_all()
        converted = self._migrate()
        self._set_enabled(True)
        self.stack.setCurrentWidget(self.canvas)
        items = pl.items()
        missing = [i for i in items if not self.resolved.get(i["id"])]
        self._refresh_playlist()
        if items:
            self.open_item(items[0]["id"])
        else:
            self._no_item("This playlist is empty.\nAdd footage with “Add media”, or drop files on the playlist.")
        self._update_title()
        if missing:
            self.status.showMessage(f"{len(missing)} clip{'s' if len(missing) != 1 else ''} couldn't be found. Select one and relink it.", 8000)
        elif converted:
            self.status.showMessage(f"Converted this playlist's strokes into {converted} layer{'s' if converted != 1 else ''}. "
                                    "Save to keep them.", 8000)

    def _migrate(self) -> int:
        """Bake strokes from version-1 playlists into layers. Returns the number of layers made."""
        made = 0
        for it in self.pl.items():
            w, h = self._size(it["id"])
            if w and h and any(not r.get("deleted") for r in it.get("strokes", {}).values()):
                made += migrate(it, w, h)
        if made:
            self.dirty = True
        return made

    def _resolve_all(self):
        maps = self.prefs.mappings
        self.resolved = {i["id"]: self.pl.resolve(i, maps) for i in self.pl.items()}

    def _update_title(self):
        if self.pl:
            # [*] shows the platform's unsaved-changes marker (a dot in the close button on macOS)
            self.setWindowTitle(f"{self.pl.name}[*] — {APP_NAME}")
            self.setWindowFilePath(self.pl.path)
            self.setWindowModified(self.dirty)
        else:
            self.setWindowTitle(APP_NAME)

    def _rename(self, name):
        if self.pl and name and name != self.pl.name:
            self.pl.rename(name)
            self._changed()

    def _refresh_playlist(self):
        if not self.pl:
            return
        rows = []
        for it in self.pl.items():
            iid = it["id"]
            info = self.infos.get(iid) or it.get("info") or {}
            path = self.resolved.get(iid)
            n = len(Playlist.live(it.get("layers", {}))) + len(Playlist.live(it["comments"]))
            detail = f"{info.get('width', '?')}×{info.get('height', '?')}  {info.get('fps', 0):g} fps\n{info.get('frames', '?')} fr"
            rows.append({"id": iid, "label": it["label"], "detail": detail, "missing": path is None,
                         "path": path, "frames": info.get("frames", 1), "notes": n})
        self.playlist_panel.set_items(rows, self.cur)

    def _reordered(self, ids):
        if self.pl:
            self.pl.set_order(ids)
            self._changed(refresh_list=False)

    # ================================================================ items

    def _no_item(self, message: str):
        self.player.unload()
        self.canvas.set_source(0, 0)
        self.canvas.message = message
        self.canvas.set_layers([], None)
        self.timeline.set_frames(1)
        self.timeline.set_marks({}, set())
        self.clip_info.setText("")
        self.tc.setText("00:00:00:00")
        self._set_enabled(True)
        self._refresh_notes()

    def open_item(self, iid: str):
        if not self.pl or iid == self.cur and self.player.info:
            return
        if self.cur and self.player.info:
            self.last_frame[self.cur] = self.player.frame
        item = self.pl.item(iid)
        if not item:
            return
        self.cur = iid
        path = self.resolved.get(iid)
        if not path:
            ref = item["media"]
            self._no_item(f"Footage not found: {ref.get('filename')}\n\nLast known location:\n{ref.get('absolute')}\n\n"
                          f"Use File ▸ Relink Footage (⌘/Ctrl+Shift+L) or double-click the clip in the playlist.")
            return
        try:
            info = probe(path)
        except MediaError as e:
            self._no_item(f"Couldn't read {os.path.basename(path)}.\n{e}")
            return
        self.infos[iid] = info
        self.canvas.message = ""
        self.canvas.set_source(info["width"], info["height"])
        self.player.load(path, info, self.canvas.wanted_decode_size())
        self.timeline.set_frames(info["frames"])
        self.clip_info.setText(f"{item['label']}   {info['width']}×{info['height']} · {info['fps']:g} fps · {info['codec']}")
        self._set_enabled(True)
        self._refresh_notes()
        self.player.seek(self.last_frame.get(iid, 0))

    def step_clip(self, d):
        if not self.pl:
            return
        ids = [i["id"] for i in self.pl.items()]
        if not ids:
            return
        k = ids.index(self.cur) if self.cur in ids else -1
        k = max(0, min(len(ids) - 1, k + d))
        self.open_item(ids[k])
        self._refresh_playlist()

    def add_media_dialog(self):
        if not self.pl:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Add footage", self.pl.folder, MEDIA_FILTER)
        if paths:
            self.add_media(paths)

    def add_media(self, paths: list[str]):
        if not self.pl:
            return
        added, failed = [], []
        for p in paths:
            if p.endswith(EXT):
                self.open_playlist(p)
                return
            try:
                info = probe(p)
            except MediaError as e:
                failed.append(f"{os.path.basename(p)}: {e}")
                continue
            item = self.pl.add_item(p, info, self.prefs.author)
            self.resolved[item["id"]] = os.path.abspath(p)
            self.infos[item["id"]] = info
            added.append(item["id"])
        if added:
            self._changed()
            self.open_item(added[0])
            self._refresh_playlist()
        if failed:
            QMessageBox.warning(self, "Some files weren't added", "\n".join(failed))

    def remove_item(self, iid):
        item = self.pl.item(iid) if self.pl else None
        if not item:
            return
        n = len(Playlist.live(item.get("layers", {}))) + len(Playlist.live(item["comments"]))
        msg = f"Remove “{item['label']}” from this playlist?"
        if n:
            msg += f"\n\nIts {n} note{'s' if n != 1 else ''} will be removed for everyone."
        if QMessageBox.question(self, "Remove clip", msg) != QMessageBox.StandardButton.Yes:
            return
        self.pl.remove_item(iid)
        if self.cur == iid:
            self.cur = None
        self._changed()
        items = self.pl.items()
        if items and self.cur is None:
            self.open_item(items[0]["id"])
        elif not items:
            self._no_item("This playlist is empty.\nAdd footage with “Add media”, or drop files on the playlist.")

    def relink(self, iid):
        item = self.pl.item(iid) if self.pl else None
        if not item:
            return
        ref = item["media"]
        start = os.path.dirname(ref.get("absolute") or "")
        if not os.path.isdir(start):
            start = self.pl.folder
        path, _ = QFileDialog.getOpenFileName(self, f"Locate {ref.get('filename')}", start, MEDIA_FILTER)
        if not path:
            return
        try:
            info = probe(path)
        except MediaError as e:
            QMessageBox.warning(self, "Couldn't use that file", str(e))
            return
        old = item.get("info") or {}
        if old.get("frames") and abs(old["frames"] - info["frames"]) > 1:
            ok = QMessageBox.question(
                self, "Different length",
                f"{os.path.basename(path)} has {info['frames']} frames, but the original had {old['frames']}. "
                "Notes are tied to frame numbers, so they may land on different frames.\n\nRelink anyway?")
            if ok != QMessageBox.StandardButton.Yes:
                return
        self.pl.relink(iid, path, info)
        self.resolved[iid] = path
        relinked = 1
        # Look for the other missing clips near the one just found.
        missing = {it["media"]["filename"]: it["id"] for it in self.pl.items() if not self.resolved.get(it["id"])}
        if missing:
            folder = os.path.dirname(path)
            found = find_by_name(folder, set(missing))
            if not found and os.path.dirname(folder) != folder:
                found = find_by_name(os.path.dirname(folder), set(missing))
            if found:
                ans = QMessageBox.question(self, "Relink others?",
                                           f"Found {len(found)} more missing clip{'s' if len(found) != 1 else ''} nearby. Relink "
                                           f"{'them' if len(found) != 1 else 'it'} too?")
                if ans == QMessageBox.StandardButton.Yes:
                    for name, p in found.items():
                        try:
                            self.pl.relink(missing[name], p, probe(p))
                            self.resolved[missing[name]] = p
                            relinked += 1
                        except MediaError:
                            pass
        self._changed()
        self.status.showMessage(f"Relinked {relinked} clip{'s' if relinked != 1 else ''}", 4000)
        self.cur = None
        self.open_item(iid)
        self._refresh_playlist()

    # ================================================================ frames

    def _layers_at(self, f: int) -> list[dict]:
        return self.by_frame.get(f, [])

    def _on_frame(self, i: int):
        self.canvas.set_frame(i, self.player.image(i), self._layers_at(i), self._active_layer(i))
        self.timeline.set_current(i)
        self.tc.setText(f"{self._tc(i)}   <b>{i + 1}</b> / {self.player.frames}")
        if not self.player.playing:
            self._refresh_frame_panels()

    def _on_image(self, i: int):
        if i == self.player.frame:
            self.canvas.set_frame(i, self.player.image(i), self._layers_at(i), self._active_layer(i))

    def _on_playing(self, playing: bool):
        self.play_btn.setIcon(icon("pause" if playing else "play"))
        if not playing:
            self._refresh_frame_panels()

    def _tc(self, f: int) -> str:
        fr = max(1, round(self.player.fps))
        s, ff = divmod(f, fr)
        return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{ff:02d}"

    def goto(self, f: int):
        if self.player.info:
            self.player.seek(f)

    def step(self, d: int):
        if self.player.info:
            self.player.pause()
            self.player.seek(self.player.frame + d)

    def toggle_play(self):
        if self.player.info:
            self.player.toggle()

    def jump_mark(self, d: int):
        if not self.player.info:
            return
        frames = sorted(set(self.all_by_frame) | {c["frame"] for c in self._visible_comments()})
        f = self.player.frame
        target = next((x for x in frames if x > f), None) if d > 0 else next((x for x in reversed(frames) if x < f), None)
        if target is not None:
            self.player.pause()
            self.player.seek(target)

    # ================================================================ layers & comments

    def _item(self):
        return self.pl.item(self.cur) if self.pl and self.cur else None

    def _frame(self) -> int:
        return self.player.frame if self.player.info else 0

    def _size(self, iid: str | None = None) -> tuple[int, int]:
        """An item's source resolution, which is its layers' resolution."""
        iid = iid or self.cur
        info = self.infos.get(iid) or ((self.pl.item(iid) or {}).get("info") if self.pl and iid else None) or {}
        return info.get("width", 0), info.get("height", 0)

    def _visible_comments(self):
        it = self._item()
        if not it:
            return []
        return [c for c in Playlist.live(it["comments"])
                if c["author"] not in self.hidden_comment_authors and c["id"] not in self.hidden_comments]

    def _pick_key(self, f: int) -> tuple:
        return (self.cur, f, self.prefs.author)

    def _active_layer(self, f: int | None = None) -> dict | None:
        """The layer your strokes on frame f go into: the one you clicked, if it's yours, else
        your top layer there. None means your next stroke makes a new layer on top."""
        if not self._item():
            return None
        f = self._frame() if f is None else f
        layers = self.all_by_frame.get(f, [])
        me = self.prefs.author
        picked = next((r for r in layers if r["id"] == self.picked_layer.get(self._pick_key(f))), None)
        if picked is not None:
            return picked if picked["author"] == me else None
        mine = [r for r in layers if r["author"] == me]
        return mine[-1] if mine else None

    def _refresh_notes(self):
        it = self._item()
        self.by_frame, self.all_by_frame = {}, {}
        marks: dict[int, list[str]] = {}
        if it:
            for r in sorted(Playlist.live(it.get("layers", {})), key=lambda x: (layer_order(x), x["created"])):
                self.all_by_frame.setdefault(r["frame"], []).append(r)
                if r["id"] in self.hidden_layers:
                    continue
                self.by_frame.setdefault(r["frame"], []).append(r)
                cols = marks.setdefault(r["frame"], [])
                c = author_color(r["author"])
                if c not in cols:
                    cols.append(c)
            for f in self.all_by_frame:
                marks.setdefault(f, [HIDDEN_MARK])  # frames whose layers are all hidden show a grey mark
        self.timeline.set_marks(marks, {c["frame"] for c in self._visible_comments()})
        self.canvas.author = self.prefs.author
        f = self.player.frame
        self.canvas.set_layers(self._layers_at(f), self._active_layer(f))
        self._refresh_frame_panels()
        self.a_undo.setEnabled(bool(self.undo_stack))
        self.a_redo.setEnabled(bool(self.redo_stack))

    @staticmethod
    def _groups(records: list[dict], me: str) -> list[tuple[str, list[dict]]]:
        """Group records by author: you first, then everyone else alphabetically."""
        by: dict[str, list[dict]] = {}
        for r in sorted(records, key=lambda x: x["created"]):
            by.setdefault(r["author"], []).append(r)
        return sorted(by.items(), key=lambda kv: (kv[0] != me, kv[0].lower()))

    def _layer_label(self, r: dict) -> str:
        """The row text; the blend mode has its own button on the row."""
        label = r["name"]
        if r.get("opacity", 1.0) < 1.0:
            label += f"   {round(r['opacity'] * 100)}%"
        return label if r["author"] == self.prefs.author else f"{label}   {r['author']}"

    @staticmethod
    def _comment_label(c: dict) -> str:
        return c["text"]

    def _refresh_frame_panels(self):
        f = self._frame()
        me = self.prefs.author
        layers = self.all_by_frame.get(f, [])
        active = self._active_layer(f)
        picked = self.picked_layer.get(self._pick_key(f))
        current = picked if any(r["id"] == picked for r in layers) else active and active["id"]
        self.layers_panel.tree.set_layers(layers, current, self.hidden_layers, me, self._layer_label)
        self.layers_panel.set_opacity(active.get("opacity", 1.0) if active else None)
        if active is not None:
            self.settings["blend"] = active["blend"]
            self.blend.set_mode(active["blend"])
        it = self._item()
        comments = [c for c in Playlist.live(it["comments"]) if c["frame"] == f] if it else []
        self.comment_tree.set_groups(self._groups(comments, me), self.hidden_comment_authors,
                                     self.hidden_comments, me, self._comment_label)
        self.compose.set_frame(f)

    def pick_layer(self, lid: str):
        if self._item():
            self.picked_layer[self._pick_key(self._frame())] = lid
            self._refresh_notes()

    def _next_layer_name(self, f: int) -> str:
        it = self._item()
        n = sum(1 for r in it.get("layers", {}).values() if r["frame"] == f and r["author"] == self.prefs.author)
        return f"Layer {n + 1}"

    def new_layer(self):
        if not self._item() or not self.player.info:
            return
        f = self._frame()
        rec = self._make_layer(f)
        self.picked_layer[self._pick_key(f)] = rec["id"]
        self._record_changes([("layers", None, rec)])

    def _make_layer(self, f: int) -> dict:
        """A new layer of yours on top of frame f's layers."""
        rec = make_layer(f, self.prefs.author, self._next_layer_name(f), self.settings["blend"])
        top = max((layer_order(r) for r in self.all_by_frame.get(f, [])), default=rec["created"])
        rec["order"] = max(rec["created"], top + 1)
        return rec

    def move_layers(self, ids: list[str], row: int):
        """Move your layers among ids so they sit before `row` of the list (top first).

        Only your own layers move: reordering rewrites a layer's record, and rewriting
        someone else's could undo pixels they painted since your last merge. Yours can go
        anywhere among theirs, since only your records change.
        """
        f = self._frame()
        rows = list(reversed(self.all_by_frame.get(f, [])))  # top first
        row = max(0, min(row, len(rows)))
        moving = [r for r in rows if r["id"] in ids and r["author"] == self.prefs.author]
        if not moving:
            if ids:
                self.status.showMessage("Only your own layers can be moved.", 4000)
            return
        row -= sum(1 for r in rows[:row] if r in moving)
        rest = [r for r in rows if r not in moving]
        above = rest[row - 1] if row > 0 else None
        below = rest[row] if row < len(rest) else None
        n = len(moving)
        hi = layer_order(above) if above else (layer_order(below) + n + 1 if below else now())
        lo = layer_order(below) if below else hi - n - 1
        step = (hi - lo) / (n + 1)
        changes = [("layers", copy.deepcopy(r), dict(r, order=hi - step * (k + 1))) for k, r in enumerate(moving)]
        if [r["id"] for r in rest[:row] + moving + rest[row:]] != [r["id"] for r in rows]:
            self._record_changes(changes)

    def step_layers(self, ids: list[str], d: int):
        """Move your selected layers one row up (d = -1) or down (d = 1)."""
        rows = [r["id"] for r in reversed(self.all_by_frame.get(self._frame(), []))]
        idx = [rows.index(i) for i in ids if i in rows]
        if idx:
            self.move_layers(ids, min(idx) - 1 if d < 0 else max(idx) + 2)

    def _step_active(self, d: int):
        a = self._active_layer()
        if a is not None:
            self.step_layers([a["id"]], d)

    def _set_layer(self, rec: dict, **fields):
        self._record_changes([("layers", copy.deepcopy(rec), dict(rec, **fields))])

    def _opacity_pressed(self):
        a = self._active_layer()
        self._opacity_before = copy.deepcopy(a) if a else None

    def _opacity_value(self, v: int):
        a = self._active_layer()
        if a is None:
            return
        if self.layers_panel.opacity.isSliderDown():
            a["opacity"] = v / 100  # a preview while dragging; recorded on release
            self.canvas.invalidate()
        elif a.get("opacity", 1.0) != v / 100:
            self._set_layer(a, opacity=v / 100)

    def _opacity_released(self):
        a, before = self._active_layer(), self._opacity_before
        self._opacity_before = None
        if a is not None and before is not None and before["id"] == a["id"] and before.get("opacity") != a.get("opacity"):
            self._record_changes([("layers", before, dict(a))])

    def _author_toggled(self, name: str, on: bool):
        (self.hidden_comment_authors.discard if on else self.hidden_comment_authors.add)(name)
        self._save_view()
        self._refresh_notes()

    def _items_toggled(self, kind: str, ids: list[str], on: bool):
        target = self.hidden_layers if kind == "layers" else self.hidden_comments
        for i in ids:
            (target.discard if on else target.add)(i)
        self._save_view()
        self._refresh_notes()

    def _save_view(self):
        if not self.pl:
            return
        live_l, live_c = set(), set()
        for it in self.pl.items():
            live_l |= {r["id"] for r in Playlist.live(it.get("layers", {}))}
            live_c |= {r["id"] for r in Playlist.live(it["comments"])}
        self.hidden_layers &= live_l  # forget layers and notes that have since been deleted
        self.hidden_comments &= live_c
        self.prefs.set_view_state(self.pl.data["id"], {
            "layers": sorted(self.hidden_layers),
            "comment_authors": sorted(self.hidden_comment_authors),
            "comments": sorted(self.hidden_comments),
        })

    def delete_records(self, kind: str, ids: list[str]):
        """Delete the viewer's own layers or comments among ids; others' can only be hidden."""
        it = self._item()
        if not it:
            return
        me = self.prefs.author
        changes = []
        for rid in ids:
            rec = it.get(kind, {}).get(rid)
            if rec and rec["author"] == me and not rec.get("deleted"):
                changes.append((kind, copy.deepcopy(rec), dict(rec, deleted=True)))
        skipped = len(ids) - len(changes)
        if changes:
            self._record_changes(changes)
        noun = kind if len(changes) != 1 else kind[:-1]
        if changes and skipped:
            self.status.showMessage(f"Deleted {len(changes)} {noun}. {skipped} by others can only be hidden. "
                                    "Undo with \u2318/Ctrl+Z.", 5000)
        elif changes:
            self.status.showMessage(f"Deleted {len(changes)} {noun}. Undo with \u2318/Ctrl+Z.", 3000)
        elif skipped:
            self.status.showMessage(f"Only your own {kind} can be deleted. Uncheck others' to hide them.", 4000)

    def _put(self, iid: str, kind: str, rec: dict) -> dict:
        if kind == "layers":
            cur = self.pl.item(iid).get("layers", {}).get(rec["id"])
            if cur is not None:  # pixels live in the layer store; changing a record never rewinds them
                rec = dict(rec, png=cur.get("png", ""), x=cur.get("x", 0), y=cur.get("y", 0))
        return self.pl.put_record(iid, kind, rec)

    def _record_changes(self, changes):
        """Apply changes to the current item and make them one undo step. Each change is either
        (kind, record before or None, record after) or ("px", layer id, rect, pixels before, after),
        for pixels already painted into the layer store."""
        iid = self.cur
        applied = []
        for ch in changes:
            if ch[0] == "px":
                self.pl.item(iid)["layers"][ch[1]]["modified"] = now()
                applied.append(("px", iid, *ch[1:]))
            else:
                kind, before, after = ch
                applied.append(("rec", iid, kind, before, self._put(iid, kind, after)))
        self.undo_stack.append(applied)
        del self.undo_stack[:-UNDO_LIMIT]
        self.redo_stack.clear()
        self._changed()

    def _begin_layer(self):
        """A stroke is starting with no layer of yours to go into: make one now, so it shows in
        the list while you draw. It joins the stroke's undo step when the stroke is finished."""
        if not self._item() or not self.player.info:
            return
        rec = self._make_layer(self._frame())
        self._pending_layer = self._put(self.cur, "layers", rec)
        self.picked_layer[self._pick_key(rec["frame"])] = rec["id"]
        self._refresh_notes()

    def _drop_pending_layer(self):
        """The stroke that made a layer was too small to keep: forget the layer too."""
        rec, self._pending_layer = self._pending_layer, None
        it = self._item()
        if rec is not None and it is not None:
            it.get("layers", {}).pop(rec["id"], None)
            self._refresh_notes()

    def add_stroke(self, s: dict):
        """Bake a finished stroke into your selected layer, or a new layer on top."""
        if not self._item():
            return
        w, h = self._size()
        target = self._active_layer(s["frame"])
        changes = []
        pending, self._pending_layer = self._pending_layer, None
        if target is not None and pending is not None and target["id"] == pending["id"]:
            changes.append(("layers", None, target))  # made when the stroke started
        elif pending is not None:
            self._item().get("layers", {}).pop(pending["id"], None)
        if target is None:
            if s["kind"] in ("erase", "smudge"):
                return
            target = self._make_layer(s["frame"])
            changes.append(("layers", None, target))
        rect, before, after = self.store.bake(target, s, w, h)
        changes.append(("px", target["id"], rect, before, after))
        self.picked_layer[self._pick_key(s["frame"])] = target["id"]
        if target["id"] in self.hidden_layers:
            # Drawing on a hidden layer would look like nothing happened.
            self.hidden_layers.discard(target["id"])
            self._save_view()
            self.status.showMessage(f"{target['name']} was hidden; showing it again", 3000)
        self._record_changes(changes)
        if s["kind"] not in ("smudge", "erase"):  # these carry no color of their own
            self.color.render_recents(self.prefs.push_color(s["color"]))

    def clear_mine(self):
        mine = [r["id"] for r in self.all_by_frame.get(self._frame(), []) if r["author"] == self.prefs.author]
        self.delete_records("layers", mine)

    def add_comment(self, text: str):
        if not self._item():
            return
        t = now()
        c = {"id": new_id(), "frame": self.player.frame, "author": self.prefs.author, "created": t, "modified": t,
             "deleted": False, "text": text}
        self._record_changes([("comments", None, c)])

    def _apply_history(self, entry, undo: bool):
        frame = None
        for op in (reversed(entry) if undo else entry):
            iid = op[1]
            if op[0] == "px":
                _, _, lid, rect, before, after = op
                rec = self.pl.item(iid)["layers"][lid]
                self.store.put_patch(rec, *self._size(iid), rect, before if undo else after)
                rec["modified"] = now()
            else:
                _, _, kind, before, after = op
                if undo:
                    rec = dict(after, deleted=True) if before is None else before
                else:
                    rec = after
                rec = self._put(iid, kind, rec)
            frame = rec["frame"]
        iid = entry[0][1]
        if iid != self.cur and self.pl.item(iid):
            self.open_item(iid)
            self._refresh_playlist()
        self._changed()
        if frame is not None and self.player.info:
            self.player.seek(frame)

    def undo(self):
        if self.undo_stack and self.pl:
            e = self.undo_stack.pop()
            self.redo_stack.append(e)
            self._apply_history(e, True)

    def redo(self):
        if self.redo_stack and self.pl:
            e = self.redo_stack.pop()
            self.undo_stack.append(e)
            self._apply_history(e, False)

    # ================================================================ saving & merging

    def _changed(self, refresh_list=True):
        """The playlist changed in memory. Nothing is written until you save."""
        self.dirty = True
        self._update_title()
        self._refresh_notes()
        if refresh_list:
            self._refresh_playlist()

    def save(self) -> bool:
        """Write the playlist (merged with the file on disk, so nobody's notes are overwritten)."""
        if not self.pl:
            return True
        self.store.flush({lid: rec for it in self.pl.data["items"].values() for lid, rec in it.get("layers", {}).items()})
        try:
            incoming = self.pl.save()
        except PlaylistError as e:
            QMessageBox.warning(self, "Couldn't save", f"{e}\n\nYour changes are still open here; try saving again.")
            return False
        self.dirty = False
        self._update_title()
        if incoming:
            self._after_merge()
            self.status.showMessage("Saved, with others' new notes merged in", 3000)
        else:
            self.status.showMessage("Saved", 2000)
        return True

    def _ask_save(self) -> str:
        """Ask what to do with unsaved changes: 'save', 'discard' or 'cancel'."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(f"Save changes to \u201c{self.pl.name}\u201d?")
        box.setInformativeText("Your drawing and comments since the last save will be lost if you don't save.")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                               | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        choice = box.exec()
        return {QMessageBox.StandardButton.Save: "save", QMessageBox.StandardButton.Discard: "discard"}.get(
            QMessageBox.StandardButton(choice), "cancel")

    def maybe_save(self) -> bool:
        """Before closing or switching playlists. Returns False if the user cancels or saving fails."""
        if not self.pl or not self.dirty:
            return True
        choice = self._ask_save()
        if choice == "save":
            return self.save()
        if choice == "discard":
            self.dirty = False
            return True
        return False

    def _poll(self):
        if not self.pl:
            return
        changed, needs_save = self.pl.poll()
        if changed:
            self._after_merge()
            self.status.showMessage("Others' notes updated", 3000)
        if needs_save:
            # Memory holds notes the file doesn't: keep them until the next save.
            self.dirty = True
            self._update_title()

    def _after_merge(self):
        if self._migrate():  # someone on an older version saved strokes
            self._update_title()
        for it in self.pl.items():
            if it["id"] not in self.resolved:
                self.resolved[it["id"]] = self.pl.resolve(it, self.prefs.mappings)
        self.playlist_panel.set_playlist(self.pl.name, self.pl.path)
        cur = self._item()
        if self.cur and (cur is None or cur.get("deleted")):
            self.cur = None
            items = self.pl.items()
            if items:
                self.open_item(items[0]["id"])
            else:
                self._no_item("This playlist is empty.")
        self._refresh_notes()
        self._refresh_playlist()
        self._update_title()

    # ================================================================ export

    def _full_frame(self):
        if not self.player.info or not self.player.path:
            return None
        r = VideoReader(self.player.path)
        try:
            f = r.read(self.player.frame)
            if f is None:
                return None
            img = frame_to_qimage(f, self.player.info["width"], self.player.info["height"])
        finally:
            r.close()
        layers = self._layers_at(self.player.frame) if self.canvas.show_marks else []
        return self.canvas.render_full(img, layers)

    def export_snapshot(self):
        it = self._item()
        if not it or not self.player.info:
            return
        self.player.pause()
        img = self._full_frame()
        if img is None:
            return
        name = f"{it['label']}_f{self.player.frame + 1:04d}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Export frame", os.path.join(os.path.expanduser("~/Desktop"), name),
                                              "PNG image (*.png);;JPEG image (*.jpg)")
        if path:
            if img.save(path):
                self.status.showMessage(f"Exported {os.path.basename(path)}", 4000)
            else:
                QMessageBox.warning(self, "Export failed", f"Couldn't write {path}.")

    def copy_snapshot(self):
        img = self._full_frame()
        if img is not None:
            QGuiApplication.clipboard().setImage(img)
            self.status.showMessage("Copied frame with marks", 3000)

    # ================================================================ prefs & lifecycle

    def _update_author_btn(self):
        self.author_btn.setText(f"●  {self.prefs.author}")
        self.author_btn.setStyleSheet(f"color: {author_color(self.prefs.author)};")

    def open_prefs(self):
        old_maps = self.prefs.mappings_text
        if PrefsDialog(self.prefs, self).exec():
            self._update_author_btn()
            self.canvas.author = self.prefs.author
            self.player.cache.budget = self.prefs.cache_mb * 1024 * 1024
            if self.pl:
                if self.prefs.mappings_text != old_maps:
                    self._resolve_all()
                    if self.cur and not self.player.info and self.resolved.get(self.cur):
                        cur, self.cur = self.cur, None
                        self.open_item(cur)
                self._refresh_notes()
                self._refresh_playlist()

    # ================================================================ updates

    def check_updates(self, quiet: bool):
        """Look for newer app code. Quietly (at startup) just says so in the status bar."""
        if self._update_worker is not None:
            return
        if not quiet:
            self.status.showMessage("Checking for updates…")
        self._update_worker = w = UpdateWorker(install=False, parent=self)
        w.done.connect(lambda r: self._update_checked(r, quiet))
        w.start()

    def _update_checked(self, r: dict, quiet: bool):
        self._update_worker = None
        state, msg = r["state"], r["message"]
        if quiet:
            if state == "available":
                self.status.showMessage(f"{msg} Install it with Help \u25b8 Check for Updates.", 15000)
            return
        self.status.clearMessage()
        if state == "error":
            QMessageBox.warning(self, "Couldn't check for updates", msg)
        elif state == "needs_full":
            box = QMessageBox(QMessageBox.Icon.Information, "New version of the app needed", msg,
                              QMessageBox.StandardButton.Close, self)
            page = box.addButton("Open Download Page", QMessageBox.ButtonRole.AcceptRole)
            box.exec()
            if box.clickedButton() is page:
                QDesktopServices.openUrl(QUrl(update.DOWNLOAD_PAGE))
        elif state == "available":
            ans = QMessageBox.question(self, "Update available",
                                       f"{msg}\n\nYou're running {update.describe(update.running())}. Install the update? "
                                       "It takes a few seconds and is used the next time BlendSketch starts.")
            if ans == QMessageBox.StandardButton.Yes:
                self.status.showMessage("Installing update…")
                self._update_worker = w = UpdateWorker(install=True, parent=self)
                w.done.connect(self._update_installed)
                w.start()
        else:
            QMessageBox.information(self, "Updates", msg)

    def _update_installed(self, r: dict):
        self._update_worker = None
        self.status.clearMessage()
        if r["state"] != "installed":
            QMessageBox.warning(self, "Update not installed", r["message"])
            return
        box = QMessageBox(QMessageBox.Icon.Information, "Update installed", r["message"],
                          QMessageBox.StandardButton.NoButton, self)
        restart = box.addButton("Restart Now", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is restart:
            self.restart()

    def restart(self):
        """Quit (asking to save first) and start the app again, which picks up an installed update."""
        if not self.maybe_save():
            return
        args = [a for a in sys.argv[1:] if not a.endswith(EXT)]
        if self.pl:
            args.append(self.pl.path)
        if getattr(sys, "frozen", False):
            QProcess.startDetached(sys.executable, args)
        else:
            QProcess.startDetached(sys.executable, [os.path.abspath(sys.argv[0]), *args])
        self.dirty = False
        QApplication.instance().quit()

    def about(self):
        info = update.running()
        lines = [f"Version {update.describe(info)}"]
        if info.get("commit"):
            lines.append(f"Commit {info['commit'][:12]}")
        nxt = update.installed()
        if nxt and nxt.get("commit") != info.get("commit"):
            lines.append(f"Update {nxt['version']} runs after a restart.")
        QMessageBox.about(self, f"About {APP_NAME}", f"<b>{APP_NAME}</b><br>" + "<br>".join(lines) +
                          "<br><br>Frame-accurate review with blend-mode layers and shared playlists.")

    def closeEvent(self, e):
        if not self.maybe_save():
            e.ignore()
            return
        self.player.unload()
        self.prefs.set_geometry(self.saveGeometry())
        super().closeEvent(e)
