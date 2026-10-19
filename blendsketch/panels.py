"""Side panels: playlist, authors, this frame, comments, preferences."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QItemSelectionModel, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QStyle, QStyleOptionViewItem, QToolButton, QTreeWidget, QTreeWidgetItem, QMenu, QVBoxLayout, QWidget,
)

from .colors import author_color
from .icons import icon

THUMB = QSize(80, 45)


def fmt_time(t: float) -> str:
    d = dt.datetime.fromtimestamp(t)
    today = dt.date.today()
    if d.date() == today:
        return d.strftime("%H:%M")
    return d.strftime("%b %d %H:%M").replace(" 0", " ")


# ---------------------------------------------------------------- thumbnails

class _ThumbSignals(QObject):
    done = Signal(str, QImage)


class _ThumbJob(QRunnable):
    def __init__(self, path, frames, signals):
        super().__init__()
        self.path, self.frames, self.signals = path, frames, signals

    def run(self):
        from .media import VideoReader, frame_to_qimage
        try:
            r = VideoReader(self.path)
            f = r.read(max(0, int(self.frames * 0.1)))
            img = frame_to_qimage(f, THUMB.width() * 2, THUMB.height() * 2) if f is not None else QImage()
            r.close()
        except Exception:
            img = QImage()
        self.signals.done.emit(self.path, img)


class PlaylistPanel(QWidget):
    itemSelected = Signal(str)
    orderChanged = Signal(list)
    addRequested = Signal()
    removeRequested = Signal(str)
    relinkRequested = Signal(str)
    renameRequested = Signal(str)
    filesDropped = Signal(list)
    thumbReady = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.name = QLineEdit()
        self.name.setObjectName("PlaylistName")
        self.name.setPlaceholderText("Playlist name")
        self.name.editingFinished.connect(lambda: self.renameRequested.emit(self.name.text().strip()))
        head.addWidget(self.name, 1)
        lay.addLayout(head)
        self.path_label = QLabel()
        self.path_label.setObjectName("Muted")
        self.path_label.setWordWrap(True)
        lay.addWidget(self.path_label)
        self.list = QListWidget()
        self.list.setObjectName("PlaylistList")
        self.list.setIconSize(THUMB)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSpacing(2)
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(self._current)
        self.list.model().rowsMoved.connect(self._moved)
        self.list.itemDoubleClicked.connect(self._double)
        lay.addWidget(self.list, 1)
        btns = QHBoxLayout()
        self.add_btn = QPushButton(icon("plus"), "Add media")
        self.add_btn.clicked.connect(self.addRequested)
        self.relink_btn = QToolButton()
        self.relink_btn.setIcon(icon("link"))
        self.relink_btn.setToolTip("Relink footage")
        self.relink_btn.clicked.connect(lambda: self._with_current(self.relinkRequested))
        self.remove_btn = QToolButton()
        self.remove_btn.setIcon(icon("minus"))
        self.remove_btn.setToolTip("Remove from playlist")
        self.remove_btn.clicked.connect(lambda: self._with_current(self.removeRequested))
        btns.addWidget(self.add_btn, 1)
        btns.addWidget(self.relink_btn)
        btns.addWidget(self.remove_btn)
        lay.addLayout(btns)
        self.setAcceptDrops(True)
        self.thumbs: dict[str, QImage] = {}
        self._pending = set()
        self._signals = _ThumbSignals()
        self._signals.done.connect(self._thumb_done)
        self._building = False

    def _with_current(self, sig):
        it = self.list.currentItem()
        if it:
            sig.emit(it.data(Qt.ItemDataRole.UserRole))

    def _current(self, cur, prev):
        if cur and not self._building:
            self.itemSelected.emit(cur.data(Qt.ItemDataRole.UserRole))

    def _moved(self, *a):
        ids = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        self.orderChanged.emit(ids)

    def _double(self, it):
        if it.data(Qt.ItemDataRole.UserRole + 1):  # missing
            self.relinkRequested.emit(it.data(Qt.ItemDataRole.UserRole))

    def set_items(self, rows: list[dict], current: str | None):
        """rows: id, label, detail, missing, path, frames, notes"""
        self._building = True
        self.list.clear()
        for r in rows:
            it = QListWidgetItem()
            note = f" · {r['notes']} note{'s' if r['notes'] != 1 else ''}" if r["notes"] else ""
            if r["missing"]:
                it.setText(f"{r['label']}\nMissing: double-click to relink")
                it.setForeground(QColor("#ef6464"))
            else:
                it.setText(f"{r['label']}\n{r['detail']}{note}")
            it.setData(Qt.ItemDataRole.UserRole, r["id"])
            it.setData(Qt.ItemDataRole.UserRole + 1, r["missing"])
            it.setSizeHint(QSize(0, THUMB.height() + 16))
            if r["path"] and r["path"] in self.thumbs and not self.thumbs[r["path"]].isNull():
                it.setIcon(QIcon(QPixmap.fromImage(self.thumbs[r["path"]])))
            elif r["path"] and r["path"] not in self._pending:
                self._pending.add(r["path"])
                QThreadPool.globalInstance().start(_ThumbJob(r["path"], r["frames"], self._signals))
            self.list.addItem(it)
            if r["id"] == current:
                self.list.setCurrentItem(it)
        self._building = False

    def _thumb_done(self, path, img):
        self._pending.discard(path)
        img.setDevicePixelRatio(2)
        self.thumbs[path] = img
        self.thumbReady.emit()

    def set_playlist(self, name: str, path: str):
        self.name.setText(name)
        self.path_label.setText(path)
        self.path_label.setToolTip(path)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)


# ---------------------------------------------------------------- notes trees

AUTHOR_ROLE = Qt.ItemDataRole.UserRole       # author name, on author rows
ID_ROLE = Qt.ItemDataRole.UserRole + 1       # record id, on item rows
MINE_ROLE = Qt.ItemDataRole.UserRole + 2
RAW_ROLE = Qt.ItemDataRole.UserRole + 3      # unwrapped text, on comment rows


def wrap_text(text: str, fm, width: int) -> str:
    """Break text into lines that fit width pixels (Qt's own item wrapping is unreliable in trees)."""
    out = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            trial = f"{line} {word}" if line else word
            if fm.horizontalAdvance(trial) <= width or not line:
                line = trial
            else:
                out.append(line)
                line = word
        out.append(line)
    return "\n".join(out)     # True for the viewer's own items


class NotesTree(QTreeWidget):
    """The current frame's strokes or comments, grouped by author, each with a show/hide checkbox.

    Author checkboxes work like a layer group: unchecking hides everything that person
    has made (now and later) while keeping the item checkboxes underneath as they were.
    Visibility is per machine and never changes the playlist. Select items (Shift/Cmd for
    several) and press Delete/Backspace or right-click to delete; only your own can be.
    """

    authorToggled = Signal(str, bool)
    itemToggled = Signal(list, bool)    # record ids, visible
    deleteRequested = Signal(list)      # record ids (the window keeps only the viewer's own)

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("NotesTree")
        self.setHeaderHidden(True)
        self.setColumnCount(1)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setIndentation(22)
        self.setUniformRowHeights(False)
        self.wrap = kind == "comments"
        self.setTextElideMode(Qt.TextElideMode.ElideNone if self.wrap else Qt.TextElideMode.ElideRight)
        self.header().setStretchLastSection(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemChanged.connect(self._changed)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.cap = 320 if kind == "comments" else 280
        self.collapsed: set[str] = set()
        self.itemCollapsed.connect(lambda it: (it.data(0, AUTHOR_ROLE) and self.collapsed.add(it.data(0, AUTHOR_ROLE)), self._fit_height()))
        self.itemExpanded.connect(lambda it: (self.collapsed.discard(it.data(0, AUTHOR_ROLE)), self._fit_height()))

    # -- building

    def set_groups(self, groups: list[tuple[str, list[dict]]], hidden_authors: set[str], hidden_items: set[str],
                   me: str, label):
        """groups: [(author, this frame's records)]; label(record) -> row text."""
        selected = {it.data(0, ID_ROLE) or ("@" + (it.data(0, AUTHOR_ROLE) or "")) for it in self.selectedItems()}
        bar = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.clear()
        bold = QFont(self.font())
        bold.setBold(True)
        if not groups:
            empty = QTreeWidgetItem([f"No {self.kind} on this frame."])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addTopLevelItem(empty)
        for author, recs in groups:
            shown = author not in hidden_authors
            top = QTreeWidgetItem([f"{author}{'  (you)' if author == me else ''}   {len(recs)}"])
            top.setData(0, AUTHOR_ROLE, author)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            top.setCheckState(0, Qt.CheckState.Checked if shown else Qt.CheckState.Unchecked)
            top.setForeground(0, QColor(author_color(author)))
            top.setFont(0, bold)
            top.setToolTip(0, f"Show or hide all of {author}'s {self.kind} (only on this machine)")
            self.addTopLevelItem(top)
            for r in recs:
                child = QTreeWidgetItem([label(r)])
                child.setData(0, RAW_ROLE, label(r))
                child.setData(0, ID_ROLE, r["id"])
                child.setData(0, MINE_ROLE, r["author"] == me)
                child.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
                child.setCheckState(0, Qt.CheckState.Unchecked if r["id"] in hidden_items else Qt.CheckState.Checked)
                if not shown or r["id"] in hidden_items:
                    child.setForeground(0, QColor("#5b6170"))
                if not shown:
                    child.setToolTip(0, f"Hidden with all of {author}'s {self.kind}")
                top.addChild(child)
                if r["id"] in selected:
                    child.setSelected(True)
            if "@" + author in selected:
                top.setSelected(True)
            top.setExpanded(author not in self.collapsed)
        self.blockSignals(False)
        self._fit_height()
        self.verticalScrollBar().setValue(bar)

    def _fit_height(self):
        """Wrap comment text to the panel width, then grow with the content up to a cap."""
        fm = self.fontMetrics()
        width = max(100, self.viewport().width() - self.indentation() - 40)
        h = 8
        self.blockSignals(True)
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            h += fm.height() + 10
            for k in range(top.childCount()):
                child = top.child(k)
                if self.wrap and child.data(0, RAW_ROLE) is not None:
                    child.setText(0, wrap_text(child.data(0, RAW_ROLE), fm, width))
                if top.isExpanded():
                    h += (child.text(0).count("\n") + 1) * fm.height() + 10
        self.blockSignals(False)
        self.setFixedHeight(min(self.cap, max(44, h)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self._fit_height()

    # -- interaction

    def _changed(self, it, col):
        # Emit after Qt finishes handling the click: the handlers rebuild this tree, and
        # deleting the item Qt is still updating would crash.
        on = it.checkState(0) == Qt.CheckState.Checked
        if it.data(0, AUTHOR_ROLE):
            name = it.data(0, AUTHOR_ROLE)
            QTimer.singleShot(0, lambda: self.authorToggled.emit(name, on))
        elif it.data(0, ID_ROLE):
            # Toggling one of several selected rows toggles them all.
            ids = self._selected_ids() if it.isSelected() else [it.data(0, ID_ROLE)]
            QTimer.singleShot(0, lambda: self.itemToggled.emit(ids, on))

    def _selected_ids(self) -> list[str]:
        ids = []
        for it in self.selectedItems():
            if it.data(0, ID_ROLE):
                ids.append(it.data(0, ID_ROLE))
            elif it.data(0, AUTHOR_ROLE):
                ids += [it.child(k).data(0, ID_ROLE) for k in range(it.childCount())]
        return list(dict.fromkeys(ids))

    def _selected_mine(self) -> list[str]:
        mine = set()
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            for it in [top] + [top.child(k) for k in range(top.childCount())]:
                if it.data(0, MINE_ROLE):
                    mine.add(it.data(0, ID_ROLE))
        return [i for i in self._selected_ids() if i in mine]

    def _on_checkbox(self, it, pos) -> bool:
        if not it.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return False
        opt = QStyleOptionViewItem()
        self.initViewItemOption(opt)
        opt.rect = self.visualItemRect(it)
        opt.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        box = self.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, self)
        return box.adjusted(-3, -3, 3, 3).contains(pos)

    def mousePressEvent(self, e):
        """Clicking the checkbox of a selected row flips every selected row, without changing the selection."""
        pos = e.position().toPoint()
        it = self.itemAt(pos)
        if it is not None and e.button() == Qt.MouseButton.LeftButton and self._on_checkbox(it, pos):
            on = it.checkState(0) != Qt.CheckState.Checked
            if it.data(0, AUTHOR_ROLE):
                name = it.data(0, AUTHOR_ROLE)
                QTimer.singleShot(0, lambda: self.authorToggled.emit(name, on))
            elif it.data(0, ID_ROLE):
                ids = self._selected_ids() if it.isSelected() else [it.data(0, ID_ROLE)]
                QTimer.singleShot(0, lambda: self.itemToggled.emit(ids, on))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        pos = e.position().toPoint()
        it = self.itemAt(pos)
        if it is not None and e.button() == Qt.MouseButton.LeftButton and self._on_checkbox(it, pos):
            e.accept()  # already handled on press; don't let Qt toggle it a second time
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        pos = e.position().toPoint()
        it = self.itemAt(pos)
        if it is not None and self._on_checkbox(it, pos):
            self.mousePressEvent(e)  # treat a fast second click as another toggle
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selectedItems():
            ids = self._selected_ids()
            QTimer.singleShot(0, lambda: self.deleteRequested.emit(ids))
            return
        super().keyPressEvent(e)

    def _menu(self, pos):
        it = self.itemAt(pos)
        if it is not None and not it.isSelected():
            self.clearSelection()
            it.setSelected(True)
        m = self.build_menu()
        if m is not None:
            m.exec(self.viewport().mapToGlobal(pos))
            m.deleteLater()

    def build_menu(self) -> QMenu | None:
        """The right-click menu for the current selection."""
        ids = self._selected_ids()
        if not ids:
            return None
        mine = self._selected_mine()
        # Parented to the window, not this tree, so the inspector's transparent styling can't reach it.
        m = QMenu(self.window())
        m.setObjectName("ContextMenu")
        m.setAutoFillBackground(True)
        noun = self.kind if len(ids) != 1 else self.kind[:-1]
        m.addAction(f"Show {len(ids)} {noun}", lambda: self.itemToggled.emit(ids, True))
        m.addAction(f"Hide {len(ids)} {noun}", lambda: self.itemToggled.emit(ids, False))
        m.addSeparator()
        if mine:
            label = f"Delete {len(mine)} {self.kind if len(mine) != 1 else self.kind[:-1]}"
            if len(mine) < len(ids):
                label += f" (yours only; {len(ids) - len(mine)} by others can only be hidden)"
            m.addAction(label, lambda: self.deleteRequested.emit(ids))
        else:
            a = m.addAction("Delete (only your own notes can be deleted)")
            a.setEnabled(False)
        return m


def _dot(color: str) -> QIcon:
    pm = QPixmap(20, 20)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(5, 5, 10, 10)
    p.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


class LayerTree(NotesTree):
    """The current frame's layers, top layer first, each with a show/hide checkbox.

    Click a layer to draw on it (your own) or to select it. Drag your own layers (or
    right-click ▸ Move up / down) to reorder them. Checkboxes, multi-select,
    Delete/Backspace and the right-click menu work as in the other notes trees.
    """

    layerPicked = Signal(str)
    moveRequested = Signal(list, int)  # layer ids, the row (top first) to insert them before
    stepRequested = Signal(list, int)  # layer ids, -1 up / +1 down

    def __init__(self, parent=None):
        super().__init__("layers", parent)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._current_id = None
        self.currentItemChanged.connect(self._current)

    def set_layers(self, layers: list[dict], current: str | None, hidden: set[str], me: str, label):
        """layers: bottom to top, like the canvas; shown top first, like Procreate."""
        selected = {it.data(0, ID_ROLE) for it in self.selectedItems()} if current == self._current_id else set()
        self._current_id = current
        bar = self.verticalScrollBar().value()
        self.blockSignals(True)
        self.clear()
        if not layers:
            empty = QTreeWidgetItem(["No layers on this frame. Drawing makes one."])
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.addTopLevelItem(empty)
        for r in reversed(layers):
            mine = r["author"] == me
            it = QTreeWidgetItem([label(r)])
            it.setData(0, ID_ROLE, r["id"])
            it.setData(0, MINE_ROLE, mine)
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
            it.setFlags(flags | Qt.ItemFlag.ItemIsDragEnabled if mine else flags)  # never a drop target: no nesting
            it.setCheckState(0, Qt.CheckState.Unchecked if r["id"] in hidden else Qt.CheckState.Checked)
            it.setIcon(0, _dot(author_color(r["author"])))
            if r["id"] in hidden:
                it.setForeground(0, QColor("#5b6170"))
            if r["id"] == current:
                f = QFont(self.font())
                f.setBold(True)
                it.setFont(0, f)
            it.setToolTip(0, f"{r['name']} by {r['author']}" + ("" if mine else " (you can hide it; you draw on your own layers)"))
            self.addTopLevelItem(it)
            if r["id"] == current:
                self.setCurrentItem(it, 0, QItemSelectionModel.SelectionFlag.NoUpdate)
            # A multi-selection survives a rebuild until the current layer changes.
            it.setSelected(r["id"] in selected if len(selected) > 1 else r["id"] == current)
        self.blockSignals(False)
        self._fit_height()
        self.verticalScrollBar().setValue(bar)

    def _fit_height(self):
        rows = self.topLevelItemCount()
        h = (self.sizeHintForRow(0) if rows else 0) * rows + 2 * self.frameWidth() + 6
        self.setFixedHeight(min(self.cap, max(44, h)))

    def _current(self, cur, prev):
        if cur is not None and cur.data(0, ID_ROLE):
            lid = cur.data(0, ID_ROLE)
            QTimer.singleShot(0, lambda: self.layerPicked.emit(lid))

    def drop_row(self, pos) -> int:
        """The row (top first) a drop at pos inserts before."""
        it = self.itemAt(pos)
        if it is None:
            return self.topLevelItemCount()
        i = self.indexOfTopLevelItem(it)
        return i + 1 if pos.y() > self.visualItemRect(it).center().y() else i

    def dropEvent(self, e):
        # The window reorders the records and rebuilds the list; Qt mustn't move rows itself.
        ids = self._selected_mine()
        row = self.drop_row(e.position().toPoint())
        e.setDropAction(Qt.DropAction.IgnoreAction)
        e.accept()
        if ids:
            QTimer.singleShot(0, lambda: self.moveRequested.emit(ids, row))

    def build_menu(self) -> QMenu | None:
        m = super().build_menu()
        mine = self._selected_mine()
        if m is not None and mine:
            first = m.actions()[0]
            up = QAction("Move up", m)
            up.triggered.connect(lambda: self.stepRequested.emit(mine, -1))
            down = QAction("Move down", m)
            down.triggered.connect(lambda: self.stepRequested.emit(mine, 1))
            m.insertActions(first, [up, down])
            m.insertSeparator(first)
        return m


class LayersPanel(QWidget):
    """The layer list plus the selected layer's opacity and new / delete buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.tree = LayerTree()
        lay.addWidget(self.tree)
        row = QHBoxLayout()
        row.setSpacing(6)
        lab = QLabel("Opacity")
        lab.setObjectName("Muted")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setToolTip("Opacity of the selected layer")
        self.opacity_value = QLabel("100%")
        self.opacity_value.setObjectName("Value")
        self.opacity.valueChanged.connect(lambda v: self.opacity_value.setText(f"{v}%"))
        self.add_btn = QToolButton()
        self.add_btn.setIcon(icon("plus"))
        self.add_btn.setToolTip("New layer on top")
        self.remove_btn = QToolButton()
        self.remove_btn.setIcon(icon("minus"))
        self.remove_btn.setToolTip("Delete the selected layers (your own)")
        self.remove_btn.clicked.connect(lambda: self.tree.deleteRequested.emit(self.tree._selected_ids()))
        row.addWidget(lab)
        row.addWidget(self.opacity, 1)
        row.addWidget(self.opacity_value)
        row.addWidget(self.add_btn)
        row.addWidget(self.remove_btn)
        lay.addLayout(row)

    def set_opacity(self, value: float | None):
        """The active layer's opacity, or None when you have no layer selected here."""
        self.opacity.blockSignals(True)
        self.opacity.setEnabled(value is not None)
        self.opacity.setValue(round((1.0 if value is None else value) * 100))
        self.opacity_value.setText(f"{self.opacity.value()}%")
        self.opacity.blockSignals(False)


class ComposeBox(QWidget):
    addRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.text = QPlainTextEdit()
        self.text.setObjectName("Compose")
        self.text.setFixedHeight(64)
        self.text.installEventFilter(self)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch(1)
        self.add = QPushButton("Add comment")
        self.add.clicked.connect(self._submit)
        row.addWidget(self.add)
        lay.addLayout(row)

    def set_frame(self, frame: int):
        self.text.setPlaceholderText(f"Comment on frame {frame + 1}  (\u2318/Ctrl+Enter)")

    def eventFilter(self, obj, e):
        if obj is self.text and e.type() == e.Type.KeyPress and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and e.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            self._submit()
            return True
        return super().eventFilter(obj, e)

    def _submit(self):
        t = self.text.toPlainText().strip()
        if t:
            self.addRequested.emit(t)
            self.text.clear()


# ---------------------------------------------------------------- preferences

class PrefsDialog(QDialog):
    def __init__(self, prefs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.prefs = prefs
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.author = QLineEdit(prefs.author)
        form.addRow("Your name", self.author)
        hint = QLabel("Shown on your layers and comments.")
        hint.setObjectName("Muted")
        form.addRow("", hint)
        self.cache = QSpinBox()
        self.cache.setRange(256, 65536)
        self.cache.setSingleStep(256)
        self.cache.setSuffix(" MB")
        self.cache.setValue(prefs.cache_mb)
        form.addRow("Frame cache", self.cache)
        lay.addLayout(form)
        lab = QLabel("Path mappings")
        lab.setStyleSheet("font-weight: 600; margin-top: 8px;")
        lay.addWidget(lab)
        expl = QLabel(
            "When the same server is mounted at different paths on different machines, list the equivalent roots "
            "on one line, separated by “=”. BlendSketch tries each when footage isn't found.")
        expl.setWordWrap(True)
        expl.setObjectName("Muted")
        lay.addWidget(expl)
        self.maps = QPlainTextEdit(prefs.mappings_text)
        self.maps.setPlaceholderText("/Volumes/prod = P:/ = /mnt/prod")
        self.maps.setMinimumHeight(90)
        lay.addWidget(self.maps)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(520, 380)

    def accept(self):
        if self.author.text().strip():
            self.prefs.author = self.author.text().strip()
        self.prefs.cache_mb = self.cache.value()
        self.prefs.mappings_text = self.maps.toPlainText()
        super().accept()


def scroll_wrap(w: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidget(w)
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return sa
