"""The viewer: shows the current frame with its layers and handles drawing input."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QLineEdit, QWidget

from . import layers as L
from . import strokes as S
from .colors import to_hex

PAINT_TOOLS = ("pen", "brush", "erase")  # stroke kinds drawn live into a layer of their own
SHAPE_TOOLS = ("line", "arrow", "rect", "ellipse")
RING_TOOLS = ("pen", "brush", "smudge", "eraser")


class Canvas(QWidget):
    strokeCommitted = Signal(dict)  # a finished stroke, for the window to bake into a layer
    strokeCancelled = Signal()      # a stroke too small to keep
    layerWanted = Signal()          # a stroke is starting and there's no layer of yours to put it in
    colorPicked = Signal(str)
    decodeSizeWanted = Signal(int, int)
    zoomChanged = Signal(float)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings  # shared with the window: tool, color, sizes, opacity, blend...
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(320, 200)
        self.src_w, self.src_h = 0, 0
        self.frame_img: QImage | None = None
        self.frame_index = 0
        self.layers: list[dict] = []           # this frame's visible layers, bottom to top
        self.active: dict | None = None         # the layer strokes go into; None makes a new one on top
        self.store: L.LayerStore = L.LayerStore()
        self.author = ""
        self.show_marks = True
        self.message = ""
        self.zoom = 1.0
        self.pan = QPointF(0, 0)
        self._disp: dict[str, tuple] = {}       # layer id -> (revision, size, image at frame_img size)
        self._base: QImage | None = None        # footage + visible layers
        self._base_key = None
        self._below: QImage | None = None       # footage + the layers under the active one
        self._above: list[dict] = []            # visible layers over the active one
        self._comp: QImage | None = None
        self.live: dict | None = None
        self._live_layer: QImage | None = None
        self._brush_state = {}
        self._smudge_img: QImage | None = None  # a copy of the active layer, smudged live
        self._smudger = None
        self._panning = None
        self._space = False
        self._mouse: QPointF | None = None
        self._tablet = False
        self._pick = None
        self._text_at = None
        self.text_edit = QLineEdit(self)
        self.text_edit.hide()
        self.text_edit.returnPressed.connect(self._commit_text)
        self.text_edit.editingFinished.connect(self._commit_text)
        self.text_edit.installEventFilter(self)
        self._toast: str | None = None
        self._toast_timer = QTimer(self, singleShot=True)
        self._toast_timer.timeout.connect(self._hide_toast)
        self._size_timer = QTimer(self, singleShot=True, interval=120)
        self._size_timer.timeout.connect(self._emit_decode_size)

    # ------------------------------------------------------------ state from the window

    @property
    def tool(self) -> str:
        return self.settings["tool"]

    def set_source(self, w: int, h: int):
        self.src_w, self.src_h = w, h
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self.frame_img = None
        self._disp.clear()
        self.invalidate()
        self._size_timer.start(0)

    def set_frame(self, index: int, img: QImage | None, layers: list[dict], active: dict | None):
        self.frame_index = index
        if img is not None:
            self.frame_img = img
        self.layers, self.active = layers, active
        self.invalidate()

    def set_store(self, store: L.LayerStore):
        self.store = store
        self._disp.clear()
        self._base = self._base_key = None
        self.invalidate()

    def set_layers(self, layers: list[dict], active: dict | None):
        self.layers, self.active = layers, active
        self.invalidate()

    def invalidate(self):
        """Something changed; _ensure_base works out whether a full rebuild is needed."""
        self._comp = None
        self.update()

    # ------------------------------------------------------------ geometry

    def _scale(self) -> float:
        if not self.src_w:
            return 1.0
        fit = min(self.width() / self.src_w, self.height() / self.src_h)
        return fit * self.zoom

    def _origin(self) -> QPointF:
        s = self._scale()
        return QPointF((self.width() - self.src_w * s) / 2 + self.pan.x(), (self.height() - self.src_h * s) / 2 + self.pan.y())

    def to_src(self, p: QPointF) -> tuple[float, float]:
        o, s = self._origin(), self._scale()
        return (p.x() - o.x()) / s, (p.y() - o.y()) / s

    def to_widget(self, x: float, y: float) -> QPointF:
        o, s = self._origin(), self._scale()
        return QPointF(o.x() + x * s, o.y() + y * s)

    def _img_scale(self) -> float:
        return self.frame_img.width() / self.src_w if self.frame_img is not None and self.src_w else 1.0

    def wanted_decode_size(self) -> tuple[int, int]:
        if not self.src_w:
            return (16, 16)
        dpr = self.devicePixelRatioF()
        k = min(1.0, self._scale() * dpr)
        w = max(16, int(self.src_w * k) // 2 * 2)
        h = max(16, int(self.src_h * k) // 2 * 2)
        return (w, h)

    def _emit_decode_size(self):
        self.decodeSizeWanted.emit(*self.wanted_decode_size())

    def resizeEvent(self, e):
        self._size_timer.start()
        super().resizeEvent(e)

    def fit(self):
        self.zoom, self.pan = 1.0, QPointF(0, 0)
        self._size_timer.start(0)
        self.zoomChanged.emit(self.zoom)
        self.update()

    def set_actual_pixels(self):
        if not self.src_w:
            return
        fit = min(self.width() / self.src_w, self.height() / self.src_h)
        self.zoom = 1 / (fit * self.devicePixelRatioF())
        self.pan = QPointF(0, 0)
        self._size_timer.start(0)
        self.zoomChanged.emit(self.zoom)
        self.update()

    def zoom_at(self, factor: float, at: QPointF):
        before = self.to_src(at)
        self.zoom = max(0.1, min(64.0, self.zoom * factor))
        after = self.to_widget(*before)
        self.pan += at - after
        self._size_timer.start()
        self.zoomChanged.emit(self.zoom)
        self.update()

    # ------------------------------------------------------------ compositing

    def _visible_layers(self) -> list[dict]:
        return self.layers if self.show_marks else []

    def _layer_image(self, rec: dict) -> QImage:
        """A layer's pixels at the decode size, rescaled only when they change."""
        full = self.store.get(rec, self.src_w, self.src_h)
        size = self.frame_img.size()
        rev = self.store.rev(rec["id"])
        hit = self._disp.get(rec["id"])
        if hit and hit[0] == rev and hit[1] == size:
            return hit[2]
        img = full if full.size() == size else full.scaled(size, Qt.AspectRatioMode.IgnoreAspectRatio,
                                                            Qt.TransformationMode.SmoothTransformation)
        self._disp[rec["id"]] = (rev, size, img)
        return img

    def _active_index(self, layers) -> int | None:
        if self.active is None:
            return None
        return next((i for i, r in enumerate(layers) if r["id"] == self.active["id"]), None)

    def _ensure_base(self):
        """Composite the visible layers onto the frame. Costs one blend per layer, however many
        strokes went into them, and only reruns when a layer, its settings or the frame change."""
        layers = self._visible_layers()
        imgs = [self._layer_image(r) for r in layers]
        key = (self.frame_img.cacheKey(), self.frame_img.size(), self.active and self.active["id"],
               [(r["id"], self.store.rev(r["id"]), r["blend"], r.get("opacity", 1.0)) for r in layers])
        if self._base is not None and key == self._base_key:
            return
        ai = self._active_index(layers)
        split = len(layers) if ai is None else ai
        below = self.frame_img.convertToFormat(L.FMT)
        for r, img in zip(layers[:split], imgs[:split]):
            L.blend_onto(below, img, r)
        if ai is None:
            self._below, self._above, self._base = None, [], below
        else:
            base = below.copy()
            for r, img in zip(layers[ai:], imgs[ai:]):
                L.blend_onto(base, img, r)
            self._below, self._above, self._base = below, layers[ai + 1:], base
        self._base_key = key
        self._comp = None
        self.store.trim({r["id"] for r in self.layers})

    def composite_image(self) -> QImage | None:
        if self.frame_img is None:
            return None
        self._ensure_base()
        if self.live is None:
            return self._base
        if self._comp is None:
            self._comp = self._compose_live()
        return self._comp

    def _compose_live(self) -> QImage:
        """The frame with the stroke being drawn shown inside its layer, under any layers above."""
        s = self.live
        k = self._img_scale()
        inside = self._below is not None  # the stroke goes into a visible layer (else a new one on top)
        out = (self._below if inside else self._base).copy()
        if s["kind"] == "smudge":
            layer = self._smudge_img
        else:
            layer = self._layer_image(self.active).copy() if inside else L.blank(out.width(), out.height())
            g = QPainter(layer)
            if s["kind"] == "erase":
                g.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            g.setOpacity(s["opacity"])
            if s["kind"] in PAINT_TOOLS and self._live_layer is not None:
                g.drawImage(0, 0, self._live_layer)
            else:
                hit = S.rasterize(s, k)
                if hit:
                    img, x, y = hit
                    g.drawImage(x, y, img)
            g.end()
        L.blend_onto(out, layer, self.active or {"blend": self.settings["blend"], "opacity": 1.0})
        if inside:
            for r in self._above:
                L.blend_onto(out, self._layer_image(r), r)
        return out

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0b0c10"))
        img = self.composite_image()
        if img is None:
            if self.message:
                p.setPen(QColor("#8a91a2"))
                p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
            return
        s = self._scale()
        o = self._origin()
        target = QRectF(o.x(), o.y(), self.src_w * s, self.src_h * s)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, s * self.devicePixelRatioF() < self._img_scale() * 0.999 or self.zoom <= 1.0)
        p.drawImage(target, img)
        self._paint_overlays(p, s)
        if self._toast:
            self._paint_toast(p)

    # ------------------------------------------------------------ on-canvas reminders

    def toast(self, text: str, ms: int = 3500):
        """Show a short reminder over the top of the picture."""
        self._toast = text
        self._toast_timer.start(ms)
        self.update()

    def _hide_toast(self):
        self._toast = None
        self.update()

    def _paint_toast(self, p: QPainter):
        f = QFont(self.font())
        f.setPixelSize(13)
        p.setFont(f)
        fm = p.fontMetrics()
        w = min(self.width() - 40, fm.horizontalAdvance(self._toast) + 32)
        text_rect = fm.boundingRect(0, 0, w - 32, 1000, Qt.TextFlag.TextWordWrap, self._toast)
        box = QRectF((self.width() - w) / 2, 18, w, text_rect.height() + 20)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#f2b84b"), 1))
        p.setBrush(QColor(11, 12, 16, 235))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("#e9ecf2"))
        p.drawText(box.adjusted(16, 10, -16, -10), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._toast)

    def _paint_overlays(self, p: QPainter, s: float):
        if self._mouse is None:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self.tool in RING_TOOLS and not self._pick and not self._panning:
            d = max(4.0, self.settings["sizes"][self.tool] * s)
            for color, w in (("#000000", 3), ("#ffffff", 1)):
                p.setPen(QPen(QColor(color), w))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(self._mouse, d / 2, d / 2)
        if self._pick:
            self._paint_loupe(p)

    def _paint_loupe(self, p: QPainter):
        pos, rgb, patch = self._pick
        L = 96
        cx = min(max(pos.x(), L / 2 + 6), self.width() - L / 2 - 6)
        top = pos.y() + 20
        box_h = 40
        if top + L + 6 + box_h > self.height():
            top = pos.y() - 20 - L - 6 - box_h
        loupe = QRectF(cx - L / 2, top, L, L)
        p.save()
        clip = loupe.adjusted(1, 1, -1, -1)
        p.setClipRect(clip)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.fillRect(loupe, QColor("#000"))
        p.drawImage(loupe, patch)
        cell = L / patch.width()
        c = QRectF(loupe.left() + cell * (patch.width() // 2), loupe.top() + cell * (patch.height() // 2), cell, cell)
        p.setPen(QPen(QColor("#000"), 3))
        p.drawRect(c)
        p.setPen(QPen(QColor("#fff"), 1))
        p.drawRect(c)
        p.restore()
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(loupe, 6, 6)
        # readout
        hexs = to_hex(rgb)
        font = QFont("Menlo")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPixelSize(11)
        p.setFont(font)
        line1 = [("R ", "#c8ccd6"), (f"{rgb[0]:>3}  ", "#ff8a8a"), ("G ", "#c8ccd6"), (f"{rgb[1]:>3}  ", "#7fe0a0"), ("B ", "#c8ccd6"), (f"{rgb[2]:>3}", "#8fb3ff")]
        fm = p.fontMetrics()
        w1 = sum(fm.horizontalAdvance(t) for t, _ in line1)
        bw = max(w1, fm.horizontalAdvance(hexs)) + 16 + 22
        box = QRectF(cx - bw / 2, top + L + 6, bw, box_h)
        p.setPen(QPen(QColor("#2d323d"), 1))
        p.setBrush(QColor(11, 12, 16, 235))
        p.drawRoundedRect(box, 5, 5)
        sw = QRectF(box.left() + 8, box.top() + 8, 16, box_h - 16)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        p.setBrush(QColor(*rgb))
        p.drawRoundedRect(sw, 3, 3)
        x = box.left() + 30
        y1 = box.top() + 16
        for t, col in line1:
            p.setPen(QColor(col))
            p.drawText(QPointF(x, y1), t)
            x += fm.horizontalAdvance(t)
        p.setPen(QColor("#8a91a2"))
        p.drawText(QPointF(box.left() + 30, y1 + 15), hexs)

    # ------------------------------------------------------------ eyedropper

    def _update_pick(self, pos: QPointF):
        img = self.composite_image()
        if img is None:
            self._pick = None
            return
        x, y = self.to_src(pos)
        if not (0 <= x < self.src_w and 0 <= y < self.src_h):
            self._pick = None
            return
        k = self._img_scale()
        ix, iy = int(x * k), int(y * k)
        n = 11
        patch = img.copy(ix - n // 2, iy - n // 2, n, n)
        c = QColor(img.pixel(min(ix, img.width() - 1), min(iy, img.height() - 1)))
        self._pick = (pos, (c.red(), c.green(), c.blue()), patch)

    def picking(self) -> bool:
        return self.tool == "picker"

    def refresh_pick(self):
        if self.picking() and self._mouse is not None:
            self._update_pick(self._mouse)
        else:
            self._pick = None
        self.update()

    # ------------------------------------------------------------ input

    def _down(self, pos: QPointF, pressure: float, button, mods):
        if self.frame_img is None:
            return
        self.setFocus()
        if button == Qt.MouseButton.MiddleButton or (button == Qt.MouseButton.LeftButton and self._space):
            self._panning = (pos, QPointF(self.pan))
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if button != Qt.MouseButton.LeftButton:
            return
        if self.picking():
            self._update_pick(pos)
            if self._pick:
                self.colorPicked.emit(to_hex(self._pick[1]))
            return
        x, y = self.to_src(pos)
        pt = (x, y, pressure)
        tool = self.tool
        if tool == "text":
            self._open_text(pos, pt)
            return
        if tool in ("eraser", "smudge"):
            if self.active is None or L.content_rect(self._layer_image(self.active)).isEmpty():
                where = "this layer is empty" if self.active is not None else "you have no layer on this frame"
                if tool == "smudge":
                    self.toast(f"You're on the smudge brush (S): it moves paint that's already on your layer, "
                               f"and {where}. Press B for the brush.")
                else:
                    self.toast(f"You're on the eraser (E), and {where}. Press B for the brush.")
                return
        elif self.active is None:
            self.layerWanted.emit()  # the window makes a layer and sets self.active before the stroke starts
        st = dict(self.settings)
        st["size"] = self.settings["sizes"][tool]
        kind = "erase" if tool == "eraser" else tool
        if kind == "erase":
            st["color"], st["opacity"] = "#000000", 1.0
        self.live = S.make_stroke(kind, self.frame_index, self.author, st, pt)
        if kind == "smudge":
            # The smudge works on the active layer's pixels, never the footage or other layers.
            self.live["blend"] = "normal"
            self._ensure_base()
            self._smudge_img = self._layer_image(self.active).copy()
            self._smudger = S.Smudger(self._smudge_img, self.live, self._img_scale())
            self._smudger.advance()
        elif kind in PAINT_TOOLS:
            k = self._img_scale()
            layer = QImage(self.frame_img.width(), self.frame_img.height(), QImage.Format.Format_ARGB32_Premultiplied)
            layer.fill(Qt.GlobalColor.transparent)
            g = QPainter(layer)
            g.setRenderHint(QPainter.RenderHint.Antialiasing)
            g.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            g.scale(k, k)
            if kind == "brush":
                S.brush_start(g, self.live, self._brush_state)
            else:
                S.pen_dot(g, self.live)
            g.end()
            self._live_layer = layer
        else:
            self.live["pts"].append(list(pt))
        self._comp = None
        self.update()

    def _move(self, pos: QPointF, pressure: float, mods):
        self._mouse = pos
        if self._panning:
            start, pan0 = self._panning
            self.pan = pan0 + (pos - start)
            self.update()
            return
        if self.picking():
            self._update_pick(pos)
            self.update()
            return
        self._pick = None
        if self.live is None:
            if self.tool in RING_TOOLS:
                self.update()
            return
        x, y = self.to_src(pos)
        s = self.live
        if s["kind"] == "smudge":
            last = s["pts"][-1]
            if math.hypot(x - last[0], y - last[1]) >= 0.75 / max(self._scale(), 0.01):
                s["pts"].append([x, y, pressure])
                self._smudger.advance()
                self._comp = None
                self.update()
            return
        if s["kind"] in PAINT_TOOLS:
            last = s["pts"][-1]
            if math.hypot(x - last[0], y - last[1]) < 0.75 / max(self._scale(), 0.01):
                return
            s["pts"].append([x, y, pressure])
            k = self._img_scale()
            g = QPainter(self._live_layer)
            g.setRenderHint(QPainter.RenderHint.Antialiasing)
            g.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            g.scale(k, k)
            if s["kind"] == "brush":
                S.brush_seg(g, s, len(s["pts"]) - 1, self._brush_state)
            else:
                S.pen_seg(g, s, len(s["pts"]) - 1)
            g.end()
        else:
            if mods & Qt.KeyboardModifier.ShiftModifier:
                x, y = self._constrain(s, x, y)
            s["pts"][1] = [x, y, pressure]
        self._comp = None
        self.update()

    def _constrain(self, s, x, y):
        a = s["pts"][0]
        dx, dy = x - a[0], y - a[1]
        if s["kind"] in ("rect", "ellipse"):
            m = max(abs(dx), abs(dy))
            return a[0] + math.copysign(m, dx or 1), a[1] + math.copysign(m, dy or 1)
        ang = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        L = math.hypot(dx, dy)
        return a[0] + math.cos(ang) * L, a[1] + math.sin(ang) * L

    def _up(self):
        if self._panning:
            self._panning = None
            self.unsetCursor()
            self._size_timer.start()
            return
        s = self.live
        if s is None:
            return
        self.live = None
        self._live_layer = None
        self._smudge_img = None
        self._smudger = None
        self._comp = None
        if s["kind"] not in PAINT_TOOLS + ("smudge",) and math.hypot(s["pts"][0][0] - s["pts"][1][0], s["pts"][0][1] - s["pts"][1][1]) < 3:
            self.strokeCancelled.emit()
            self.update()
            return
        self.strokeCommitted.emit(S.tidy(s))

    def mousePressEvent(self, e):
        if self._tablet:
            return
        self._down(e.position(), 1.0, e.button(), e.modifiers())

    def mouseMoveEvent(self, e):
        if self._tablet:
            return
        self._move(e.position(), 1.0, e.modifiers())

    def mouseReleaseEvent(self, e):
        if self._tablet:
            return
        self._up()

    def tabletEvent(self, e):
        t = e.type()
        p = max(0.05, e.pressure())
        if t == e.Type.TabletPress:
            self._tablet = True
            self._down(e.position(), p, e.button(), e.modifiers())
        elif t == e.Type.TabletMove:
            self._move(e.position(), p, e.modifiers())
        elif t == e.Type.TabletRelease:
            self._up()
            QTimer.singleShot(0, self._tablet_done)
        e.accept()

    def _tablet_done(self):
        self._tablet = False

    def leaveEvent(self, e):
        self._mouse = None
        self._pick = None
        self.update()

    def wheelEvent(self, e):
        if self.frame_img is None:
            return
        if not e.pixelDelta().isNull() and not (e.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.pan += QPointF(e.pixelDelta())  # trackpad scroll pans
            self.update()
        else:
            steps = e.angleDelta().y() / 120
            self.zoom_at(1.15 ** steps, e.position())

    def event(self, e):
        if e.type() == e.Type.NativeGesture and self.frame_img is not None:
            if e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
                self.zoom_at(1 + e.value(), e.position())
                return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = True
            e.ignore()  # let the window toggle playback when not dragging
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key.Key_Space and not e.isAutoRepeat():
            self._space = False
        super().keyReleaseEvent(e)

    # ------------------------------------------------------------ text tool

    def _open_text(self, pos: QPointF, pt):
        if self._text_at:
            self._commit_text()
        self._text_at = pt
        size = self.settings["sizes"]["text"]
        f = S.text_font({"size": size})
        f.setPixelSize(max(8, int(size * 4 * self._scale())))
        self.text_edit.setFont(f)
        self.text_edit.setStyleSheet(
            f"QLineEdit {{ background: rgba(0,0,0,90); color: {self.settings['color']}; border: 1px dashed #f2b84b; padding: 0 4px; }}")
        self.text_edit.clear()
        h = int(f.pixelSize() * 1.5)
        self.text_edit.setGeometry(int(pos.x()), int(pos.y()), 320, h)
        self.text_edit.show()
        self.text_edit.setFocus()

    def _commit_text(self):
        if not self._text_at:
            return
        text = self.text_edit.text().strip()
        pt = self._text_at
        self._text_at = None
        self.text_edit.hide()
        self.setFocus()
        if not text:
            return
        st = dict(self.settings)
        st["size"] = self.settings["sizes"]["text"]
        s = S.make_stroke("text", self.frame_index, self.author, st, (pt[0], pt[1], 1.0))
        s["text"] = text
        self.strokeCommitted.emit(S.tidy(s))

    def cancel_text(self):
        self._text_at = None
        self.text_edit.hide()

    def eventFilter(self, obj, e):
        if obj is self.text_edit and e.type() == e.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.cancel_text()
            return True
        return super().eventFilter(obj, e)

    # ------------------------------------------------------------ export

    def render_full(self, frame_img: QImage, layers) -> QImage:
        """Composite layers onto a full-resolution frame (for snapshots)."""
        out = frame_img.convertToFormat(L.FMT)
        for r in layers:
            img = self.store.get(r, self.src_w, self.src_h)
            if img.size() != out.size():
                img = img.scaled(out.size(), Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
            L.blend_onto(out, img, r)
        return out

    def sizeHint(self):
        return QSize(960, 540)
