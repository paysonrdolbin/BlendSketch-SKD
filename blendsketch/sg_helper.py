"""ShotGrid reads for SKD Open Playlist, run by the pipeline's own Python.

BlendSketch never holds ShotGrid credentials. It runs this file with the sandwich
pipeline's interpreter, which can import `env_sg` (the gitignored credentials) and
`shotgun_api3`, and reads one JSON document from stdout:

    python -I sg_helper.py <pipeline src> playlists
    python -I sg_helper.py <pipeline src> versions <playlist id>

On failure it prints {"error": "..."} and exits 1. Read-only: nothing is written to ShotGrid.
"""

import json
import sys


def connect(src: str):
    sys.path.insert(0, src)
    import shotgun_api3
    from env_sg import DB_Config

    sg = shotgun_api3.Shotgun(DB_Config.sg_server, DB_Config.sg_script, DB_Config.sg_key)
    return sg, {"type": "Project", "id": DB_Config.project_id}


def _iso(t):
    return t.isoformat() if t else None


def playlists(sg, project):
    rows = sg.find("Playlist", [["project", "is", project]], ["code", "created_at", "updated_at", "versions"],
                   order=[{"field_name": "updated_at", "direction": "desc"}])
    return [{"id": r["id"], "code": r["code"] or "", "created": _iso(r["created_at"]),
             "updated": _iso(r["updated_at"]), "versions": len(r["versions"] or [])} for r in rows]


def versions(sg, project, playlist_id: int):
    pl = sg.find_one("Playlist", [["project", "is", project], ["id", "is", playlist_id]], ["code"])
    if pl is None:
        raise LookupError(f"Playlist {playlist_id} isn't in this project anymore.")
    links = sg.find("PlaylistVersionConnection", [["playlist", "is", {"type": "Playlist", "id": playlist_id}]],
                    ["version", "sg_sort_order"], order=[{"field_name": "sg_sort_order", "direction": "asc"}])
    ids = [link["version"]["id"] for link in links if link["version"]]
    found = {v["id"]: v for v in sg.find(
        "Version", [["id", "in", ids]] if ids else [["id", "is", 0]],
        ["code", "entity", "user", "sg_task", "description", "sg_path_to_frames", "sg_path_to_movie",
         "sg_uploaded_movie", "created_at"])}
    out = []
    for vid in ids:
        v = found.get(vid)
        if v is None:
            continue
        movie = v["sg_uploaded_movie"]
        out.append({
            "id": vid,
            "code": v["code"] or f"Version {vid}",
            "shot": (v["entity"] or {}).get("name"),
            "artist": (v["user"] or {}).get("name"),
            "task": (v["sg_task"] or {}).get("name"),
            "description": v["description"],
            "paths": [p for p in (v["sg_path_to_movie"], v["sg_path_to_frames"]) if p],
            "movie": {"name": movie.get("name"), "url": movie.get("url"), "id": movie.get("id")}
                     if isinstance(movie, dict) and movie.get("url") else None,
            "created": _iso(v["created_at"]),
        })
    return {"playlist": {"id": pl["id"], "code": pl["code"] or ""}, "versions": out}


def main(argv):
    src, cmd, *rest = argv
    sg, project = connect(src)
    if cmd == "playlists":
        return playlists(sg, project)
    if cmd == "versions":
        return versions(sg, project, int(rest[0]))
    raise ValueError(f"Unknown command {cmd!r}")


if __name__ == "__main__":
    try:
        result = main(sys.argv[1:])
    except Exception as e:  # report every failure as JSON so the app can show it
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}))
        sys.exit(1)
    print(json.dumps(result))
