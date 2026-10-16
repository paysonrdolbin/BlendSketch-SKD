"""Per-machine settings (not shared through playlists)."""

from __future__ import annotations

import getpass
import json
import os

from PySide6.QtCore import QSettings

from .playlist import parse_mappings

RECENT_COLORS_MAX = 11
RECENT_FILES_MAX = 10


class Prefs:
    def __init__(self):
        override = os.environ.get("BLENDSKETCH_SETTINGS")  # an .ini path, used by tests
        self.s = QSettings(override, QSettings.Format.IniFormat) if override else QSettings("BlendSketch", "BlendSketch")

    def _get(self, key, default):
        v = self.s.value(key)
        if v is None:
            return default
        try:
            return json.loads(v)
        except (TypeError, ValueError):
            return default

    def _set(self, key, value):
        self.s.setValue(key, json.dumps(value))

    @property
    def author(self) -> str:
        name = self._get("author", "")
        if not name:
            try:
                name = getpass.getuser()
            except Exception:
                name = "Reviewer"
        return name

    @author.setter
    def author(self, v: str):
        self._set("author", v.strip())

    @property
    def mappings_text(self) -> str:
        return self._get("path_mappings", "")

    @mappings_text.setter
    def mappings_text(self, v: str):
        self._set("path_mappings", v)

    @property
    def mappings(self):
        return parse_mappings(self.mappings_text)

    @property
    def cache_mb(self) -> int:
        return int(self._get("cache_mb", 2048))

    @cache_mb.setter
    def cache_mb(self, v: int):
        self._set("cache_mb", int(v))

    @property
    def recent_colors(self) -> list[str]:
        return self._get("recent_colors", ["#6f7fb8", "#ff9a4d", "#ef6464", "#ffd28a"])

    def push_color(self, hex_: str) -> list[str]:
        cols = [hex_] + [c for c in self.recent_colors if c != hex_]
        cols = cols[:RECENT_COLORS_MAX]
        self._set("recent_colors", cols)
        return cols

    @property
    def color_mode(self) -> str:
        return self._get("color_mode", "HSV")

    @color_mode.setter
    def color_mode(self, v: str):
        self._set("color_mode", v)

    @property
    def skd_department(self) -> str:
        """The department last picked in SKD Open Playlist."""
        return self._get("skd_department", "")

    @skd_department.setter
    def skd_department(self, v: str):
        self._set("skd_department", v)

    @property
    def recent_files(self) -> list[str]:
        return self._get("recent_files", [])

    def push_file(self, path: str):
        files = [path] + [p for p in self.recent_files if p != path]
        self._set("recent_files", files[:RECENT_FILES_MAX])

    def forget_file(self, path: str):
        self._set("recent_files", [p for p in self.recent_files if p != path])

    def view_state(self, playlist_id: str) -> dict:
        """What this machine hides in a playlist (authors' strokes/comments, single strokes)."""
        return self._get(f"view/{playlist_id}", {})

    def set_view_state(self, playlist_id: str, state: dict):
        self._set(f"view/{playlist_id}", state)

    def geometry(self):
        return self.s.value("geometry")

    def set_geometry(self, g):
        self.s.setValue("geometry", g)
