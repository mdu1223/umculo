"""Chapters for DJ mixes: which song is playing in the mix right now, and a jump to any of them.

The songs come from the song finder (audio recognition plus the published tracklist). A mix that
never went through the finder can be listened to from the library copy, nothing downloaded again.

GET  /api/chapters/<song id>        -> {"state": "none"|"queued"|"listening"|"done"|"failed", "progress",
                                         "message", "tracks": [{"t", "artist", "title", "owned"}]}
POST /api/chapters/<song id>/find   -> start the song finder on the library copy
GET  /api/chapters/wanted           -> every song in your mixes' tracklists that you don't have yet
POST /api/chapters/wanted/get       {keys: [...]} (or all) -> one playlist, "Songs from my mixes", fetched from your sources
"""
import os
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
    out = find_view(f)
    with core.links.LOCK:                          # listening to it again: the earlier songs show until that's done
        again = next((x for x in core.links.FINDS.values() if x.get("file") == r["path"] and x is not f and core.users.mine(x)
                      and x["state"] in ("queued", "downloading", "listening")), None)
    if again and f["state"] == "done":
        out["again"] = {"find": again["id"], "progress": again.get("progress", 0), "message": again.get("message", "")}
    return out


def find_view(f):
    tracks = []
    got = core.links.get_states(f) if f.get("get_job") else {}
    for t in f.get("tracks") or []:
        a, owned = t.get("artist", ""), t.get("owned") or ""
        clean = core.links.clean_artist(a)
        if clean != a:                     # found before 2.9.5: "4..Artist" -> "Artist", and look again
            a, owned = clean, core.links.owned(clean, t.get("title", "")) or ""
        g = got.get(core.song_key(a, t.get("title", ""))) or got.get("t:" + core.links.title_key(t.get("title", ""))) or "" if got else ""
        tracks.append({"t": t.get("t"), "about": t.get("about"), "artist": a, "title": t.get("title", ""), "owned": owned, "getting": g})
    tracks.sort(key=lambda t: (t["t"] is None and t["about"] is None, t["t"] if t["t"] is not None else t["about"] or 0))
    return {"state": f["state"], "progress": f.get("progress", 0), "message": f.get("message", ""), "find": f["id"],
            "tracks": tracks, "file": bool(f.get("file")), "get": bool(f.get("get")),
            "getting": sum(1 for t in tracks if t["getting"] in ("pending", "searching", "downloading", "filing", "scanning"))}


def wanted():
    """Across every mix you ran the song finder on: the songs you don't have, once each, with the mixes they're in."""
    out = {}
    with core.links.LOCK:
        finds = [f for f in core.links.FINDS.values() if core.users.mine(f) and f.get("state") == "done"]
    for f in finds:
        for t in f.get("tracks") or []:
            a, title = core.links.clean_artist(t.get("artist", "")), t.get("title", "")
            if not (a and title):
                continue
            k = core.song_key(a, title)
            if k in out:
                if f["title"] not in out[k]["mixes"]:
                    out[k]["mixes"].append(f["title"])
                    out[k]["finds"].append(f["id"])
                continue
            if t.get("owned") and a == t.get("artist"):
                continue
            if core.links.owned(a, title):
                continue
            out[k] = {"key": k, "artist": a, "title": title, "mixes": [f["title"]], "finds": [f["id"]], "show": f.get("show", "")}
    return sorted(out.values(), key=lambda x: (-len(x["mixes"]), x["artist"].lower()))


def get_wanted(b):
    if not core.users.can("download") and not core.users.is_admin():
        raise PermissionError("Getting songs needs download permission")
    items = wanted()
    keys = set(b.get("keys") or [])
    if keys:
        items = [x for x in items if x["key"] in keys]
    if b.get("find"):                                  # just this mix's missing songs
        items = [x for x in items if b["find"] in x["finds"]]
    if not items:
        raise ValueError("Nothing to get: you have every song from your mixes")
    name = core.safe(b.get("name") or "Songs from my mixes")
    tracks = [{"artist": x["artist"], "title": x["title"], "duration": 0} for x in items[:500]]
    return {"job": core.new_job(name, "import", os.path.join(core.CONFIG["playlists"], name + ".m3u8"), tracks), "songs": len(tracks)}


def handle_get(path, _qs):
    if path == "/api/chapters/wanted":
        return {"songs": wanted()}
    m = re.match(r"^/api/chapters/find/([0-9a-f]{12})$", path)
    if m:                                      # a mix that's still downloading / being listened to
        with core.links.LOCK:
            f = core.links.FINDS.get(m.group(1))
        if not f or not core.users.mine(f):
            raise LookupError("Song finder not found")
        return find_view(f)
    m = re.match(r"^/api/chapters/([\w-]{1,40})$", path)
    if not m:
        return None
    return view(row(m.group(1)))


def handle_post(path, b):
    if path == "/api/chapters/wanted/get":
        return get_wanted(b or {})
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
    fid = core.links.start_local_find(r["path"], r["title"], show, int(r["duration"] or 0), page=page, get=(b or {}).get("get"),
                                      again=bool((b or {}).get("again")))
    return {"ok": True, "find": fid}


def init(umculo):
    global core
    core = umculo
