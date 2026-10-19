"""Strokes: drawing them, and the blend modes layers composite with.

A stroke is a plain dict that lives only while it's being drawn; when it's finished it's
painted into a layer (see layers.py) and not kept. Points are in source-pixel coordinates
so marks line up at any decode or display resolution. A stroke is rasterized at full
opacity, then painted into its layer at the stroke's opacity, so a semi-transparent stroke
doesn't darken where it crosses itself.
"""

from __future__ import annotations

import math

import numpy as np

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen, QRadialGradient

from .playlist import new_id, now

CM = QPainter.CompositionMode
BLENDS = {
    "normal": ("Normal", CM.CompositionMode_SourceOver),
    "multiply": ("Multiply", CM.CompositionMode_Multiply),
    "screen": ("Screen", CM.CompositionMode_Screen),
    "overlay": ("Overlay", CM.CompositionMode_Overlay),
    "soft-light": ("Soft light", CM.CompositionMode_SoftLight),
    "hard-light": ("Hard light", CM.CompositionMode_HardLight),
    "add": ("Add (linear dodge)", CM.CompositionMode_Plus),
    "darken": ("Darken", CM.CompositionMode_Darken),
    "lighten": ("Lighten", CM.CompositionMode_Lighten),
    "color-dodge": ("Color dodge", CM.CompositionMode_ColorDodge),
    "color-burn": ("Color burn", CM.CompositionMode_ColorBurn),
    "difference": ("Difference", CM.CompositionMode_Difference),
    "exclusion": ("Exclusion", CM.CompositionMode_Exclusion),
}
MAIN_BLENDS = ("normal", "multiply", "screen")
TEXT_FAMILY = "Helvetica Neue"


def make_stroke(kind: str, frame: int, author: str, settings: dict, pt) -> dict:
    t = now()
    return {
        "id": new_id(),
        "frame": frame,
        "author": author,
        "created": t,
        "modified": t,
        "deleted": False,
        "kind": kind,
        "color": settings["color"],
        "size": settings["size"],
        "opacity": settings["opacity"],
        "blend": settings["blend"],
        "pressure": settings["pressure"],
        "fill": settings["fill"],
        "pts": [list(pt)],
    }


def tidy(stroke: dict) -> dict:
    """Round coordinates before saving so playlist files stay small."""
    stroke["pts"] = [[round(p[0], 1), round(p[1], 1), round(p[2], 2)] for p in stroke["pts"]]
    return stroke


# ---------------------------------------------------------------- painting

