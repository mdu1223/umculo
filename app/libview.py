"""Library tab: everything you have, by artist, album and song, with full control.

Reads Navidrome as the person who is looking (so family members see their own music and what you
share with them), and deletes for real: a whole artist, an album or single songs, files and all.
"""
import os
import re
import threading
import time

core = None
STATS = {}                       # user key -> (built at, stats)
LOSSLESS = {"flac", "wav", "aiff", "aif", "alac", "ape", "wv", "dsf"}


def me():
    return core.users.current()


def nd(endpoint, **params):
    return core.users.nd_as(me(), endpoint, **params) or {}


def quality(s):
    suf, br = (s.get("suffix") or "").lower(), int(s.get("bitRate") or 0)
    if suf in LOSSLESS:
        return "lossless", suf.upper()
    tier = "high" if br >= 315 else "ok" if br >= 250 else "low"
    return tier, f"{suf.upper()} {br}".strip()


def song_view(s):
    tier, label = quality(s)
    return {"id": s["id"], "title": s.get("title", ""), "artist": s.get("artist", ""), "album": s.get("album", ""),
            "track": s.get("track") or 0, "disc": s.get("discNumber") or 0, "duration": s.get("duration") or 0,
            "tier": tier, "quality": label, "size": s.get("size") or 0, "cover": s.get("coverArt") or "",
            "year": s.get("year") or 0, "plays": s.get("playCount") or 0, "nd_id": s["id"], "album_id": s.get("albumId") or "",
            "artist_id": ((s.get("albumArtists") or [{}])[0].get("id")) or s.get("artistId") or "", "starred": bool(s.get("starred"))}


def album_view(a):
    return {"id": a["id"], "name": a.get("name") or a.get("title", ""), "artist": a.get("artist", ""),
            "artist_id": a.get("artistId", ""), "year": a.get("year") or 0, "songs": a.get("songCount") or 0,
            "duration": a.get("duration") or 0, "cover": a.get("coverArt") or "", "plays": a.get("playCount") or 0,
            "created": a.get("created", "")}


# --------------------------------------------------------------------------- reading
def all_songs():
    out, off = [], 0
    while True:
        r = nd("search3", query="", songCount=500, songOffset=off, artistCount=0, albumCount=0)
        batch = (r.get("searchResult3") or {}).get("song") or []
        out += batch
        if len(batch) < 500 or off > 200000:
            return out
        off += 500


def stats(fresh=False):
    u = me()
    key = (u or {}).get("id") or "admin"
    hit = STATS.get(key)
    if hit and not fresh and time.time() - hit[0] < 600:
        return hit[1]
    songs = all_songs()
    tiers = {}
    for s in songs:
        t = quality(s)[0]
        tiers[t] = tiers.get(t, 0) + 1
    st = {"songs": len(songs), "albums": len({s.get("albumId") for s in songs}),
          "artists": len({((s.get("albumArtists") or [{}])[0].get("id")) or s.get("artistId") for s in songs}), "size": sum(s.get("size") or 0 for s in songs),
          "duration": sum(s.get("duration") or 0 for s in songs), "tiers": tiers,
          "plays": sum(s.get("playCount") or 0 for s in songs)}
    STATS[key] = (time.time(), st)
    return st


def album_list(kind, n=12):
    r = nd("getAlbumList2", type=kind, size=n)
    return [album_view(a) for a in ((r.get("albumList2") or {}).get("album") or [])]


def home():
    return {"stats": stats(), "newest": album_list("newest"), "frequent": album_list("frequent"),
            "recent": album_list("recent"), "me": (me() or {}).get("name", "")}


def artists():
    r = nd("getArtists")
    out = []
    for idx in ((r.get("artists") or {}).get("index") or []):
        for a in idx.get("artist") or []:
            out.append({"id": a["id"], "name": a.get("name", ""), "albums": a.get("albumCount") or 0,
                        "cover": a.get("coverArt") or "", "image": a.get("artistImageUrl") or ""})
    return out


def artist(aid):
    r = nd("getArtist", id=aid).get("artist")
    if not r:
        raise LookupError("Artist not found")
    albums = sorted([album_view(a) for a in r.get("album") or []], key=lambda a: -(a["year"] or 0))
    return {"id": r["id"], "name": r.get("name", ""), "image": r.get("artistImageUrl") or "", "albums": albums,
            "songs": sum(a["songs"] for a in albums), "cover": r.get("coverArt") or ""}


def album(alid):
    r = nd("getAlbum", id=alid).get("album")
    if not r:
        raise LookupError("Album not found")
    songs = r.get("song") or []
    out = album_view(r)
    out["tracks"] = sorted([song_view(s) for s in songs], key=lambda s: (s["disc"], s["track"], s["title"]))
    out["size"] = sum(s["size"] for s in out["tracks"])
    files = [locate(s.get("path") or "") for s in songs]
    folder = os.path.dirname(files[0]) if files and files[0] else ""
    out["folder"] = os.path.relpath(folder, core.CONFIG["music"]) if folder and core.within(folder, core.CONFIG["music"]) else ""
    out["shared_with"] = core.users.shared_with("album", out["folder"]) if out["folder"] else []
    out["mine"] = bool(files and files[0] and allowed(files[0]))
    return out


