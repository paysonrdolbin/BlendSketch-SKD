"""SKD Open Playlist: playlist-name parsing and syncing a ShotGrid playlist into a shared file.
Run: .venv/bin/python -m pytest tests"""

import datetime as dt
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blendsketch import skd  # noqa: E402
from blendsketch.playlist import Playlist  # noqa: E402

INFO = {"width": 1920, "height": 1080, "fps": 24.0, "frames": 96}
REF = dt.date(2026, 10, 15)


def test_parse_real_playlist_names():
    cases = {
        "oct19 fx": ("FX", dt.date(2026, 10, 19)),
        "10/19 CFX Dailies": ("CFX", dt.date(2026, 10, 19)),
        "10/5 Dailies CFX": ("CFX", dt.date(2026, 10, 5)),
        "10/13 dailies <Shading>": ("Shading", dt.date(2026, 10, 13)),
        "8/28/ dailies Shading": ("Shading", dt.date(2026, 8, 28)),
        "Anim Dailies 8/6/26": ("Animation", dt.date(2026, 8, 6)),
        "Previs Progress 6/17/26": ("Previs", dt.date(2026, 6, 17)),
        "04/17 dailies <lighting>": ("Lighting", dt.date(2026, 4, 17)),
        "FX 4/8": ("FX", dt.date(2026, 4, 8)),
        "03-04-2026 Modeling": ("Modeling", dt.date(2026, 3, 4)),
        "3/2 dailies <rlo>": ("RLO", dt.date(2026, 3, 2)),
        "2026-10-15 Comp": ("Compositing", dt.date(2026, 10, 15)),
        "Oct 9, 2025 lighting": ("Lighting", dt.date(2025, 10, 9)),
        "Show and tell": (skd.OTHER, None),
    }
    for code, want in cases.items():
        assert skd.parse_name(code, REF) == want, code


def test_year_is_nearest_the_update():
    assert skd.parse_name("12/30 fx", dt.date(2027, 1, 2)) == ("FX", dt.date(2026, 12, 30))
    assert skd.parse_name("13/40 fx", REF) == ("FX", None)  # not a date


def test_group_newest_first_and_known_departments_first():
    rows = [{"id": 1, "code": "FX 4/8", "updated": "2026-04-09T10:00:00"},
            {"id": 2, "code": "oct19 fx", "updated": "2026-10-15T10:00:00"},
            {"id": 3, "code": "misc", "updated": "2026-10-15T10:00:00"},
            {"id": 4, "code": "10/13 dailies <Shading>", "updated": "2026-10-14T10:00:00"}]
    groups = skd.group_by_department(rows)
    assert list(groups) == ["FX", "Shading", skd.OTHER]
    assert [p.id for p in groups["FX"]] == [2, 1]


def _versions(d, *vids):
    out = []
    for vid in vids:
        path = os.path.join(d, f"v{vid}.mov")
        open(path, "wb").close()
        out.append({"id": vid, "code": f"A_{vid}.v001", "shot": "A_100", "artist": "dana", "paths": [path]})
    return out


def test_sync_adds_versions_once_and_keeps_order():
    d = tempfile.mkdtemp()
    versions = _versions(d, 10, 11, 12)
    media = {v["id"]: (v["paths"][0], INFO) for v in versions}
    path = os.path.join(d, "sg_playlist_7.bsplaylist")
    a = Playlist.create(path, "10/13 dailies", "alice", pid="sg_playlist_7")
    assert skd.sync(a, versions, media, [], "alice")
    assert [i["id"] for i in a.items()] == ["sg10", "sg11", "sg12"]
    assert a.items()[0]["sg"]["version_id"] == 10
    assert not skd.sync(a, versions, media, [], "alice")  # nothing new: no change, no save

    # Two people creating the same file at once merge, because the id is fixed.
    b = Playlist.create(path + ".b", "10/13 dailies", "bob", pid="sg_playlist_7")
    skd.sync(b, versions, media, [], "bob")
    a.save()
    b.path = a.path
    b.save()
    assert [i["id"] for i in Playlist.open(path).items()] == ["sg10", "sg11", "sg12"]


def test_sync_reorders_and_keeps_removed_versions_notes():
    d = tempfile.mkdtemp()
    versions = _versions(d, 10, 11, 12)
    media = {v["id"]: (v["paths"][0], INFO) for v in versions}
    pl = Playlist.create(os.path.join(d, "p.bsplaylist"), "p", "alice", pid="x")
    skd.sync(pl, versions, media, [], "alice")
    # Version 10 leaves the ShotGrid playlist, 12 moves to the front, 13 is added.
    new = [versions[2], versions[1], *_versions(d, 13)]
    media[13] = (new[2]["paths"][0], INFO)
    assert skd.sync(pl, new, media, [], "alice")
    assert [i["id"] for i in pl.items()] == ["sg12", "sg11", "sg13", "sg10"]


def test_sync_relinks_missing_footage_and_skips_versions_without_any():
    d = tempfile.mkdtemp()
    versions = _versions(d, 10, 11)
    pl = Playlist.create(os.path.join(d, "p.bsplaylist"), "p", "alice", pid="x")
    skd.sync(pl, versions, {10: (versions[0]["paths"][0], INFO)}, [], "alice")
    assert [i["id"] for i in pl.items()] == ["sg10"]  # 11 had no footage
    os.remove(versions[0]["paths"][0])
    cached = os.path.join(d, "cache", "10", "A_10.mp4")
    os.makedirs(os.path.dirname(cached))
    open(cached, "wb").close()
    assert skd.sync(pl, versions, {10: (cached, INFO)}, [], "alice")
    assert pl.resolve(pl.item("sg10"), []) == cached


def test_disk_movie_ignores_frame_sequences_and_missing_files():
    d = tempfile.mkdtemp()
    mov = os.path.join(d, "a.mov")
    open(mov, "wb").close()
    assert skd.disk_movie({"paths": [os.path.join(d, "a.####.exr"), mov]}) == mov
    assert skd.disk_movie({"paths": [os.path.join(d, "gone.mov")]}) is None


def test_prune_cache_deletes_only_unused_downloads():
    d = tempfile.mkdtemp()
    old_cache, skd.MEDIA_CACHE = skd.MEDIA_CACHE, d
    try:
        day = 86400
        for vid, age in (("1", 40), ("2", 5), ("3", 40)):
            os.makedirs(os.path.join(d, vid))
            f = os.path.join(d, vid, "movie.mp4")
            open(f, "wb").close()
            t = time.time() - age * day
            os.utime(f, (t, t))
        # Version 3 was opened yesterday: one recent file keeps the whole folder.
        open(os.path.join(d, "3", ".movie.mp4.part"), "wb").close()
        os.makedirs(os.path.join(d, "not-a-version"))
        assert skd.prune_cache(30) == 1
        assert sorted(os.listdir(d)) == ["2", "3", "not-a-version"]
        assert skd.prune_cache(30) == 0
    finally:
        skd.MEDIA_CACHE = old_cache


def test_prune_cache_without_a_cache_folder():
    old_cache, skd.MEDIA_CACHE = skd.MEDIA_CACHE, os.path.join(tempfile.mkdtemp(), "missing")
    try:
        assert skd.prune_cache() == 0
    finally:
        skd.MEDIA_CACHE = old_cache