def _mid(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def pen_width(s, p):
    return max(0.6, s["size"] * ((0.25 + 0.75 * p) if s["pressure"] else 1))


def text_font(s) -> QFont:
    f = QFont(TEXT_FAMILY)
    f.setPixelSize(max(4, int(s["size"] * 4)))
    f.setWeight(QFont.Weight.DemiBold)
    return f


def _pen(s, w):
    pen = QPen(QColor(s["color"]))
    pen.setWidthF(w)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def pen_dot(g: QPainter, s):
    a = s["pts"][0]
    r = pen_width(s, a[2]) / 2
    g.setPen(Qt.PenStyle.NoPen)
    g.setBrush(QColor(s["color"]))
    g.drawEllipse(QPointF(a[0], a[1]), r, r)


def pen_seg(g: QPainter, s, i):
    P = s["pts"]
    a, b = P[i - 1], P[i]
    m1 = a if i == 1 else _mid(P[i - 2], a)
    m2 = _mid(a, b)
    path = QPainterPath(QPointF(m1[0], m1[1]))
    path.quadTo(QPointF(a[0], a[1]), QPointF(m2[0], m2[1]))
    g.setPen(_pen(s, pen_width(s, (a[2] + b[2]) / 2)))
    g.setBrush(Qt.BrushStyle.NoBrush)
    g.drawPath(path)


def pen_tail(g: QPainter, s):
    P = s["pts"]
    if len(P) < 2:
        return
    a, b = P[-2], P[-1]
    m = _mid(a, b)
    g.setPen(_pen(s, pen_width(s, b[2])))
    g.drawLine(QPointF(*m), QPointF(b[0], b[1]))


_sprites: dict = {}


def dab_sprite(color: str) -> QImage:
    """A soft round dab, drawn once per color at 128 px and scaled per dab."""
    spr = _sprites.get(color)
    if spr is None:
        spr = QImage(128, 128, QImage.Format.Format_ARGB32_Premultiplied)
        spr.fill(Qt.GlobalColor.transparent)
        p = QPainter(spr)
        grad = QRadialGradient(64, 64, 64)
        c = QColor(color)
        for pos, a in ((0.0, 0.32), (0.45, 0.2), (1.0, 0.0)):
            cc = QColor(c)
            cc.setAlphaF(a)
            grad.setColorAt(pos, cc)
        p.fillRect(0, 0, 128, 128, QBrush(grad))
        p.end()
        _sprites[color] = spr
    return spr


def _spacing(s):
    return max(1.0, s["size"] * 0.07)


def _dab(g, s, x, y, p):
    k = (0.35 + 0.65 * p) if s["pressure"] else 1
    r = s["size"] / 2 * k
    g.drawImage(QRectF(x - r, y - r, r * 2, r * 2), dab_sprite(s["color"]))


def brush_start(g, s, state):
    a = s["pts"][0]
    _dab(g, s, a[0], a[1], a[2])
    state["next"] = _spacing(s)


def brush_seg(g, s, i, state):
    a, b = s["pts"][i - 1], s["pts"][i]
    d = _dist(a, b)
    step = _spacing(s)
    at = state["next"]
    while at <= d:
        t = at / d if d else 0
        _dab(g, s, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
        at += step
    state["next"] = at - d


def head_len(s):
    return max(14.0, s["size"] * 4)


def paint(g: QPainter, s: dict):
    g.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    g.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    P = s["pts"]
    kind = s["kind"]
    color = QColor(s["color"])
    if kind in ("pen", "erase"):
        pen_dot(g, s)
        for i in range(1, len(P)):
            pen_seg(g, s, i)
        pen_tail(g, s)
    elif kind == "brush":
        state = {}
        brush_start(g, s, state)
        for i in range(1, len(P)):
            brush_seg(g, s, i, state)
    elif kind in ("line", "arrow"):
        a, b = P[0], P[1]
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        L = head_len(s)
        ex, ey = b[0], b[1]
        if kind == "arrow":
            ex -= math.cos(ang) * L * 0.6
            ey -= math.sin(ang) * L * 0.6
        g.setPen(_pen(s, s["size"]))
        g.drawLine(QPointF(a[0], a[1]), QPointF(ex, ey))
        if kind == "arrow":
            head = QPainterPath(QPointF(b[0], b[1]))
            head.lineTo(b[0] - math.cos(ang - 0.45) * L, b[1] - math.sin(ang - 0.45) * L)
            head.lineTo(b[0] - math.cos(ang + 0.45) * L, b[1] - math.sin(ang + 0.45) * L)
            head.closeSubpath()
            g.setPen(Qt.PenStyle.NoPen)
            g.setBrush(color)
            g.drawPath(head)
    elif kind in ("rect", "ellipse"):
        a, b = P[0], P[1]
        r = QRectF(QPointF(min(a[0], b[0]), min(a[1], b[1])), QPointF(max(a[0], b[0]), max(a[1], b[1])))
        if s["fill"]:
            g.setPen(Qt.PenStyle.NoPen)
            g.setBrush(color)
        else:
            pen = _pen(s, s["size"])
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin if kind == "rect" else Qt.PenJoinStyle.RoundJoin)
            g.setPen(pen)
            g.setBrush(Qt.BrushStyle.NoBrush)
        g.drawRect(r) if kind == "rect" else g.drawEllipse(r)
    elif kind == "text":
        g.setFont(text_font(s))
        g.setPen(color)
        fm = QFontMetricsF(g.font())
        g.drawText(QPointF(P[0][0], P[0][1] + fm.ascent()), s.get("text", ""))


def bbox(s: dict) -> QRectF:
    P = s["pts"]
    if s["kind"] == "text":
        fm = QFontMetricsF(text_font(s))
        w = fm.horizontalAdvance(s.get("text", ""))
        return QRectF(P[0][0] - 4, P[0][1] - 4, w + 8, fm.height() + 8)
    xs = [p[0] for p in P]
    ys = [p[1] for p in P]
    pad = (head_len(s) if s["kind"] == "arrow" else 0) + s["size"] / 2 + 3
    return QRectF(min(xs) - pad, min(ys) - pad, max(xs) - min(xs) + pad * 2, max(ys) - min(ys) + pad * 2)


def rasterize(s: dict, scale: float):
    r = bbox(s)
    x0, y0 = math.floor(r.left() * scale), math.floor(r.top() * scale)
    w, h = math.ceil(r.right() * scale) - x0, math.ceil(r.bottom() * scale) - y0
    if w < 1 or h < 1 or w * h > 80_000_000:
        return None
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    g = QPainter(img)
    g.translate(-x0, -y0)
    g.scale(scale, scale)
    paint(g, s)
    g.end()
    return img, x0, y0


# ---------------------------------------------------------------- smudge

def image_array(img: QImage) -> np.ndarray:
    """A writable H x W x 4 uint8 view of a 32-bit QImage's pixels."""
    h, w, bpl = img.height(), img.width(), img.bytesPerLine()
    buf = np.frombuffer(img.bits(), np.uint8, count=h * bpl)
    return buf.reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)


