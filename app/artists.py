"""Artists: follow the ones you love, hear about new releases, fetch whole albums.

Following an artist:
  * Umculo checks their releases every few hours (Deezer's catalogue)
  * a new album, EP or single shows up in For You ("New from artists you follow") and on your phone
  * with "Download new releases" on, the release is fetched in your quality profile by itself
Albums: "Get the album" fetches every song of an album, the same way playlist imports work, but
without making a playlist (the songs land in your library under the artist).
"""
import datetime
import re
import threading
import time
import traceback
import urllib.parse

core = None
LOCK = threading.Lock()
FOLLOWS = {}                 # deezer artist id (str) -> {...}
RELEASES = []                # newest first: {artist_id, artist, album_id, title, type, date, cover, seen_at}
CHECK_HOURS = 6
NEW_DAYS = 120               # a release counts as "new" for this long


def log(*a):
    core.log(*a)


def dz(path, ttl=3600):
    try:
        return core.dz_get(path, ttl)
    except Exception as e:  # noqa: BLE001
        log("artists: catalogue call failed:", path, e)
        return {}


def save():
    with LOCK:
        data = {"follows": dict(FOLLOWS), "releases": RELEASES[:200]}
    core._save_json("artists.json", data)


def recent(date):
    try:
        d = datetime.date.fromisoformat(date)
    except (TypeError, ValueError):
        return False
    return (datetime.date.today() - d).days <= NEW_DAYS


def albums_of(aid, fresh=False):
    data = dz(f"/artist/{aid}/albums?limit=100", 0 if fresh else 3600).get("data") or []
    return sorted(data, key=lambda a: a.get("release_date") or "", reverse=True)


def release_view(a, artist):
    return {"album_id": a["id"], "title": a["title"], "type": a.get("record_type") or "album",
            "date": a.get("release_date") or "", "cover": a.get("cover_medium") or a.get("cover") or "",
            "cover_big": a.get("cover_xl") or a.get("cover_big") or "", "artist": artist}


# --------------------------------------------------------------------------- albums
def get_album(album_id, why="you"):
    """Fetch every song of an album into the library. -> job id"""
    al = dz(f"/album/{album_id}", 86400)
    if not al or not al.get("tracks"):
        raise LookupError("Album not found in the catalogue")
    artist = (al.get("artist") or {}).get("name") or ""
    tracks = []
    for d in (al["tracks"].get("data") or []):
        tracks.append({"artist": (d.get("artist") or {}).get("name") or artist,
                       "title": d["title"] + (f" ({d['title_version']})" if d.get("title_version") and
                                              d["title_version"].lower() not in d["title"].lower() else ""),
                       "duration": d.get("duration") or 0, "album": al["title"],
                       "cover": al.get("cover_xl") or al.get("cover_big") or ""})
    with core.JOB_LOCK:
        for j in core.JOBS.values():
            if j["kind"] == "album" and j["m3u"] == f"album:{album_id}" and core.job_summary_running(j):
                return j["id"]
    jid = core.new_job(f"{artist} – {al['title']}", "album", f"album:{album_id}", tracks)
    core.JOBS[jid]["album"] = {"id": album_id, "title": al["title"], "artist": artist,
                               "cover": al.get("cover_medium") or "", "date": al.get("release_date") or ""}
    core.save_jobs()
    log(f"album: fetching {artist} – {al['title']} ({len(tracks)} songs, asked by {why})")
    return jid


def album_jobs():
    with core.JOB_LOCK:
        js = [j for j in core.JOBS.values() if j["kind"] == "album" and core.users.sees_job(j)]
    out = []
    for j in sorted(js, key=lambda j: j["created"], reverse=True)[:30]:
        s = core.job_summary(j)
        s["album"] = j.get("album") or {}
        out.append(s)
    return out


# --------------------------------------------------------------------------- following
def fans(f):
    """Who follows this artist: {person: {"auto": bool}} (older follows are yours)."""
    if "fans_of" not in f:
        f["fans_of"] = {"admin": {"auto": bool(f.get("auto")), "since": f.get("since") or time.time()}}
    return f["fans_of"]


def my_follow(f):
    return bool(f) and core.users.me_key() in fans(f)


def follow(aid=None, name=None, auto=False):
    if not aid and name:
        hit = (dz("/search/artist?limit=5&q=" + urllib.parse.quote(name)).get("data") or [])
        exact = [a for a in hit if core.norm(a["name"]) == core.norm(name)]
        if not (exact or hit):
            raise LookupError("Artist not found")
        aid = (exact or hit)[0]["id"]
    aid = str(aid)
    a = dz(f"/artist/{aid}", 86400)
    if not a or not a.get("name"):
        raise LookupError("Artist not found")
    albums = albums_of(aid, True)
    with LOCK:
        f = FOLLOWS.get(aid) or {"id": aid, "since": time.time(), "seen": [], "fans_of": {}}
        f.update(name=a["name"], picture=a.get("picture_medium") or "", fans=a.get("nb_fan") or 0, last_check=time.time())
        fans(f)[core.users.me_key()] = {"auto": bool(auto), "since": time.time()}
        if not f["seen"]:
            f["seen"] = [str(x["id"]) for x in albums]           # what's out today isn't "new"
            if albums and recent(albums[0].get("release_date")):  # but show their latest release
                add_release(albums[0], a["name"], aid)
        FOLLOWS[aid] = f
    save()
    log(f"following {a['name']}" + (" (new releases download by themselves)" if auto else ""))
    return view(f)


def add_release(a, artist, aid):
    r = {**release_view(a, artist), "artist_id": aid, "seen_at": time.time()}
    if not any(x["album_id"] == r["album_id"] for x in RELEASES):
        RELEASES.insert(0, r)
    return r


