"""Chapters for DJ mixes: which song is playing in the mix right now, and a jump to any of them.

The songs come from the song finder (audio recognition plus the published tracklist). A mix that
never went through the finder can be listened to from the library copy, nothing downloaded again.

GET  /api/chapters/<song id>        -> {"state": "none"|"queued"|"listening"|"done"|"failed", "progress",
                                         "message", "tracks": [{"t", "artist", "title", "owned"}]}
POST /api/chapters/<song id>/find   -> start the song finder on the library copy
"""
import re

core = None


def row(sid):
    srv = core.server
    r = srv.db.q(f"SELECT * FROM song WHERE id=? AND {srv.lib_sql(core.users.current())}", (sid,), one=True)
    if not r:
        raise LookupError("Song not found")
    return r


def view(r):
    f = core.links.find_for_file(r["path"])
    if not f:
        return {"state": "none", "tracks": [], "message": "", "progress": 0,
                "can_find": core.links.recognition_available()}
    tracks = sorted([{"t": t.get("t"), "artist": t.get("artist", ""), "title": t.get("title", ""), "owned": t.get("owned") or ""}
                     for t in f.get("tracks") or []], key=lambda t: (t["t"] is None, t["t"] or 0))
    return {"state": f["state"], "progress": f.get("progress", 0), "message": f.get("message", ""), "find": f["id"],
            "tracks": tracks}


def handle_get(path, _qs):
    m = re.match(r"^/api/chapters/([\w-]{1,40})$", path)
    if not m:
        return None
    return view(row(m.group(1)))


def handle_post(path, _b):
    m = re.match(r"^/api/chapters/([\w-]{1,40})/find$", path)
    if not m:
        return None
    if not core.users.can("download") and not core.users.is_admin():
        raise PermissionError("The song finder needs download permission")
    r = row(m.group(1))
    show = r["albumartist"] if "albumartist" in r.keys() and r["albumartist"] else (r["album"] or r["artist"] or "Mix")
    page = ""
    try:
        page = core.video.page_for(r) if core.video.is_mix(r) else ""   # where it came from: its tracklist is often there
    except Exception:  # noqa: BLE001
        pass
    fid = core.links.start_local_find(r["path"], r["title"], show, int(r["duration"] or 0), page=page)
    return {"ok": True, "find": fid}


def init(umculo):
    global core
    core = umculo
