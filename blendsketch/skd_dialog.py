"""The SKD Open Playlist picker (department, then review date) and its background worker."""

from __future__ import annotations

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from . import skd
from .widgets import section_label


class Worker(QThread):
    """Runs fn(report, cancelled) off the UI thread. report(text, done, total) emits progress."""

    progress = Signal(str, int, int)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            result = self.fn(lambda text, done=0, total=0: self.progress.emit(text, done, total), lambda: self._cancel)
        except skd.SKDError as e:
            self.failed.emit(str(e))
        except Exception as e:  # anything else still reaches the user instead of killing the thread silently
            self.failed.emit(f"{type(e).__name__}: {e}")
        else:
            self.succeeded.emit(result)


class SKDOpenDialog(QDialog):
    """Pick a department, then a dailies date. `chosen` is the ReviewPlaylist picked."""

    def __init__(self, prefs, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SKD Open Playlist")
        self.prefs = prefs
        self.groups: dict[str, list[skd.ReviewPlaylist]] = {}
        self.chosen: skd.ReviewPlaylist | None = None
        self.worker: Worker | None = None

        lay = QVBoxLayout(self)
        cols = QHBoxLayout()
        left, right = QVBoxLayout(), QVBoxLayout()
        left.addWidget(section_label("1  DEPARTMENT"))
        self.depts = QListWidget()
        self.depts.setMinimumWidth(180)
        left.addWidget(self.depts)
        right.addWidget(section_label("2  DAILIES / REVIEW DATE"))
        self.dates = QListWidget()
        self.dates.setMinimumWidth(360)
        right.addWidget(self.dates)
        cols.addLayout(left, 2)
        cols.addLayout(right, 5)
        lay.addLayout(cols, 1)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        bb = QDialogButtonBox()
        self.refresh_btn = bb.addButton("Refresh", QDialogButtonBox.ButtonRole.ResetRole)
        self.open_btn = QPushButton("Open")
        self.open_btn.setObjectName("Primary")
        self.open_btn.setDefault(True)
        bb.addButton(self.open_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        self.depts.currentRowChanged.connect(self._dept_changed)
        self.dates.currentRowChanged.connect(lambda _: self._update_open())
        self.dates.itemDoubleClicked.connect(lambda _: self.accept())
        self.refresh_btn.clicked.connect(self.load)
        self.resize(640, 440)
        self._update_open()
        self.load()

    def load(self):
        if self.worker and self.worker.isRunning():
            return
        self.status.setText("Loading playlists from ShotGrid…")
        self.refresh_btn.setEnabled(False)
        self.worker = Worker(lambda report, cancelled: skd.fetch_playlists(), self)
        self.worker.succeeded.connect(self._loaded)
        self.worker.failed.connect(self._failed)
        self.worker.start()

    def _loaded(self, groups):
        self.refresh_btn.setEnabled(True)
        self.groups = groups
        keep = self.depts.currentItem().data(Qt.ItemDataRole.UserRole) if self.depts.currentItem() else self.prefs.skd_department
        self.depts.clear()
        for name, pls in groups.items():
            it = QListWidgetItem(f"{name}   ({len(pls)})")
            it.setData(Qt.ItemDataRole.UserRole, name)
            self.depts.addItem(it)
        names = list(groups)
        n = sum(len(p) for p in groups.values())
        self.status.setText(f"{n} playlist{'s' if n != 1 else ''} in ShotGrid." if n else "There are no playlists in ShotGrid.")
        if names:
            self.depts.setCurrentRow(names.index(keep) if keep in names else 0)
        self.depts.setFocus()

    def _failed(self, message):
        self.refresh_btn.setEnabled(True)
        self.status.setText(message)

    def _dept_changed(self, row):
        self.dates.clear()
        item = self.depts.item(row)
        if item is None:
            self._update_open()
            return
        for pl in self.groups.get(item.data(Qt.ItemDataRole.UserRole), []):
            it = QListWidgetItem(f"{pl.date_label}    ·    {pl.code}    ·    {pl.versions} version{'s' if pl.versions != 1 else ''}")
            it.setData(Qt.ItemDataRole.UserRole, pl)
            self.dates.addItem(it)
        if self.dates.count():
            self.dates.setCurrentRow(0)
        self._update_open()

    def _update_open(self):
        self.open_btn.setEnabled(self.dates.currentItem() is not None)

    def accept(self):
        item = self.dates.currentItem()
        if item is None:
            return
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        self.prefs.skd_department = self.chosen.department
        super().accept()

    def done(self, r):
        if self.worker and self.worker.isRunning():
            self.worker.wait()  # a ShotGrid read finishes on its own; don't destroy a running thread
        super().done(r)
