"""Application entry point and theme."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .playlist import EXT

BG, PANEL, RAISE, WELL, LINE, FG, MUTED, ACCENT, INK = (
    "#121419", "#1a1d24", "#232731", "#0b0c10", "#2d323d", "#e6e8ee", "#8a91a2", "#f2b84b", "#1b1404")

STYLE = f"""
QMainWindow, QWidget {{ background: {BG}; color: {FG}; }}
QSplitter::handle {{ background: {BG}; width: 1px; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {LINE}; color: {MUTED}; }}
QMenuBar {{ background: {PANEL}; }}
QToolTip {{ background: {FG}; color: {BG}; border: 0; padding: 4px 6px; }}

#Rail, #Transport {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 8px; }}
#Rail QWidget, #Transport QWidget, #Transport QLabel {{ background: transparent; }}
#RailSep {{ background: {LINE}; }}
#Sep {{ background: {LINE}; }}
#Inspector {{ background: {PANEL}; }}
QScrollArea {{ background: {PANEL}; border-left: 1px solid {LINE}; }}
#Inspector QWidget {{ background: transparent; }}
#Inspector QToolButton#PickButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
#Inspector QToolButton#VisToggle {{ color: #5b6170; padding: 2px 6px; font-family: Menlo, Consolas, monospace; font-size: 11px; }}
#Inspector QToolButton#VisToggle:checked {{ color: {FG}; background: {RAISE}; border-color: {LINE}; }}
PlaylistPanel {{ background: {PANEL}; border-right: 1px solid {LINE}; }}

QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 5px; }}
QToolButton:hover {{ background: {RAISE}; }}
QToolButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
#ToolButton {{ padding: 8px; }}
#ToolButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
#BlendButton {{ background: {RAISE}; border: 1px solid {LINE}; padding: 6px; font-weight: 600; font-size: 12px; }}
#BlendButton:checked {{ border: 2px solid {ACCENT}; background: {RAISE}; }}
#Swatch {{ border: 1px solid rgba(255,255,255,30); border-radius: 5px; }}
#Swatch:checked {{ border: 2px solid {FG}; }}
#EmptySwatch {{ border: 1px dashed {LINE}; border-radius: 5px; }}
#DeleteBtn {{ color: {MUTED}; padding: 0 4px; }}
#DeleteBtn:hover {{ color: #ef6464; }}

