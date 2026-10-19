"""End-to-end smoke test of the window, run offscreen. Saves screenshots to testdata/shots/.

Run: QT_QPA_PLATFORM=offscreen .venv/bin/python tests/gui_smoke.py
"""

import os
import math
import shutil
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["BLENDSKETCH_SETTINGS"] = os.path.join(tempfile.mkdtemp(), "prefs.ini")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from blendsketch.app import App, STYLE, checkbox_style  # noqa: E402
from blendsketch.playlist import Playlist  # noqa: E402
from blendsketch.prefs import Prefs  # noqa: E402
from blendsketch.window import MainWindow  # noqa: E402

FOOT = os.path.join(ROOT, "testdata", "footage")
SHOTS = os.path.join(ROOT, "testdata", "shots")
os.makedirs(SHOTS, exist_ok=True)


def pump(sec=0.3):
    end = time.time() + sec
    while time.time() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def drag(win, pts, pressure=1.0):
    c = win.canvas
    c._down(c.to_widget(*pts[0][:2]), pressure, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    for p in pts[1:]:
        c._move(c.to_widget(*p[:2]), pressure, Qt.KeyboardModifier.NoModifier)
    c._up()


def main():
    app = App(sys.argv[:1])
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE + checkbox_style())
    prefs = Prefs()
    prefs.author = "dana"
    win = MainWindow(prefs)
    app.window = win
    win.resize(1500, 920)
    win.show()
    pump()
    win.grab().save(os.path.join(SHOTS, "0_welcome.png"))

    pdir = tempfile.mkdtemp()
    path = os.path.join(pdir, "Shot review.bsplaylist")
    win._load_playlist(Playlist.create(path, "Shot review", prefs.author))
    win.add_media([os.path.join(FOOT, f) for f in ("shot010_v003.mov", "shot020_v001_4k4444.mov", "edit_ref.mp4")])
    assert len(win.pl.items()) == 3, "three clips added"
    win.open_item(win.pl.items()[0]["id"])
    assert wait_for(lambda: win.canvas.frame_img is not None), "first frame decoded"
    win.goto(12)
    assert wait_for(lambda: win.player.image(12) is not None), "seek decoded"
    pump(0.3)

    # dana: a multiply soft-brush layer, a screen glow layer and an arrow layer
    win.set_tool("brush"); win.set_blend("multiply"); win.color.set_hex("#6f7fb8", emit=True)
    drag(win, [(300 + i * 25, 700 + (i % 2) * 30, 1) for i in range(40)])
    drag(win, [(300 + i * 25, 760 + (i % 2) * 30, 1) for i in range(40)])  # same layer
    win.new_layer(); win.set_blend("screen"); win.color.set_hex("#ff9a4d", emit=True)
    drag(win, [(1500 + 120 * math.cos(a / 10), 400 + 120 * math.sin(a / 10), 1) for a in range(70)])
    win.new_layer(); win.set_tool("arrow"); win.set_blend("normal"); win.color.set_hex("#ef6464", emit=True)
    drag(win, [(900, 200, 1), (1200, 500, 1)])
    win.add_comment("Shadow should pool tighter under the ball here.")
    # another reviewer adds notes in the same file: lead has no layer here, so drawing makes one
    prefs.author = "lead"
    win.canvas.author = "lead"
    win._refresh_notes()
    win.set_tool("ellipse"); win.color.set_hex("#7fe0a0", emit=True)
    drag(win, [(1300, 650, 1), (1650, 900, 1)])
    win.add_comment("Agree. Also check the rim light on frame 30.")
    pump(0.2)
    item = win._item()
    layers = sorted(Playlist.live(item["layers"]), key=lambda r: r["created"])
    assert [(r["author"], r["blend"]) for r in layers] == [("dana", "multiply"), ("dana", "screen"), ("dana", "normal"),
                                                            ("lead", "normal")], layers
    assert len(Playlist.live(item["comments"])) == 2
    assert not item.get("strokes"), "strokes aren't stored"
    win.grab().save(os.path.join(SHOTS, "1_review.png"))

    # manual saving: nothing is written until you save
    def disk_layers():
        item = Playlist.open(path).item(win.cur)
        return Playlist.live(item.get("layers", {})) if item else []

    assert win.dirty and win.isWindowModified(), "unsaved changes are flagged"
    pump(1.2)
    assert not disk_layers(), "no autosave: the file still has none of the session's layers"
    assert win.save() and not win.dirty and not win.isWindowModified()
    saved = disk_layers()
    assert len(saved) == 4 and all(r["png"] for r in saved), "saved on request, with pixels"
    from blendsketch.layers import decode
    w, h = win._size()
    arrow = next(r for r in saved if r["blend"] == "normal" and r["author"] == "dana")
    assert decode(arrow, w, h).pixelColor(1050, 350).alpha() > 200, "pixels round-trip through the file"

    # --- the layer list: visibility (view-only) and deleting (own only) ---
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from blendsketch.playlist import canonical
    prefs.author = "dana"
    win.canvas.author = "dana"
    win._refresh_notes()
    win.save()
    before = canonical(win.pl.data)
    tree = win.layers_panel.tree

    def rows(t):
        return [t.topLevelItem(i) for i in range(t.topLevelItemCount())]

    def row(lid):
        return next(r for r in rows(tree) if r.data(0, Qt.ItemDataRole.UserRole + 1) == lid)

    def top(t, name):
        return next(r for r in rows(t) if r.data(0, Qt.ItemDataRole.UserRole) == name)

    ids = [r["id"] for r in layers]
    assert [r.data(0, Qt.ItemDataRole.UserRole + 1) for r in rows(tree)] == ids[::-1], "top layer first"
    assert win._active_layer()["id"] == ids[2], "dana draws on her top layer"
    assert win.blend.mode == "normal", "the blend panel shows the selected layer's mode"

    # a layer's checkbox hides it; comments are separate
    row(ids[3]).setCheckState(0, Qt.CheckState.Unchecked)
    pump(0.05)
    assert [r["id"] for r in win.canvas.layers] == ids[:3]
    assert any(c["author"] == "lead" for c in win._visible_comments())
    win.grab().save(os.path.join(SHOTS, "2_layer_hidden.png"))
    row(ids[3]).setCheckState(0, Qt.CheckState.Checked)
    pump(0.05)
    top(win.comment_tree, "lead").setCheckState(0, Qt.CheckState.Unchecked)
    pump(0.05)
    assert all(c["author"] != "lead" for c in win._visible_comments())
    top(win.comment_tree, "lead").setCheckState(0, Qt.CheckState.Checked)
    pump(0.05)
    assert canonical(win.pl.data) == before, "toggling never changes the playlist"
    assert win.prefs.view_state(win.pl.data["id"]) is not None

    # changing the selected layer's blend mode changes the whole layer
    win.pick_layer(ids[0])
    pump(0.05)
    assert win._active_layer()["id"] == ids[0] and win.blend.mode == "multiply"
    win.set_blend("color-burn")
    assert win._item()["layers"][ids[0]]["blend"] == "color-burn"
    win.undo()
    assert win._item()["layers"][ids[0]]["blend"] == "multiply"
    # layer opacity
    win._opacity_value(50)
    assert win._item()["layers"][ids[0]]["opacity"] == 0.5
    win.undo()

    # multi-select dana's first two layers + lead's, press Delete: only dana's go
    tree = win.layers_panel.tree
    tree.clearSelection()
    for lid in (ids[0], ids[1], ids[3]):
        row(lid).setSelected(True)
    tree.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Backspace, Qt.KeyboardModifier.NoModifier))
    pump(0.05)
    live = Playlist.live(win._item()["layers"])
    assert {r["id"] for r in live} == {ids[2], ids[3]}, "dana's layers deleted, lead's kept"
    win.grab().save(os.path.join(SHOTS, "2b_after_delete.png"))
    win.undo()
    assert len(Playlist.live(win._item()["layers"])) == 4, "undo restores both"

    # clicking the checkbox of one of several selected rows toggles all of them
    from PySide6.QtTest import QTest
    tree.clearSelection()
    sel = [row(ids[0]), row(ids[1])]
    for r in sel:
        r.setSelected(True)
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    opt = QStyleOptionViewItem()
    tree.initViewItemOption(opt)
    opt.rect = tree.visualItemRect(sel[1])
    opt.features |= QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
    box = tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, tree)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, box.center())
    pump(0.1)
    assert len(win.canvas.layers) == 2, f"both selected layers hidden, {len(win.canvas.layers)} visible"
    assert [row(i).checkState(0) for i in ids[:2]] == [Qt.CheckState.Unchecked] * 2
    assert len(tree.selectedItems()) == 2, "selection kept"
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, box.center())
    pump(0.1)
    assert len(win.canvas.layers) == 4, "and shown again"

    # reordering: your own layers move, anywhere among others'; others' stay put
    order = lambda: [r["id"] for r in win.canvas.layers]  # bottom to top
    assert order() == ids
    win.move_layers([ids[0]], 0)  # dana's bottom layer to the top of the list
    assert order() == [ids[1], ids[2], ids[3], ids[0]], order()
    assert [r.data(0, Qt.ItemDataRole.UserRole + 1) for r in rows(tree)][0] == ids[0], "list follows"
    win.step_layers([ids[0]], 1)  # one row down: under lead's layer
    assert order() == [ids[1], ids[2], ids[0], ids[3]], order()
    win.move_layers([ids[3]], 4)  # lead's layer: not ours to move
    assert order() == [ids[1], ids[2], ids[0], ids[3]]
    tree.clearSelection()
    row(ids[1]).setSelected(True)
    m = tree.build_menu()
    assert [a.text() for a in m.actions()][:2] == ["Move up", "Move down"]
    m.deleteLater()
    tree.clearSelection()
    win.undo(); win.undo()
    assert order() == ids, "undo puts them back"
    win.redo()
    assert order() == [ids[1], ids[2], ids[3], ids[0]]
    win.save()
    reopened = sorted(Playlist.live(Playlist.open(path).item(win.cur)["layers"]),
                      key=lambda r: r.get("order", r["created"]))
    assert [r["id"] for r in reopened] == order(), "order is saved"
    win.undo()
    assert order() == ids
    # a real drop: drag dana's bottom layer (last row) onto the top half of the first row
    from PySide6.QtCore import QMimeData, QPointF as QPF
    from PySide6.QtGui import QDropEvent
    tree.clearSelection()
    row(ids[0]).setSelected(True)
    first = tree.visualItemRect(rows(tree)[0])
    at = QPF(first.center().x(), first.top() + 2)
    drop = QDropEvent(at, Qt.DropAction.MoveAction, QMimeData(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    tree.dropEvent(drop)
    pump(0.05)
    assert order() == [ids[1], ids[2], ids[3], ids[0]], ("dropped on top", order())
    assert tree.topLevelItemCount() == 4, "no rows nested or lost"
    win.undo()
    assert order() == ids

    # right-click menu has a solid background (it used to inherit the inspector's transparency)
    from PySide6.QtGui import QColor
    m = tree.build_menu()
    m.adjustSize()
    m.show()
    pump(0.05)
    shot = m.grab().toImage()
    bg = QColor(shot.pixel(shot.width() - 6, shot.height() // 2))
    m.close()
    assert bg.alpha() == 255 and bg.name() == "#1a1d24", f"menu background {bg.name()} alpha {bg.alpha()}"

    # deleting your own comment through the comments tree
    ct = win.comment_tree
    mine = top(ct, "dana").child(0)
    ct.clearSelection()
    mine.setSelected(True)
    ct.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    pump(0.05)
    assert len(Playlist.live(win._item()["comments"])) == 1
    win.undo()
    assert len(Playlist.live(win._item()["comments"])) == 2

    # the eraser erases pixels from your selected layer only
    store = win.store
    lead_rec, arrow_rec = win._item()["layers"][ids[3]], win._item()["layers"][ids[2]]
    alpha = lambda rec, x, y: store.get(rec, w, h).pixelColor(x, y).alpha()
    win.pick_layer(ids[2])
    win.set_tool("eraser")
    assert alpha(lead_rec, 1650, 775) > 0
    drag(win, [(1640, 775, 1), (1660, 775, 1)])  # over lead's ellipse edge
    assert alpha(lead_rec, 1650, 775) > 0, "lead's ellipse survives dana's eraser"
    assert alpha(arrow_rec, 1050, 350) > 200
    drag(win, [(1000, 300, 1), (1100, 400, 1)])  # across dana's arrow
    assert alpha(arrow_rec, 1050, 350) == 0, "dana's arrow erased where the eraser went"
    assert alpha(arrow_rec, 920, 220) > 200, "and kept elsewhere"
    assert len(Playlist.live(win._item()["layers"])) == 4, "erasing doesn't delete layers"
    win.undo()
    assert alpha(arrow_rec, 1050, 350) > 200, "undo restores the pixels"
    win.redo()
    assert alpha(arrow_rec, 1050, 350) == 0, "and redo erases them again"
    win.undo()

    # smudge moves the selected layer's paint only, never the footage
    c = win.canvas
    k = c._img_scale()
    px = lambda x, y: c.composite_image().pixelColor(int(x * k), int(y * k))
    win.set_tool("smudge")
    win.settings["opacity"] = 0.9
    before_bare = px(470, 100)
    drag(win, [(250 + i * 10, 100, 1) for i in range(30)])  # across the red/green bars, nothing painted there
    pump(0.1)
    assert px(470, 100).name() == before_bare.name(), "footage untouched by smudge"
    # a thick blue pen stroke on a new layer over the magenta bar, then smudge sideways out of it
    win.new_layer()
    win.set_tool("pen"); win.set_blend("normal"); win.color.set_hex("#0000ff", emit=True)
    win.settings["sizes"]["pen"] = 40
    win.settings["opacity"] = 1.0
    drag(win, [(1330, 140, 1), (1330, 260, 1)])
    pump(0.1)
    outside = px(1420, 200)
    win.set_tool("smudge")
    win.settings["opacity"] = 0.9
    live_px = None
    c._down(c.to_widget(1320, 200), 1, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    for i in range(1, 30):
        c._move(c.to_widget(1320 + i * 6, 200), 1, Qt.KeyboardModifier.NoModifier)
    live_px = px(1420, 200)
    c._up()
    pump(0.2)
    after = px(1420, 200)
    assert after.red() < outside.red() - 40, f"blue paint dragged across the magenta: {outside.name()} -> {after.name()}"
    assert abs(after.red() - live_px.red()) < 40, f"the baked smudge matches the live one: {live_px.name()} vs {after.name()}"
    assert len(Playlist.live(win._item()["layers"])) == 5, "a smudge makes no layer of its own"
    win.grab().save(os.path.join(SHOTS, "2c_smudge.png"))
    for _ in range(4):  # smudge, pen, new layer, the smudge over bare footage
        win.undo()
    assert len(Playlist.live(win._item()["layers"])) == 4
    win.settings["opacity"] = 0.8
    win.set_tool("brush")

    # many strokes on one layer: drawing cost doesn't grow with the stroke count
    win.goto(30)
    assert wait_for(lambda: win.canvas.frame_index == 30 and win.player.image(30) is not None)
    win.set_tool("brush")
    times = []
    for n in range(80):
        t = time.perf_counter()
        drag(win, [(200 + (n * 37) % 1400 + 200 * math.cos(i / 8), 300 + (n * 53) % 500 + 100 * math.sin(i / 8), 1)
                   for i in range(30)])
        win.canvas.composite_image()
        times.append(time.perf_counter() - t)
    assert len(win.all_by_frame[30]) == 1, "80 strokes, one layer"
    early, late = sum(times[5:15]) / 10, sum(times[-10:]) / 10
    print(f"stroke incl. 30 moves: {early * 1000:.0f} ms early, {late * 1000:.0f} ms after 80 strokes")
    assert late < early * 2 + 0.02, "no slowdown as strokes pile up"
    t = time.perf_counter()
    win.undo(); win.canvas.composite_image()
    print(f"undo with 80 strokes: {(time.perf_counter() - t) * 1000:.0f} ms")
    win.goto(12)
    pump(0.2)

    # eyedropper readout
    win.set_tool("picker")
    c = win.canvas
    c._mouse = c.to_widget(500, 500)
    c._move(c._mouse, 1, Qt.KeyboardModifier.NoModifier)
    pump(0.1)
    win.grab().save(os.path.join(SHOTS, "3_eyedropper.png"))
    assert c._pick is not None
    win.set_tool("brush")

    # save, then a second machine adds a note and the window picks it up
    win.save()
    other = Playlist.open(path)
    oid = win.cur
    other.put_record(oid, "comments", {"id": "x1", "frame": 40, "author": "sam", "created": time.time(),
                                       "modified": time.time(), "deleted": False, "text": "From another machine."})
    time.sleep(0.02)
    other.save()
    win._poll()
    assert "x1" in win._item()["comments"], "poll merged sam's comment"

    # playback advances
    win.goto(0)
    pump(0.3)
    win.player.play()
    pump(1.2)
    win.player.pause()
    played = win.player.frame
    assert played > 10, f"playback advanced to {played}"

    # 4K ProRes 4444
    win.open_item(win.pl.items()[1]["id"])
    assert wait_for(lambda: win.player.image(0) is not None, 8), "4K frame decoded"
    t = time.time()
    win.player.play()
    pump(1.0)
    win.player.pause()
    print(f"4K 4444 playback reached frame {win.player.frame} in {time.time() - t:.1f}s")
    win.grab().save(os.path.join(SHOTS, "4_4k.png"))

    # missing footage + relink by moving a clip
    moved = os.path.join(pdir, "moved")
    os.makedirs(moved)
    shutil.copy(os.path.join(FOOT, "edit_ref.mp4"), moved)
    third = win.pl.items()[2]
    third["media"] = {"relative": "nope/edit_ref.mp4", "absolute": "/Volumes/gone/edit_ref.mp4", "filename": "edit_ref.mp4"}
    win._resolve_all()
    win.cur = None
    win.open_item(third["id"])
    win._refresh_playlist()
    pump(0.2)
    win.grab().save(os.path.join(SHOTS, "5_missing.png"))
    win.pl.relink(third["id"], os.path.join(moved, "edit_ref.mp4"))
    win._resolve_all()
    assert win.resolved[third["id"]], "relinked"
    win.save()
    reopened = Playlist.open(path)
    assert reopened.item(third["id"])["media"]["relative"] == "moved/edit_ref.mp4"
    print("size of playlist file:", os.path.getsize(path), "bytes")
    # quitting with unsaved changes asks first
    win.add_comment("Unsaved note")
    assert win.dirty
    win._ask_save = lambda: "cancel"
    win.close()
    pump(0.1)
    assert win.isVisible(), "Cancel keeps the window open"
    win._ask_save = lambda: "save"
    win.close()
    pump(0.1)
    assert not win.isVisible(), "Save closes"
    assert any(c["text"] == "Unsaved note" for c in Playlist.live(Playlist.open(path).item(win.cur)["comments"])), "and saves"

    # a version-1 playlist (one record per stroke) converts to layers when opened
    import json
    v1 = os.path.join(pdir, "Old review.bsplaylist")
    clip = os.path.join(FOOT, "shot010_v003.mov")
    t0 = time.time()

    def old_stroke(i, author, blend, kind="brush", frame=5):
        return {"id": f"s{i}", "frame": frame, "author": author, "created": t0 + i, "modified": t0 + i, "deleted": False,
                "kind": kind, "color": "#ff0000", "size": 60, "opacity": 0.8, "blend": blend, "pressure": False,
                "fill": False, "pts": [[300 + 20 * j, 300, 1] for j in range(20)]}

    olds = [old_stroke(0, "dana", "multiply"), old_stroke(1, "dana", "multiply"), old_stroke(2, "dana", "smudge", "smudge"),
            old_stroke(3, "lead", "screen"), old_stroke(4, "dana", "multiply"), old_stroke(5, "dana", "normal", frame=9)]
    info = win.pl.items()[0]["info"]
    data = {"format": "blendsketch-playlist", "version": 1, "id": "old", "created": t0, "created_by": "dana",
            "name": {"value": "Old review", "modified": t0}, "order": {"value": ["i1"], "modified": t0},
            "items": {"i1": {"id": "i1", "label": "shot010", "added_by": "dana", "created": t0, "modified": t0,
                             "deleted": False, "media": {"relative": os.path.relpath(clip, pdir), "absolute": clip,
                                                         "filename": "shot010_v003.mov"},
                             "info": info, "strokes": {s["id"]: s for s in olds}, "comments": {}}}}
    with open(v1, "w") as f:
        json.dump(data, f)
    win._load_playlist(Playlist.open(v1))
    item = win._item()
    made = sorted(Playlist.live(item["layers"]), key=lambda r: (r["frame"], r["created"]))
    assert [(r["frame"], r["author"], r["blend"]) for r in made] == [
        (5, "dana", "multiply"), (5, "lead", "screen"), (5, "dana", "multiply"), (9, "dana", "normal")], made
    assert all(r["png"] for r in made) and not Playlist.live(item["strokes"]), "strokes baked and tombstoned"
    assert win.dirty, "converting asks to be saved"
    win.save()
    assert Playlist.open(v1).data["version"] == 2
    win.dirty = False
    print("ALL OK")


if __name__ == "__main__":
    main()
