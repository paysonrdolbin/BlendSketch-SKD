"""SKD Open Playlist: review a sandwich ShotGrid playlist in BlendSketch.

A ShotGrid playlist becomes one shared .bsplaylist in REVIEW_DIR, named by the playlist's
ShotGrid id, so everyone who opens it draws into the same file. Each Version becomes the
clip with id `sg<version id>`, so two people syncing the same playlist add the same clip
instead of two copies. A Version plays from its movie on disk when there is one;
otherwise its ShotGrid upload is downloaded once into MEDIA_CACHE and shared from there.
Downloads nobody has opened for CACHE_DAYS are deleted; opening the playlist again
downloads them again, and its notes are untouched.

ShotGrid is read through sg_helper.py, run with the pipeline's Python (see that file).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
import uuid
from dataclasses import dataclass

from .playlist import now

PIPELINE = os.environ.get("SKD_PIPELINE", "/job/sandwich/.pipeline")
PIPELINE_SRC = os.path.join(PIPELINE, "src")
PIPELINE_PYTHON = os.environ.get("SKD_PIPELINE_PYTHON", os.path.join(PIPELINE, ".venv", "bin", "python"))
REVIEW_DIR = os.environ.get("SKD_REVIEW_DIR", "/job/sandwich/edit/review")
MEDIA_CACHE = os.environ.get("SKD_MEDIA_CACHE", "/cache/sandwich/review_media")
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sg_helper.py")
CACHE_DAYS = 30
MOVIE_EXTS = {".mov", ".mp4", ".m4v", ".mxf", ".mkv", ".avi", ".webm"}

# Department, then the words in playlist names that mean it. Names are typed by hand
# ("oct19 fx", "10/13 dailies <Shading>", "Anim Dailies 8/6/26"), so match whole words.
DEPARTMENTS: list[tuple[str, tuple[str, ...]]] = [
    ("Animation", ("anim", "animation")),
    ("CFX", ("cfx",)),
    ("Compositing", ("comp", "comps", "compositing")),
    ("FX", ("fx", "effects")),
    ("Layout", ("layout",)),
    ("Lighting", ("lighting", "light", "lgt")),
    ("Modeling", ("modeling", "modelling", "model", "models")),
    ("Previs", ("previs",)),
    ("Rigging", ("rig", "rigging")),
    ("RLO", ("rlo",)),
    ("Shading", ("shading", "shd", "lookdev", "surfacing")),
]
OTHER = "Other"
_ALIASES = {word: name for name, words in DEPARTMENTS for word in words}
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_NUMERIC = re.compile(r"(?<![\d/-])(\d{1,2})[/-](\d{1,2})(?:[/-](\d{4}|\d{2})?)?(?![\d/-]*\d)")
_NAMED = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s*(\d{1,2})(?:,?\s+(\d{4}))?(?!\d)",
                    re.IGNORECASE)
# A name without a year is in the year that puts it nearest the playlist's last update,
# so "12/30" updated on 1/2 is last December's, while "10/19" made on 10/15 is this week's.
_AHEAD = dt.timedelta(days=120)


class SKDError(Exception):
    pass


@dataclass
class ReviewPlaylist:
    id: int
    code: str
    department: str
    date: dt.date | None
    versions: int

    @property
    def date_label(self) -> str:
        return f"{self.date:%a %b} {self.date.day}, {self.date.year}" if self.date else "No date"


# ---------------------------------------------------------------- names

def _date(month: int, day: int, year: str | None, ref: dt.date) -> dt.date | None:
    try:
        if year:
            y = int(year)
            return dt.date(y + 2000 if y < 100 else y, month, day)
        d = dt.date(ref.year, month, day)
        return d.replace(year=ref.year - 1) if d - ref > _AHEAD else d
    except ValueError:  # 2/30, 13/1 and so on: not a date
        return None


def parse_name(code: str, ref: dt.date) -> tuple[str, dt.date | None]:
    """The department and review date a playlist name means. `ref` is when it was last updated."""
    date, rest = None, code
    for pattern in (_ISO, _NUMERIC, _NAMED):
        m = pattern.search(code)
        if not m:
            continue
        if pattern is _ISO:
            year, a, b = m.groups()
        else:
            a, b, year = m.groups()
        month = _MONTHS.index(a.lower()[:3]) + 1 if pattern is _NAMED else int(a)
        date = _date(month, int(b), year, ref)
        if date:
            rest = code[:m.start()] + " " + code[m.end():]
            break
    words = re.findall(r"[a-z]+", rest.lower())
    department = next((_ALIASES[w] for w in words if w in _ALIASES), OTHER)
    return department, date


def _ref_date(row: dict) -> dt.date:
    for key in ("updated", "created"):
        if row.get(key):
            return dt.datetime.fromisoformat(row[key]).date()
    return dt.date.today()


def group_by_department(rows: list[dict]) -> dict[str, list[ReviewPlaylist]]:
    """Playlists by department, newest review date first; undated ones last."""
    groups: dict[str, list[ReviewPlaylist]] = {}
    for row in rows:
        dept, date = parse_name(row["code"], _ref_date(row))
        groups.setdefault(dept, []).append(ReviewPlaylist(row["id"], row["code"], dept, date, row.get("versions", 0)))
    for pls in groups.values():
        pls.sort(key=lambda p: (p.date is not None, p.date or dt.date.min, p.id), reverse=True)
    order = [name for name, _ in DEPARTMENTS] + [OTHER]
    return {name: groups[name] for name in order if name in groups}


# ---------------------------------------------------------------- ShotGrid

def _helper(*args: str) -> object:
    if not os.path.isfile(PIPELINE_PYTHON):
        raise SKDError(f"The sandwich pipeline isn't available on this machine ({PIPELINE_PYTHON} is missing).")
    try:
        r = subprocess.run([PIPELINE_PYTHON, "-I", HELPER, PIPELINE_SRC, *args],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SKDError(f"Couldn't reach ShotGrid: {e}") from e
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        raise SKDError(f"Couldn't read ShotGrid's answer.\n{(r.stderr or r.stdout).strip()[-600:]}") from None
    if isinstance(out, dict) and "error" in out:
        raise SKDError(f"ShotGrid: {out['error']}")
    return out


def fetch_playlists() -> dict[str, list[ReviewPlaylist]]:
    return group_by_department(_helper("playlists"))


def fetch_versions(playlist_id: int) -> dict:
    """{"playlist": {id, code}, "versions": [...]} in the playlist's order."""
    return _helper("versions", str(playlist_id))