def search(q):
    """Your own music only, fast (as you type): songs, albums and artists, best matches first."""
    q = (q or "").strip()
    if len(q) < 1:
        return {"songs": [], "albums": [], "artists": []}
    r = nd("search3", query=q, songCount=40, albumCount=12, artistCount=10).get("searchResult3") or {}
    words = core.norm(q)
    def rank(name):                                  # exact, then starts with, then contains
        n = core.norm(name)
        return 0 if n == words else 1 if n.startswith(words) else 2
    songs = sorted([song_view(s) for s in r.get("song") or []], key=lambda s: (rank(s["title"]), -s["plays"]))
    albums = sorted([album_view(a) for a in r.get("album") or []], key=lambda a: rank(a["name"]))
    arts = sorted([{"id": a["id"], "name": a.get("name", ""), "albums": a.get("albumCount") or 0, "cover": a.get("coverArt") or ""}
                   for a in r.get("artist") or [] if (a.get("albumCount") or 0) > 0], key=lambda a: rank(a["name"]))
    return {"songs": songs, "albums": albums, "artists": arts, "can_delete": core.users.is_admin(me()) or
            bool(((me() or {}).get("perms") or {}).get("delete"))}


# --------------------------------------------------------------------------- deleting
def locate(rel):
    """Navidrome paths are relative to the library they're in: find the file on disk."""
    if not rel:
        return ""
    if os.path.isabs(rel) and os.path.exists(rel):
        return rel
    for root in [core.CONFIG["music"]] + people_roots():
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    return ""


def people_roots():
    fam = core.CONFIG["family"]
    return [os.path.join(fam, d) for d in (os.listdir(fam) if os.path.isdir(fam) else [])]


def allowed(path):
    return any(core.within(path, r) for r in core.users.roots_for(me()))


def files_for(kind, i):
    if kind == "song":
        s = nd("getSong", id=i).get("song")
        return [s] if s else []
    if kind == "album":
        return (nd("getAlbum", id=i).get("album") or {}).get("song") or []
    if kind == "artist":
        out = []
        for a in (nd("getArtist", id=i).get("artist") or {}).get("album") or []:
            out += (nd("getAlbum", id=a["id"]).get("album") or {}).get("song") or []
        return out
    raise ValueError("Unknown kind")


def preview(kind, i):
    songs = files_for(kind, i)
    paths = [locate(s.get("path") or "") for s in songs]
    ok = [p for p in paths if p and allowed(p)]
    return {"songs": len(songs), "deletable": len(ok), "size": sum(os.path.getsize(p) for p in ok if os.path.exists(p)),
            "playlists": len(playlists_using(set(ok)))}


def playlists_using(paths):
    hits = []
    for p in core.playlists.files():
        try:
            if paths & set(core.m3u_entries(p)):
                hits.append(p)
        except OSError:
            pass
    return hits


def delete(kind, i):
    songs = files_for(kind, i)
    paths = [locate(s.get("path") or "") for s in songs]
    ok = [p for p in dict.fromkeys(paths) if p and allowed(p)]
    if not ok:
        raise PermissionError("Nothing here that you're allowed to delete")
    u = me()
    gone = set()
    for p in ok:
        if core.delete_song_file(p):
            gone.add(p)
    for pl in playlists_using(gone):                   # take them out of playlists too
        with core.M3U_LOCK:
            core.write_m3u(pl, [e for e in core.m3u_entries(pl) if e not in gone])
    if u and not core.users.is_admin(u) and core.users.HOUSE["mode"] == "separate":
        shared = os.path.join(core.users.folder(u), "Shared")
        for p in gone:                                 # deleting something shared = you stop sharing it
            if core.within(p, shared):
                rel_dir = os.path.dirname(os.path.relpath(p, shared))
                if rel_dir in (u.get("shared_albums") or []):
                    u["shared_albums"].remove(rel_dir)
        core.users.save()
    for root in [core.CONFIG["music"]] + people_roots():  # tidy empty folders
        for p in gone:
            d = os.path.dirname(p)
            while core.within(d, root) and d != root and os.path.isdir(d) and not [f for f in os.listdir(d) if f not in ("cover.jpg", "folder.jpg")]:
                for f in os.listdir(d):
                    os.remove(os.path.join(d, f))
                os.rmdir(d)
                d = os.path.dirname(d)
    if core.users.HOUSE["mode"] == "separate":           # shared copies of what's gone go too
        for p_ in [x for x in core.users.USERS.values() if x.get("role") != "admin"]:
            core.users.kick(p_)
    STATS.clear()
    core.playlists.CACHE["at"] = 0
    core.nd_scan()
    core.log(f"{(u or {}).get('name', 'you')} deleted {len(gone)} songs ({kind})")
    return {"deleted": len(gone), "skipped": len(songs) - len(gone)}


# --------------------------------------------------------------------------- web
def handle_get(path, qs):
    if path == "/api/lib":
        return home()
    if path == "/api/lib/search":
        return search((qs.get("q") or [""])[0])
    if path == "/api/lib/stats":
        return stats(bool(qs.get("fresh")))
    if path == "/api/lib/artists":
        return {"artists": artists()}
    m = re.match(r"^/api/lib/(artist|album)/([^/]+)$", path)
    if m:
        return artist(m.group(2)) if m.group(1) == "artist" else album(m.group(2))
    m = re.match(r"^/api/lib/delete-preview/(song|album|artist)/([^/]+)$", path)
    if m:
        return preview(m.group(1), m.group(2))
    return None


def handle_post(path, b):
    if path == "/api/lib/delete":
        return delete(b.get("kind"), str(b.get("id") or ""))
    if path == "/api/lib/share":
        if not core.users.is_admin():
            raise PermissionError("Only the admin can share")
        return core.users.share(b.get("user"), "album", b.get("folder"), bool(b.get("on")))
    return None


def init(umculo):
    global core
    core = umculo