QPushButton {{ background: {RAISE}; border: 1px solid {LINE}; border-radius: 6px; padding: 6px 12px; }}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:disabled {{ color: #5b6170; }}
QPushButton#Primary {{ background: {ACCENT}; color: {INK}; border-color: {ACCENT}; font-weight: 600; }}
QPushButton#ModeTab {{ background: {WELL}; border: 1px solid {LINE}; border-radius: 4px; padding: 3px 0; font-size: 12px; font-weight: 600; color: {MUTED}; }}
QPushButton#ModeTab:checked {{ background: {RAISE}; color: {FG}; }}
QPushButton#FrameChip {{ background: {WELL}; color: #8fb3ff; font-family: Menlo, Consolas, monospace; font-size: 11px; padding: 1px 6px; border-radius: 4px; }}
QPushButton#RecentLink {{ background: transparent; border: 0; color: #8fb3ff; padding: 2px; }}
QPushButton#RecentLink:hover {{ text-decoration: underline; }}
QPushButton#AuthorButton {{ background: transparent; border: 0; padding: 0 8px; font-weight: 600; }}

QLineEdit, QPlainTextEdit, QSpinBox, QComboBox {{ background: {WELL}; border: 1px solid {LINE}; border-radius: 5px; padding: 4px 6px; selection-background-color: {ACCENT}; selection-color: {INK}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QLineEdit#PlaylistName {{ background: transparent; border: 1px solid transparent; font-size: 16px; font-weight: 700; padding: 2px 4px; }}
QLineEdit#PlaylistName:hover {{ border-color: {LINE}; }}
QLineEdit#HexEdit, QSpinBox {{ font-family: Menlo, Consolas, monospace; }}
QLineEdit#ChanValue {{ font-family: Menlo, Consolas, monospace; font-size: 12px; padding: 2px 4px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {LINE}; selection-background-color: {RAISE}; }}

QListWidget {{ background: {WELL}; border: 1px solid {LINE}; border-radius: 6px; padding: 2px; outline: 0; }}
QListWidget::item {{ padding: 4px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {RAISE}; color: {FG}; border: 1px solid {ACCENT}; }}
#StrokeList {{ font-size: 12px; }}
QTreeWidget#NotesTree {{ background: {WELL}; border: 1px solid {LINE}; border-radius: 6px; padding: 2px; outline: 0; font-size: 12px; }}
QTreeWidget#NotesTree::item {{ padding: 3px 2px; border-radius: 4px; }}
QTreeWidget#NotesTree::item:selected {{ background: {RAISE}; color: {FG}; border: 1px solid {ACCENT}; }}
QTreeWidget#NotesTree::branch:selected {{ background: transparent; border: 0; }}
QToolButton#BlendChip {{ font-size: 11px; padding: 1px 7px; margin: 1px 2px; border: 1px solid {LINE}; border-radius: 4px; background: {RAISE}; color: {FG}; }}
QToolButton#BlendChip:hover {{ border-color: {ACCENT}; }}
QToolButton#BlendChip:disabled {{ color: {MUTED}; background: transparent; }}
QToolButton#BlendChip::menu-indicator {{ image: none; width: 0; }}

QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {FG}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ height: 0; }}

#SectionLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
#Muted {{ color: {MUTED}; font-size: 12px; }}
#Value {{ color: {MUTED}; font-family: Menlo, Consolas, monospace; font-size: 12px; min-width: 36px; }}
#ChanLabel {{ color: {MUTED}; font-family: Menlo, Consolas, monospace; font-size: 12px; }}
#Timecode {{ font-family: Menlo, Consolas, monospace; font-size: 13px; background: {WELL}; border: 1px solid {LINE}; border-radius: 5px; padding: 3px 8px; }}
#Transport #Timecode {{ background: {WELL}; }}
#Comment, #CommentHere {{ background: {RAISE}; border: 1px solid {LINE}; border-radius: 6px; }}
#CommentHere {{ border-color: #8fb3ff; }}
#Comment QLabel, #CommentHere QLabel {{ background: transparent; }}
#WelcomeTitle {{ font-size: 34px; font-weight: 700; }}

/* Menus last, so no container rule (like the inspector's transparent background) can override them.
   Square corners: macOS can only draw rounded menu corners by making the whole menu translucent. */
QMenu, QWidget QMenu, #Inspector QMenu, QMenu#ContextMenu {{ background-color: {PANEL}; border: 1px solid {LINE}; padding: 4px; }}
QMenu::item {{ background-color: transparent; color: {FG}; padding: 5px 18px 5px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {ACCENT}; color: {INK}; }}
QMenu::item:disabled {{ color: #5b6170; background-color: transparent; }}
QMenu::separator {{ height: 1px; background-color: {LINE}; margin: 4px 6px; }}
"""


def checkbox_style() -> str:
    """Checkboxes with a real check mark (drawn once to PNGs, since stylesheets can't draw one)."""
    from .icons import checkbox_images
    on, off = checkbox_images()
    rules = []
    for sel in ("QCheckBox::indicator", "QTreeWidget#NotesTree::indicator"):
        rules.append(f"{sel} {{ width: 14px; height: 14px; }}")
        rules.append(f"{sel}:unchecked {{ image: url({off}); }}")
        rules.append(f"{sel}:checked {{ image: url({on}); }}")
    return "\n" + "\n".join(rules)


class App(QApplication):
    """Handles macOS 'open document' events (double-clicking a playlist in Finder)."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self.pending = [a for a in argv[1:] if a.endswith(EXT)]

    def event(self, e):
        if e.type() == QEvent.Type.FileOpen:
            path = e.file()
            if path.endswith(EXT):
                if self.window:
                    self.window.open_playlist(path)
                else:
                    self.pending.append(path)
            return True
        return super().event(e)


def check(paths: list[str]) -> int:
    """`BlendSketch --check clip.mov ...`: report whether each file decodes, without opening a window."""
    import time

    from PySide6.QtGui import QGuiApplication

    from .media import MediaError, VideoReader, frame_to_qimage, probe
    QGuiApplication(sys.argv[:1])
    bad = 0
    for p in paths:
        try:
            info = probe(p)
            r = VideoReader(p)
            t = time.perf_counter()
            n = min(24, info["frames"])
            for i in range(n):
                f = r.read(i)
                frame_to_qimage(f, info["width"] // 2, info["height"] // 2)
            fps = n / (time.perf_counter() - t)
            r.close()
            audio = "no"
            if info["audio"]:
                from .media import AudioPlayer
                ap = AudioPlayer(p)
                ap.set_muted(True)
                ap.start(0.0)
                for _ in range(5):
                    ap._feed()
                audio = f"yes, {len(ap.pending) + (ap.sink.bufferSize() - ap.sink.bytesFree() if ap.sink else 0)} bytes decoded" \
                    if ap.ok else "yes, but no audio output device"
                ap.stop()
            print(f"OK   {p}\n     {info['width']}x{info['height']} {info['fps']:g} fps, {info['frames']} frames, "
                  f"{info['codec']} {info['pix_fmt']}, audio: {audio}, decodes at {fps:.0f} fps")
        except (MediaError, Exception) as e:
            bad += 1
            print(f"FAIL {p}\n     {e}")
    return 1 if bad else 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        sys.exit(check(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] in ("--update", "--version"):
        from .update import cli
        sys.exit(cli(sys.argv[1:]))
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = App(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, FG), (QPalette.ColorRole.Base, WELL),
                    (QPalette.ColorRole.Text, FG), (QPalette.ColorRole.Button, RAISE), (QPalette.ColorRole.ButtonText, FG),
                    (QPalette.ColorRole.Highlight, ACCENT), (QPalette.ColorRole.HighlightedText, INK),
                    (QPalette.ColorRole.ToolTipBase, FG), (QPalette.ColorRole.ToolTipText, BG),
                    (QPalette.ColorRole.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    f = QFont()
    f.setPointSize(13 if sys.platform == "darwin" else 10)
    app.setFont(f)
    app.setStyleSheet(STYLE + checkbox_style())

    from .prefs import Prefs
    from .window import MainWindow
    win = MainWindow(Prefs())
    app.window = win
    win.show()
    for p in app.pending:
        win.open_playlist(p)
    sys.exit(app.exec())