def check(aid):
    with LOCK:
        f = FOLLOWS.get(aid)
    if not f:
        return 0
    new = []
    for a in albums_of(aid, True):
        if str(a["id"]) in f["seen"]:
            continue
        with LOCK:
            f["seen"].append(str(a["id"]))
        if recent(a.get("release_date")):
            with LOCK:
                new.append(add_release(a, f["name"], aid))
    f["last_check"] = time.time()
    for r in new:
        kind = {"single": "single", "ep": "EP"}.get(r["type"], "album")
        autos = [k for k, v in fans(f).items() if v.get("auto")]
        for k in autos:                                  # each fan who asked for new releases gets them, in their music
            who = core.users.person(k)
            if not who or (not core.users.is_admin(who) and (who.get("perms") or {}).get("download") != "yes"):
                continue
            try:
                core.subsonic.as_person(who, get_album, r["album_id"], "following").result()
            except Exception as e:  # noqa: BLE001
                log("new release download failed:", e)
        core.notify(f"New {kind} from {f['name']}: {r['title']}" + (". Downloading it now" if autos else ""))
    save()
    return len(new)


def loop():
    time.sleep(120)
    while True:
        with LOCK:
            due = [aid for aid, f in FOLLOWS.items() if time.time() - f.get("last_check", 0) > CHECK_HOURS * 3600]
        for aid in due:
            try:
                n = check(aid)
                if n:
                    log(f"{n} new release(s) from {FOLLOWS[aid]['name']}")
            except Exception:  # noqa: BLE001
                log("artist check failed:", traceback.format_exc())
            time.sleep(2)
        time.sleep(600)


def view(f):
    me = fans(f).get(core.users.me_key()) or {}
    return {**{k: f.get(k) for k in ("id", "name", "picture", "fans", "last_check")}, "auto": bool(me.get("auto")),
            "since": me.get("since") or f.get("since")}


# --------------------------------------------------------------------------- artist page
def page(aid):
    a = dz(f"/artist/{aid}", 86400)
    if not a or not a.get("name"):
        raise LookupError("Artist not found")
    top = [core.foryou.item_from_dz(d, "Popular") for d in (dz(f"/artist/{aid}/top?limit=10").get("data") or [])]
    have = {}
    for it in top:
        have[it["key"]] = bool(core.index_get(it["artist"], it["title"]))
    for it in top:
        it["owned"] = have[it["key"]]
    albums = [release_view(x, a["name"]) for x in albums_of(aid)]
    related = [{"id": r["id"], "name": r["name"], "picture": r.get("picture_medium") or ""}
               for r in (dz(f"/artist/{aid}/related?limit=8").get("data") or [])]
    with LOCK:
        f = FOLLOWS.get(str(aid))
    fetching = {str(j["album"].get("id")): j for j in album_jobs() if j.get("album")}
    for al in albums:
        j = fetching.get(str(al["album_id"]))
        al["job"] = {"running": j["running"], "settled": j["settled"], "total": j["total"],
                     "done": j["counts"].get("done", 0) + j["counts"].get("kept", 0)} if j else None
    return {"id": str(aid), "name": a["name"], "picture": a.get("picture_xl") or a.get("picture_big") or "",
            "fans": a.get("nb_fan") or 0, "albums_count": a.get("nb_album") or len(albums), "top": top,
            "albums": albums, "related": related, "following": view(f) if my_follow(f) else None}


def search(q):
    data = dz("/search/artist?limit=4&q=" + urllib.parse.quote(q), 3600).get("data") or []
    with LOCK:
        fol = {k for k, f in FOLLOWS.items() if my_follow(f)}
    return [{"id": str(a["id"]), "name": a["name"], "picture": a.get("picture_medium") or "", "fans": a.get("nb_fan") or 0,
             "following": str(a["id"]) in fol} for a in data]


# --------------------------------------------------------------------------- web
def handle_get(path, qs):
    if path == "/api/artists":
        with LOCK:
            fs = sorted((f for f in FOLLOWS.values() if my_follow(f)), key=lambda f: f["name"].lower())
            mine = {f["id"] for f in fs}
            rel = [r for r in RELEASES if recent(r.get("date")) and r.get("artist_id") in mine][:40]
        return {"following": [view(f) for f in fs], "releases": rel, "albums": album_jobs()}
    if path == "/api/artists/search":
        return {"artists": search((qs.get("q") or [""])[0])}
    m = re.match(r"^/api/artist/(\d+)$", path)
    if m:
        return page(m.group(1))
    return None


def handle_post(path, b):
    if path == "/api/artists/follow":
        return follow(b.get("id"), b.get("name"), bool(b.get("auto")))
    if path == "/api/album/get":
        return {"job": get_album(str(b.get("id") or ""))}
    m = re.match(r"^/api/artists/(\d+)/(unfollow|auto|check)$", path)
    if not m:
        return None
    aid, act = m.groups()
    with LOCK:
        f = FOLLOWS.get(aid)
    if not my_follow(f):
        raise LookupError("You don't follow them")
    if act == "unfollow":
        with LOCK:
            fans(f).pop(core.users.me_key(), None)
            if not fans(f):                              # nobody follows them any more
                FOLLOWS.pop(aid, None)
                RELEASES[:] = [r for r in RELEASES if r.get("artist_id") != aid]
        save()
        return {"ok": True}
    if act == "auto":
        with LOCK:
            fans(f)[core.users.me_key()]["auto"] = bool(b.get("on"))
        save()
        return {"ok": True}
    return {"new": check(aid)}


def init(umculo):
    global core
    core = umculo
    data = core._load_json("artists.json", {})
    FOLLOWS.update(data.get("follows") or {})
    RELEASES[:] = data.get("releases") or []
    threading.Thread(target=loop, daemon=True).start()