# ---------------------------------------------------------------- files

def _shared_dir(path: str) -> str:
    """Make a folder the whole sandwich group can add to (umask would drop group write)."""
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
        try:
            os.chmod(path, 0o2775)
        except OSError:
            pass
    return path


def notes_path(playlist_id: int) -> str:
    return os.path.join(REVIEW_DIR, f"sg_playlist_{playlist_id}.bsplaylist")


def ensure_review_dir():
    _shared_dir(REVIEW_DIR)


def disk_movie(version: dict) -> str | None:
    """The Version's movie on disk, if it names one that exists."""
    for p in version.get("paths", []):
        if os.path.splitext(p)[1].lower() in MOVIE_EXTS and os.path.isfile(p):
            return p
    return None


def prune_cache(days: float = CACHE_DAYS) -> int:
    """Delete downloaded Versions nobody has opened in `days`. Returns how many were deleted.

    Each Version has its own folder; opening a playlist touches the files it uses (see
    prepare), so a folder whose newest file is older than the cutoff is unused.
    """
    cutoff = time.time() - days * 86400
    try:
        folders = [e for e in os.scandir(MEDIA_CACHE) if e.is_dir(follow_symlinks=False) and e.name.isdigit()]
    except OSError:
        return 0
    deleted = 0
    for folder in folders:
        try:
            newest = max((f.stat().st_mtime for f in os.scandir(folder.path)), default=folder.stat().st_mtime)
            if newest < cutoff:
                shutil.rmtree(folder.path)
                deleted += 1
        except OSError:  # someone else deleted it first, or a file isn't ours to remove: try again next time
            pass
    return deleted


