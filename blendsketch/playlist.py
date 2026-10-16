"""Playlist files.

A playlist is one JSON file (.bsplaylist). Footage is referenced by path, never embedded;
strokes and comments live inside the file so anyone with the app and access to the
folder can open it.

Shared editing works by merging, never by locking. Every stroke, comment, item and
playlist field is a record with an id and a `modified` time. Deleting writes a tombstone
(`deleted: true`) instead of removing the record, so a merge can't bring it back. Saving
reads whatever is on disk, merges it with what's in memory (newest record wins), and
writes the result atomically.
"""

from __future__ import annotations

import copy
import json
import os
import time
import uuid

FORMAT = "blendsketch-playlist"
VERSION = 1
EXT = ".bsplaylist"


def now() -> float:
    return round(time.time(), 3)


def new_id() -> str:
    return uuid.uuid4().hex


class PlaylistError(Exception):
    pass


# ---------------------------------------------------------------- merging

def _newer(a: dict, b: dict) -> bool:
    """True when record a should win over b."""
    return a.get("modified", 0) > b.get("modified", 0)


def merge_records(mine: dict, theirs: dict) -> dict:
    out = dict(theirs)
    for rid, rec in mine.items():
        other = out.get(rid)
        if other is None or _newer(rec, other):
            out[rid] = rec
    return out


def merge_item(a: dict, b: dict) -> dict:
    base = a if a.get("modified", 0) >= b.get("modified", 0) else b
    item = {k: v for k, v in base.items() if k not in ("strokes", "comments")}
    item["strokes"] = merge_records(a.get("strokes", {}), b.get("strokes", {}))
    item["comments"] = merge_records(a.get("comments", {}), b.get("comments", {}))
    return item


def merge_playlists(mine: dict, theirs: dict) -> dict:
    out = {k: v for k, v in mine.items() if k not in ("items", "name", "order")}
    for field in ("name", "order"):
        a, b = mine.get(field), theirs.get(field)
        out[field] = a if b is None or (a is not None and a.get("modified", 0) >= b.get("modified", 0)) else b
    items = dict(theirs.get("items", {}))
    for iid, item in mine.get("items", {}).items():
        items[iid] = merge_item(item, items[iid]) if iid in items else item
    out["items"] = items
    # Someone may have added an item while someone else reordered: keep both.
    order = [i for i in out["order"]["value"] if i in items]
    order += [i for i in items if i not in order]
    out["order"] = {"value": order, "modified": out["order"]["modified"]}
    return out


def canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------- paths

def _slashes(p: str) -> str:
    return p.replace("\\", "/")


def parse_mappings(text: str) -> list[list[str]]:
    """One group of equivalent roots per line, separated by '=' (e.g. /Volumes/prod = P:/ = /mnt/prod)."""
    groups = []
    for line in text.splitlines():
        roots = [_slashes(r.strip()).rstrip("/") for r in line.split("=") if r.strip()]
        if len(roots) >= 2:
            groups.append(roots)
    return groups


def mapped_candidates(path: str, mappings: list[list[str]]) -> list[str]:
    p = _slashes(path)
    out = []
    for group in mappings:
        for root in group:
            if p.lower().startswith(root.lower() + "/") or p.lower() == root.lower():
                rest = p[len(root):]
                out += [os.path.normpath(other + rest) for other in group if other != root]
    return out


def media_ref(path: str, playlist_dir: str) -> dict:
    path = os.path.abspath(path)
    try:
        rel = _slashes(os.path.relpath(path, playlist_dir))
    except ValueError:  # different drive on Windows
        rel = None
    return {"relative": rel, "absolute": path, "filename": os.path.basename(path)}


def resolve_media(ref: dict, playlist_dir: str, mappings: list[list[str]]) -> str | None:
    candidates = []
    if ref.get("relative"):
        candidates.append(os.path.normpath(os.path.join(playlist_dir, ref["relative"])))
    if ref.get("absolute"):
        candidates.append(os.path.normpath(ref["absolute"]))
        candidates += mapped_candidates(ref["absolute"], mappings)
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def find_by_name(folder: str, names: set[str], max_depth: int = 3) -> dict[str, str]:
    """Search folder (and a few levels below it) for files with the given names."""
    found: dict[str, str] = {}
    base_depth = folder.rstrip(os.sep).count(os.sep)
    for root, dirs, files in os.walk(folder):
        if root.count(os.sep) - base_depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f in names and f not in found:
                found[f] = os.path.join(root, f)
        if len(found) == len(names):
            break
    return found


# ---------------------------------------------------------------- the playlist

