"""Layers: baked bitmaps that strokes are painted into, like Procreate.

A layer is a record in the playlist (id, frame, author, name, blend, opacity, order) plus its
pixels, a source-resolution image saved as a PNG cropped to what's been painted. Strokes
aren't kept: each one is painted into a layer when it's finished, with Normal blending at
the stroke's opacity, so drawing costs the same however many strokes a frame has. The
layer as a whole is blended onto the frame with its own blend mode and opacity.

Only a layer's author paints on it, so a merge can't mix two people's pixels: the newest
version of a layer wins, like any other record.
"""

from __future__ import annotations

import base64

import numpy as np

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect, QSize, Qt
from PySide6.QtGui import QImage, QPainter

from . import strokes as S
from .playlist import new_id, now

FMT = QImage.Format.Format_ARGB32_Premultiplied
CM = QPainter.CompositionMode


def make_layer(frame: int, author: str, name: str, blend: str, lid: str | None = None) -> dict:
    t = now()
    return {"id": lid or new_id(), "frame": frame, "author": author, "created": t, "modified": t, "deleted": False,
            "name": name, "blend": blend, "opacity": 1.0, "png": "", "x": 0, "y": 0}


def layer_order(rec: dict) -> float:
    """Where a layer sits in its frame's stack (higher is on top). Layers made before
    reordering existed have no order and stack by creation time."""
    return rec.get("order", rec["created"])


def blank(w: int, h: int) -> QImage:
    img = QImage(w, h, FMT)
    img.fill(Qt.GlobalColor.transparent)
    return img


def blend_onto(target: QImage, img: QImage, rec: dict):
    """Composite a layer image (same size as target) with the layer's blend mode and opacity."""
    g = QPainter(target)
    g.setCompositionMode(S.BLENDS.get(rec["blend"], S.BLENDS["normal"])[1])
    g.setOpacity(rec.get("opacity", 1.0))
    g.drawImage(0, 0, img)
    g.end()


# ---------------------------------------------------------------- pixels <-> file

def content_rect(img: QImage) -> QRect:
    """The bounding box of the painted (non-transparent) pixels."""
    h, w, bpl = img.height(), img.width(), img.bytesPerLine()
    a = np.frombuffer(img.constBits(), np.uint8, count=h * bpl).reshape(h, bpl)[:, : w * 4].reshape(h, w, 4)[..., 3]
    rows = np.flatnonzero(a.any(axis=1))
    if not rows.size:
        return QRect()
    cols = np.flatnonzero(a[rows[0]:rows[-1] + 1].any(axis=0))
    return QRect(int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1))


def encode(img: QImage) -> tuple[str, int, int]:
    """(base64 PNG of the painted area, x, y). An empty layer is ("", 0, 0)."""
    r = content_rect(img)
    if r.isEmpty():
        return "", 0, 0
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.copy(r).save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode("ascii"), r.x(), r.y()


def decode(rec: dict, w: int, h: int) -> QImage:
    img = blank(w, h)
    if rec.get("png"):
        part = QImage.fromData(QByteArray(base64.b64decode(rec["png"])), "PNG")
        if not part.isNull():
            g = QPainter(img)
            g.setCompositionMode(CM.CompositionMode_Source)
            g.drawImage(rec.get("x", 0), rec.get("y", 0), part)
            g.end()
    return img


# ---------------------------------------------------------------- baking strokes

def stroke_rect(s: dict, img: QImage, scale: float = 1.0) -> QRect:
    """The pixels a stroke can change, in an image at `scale` x source resolution."""
    r = S.bbox(s)
    out = QRect(int(r.left() * scale) - 2, int(r.top() * scale) - 2, int(r.width() * scale) + 5, int(r.height() * scale) + 5)
    return out.intersected(img.rect())


def paint_stroke(img: QImage, s: dict, scale: float = 1.0):
    """Paint one finished stroke into a layer image whose pixels are `scale` x source pixels."""
    if s["kind"] == "smudge":
        S.Smudger(img, s, scale).run()
        return
    hit = S.rasterize(s, scale)
    if hit is None:
        return
    part, x, y = hit
    g = QPainter(img)
    if s["kind"] == "erase":
        g.setCompositionMode(CM.CompositionMode_DestinationOut)
    g.setOpacity(s["opacity"])
    g.drawImage(x, y, part)
    g.end()