def cached_movie(version: dict) -> str | None:
    """Where the Version's ShotGrid upload is (or would be) cached."""
    movie = version.get("movie")
    if not movie:
        return None
    name = os.path.basename(movie.get("name") or f"{version['code']}.mp4")
    return os.path.join(MEDIA_CACHE, str(version["id"]), name)


def download(url: str, dest: str, progress=None, cancelled=lambda: False) -> bool:
    """Download to dest atomically. progress(done, total) is called as bytes arrive. False if cancelled."""
    folder = os.path.dirname(dest)
    _shared_dir(MEDIA_CACHE)
    _shared_dir(folder)
    tmp = os.path.join(folder, f".{os.path.basename(dest)}.{uuid.uuid4().hex[:8]}.part")
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                if cancelled():
                    return False
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
        os.replace(tmp, dest)
        return True
    except OSError as e:
        raise SKDError(f"Couldn't download {os.path.basename(dest)}: {e}") from e
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------- the shared playlist

def item_id(version: dict) -> str:
    return f"sg{version['id']}"


def sync(pl, versions: list[dict], media: dict[int, tuple[str, dict]], mappings, author: str) -> bool:
    """Bring the BlendSketch playlist in line with the ShotGrid one. Returns True if anything changed.

    media maps version id to (path, probe info) for the Versions that have footage. Clips
    whose Version left the ShotGrid playlist are kept, after the rest, so their notes survive.
    """
    changed = False
    for v in versions:
        iid = item_id(v)
        found = media.get(v["id"])
        item = pl.item(iid)
        if item is None or item.get("deleted"):
            if not found:
                continue
            path, info = found
            item = pl.add_item(path, info, author, iid=iid)
            changed = True
        elif found and not pl.resolve(item, mappings):
            pl.relink(iid, *found)
            changed = True
        sg = {"version_id": v["id"], "code": v["code"], "shot": v.get("shot"), "artist": v.get("artist")}
        if item.get("sg") != sg or item["label"] != v["code"]:
            item.update(sg=sg, label=v["code"], modified=now())
            changed = True
    wanted = [item_id(v) for v in versions if pl.item(item_id(v))]
    order = wanted + [i for i in pl.data["order"]["value"] if i not in wanted]
    if order != pl.data["order"]["value"]:
        pl.set_order(order)
        changed = True
    return changed



def prepare(playlist_id: int, report, cancelled) -> dict | None:
    """Fetch a playlist's Versions and get footage for each, downloading uploads not yet cached.

    report(text, done, total) shows progress. Returns None if cancelled, else
    {"playlist", "versions", "media": {version id: (path, info)}, "skipped": [why, ...]}.
    """
    from .media import MediaError, probe

    report("Reading the playlist from ShotGrid…", 0, 0)
    prune_cache()
    data = fetch_versions(playlist_id)
    versions = data["versions"]
    media: dict[int, tuple[str, dict]] = {}
    skipped: list[str] = []
    for n, v in enumerate(versions, 1):
        if cancelled():
            return None
        label = f"{v['code']}  ({n} of {len(versions)})"
        path = disk_movie(v)
        if not path:
            path = cached_movie(v)
            if not path:
                skipped.append(f"{v['code']}: no movie on disk or in ShotGrid")
                continue
            if not os.path.isfile(path):
                def progress(done, total, label=label):
                    report(f"Downloading {label}… {done / 1e6:.1f} of {total / 1e6:.1f} MB" if total
                           else f"Downloading {label}… {done / 1e6:.1f} MB", done, total)
                try:
                    if not download(v["movie"]["url"], path, progress, cancelled):
                        return None
                except SKDError as e:
                    skipped.append(f"{v['code']}: {e}")
                    continue
            else:
                try:
                    os.utime(path)  # in use: keeps prune_cache from deleting it
                except OSError:
                    pass
        report(f"Reading {label}…", n, len(versions))
        try:
            media[v["id"]] = (path, probe(path))
        except MediaError as e:
            skipped.append(f"{v['code']}: {e}")
    return {**data, "media": media, "skipped": skipped}