class Playlist:
    def __init__(self, path: str, data: dict):
        self.path = os.path.abspath(path)
        self.data = data
        self._disk_sig = None
        self._disk_canon = None

    # -- creation / IO

    @classmethod
    def create(cls, path: str, name: str, author: str, pid: str | None = None) -> "Playlist":
        """pid fixes the playlist's id, so two people creating the same file merge instead of overwriting."""
        t = now()
        data = {
            "format": FORMAT,
            "version": VERSION,
            "id": pid or new_id(),
            "created": t,
            "created_by": author,
            "name": {"value": name, "modified": t},
            "order": {"value": [], "modified": t},
            "items": {},
        }
        pl = cls(path, data)
        pl.save()
        return pl

    @classmethod
    def open(cls, path: str) -> "Playlist":
        data = cls._read(path)
        pl = cls(path, data)
        pl._remember_disk(data)
        return pl

    @staticmethod
    def _read(path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            raise PlaylistError(f"Couldn't read {os.path.basename(path)}: {e}") from e
        if data.get("format") != FORMAT:
            raise PlaylistError(f"{os.path.basename(path)} isn't a BlendSketch playlist.")
        if data.get("version", 0) > VERSION:
            raise PlaylistError("This playlist was saved by a newer version of BlendSketch. Update the app to open it.")
        return data

    def _disk_signature(self):
        try:
            st = os.stat(self.path)
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def _remember_disk(self, data: dict):
        self._disk_sig = self._disk_signature()
        self._disk_canon = canonical(data)

    def save(self) -> bool:
        """Merge with the file on disk, then write. Returns True if others' changes came in."""
        incoming = False
        if os.path.exists(self.path):
            try:
                disk = self._read(self.path)
            except PlaylistError:
                disk = None
            if disk and disk.get("id") == self.data["id"]:
                merged = merge_playlists(self.data, disk)
                incoming = canonical(merged) != canonical(self.data)
                self.data = merged
        folder = os.path.dirname(self.path)
        tmp = os.path.join(folder, f".{os.path.basename(self.path)}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise PlaylistError(f"Couldn't save {os.path.basename(self.path)}: {e}") from e
        self._remember_disk(self.data)
        return incoming

    def poll(self) -> tuple[bool, bool]:
        """Check the file for others' saves. Returns (changed_in_memory, needs_save).

        needs_save is True when memory holds records the file doesn't, for example because
        two people saved at nearly the same moment and one write replaced the other.
        """
        sig = self._disk_signature()
        if sig is None or sig == self._disk_sig:
            return False, False
        try:
            disk = self._read(self.path)
        except PlaylistError:
            return False, False
        if disk.get("id") != self.data["id"]:
            return False, False
        before = canonical(self.data)
        merged = merge_playlists(self.data, disk)
        merged_c = canonical(merged)
        self.data = merged
        self._disk_sig = sig
        self._disk_canon = canonical(disk)
        return merged_c != before, merged_c != self._disk_canon

    # -- convenience

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def name(self) -> str:
        return self.data["name"]["value"]

    def rename(self, name: str):
        self.data["name"] = {"value": name, "modified": now()}

    def items(self) -> list[dict]:
        items = self.data["items"]
        return [items[i] for i in self.data["order"]["value"] if i in items and not items[i].get("deleted")]

    def item(self, iid: str) -> dict | None:
        return self.data["items"].get(iid)

    def add_item(self, path: str, info: dict, author: str, iid: str | None = None) -> dict:
        t = now()
        label = os.path.splitext(os.path.basename(path))[0]
        item = {
            "id": iid or new_id(),
            "label": label,
            "added_by": author,
            "created": t,
            "modified": t,
            "deleted": False,
            "media": media_ref(path, self.folder),
            "info": info,
            "strokes": {},
            "comments": {},
        }
        self.data["items"][item["id"]] = item
        self.set_order([*(i for i in self.data["order"]["value"] if i != item["id"]), item["id"]])
        return item

    def remove_item(self, iid: str):
        item = self.data["items"].get(iid)
        if item:
            item["deleted"] = True
            item["modified"] = now()

    def set_order(self, ids: list[str]):
        self.data["order"] = {"value": list(ids), "modified": now()}

    def relink(self, iid: str, path: str, info: dict | None = None):
        item = self.data["items"][iid]
        item["media"] = media_ref(path, self.folder)
        if info:
            item["info"] = info
        item["modified"] = now()

    def resolve(self, item: dict, mappings) -> str | None:
        return resolve_media(item["media"], self.folder, mappings)

    # -- notes

    @staticmethod
    def live(records: dict) -> list[dict]:
        return [r for r in records.values() if not r.get("deleted")]

    def put_record(self, iid: str, kind: str, rec: dict):
        """Store a copy of rec as the newest version (used for add, delete, undo and redo)."""
        rec = copy.deepcopy(rec)
        rec["modified"] = now()
        self.data["items"][iid][kind][rec["id"]] = rec
        return rec

    def authors(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items():
            for kind in ("strokes", "comments"):
                for r in self.live(item[kind]):
                    counts[r["author"]] = counts.get(r["author"], 0) + 1
        return counts
