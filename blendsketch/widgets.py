"""Inspector widgets: blend modes, color (recent colors, hex, eyedropper, channel sliders) and brush settings."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QIntValidator, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from . import colors as C
from .icons import icon
from .strokes import BLENDS, MAIN_BLENDS

ACCENT = "#f2b84b"


def section_label(text: str) -> QLabel:
    lab = QLabel(text.upper())
    lab.setObjectName("SectionLabel")
    return lab


# ---------------------------------------------------------------- blend modes

class BlendPanel(QWidget):
    changed = Signal(str)
    FORMULAS = {"normal": "b", "multiply": "a×b", "screen": "1−(1−a)(1−b)"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "multiply"
        self.color = "#6f7fb8"
        self.opacity = 1.0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.buttons = {}
        group = QButtonGroup(self)
        for i, key in enumerate(MAIN_BLENDS):
            b = QToolButton()
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            b.setText(f"{BLENDS[key][0]}  {i + 1}\n{self.FORMULAS[key]}")
            b.setObjectName("BlendButton")
            b.setIconSize(QSize(76, 22))
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setToolTip(f"{BLENDS[key][0]} (key {i + 1})")
            b.clicked.connect(lambda _=False, k=key: self.set_mode(k, emit=True))
            group.addButton(b)
            row.addWidget(b)
            self.buttons[key] = b
        lay.addLayout(row)
        self.more = QComboBox()
        self.more.addItem("More modes…", "")
        for key, (label, _) in BLENDS.items():
            if key not in MAIN_BLENDS:
                self.more.addItem(label, key)
        self.more.activated.connect(self._more_picked)
        lay.addWidget(self.more)
        self.set_mode(self.mode)

    def _more_picked(self, idx):
        key = self.more.itemData(idx)
        if key:
            self.set_mode(key, emit=True)

    def set_mode(self, key: str, emit=False):
        self.mode = key
        for k, b in self.buttons.items():
            b.setChecked(k == key)
        i = self.more.findData(key if key not in MAIN_BLENDS else "")
        self.more.setCurrentIndex(max(0, i))
        if emit:
            self.changed.emit(key)

    def set_preview(self, color: str, opacity: float):
        self.color, self.opacity = color, opacity
        for key, b in self.buttons.items():
            b.setIcon(QIcon(self._preview(key)))

    def _preview(self, key) -> QPixmap:
        w, h = 152, 44
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        grad = QLinearGradient(0, 0, w, 0)
        grad.setColorAt(0, QColor("#000"))
        grad.setColorAt(1, QColor("#fff"))
        p.fillRect(0, 0, w, h, grad)
        p.setCompositionMode(BLENDS[key][1])
        p.setOpacity(self.opacity)
        p.fillRect(QRectF(0, h * 0.3, w, h * 0.4), QColor(self.color))
        p.end()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(2)
        return pm


# ---------------------------------------------------------------- color

class GradientSlider(QWidget):
    valueChanged = Signal(int)

    def __init__(self, maximum: int, parent=None):
        super().__init__(parent)
        self.maximum = maximum
        self.value = 0
        self.stops: list[str] = ["#000", "#fff"]
        self.setFixedHeight(18)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set(self, value: int, stops: list[str]):
        self.value, self.stops = value, stops
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(7, 3, self.width() - 14, 12)
        grad = QLinearGradient(track.left(), 0, track.right(), 0)
        n = len(self.stops) - 1
        for i, c in enumerate(self.stops):
            grad.setColorAt(i / n, QColor(c))
        path = QPainterPath()
        path.addRoundedRect(track, 6, 6)
        p.fillPath(path, grad)
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.drawPath(path)
        x = track.left() + track.width() * self.value / self.maximum
        p.setPen(QPen(QColor("#15171c"), 2))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(x, track.center().y()), 7, 7)
        if self.hasFocus():
            p.setPen(QPen(QColor(ACCENT), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(x, track.center().y()), 9, 9)

    def _set_from(self, x):
        t = (x - 7) / max(1, self.width() - 14)
        v = int(round(max(0.0, min(1.0, t)) * self.maximum))
        if v != self.value:
            self.value = v
            self.update()
            self.valueChanged.emit(v)

    def mousePressEvent(self, e):
        self._set_from(e.position().x())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._set_from(e.position().x())

    def keyPressEvent(self, e):
        step = 10 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        if e.key() in (Qt.Key.Key_Left, Qt.Key.Key_Down):
            self.value = max(0, self.value - step)
        elif e.key() in (Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.value = min(self.maximum, self.value + step)
        else:
            return super().keyPressEvent(e)
        self.update()
        self.valueChanged.emit(self.value)


class ColorPanel(QWidget):
    colorChanged = Signal(str)       # live edits (sliders, hex, swatches)
    pickToggled = Signal(bool)       # eyedropper button

    MODES = ("RGB", "HSV", "HSL", "CMYK")

    def __init__(self, prefs, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.h, self.s, self.v = 0.0, 0.0, 0.0
        self.hex = "#000000"
        self.mode = prefs.color_mode if prefs.color_mode in self.MODES else "HSV"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.chip = QLabel()
        self.chip.setFixedSize(30, 28)
        self.chip.setObjectName("ColorChip")
        self.hex_edit = QLineEdit()
        self.hex_edit.setObjectName("HexEdit")
        self.hex_edit.setMaxLength(7)
        self.hex_edit.textEdited.connect(self._hex_typed)
        self.hex_edit.editingFinished.connect(self._sync)
        self.pick_btn = QToolButton()
        self.pick_btn.setObjectName("PickButton")
        self.pick_btn.setCheckable(True)
        self.pick_btn.setIcon(icon("picker"))
        self.pick_btn.setToolTip("Eyedropper (I, or hold Alt/Option)")
        self.pick_btn.toggled.connect(self.pickToggled)
        self.sys_btn = QToolButton()
        self.sys_btn.setIcon(icon("palette"))
        self.sys_btn.setToolTip("System color picker")
        self.sys_btn.clicked.connect(self._system_picker)
        top.addWidget(self.chip)
        top.addWidget(self.hex_edit, 1)
        top.addWidget(self.pick_btn)
        top.addWidget(self.sys_btn)
        lay.addLayout(top)

        lab = QLabel("Recent colors")
        lab.setObjectName("Muted")
        lay.addWidget(lab)
        self.swatch_grid = QGridLayout()
        self.swatch_grid.setSpacing(5)
        lay.addLayout(self.swatch_grid)

        tabs = QHBoxLayout()
        tabs.setSpacing(2)
        self.mode_buttons = {}
        group = QButtonGroup(self)
        for m in self.MODES:
            b = QPushButton(m)
            b.setCheckable(True)
            b.setObjectName("ModeTab")
            b.clicked.connect(lambda _=False, mm=m: self.set_mode(mm))
            group.addButton(b)
            tabs.addWidget(b)
            self.mode_buttons[m] = b
        lay.addLayout(tabs)

        self.chan_box = QGridLayout()
        self.chan_box.setHorizontalSpacing(8)
        self.chan_box.setVerticalSpacing(6)
        lay.addLayout(self.chan_box)
        self.rows = []
        self.render_recents(prefs.recent_colors)
        self.set_mode(self.mode)

    # -- channel definitions (HSV is the source of truth)

    def rgb(self):
        return C.hsv_to_rgb(self.h, self.s, self.v)

    def _adopt(self, h, s, v):
        self.v = v
        if v > 0:
            self.s = s
            if s > 0:
                self.h = h

    def _channels(self):
        def rgb_ch(i, k):
            def get():
                return self.rgb()[i]

            def set_(x):
                c = list(self.rgb())
                c[i] = x
                self._adopt(*C.rgb_to_hsv(*c))

            def at(t):
                c = list(self.rgb())
                c[i] = int(round(t * 255))
                return c
            return k, 255, get, set_, at

        def hsv_ch(key, k):
            mx = 360 if key == "h" else 100

            def get():
                return int(round(self.h if key == "h" else getattr(self, key) * 100))

            def set_(x):
                setattr(self, key, x if key == "h" else x / 100)

            def at(t):
                if key == "h":
                    return C.hsv_to_rgb(t * 360, 1, 1)
                return C.hsv_to_rgb(self.h, t if key == "s" else self.s, t if key == "v" else self.v)
            return k, mx, get, set_, at

        def hsl_ch(idx, k):
            mx = 360 if idx == 0 else 100

            def get():
                v = C.hsv_to_hsl(self.h, self.s, self.v)[idx]
                return int(round(v if idx == 0 else v * 100))

            def set_(x):
                hsl = list(C.hsv_to_hsl(self.h, self.s, self.v))
                hsl[idx] = x if idx == 0 else x / 100
                self._adopt(*C.hsl_to_hsv(*hsl))
                self.h = hsl[0]

            def at(t):
                if idx == 0:
                    return C.hsv_to_rgb(t * 360, 1, 1)
                hsl = list(C.hsv_to_hsl(self.h, self.s, self.v))
                hsl[idx] = t
                return C.hsv_to_rgb(*C.hsl_to_hsv(*hsl))
            return k, mx, get, set_, at

        def cmyk_ch(i, k):
            def get():
                return int(round(C.rgb_to_cmyk(*self.rgb())[i] * 100))

            def set_(x):
                c = list(C.rgb_to_cmyk(*self.rgb()))
                c[i] = x / 100
                self._adopt(*C.rgb_to_hsv(*C.cmyk_to_rgb(*c)))

            def at(t):
                c = list(C.rgb_to_cmyk(*self.rgb()))
                c[i] = t
                return C.cmyk_to_rgb(*c)
            return k, 100, get, set_, at

        return {
            "RGB": [rgb_ch(0, "R"), rgb_ch(1, "G"), rgb_ch(2, "B")],
            "HSV": [hsv_ch("h", "H"), hsv_ch("s", "S"), hsv_ch("v", "V")],
            "HSL": [hsl_ch(0, "H"), hsl_ch(1, "S"), hsl_ch(2, "L")],
            "CMYK": [cmyk_ch(0, "C"), cmyk_ch(1, "M"), cmyk_ch(2, "Y"), cmyk_ch(3, "K")],
        }[self.mode]

    def set_mode(self, mode: str):
        self.mode = mode
        self.prefs.color_mode = mode
        for m, b in self.mode_buttons.items():
            b.setChecked(m == mode)
        while self.chan_box.count():
            w = self.chan_box.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        self.rows = []
        for r, (k, mx, get, set_, at) in enumerate(self._channels()):
            lab = QLabel(k)
            lab.setObjectName("ChanLabel")
            sl = GradientSlider(mx)
            spin = QLineEdit()
            spin.setObjectName("ChanValue")
            spin.setValidator(QIntValidator(0, mx, spin))
            spin.setFixedWidth(50)
            spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            sl.valueChanged.connect(lambda v, s=set_: self._edit(s, v))
            spin.textEdited.connect(lambda t, s=set_, m=mx: t.isdigit() and self._edit(s, min(m, int(t))))
            spin.editingFinished.connect(self._sync)
            self.chan_box.addWidget(lab, r, 0)
            self.chan_box.addWidget(sl, r, 1)
            self.chan_box.addWidget(spin, r, 2)
            self.rows.append((get, at, sl, spin))
        self._sync()

    def _edit(self, setter, v):
        setter(v)
        self._apply()

    def _apply(self):
        self.hex = C.to_hex(self.rgb())
        self._sync()
        self.colorChanged.emit(self.hex)

    def set_hex(self, hex_: str, emit=False):
        self._adopt(*C.rgb_to_hsv(*C.hex_to_rgb(hex_)))
        self.hex = C.to_hex(self.rgb())
        self._sync()
        if emit:
            self.colorChanged.emit(self.hex)

    def _hex_typed(self, text):
        h = C.parse_hex(text)
        if h:
            self._adopt(*C.rgb_to_hsv(*C.hex_to_rgb(h)))
            self.hex = h
            self._sync(skip_hex=True)
            self.colorChanged.emit(h)

    def _sync(self, skip_hex=False):
        for get, at, sl, spin in self.rows:
            n = 12 if sl.maximum == 360 else 8
            sl.set(get(), [C.to_hex(at(i / n)) for i in range(n + 1)])
            if not spin.hasFocus():
                spin.setText(str(get()))
        self.chip.setStyleSheet(f"background: {self.hex}; border-radius: 5px; border: 1px solid rgba(255,255,255,40);")
        if not skip_hex and not self.hex_edit.hasFocus():
            self.hex_edit.setText(self.hex)
        for i in range(self.swatch_grid.count()):
            w = self.swatch_grid.itemAt(i).widget()
            if isinstance(w, QToolButton) and w.property("hex"):
                w.setChecked(w.property("hex") == self.hex)

    def render_recents(self, recents: list[str]):
        while self.swatch_grid.count():
            w = self.swatch_grid.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for i in range(12):
            b = QToolButton()
            b.setFixedSize(30, 26)
            if i < len(recents):
                c = recents[i]
                b.setProperty("hex", c)
                b.setCheckable(True)
                b.setObjectName("Swatch")
                b.setStyleSheet(f"QToolButton#Swatch {{ background: {c}; }}")
                b.setToolTip(c)
                b.clicked.connect(lambda _=False, cc=c: self.set_hex(cc, emit=True))
            else:
                b.setObjectName("EmptySwatch")
                b.setEnabled(False)
            self.swatch_grid.addWidget(b, i // 6, i % 6)
        self._sync()

    def _system_picker(self):
        c = QColorDialog.getColor(QColor(self.hex), self, "Color")
        if c.isValid():
            self.set_hex(c.name(), emit=True)


# ---------------------------------------------------------------- brush

class BrushPanel(QWidget):
    sizeChanged = Signal(int)
    opacityChanged = Signal(float)
    pressureChanged = Signal(bool)
    fillChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(1, 300)
        self.size_val = QLabel()
        self.size_val.setObjectName("Value")
        self.size.valueChanged.connect(lambda v: (self.size_val.setText(str(v)), self.sizeChanged.emit(v)))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.op_val = QLabel()
        self.op_val.setObjectName("Value")
        self.opacity.valueChanged.connect(lambda v: (self.op_val.setText(f"{v}%"), self.opacityChanged.emit(v / 100)))
        grid.addWidget(QLabel("Size"), 0, 0)
        grid.addWidget(self.size, 0, 1)
        grid.addWidget(self.size_val, 0, 2)
        self.op_label = QLabel("Opacity")
        grid.addWidget(self.op_label, 1, 0)
        grid.addWidget(self.opacity, 1, 1)
        grid.addWidget(self.op_val, 1, 2)
        checks = QHBoxLayout()
        self.pressure = QCheckBox("Pen pressure")
        self.pressure.toggled.connect(self.pressureChanged)
        self.fill = QCheckBox("Fill shapes")
        self.fill.toggled.connect(self.fillChanged)
        checks.addWidget(self.pressure)
        checks.addWidget(self.fill)
        checks.addStretch(1)
        grid.addLayout(checks, 2, 0, 1, 3)

    def set_strength_label(self, smudge: bool):
        """The smudge tool uses the opacity slider as its strength."""
        self.op_label.setText("Strength" if smudge else "Opacity")

    def set_values(self, size: int, opacity: float, pressure: bool, fill: bool):
        for w in (self.size, self.opacity, self.pressure, self.fill):
            w.blockSignals(True)
        self.size.setValue(size)
        self.size_val.setText(str(size))
        self.opacity.setValue(int(round(opacity * 100)))
        self.op_val.setText(f"{int(round(opacity * 100))}%")
        self.pressure.setChecked(pressure)
        self.fill.setChecked(fill)
        for w in (self.size, self.opacity, self.pressure, self.fill):
            w.blockSignals(False)