class LayerStore:
    """Every layer's pixels in memory, at source resolution, decoded from the playlist on demand.

    Records carry the pixels as of the last save; layers painted since then are `dirty` and
    their images are the truth until `flush` writes them back into the records. Each image
    has a revision number that changes whenever its pixels do, for display caches.
    """

    def __init__(self):
        self.imgs: dict[str, list] = {}  # layer id -> [image, png it matches, revision]
        self.dirty: set[str] = set()
        self._rev = 0

    def _next(self) -> int:
        self._rev += 1
        return self._rev

    def get(self, rec: dict, w: int, h: int) -> QImage:
        lid = rec["id"]
        png = rec.get("png", "")
        e = self.imgs.get(lid)
        if e is not None and lid not in self.dirty and (e[1] is not png or e[0].size() != QSize(w, h)):
            if e[1] == png and e[0].size() == QSize(w, h):
                e[1] = png  # same pixels, new string (after a merge): compare by identity from now on
            else:
                e = None  # someone else's newer version merged in
        if e is None:
            e = self.imgs[lid] = [decode(rec, w, h), png, self._next()]
        return e[0]

    def is_empty(self, rec: dict, w: int, h: int) -> bool:
        if rec["id"] not in self.dirty and not rec.get("png"):
            return True  # never painted (or only erased) since it was saved or made
        return content_rect(self.get(rec, w, h)).isEmpty()

    def rev(self, lid: str) -> int:
        e = self.imgs.get(lid)
        return e[2] if e else 0

    def _touch(self, lid: str):
        self.imgs[lid][2] = self._next()
        self.dirty.add(lid)

    def bake(self, rec: dict, s: dict, w: int, h: int) -> tuple[QRect, QImage, QImage]:
        """Paint stroke s into the layer. Returns (changed rect, pixels before, pixels after) for undo."""
        img = self.get(rec, w, h)
        r = stroke_rect(s, img)
        before = img.copy(r)
        paint_stroke(img, s)
        self._touch(rec["id"])
        return r, before, img.copy(r)

    def put_patch(self, rec: dict, w: int, h: int, rect: QRect, patch: QImage):
        img = self.get(rec, w, h)
        g = QPainter(img)
        g.setCompositionMode(CM.CompositionMode_Source)
        g.drawImage(rect.topLeft(), patch)
        g.end()
        self._touch(rec["id"])

    def flush(self, recs: dict[str, dict]):
        """Write painted layers into their records (recs: layer id -> record), before saving."""
        for lid in list(self.dirty):
            rec, e = recs.get(lid), self.imgs.get(lid)
            if rec is not None and e is not None:
                rec["png"], rec["x"], rec["y"] = encode(e[0])
                e[1] = rec["png"]
            self.dirty.discard(lid)

    def trim(self, keep: set[str], limit: int = 24):
        """Drop decoded images that can be decoded again, keeping memory bounded."""
        if len(self.imgs) > limit:
            for lid in [k for k in self.imgs if k not in keep and k not in self.dirty]:
                del self.imgs[lid]


# ---------------------------------------------------------------- older playlists

def migrate(item: dict, w: int, h: int) -> int:
    """Bake a version-1 item's strokes into layers and tombstone the strokes. Returns layers made.

    On each frame, consecutive strokes by the same person with the same blend mode become one
    layer; a smudge smudges that person's newest layer. Layer ids come from the first stroke's
    id, so two people converting the same file at once make the same layers.
    """
    live = sorted((s for s in item.get("strokes", {}).values() if not s.get("deleted") and s.get("pts")),
                  key=lambda s: s["created"])
    if not live:
        return 0
    layers = item.setdefault("layers", {})
    by_frame: dict[int, list] = {}
    for s in live:
        by_frame.setdefault(s["frame"], []).append(s)
    made = 0
    for frame, strokes in by_frame.items():
        runs: list[list] = []  # [author, blend, strokes]
        for s in strokes:
            if s["kind"] == "smudge":
                run = next((r for r in reversed(runs) if r[0] == s["author"]), None)
                if run:
                    run[2].append(s)
            elif runs and runs[-1][0] == s["author"] and runs[-1][1] == s["blend"]:
                runs[-1][2].append(s)
            else:
                runs.append([s["author"], s["blend"], [s]])
        n0 = len([r for r in layers.values() if r["frame"] == frame and not r.get("deleted")])
        for n, (author, blend, ss) in enumerate(runs, n0 + 1):
            rec = make_layer(frame, author, f"Layer {n}", blend, lid="m" + ss[0]["id"])
            rec["created"] = ss[0]["created"]
            img = blank(w, h)
            for s in ss:
                paint_stroke(img, s)
            rec["png"], rec["x"], rec["y"] = encode(img)
            layers[rec["id"]] = rec
            made += 1
    t = now()
    for s in live:
        item["strokes"][s["id"]] = {"id": s["id"], "frame": s["frame"], "author": s["author"], "created": s["created"],
                                    "modified": t, "deleted": True}
    return made