class Smudger:
    """Procreate-style smudge: the brush picks up the pixels under it and drags them along.

    Works on one layer's pixels, never the footage. Each dab blends the paint it's carrying
    into the image under a soft round mask, then
    carries the result forward, so the smear fades out over the length of the stroke.
    Strength comes from the stroke's opacity, scaled by pen pressure.
    Works in premultiplied ARGB, which is what keeps edges from going dark.
    """

    def __init__(self, img: QImage, s: dict, scale: float):
        self.img, self.s, self.scale = img, s, scale
        self.arr = image_array(img)
        self.r = max(1.0, s["size"] * scale / 2)
        R = self.R = int(math.ceil(self.r))
        yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
        d2 = (xx * xx + yy * yy) / (self.r * self.r)
        self.mask = np.clip(1 - d2, 0, 1) ** 2  # soft edge
        self.carry = None
        self.next = 0.0
        self.done = 0  # points consumed

    def _dab(self, cx: float, cy: float, p: float):
        R, D = self.R, 2 * self.R + 1
        H, W = self.arr.shape[:2]
        x0, y0 = int(round(cx)) - R, int(round(cy)) - R
        sx, sy = max(0, -x0), max(0, -y0)
        ex, ey = min(D, W - x0), min(D, H - y0)
        if ex <= sx or ey <= sy:
            return
        region = self.arr[y0 + sy:y0 + ey, x0 + sx:x0 + ex]
        if not region[..., 3].any() and (self.carry is None or not self.carry[sy:ey, sx:ex, 3].any()):
            # Nothing under the brush and nothing being carried: a dab can't change anything.
            if self.carry is None:
                self.carry = np.zeros((D, D, 4), np.float32)
            return
        tgt = region.astype(np.float32)
        if self.carry is None:
            self.carry = np.zeros((D, D, 4), np.float32)
            self.carry[sy:ey, sx:ex] = tgt
            return
        k = (0.3 + 0.7 * p) if self.s["pressure"] else 1.0
        a = (self.mask[sy:ey, sx:ex] * self.s["opacity"] * k)[..., None]
        new = tgt + (self.carry[sy:ey, sx:ex] - tgt) * a
        region[...] = np.clip(new + 0.5, 0, 255).astype(np.uint8)
        self.carry[sy:ey, sx:ex] = new

    def advance(self):
        """Smudge along any points added since the last call."""
        P = self.s["pts"]
        k = self.scale
        step = max(1.0, self.r * 0.18)
        if self.done == 0 and P:
            a = P[0]
            self._dab(a[0] * k, a[1] * k, a[2])
            self.done = 1
        while self.done < len(P):
            a, b = P[self.done - 1], P[self.done]
            ax, ay, bx, by = a[0] * k, a[1] * k, b[0] * k, b[1] * k
            d = math.hypot(bx - ax, by - ay)
            at = self.next
            while at <= d:
                t = at / d if d else 0
                self._dab(ax + (bx - ax) * t, ay + (by - ay) * t, a[2] + (b[2] - a[2]) * t)
                at += step
            self.next = at - d
            self.done += 1

    def run(self):
        self.advance()
