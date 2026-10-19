"""Playlist format, merging and path resolution. Run: .venv/bin/python -m pytest tests  (or python tests/test_playlist.py)"""

import copy
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blendsketch.playlist import Playlist, mapped_candidates, merge_playlists, parse_mappings, resolve_media  # noqa: E402

INFO = {"width": 1920, "height": 1080, "fps": 24.0, "frames": 96}


def stroke(author, frame, sid=None):
    t = time.time()
    return {"id": sid or f"{author}-{frame}-{t}", "frame": frame, "author": author, "created": t, "modified": t,
            "deleted": False, "kind": "pen", "color": "#ff0000", "size": 4, "opacity": 1, "blend": "multiply",
            "pressure": False, "fill": False, "pts": [[1, 2, 1]]}


def test_two_people_merge_and_deletes_stick():
    d = tempfile.mkdtemp()
    footage = os.path.join(d, "footage", "a.mov")
    os.makedirs(os.path.dirname(footage))
    open(footage, "wb").close()
    path = os.path.join(d, "review.bsplaylist")
    alice = Playlist.create(path, "Review", "alice")
    item = alice.add_item(footage, INFO, "alice")
    alice.save()
    bob = Playlist.open(path)
    iid = item["id"]

    a1 = alice.put_record(iid, "strokes", stroke("alice", 3))
    alice.save()
    b1 = bob.put_record(iid, "strokes", stroke("bob", 5))
    bob.put_record(iid, "comments", {"id": "c1", "frame": 5, "author": "bob", "created": 1, "modified": 1, "deleted": False, "text": "hi"})
    incoming = bob.save()  # bob's save must merge alice's stroke instead of overwriting it
    assert incoming
    on_disk = Playlist.open(path)
    ids = set(on_disk.item(iid)["strokes"])
    assert {a1["id"], b1["id"]} <= ids

    # alice deletes her stroke; bob's stale copy must not bring it back
    time.sleep(0.01)
    alice.poll()
    alice.put_record(iid, "strokes", dict(a1, deleted=True))
    alice.save()
    bob.save()
    final = Playlist.open(path).item(iid)["strokes"]
    assert final[a1["id"]]["deleted"] is True
    assert final[b1["id"]]["deleted"] is False


def test_poll_picks_up_changes_and_flags_lost_writes():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "p.bsplaylist")
    a = Playlist.create(path, "P", "a")
    item = a.add_item(os.path.join(d, "x.mov"), INFO, "a")
    a.save()
    b = Playlist.open(path)
    b.put_record(item["id"], "strokes", stroke("b", 1, "sb"))
    b.save()
    a.put_record(item["id"], "strokes", stroke("a", 2, "sa"))  # unsaved in memory
    time.sleep(0.01)
    os.utime(path)  # make sure the mtime differs on fast filesystems
    changed, needs_save = a.poll()
    assert changed and needs_save
    assert "sb" in a.item(item["id"])["strokes"] and "sa" in a.item(item["id"])["strokes"]


def layer(author, frame, lid, png="", modified=None):
    t = modified or time.time()
    return {"id": lid, "frame": frame, "author": author, "created": t, "modified": t, "deleted": False, "name": "Layer 1",
            "blend": "multiply", "opacity": 1.0, "png": png, "x": 0, "y": 0}


def test_layers_merge_newest_wins_and_version_2():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "p.bsplaylist")
    a = Playlist.create(path, "P", "a")
    item = a.add_item(os.path.join(d, "x.mov"), INFO, "a")
    iid = item["id"]
    a.put_record(iid, "layers", layer("a", 3, "la", "AAA"))
    a.save()
    b = Playlist.open(path)
    b.put_record(iid, "layers", layer("b", 3, "lb", "BBB"))
    time.sleep(0.01)
    a.put_record(iid, "layers", dict(a.item(iid)["layers"]["la"], png="AAA2"))  # a paints more on their layer
    a.save()
    b.save()  # b's stale copy of a's layer must not win
    on_disk = Playlist.open(path)
    layers = on_disk.item(iid)["layers"]
    assert layers["la"]["png"] == "AAA2" and layers["lb"]["png"] == "BBB"
    assert on_disk.data["version"] == 2


def test_order_keeps_items_added_concurrently():
    base = {"id": "p", "name": {"value": "n", "modified": 1}, "order": {"value": ["x"], "modified": 1},
            "items": {"x": {"id": "x", "modified": 1, "strokes": {}, "comments": {}}}}
    mine = copy.deepcopy(base)
    mine["items"]["y"] = {"id": "y", "modified": 2, "strokes": {}, "comments": {}}
    mine["order"] = {"value": ["x", "y"], "modified": 2}
    theirs = copy.deepcopy(base)
    theirs["order"] = {"value": ["x"], "modified": 3}  # they reordered later, without y
    m = merge_playlists(mine, theirs)
    assert m["order"]["value"] == ["x", "y"]


def test_relative_paths_and_mappings():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "shots"))
    f = os.path.join(d, "shots", "a.mov")
    open(f, "wb").close()
    ref = {"relative": "shots/a.mov", "absolute": "/Volumes/gone/shots/a.mov", "filename": "a.mov"}
    assert resolve_media(ref, d, []) == os.path.normpath(f)
    ref = {"relative": None, "absolute": "P:\\proj\\shots\\a.mov", "filename": "a.mov"}
    maps = parse_mappings(f"P:/proj = {d}")
    assert resolve_media(ref, "/nowhere", maps) == os.path.normpath(f)
    assert mapped_candidates("/Volumes/prod/x/y.mov", parse_mappings("/Volumes/prod = /mnt/prod")) == [os.path.normpath("/mnt/prod/x/y.mov")]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
