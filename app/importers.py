"""Bring play counts, favourites, ratings and playlists over from Plex, Jellyfin / Emby and iTunes / Apple Music.

Every source is read into the same shape, then matched to your songs by file path (the last folders and name),
else by title + artist + length, else by title + length. Counts are merged (the higher one wins), never added twice.
"""
import datetime
import os
import plistlib
import re
import threading
import time
import urllib.parse

core = None


# --------------------------------------------------------------------------- matching and applying
class Matcher:
    def __init__(self, libs):
        db = core.musicdb
        self.by_tail, self.by_tag, self.by_title = {}, {}, {}
        where = f"lib IN ({','.join(str(int(i)) for i in libs)})" if libs else "1=1"
        for r in db.q(f"SELECT id, path, title, artist, duration FROM song WHERE {where}"):
            parts = r["path"].replace("\\", "/").lower().split("/")
            for n in (3, 2):
                self.by_tail.setdefault("/".join(parts[-n:]), r["id"])
            d = int(round(r["duration"] or 0))
            t, a = db.fold(r["title"]), db.fold(db.primary(r["artist"]))
            for dd in (d - 2, d - 1, d, d + 1, d + 2):
                self.by_tag.setdefault((t, a, dd), r["id"])
                self.by_title.setdefault((t, dd), r["id"])

    def find(self, rec):
        db = core.musicdb
        p = (rec.get("path") or "").replace("\\", "/").lower()
        if p:
            parts = p.split("/")
            for n in (3, 2):
                hit = self.by_tail.get("/".join(parts[-n:]))
                if hit:
                    return hit
        d = int(round(rec.get("duration") or 0))
        t = db.fold(re.sub(r"\s*[(\[].*$", "", rec.get("title") or "")) or db.fold(rec.get("title"))
        hit = self.by_tag.get((db.fold(rec.get("title")), db.fold(db.primary(rec.get("artist") or "")), d))
        return hit or self.by_tag.get((t, db.fold(db.primary(rec.get("artist") or "")), d)) or self.by_title.get((db.fold(rec.get("title")), d))


def apply(who, songs, playlists, say, source):
    srv, db = core.server, core.musicdb
    m = Matcher(db.libraries_for(who))
    tot = {"songs": 0, "stars": 0, "playlists": 0, "missed": 0, "people": 1}
    say(f"Matching {len(songs):,} songs from {source} to yours")
    for rec in songs:
        if not (rec.get("plays") or rec.get("starred") or rec.get("rating")):
            continue
        sid = m.find(rec)
        if not sid:
            tot["missed"] += 1
            continue
        if rec.get("plays"):
            srv.set_plays(who, sid, int(rec["plays"]), rec.get("played") or time.time())
        kw = {}
        if rec.get("starred"):
            kw["starred"] = rec["starred"]
            tot["stars"] += 1
        if rec.get("rating"):
            kw["rating"] = max(1, min(5, int(rec["rating"])))
        if kw:
            srv.touch_ann(who, sid, "song", **kw)
        tot["songs"] += 1
    srv.roll_up_plays(who)
    owner = srv.uid(who)
    have = {r["name"].lower() for r in db.q("SELECT name FROM playlist WHERE owner=?", (owner,))}
    for pl in playlists:
        if not pl.get("name") or pl["name"].lower() in have:
            continue
        ids = [i for i in (m.find(r) for r in pl.get("songs") or []) if i]
        if not ids:
            continue
        pid = "p" + db.h(source, owner, pl["name"])
        db.x("INSERT OR REPLACE INTO playlist (id, name, owner, public, comment, created, changed, path, mtime) VALUES (?,?,?,?,?,?,?,?,?)",
             (pid, pl["name"], owner, 0, f"From {source}", time.time(), time.time(), "", 0))
        srv.set_pl_songs(pid, list(dict.fromkeys(ids)))
        tot["playlists"] += 1
    n = lambda k, w: f"{tot[k]:,} {w}" + ("" if tot[k] == 1 else "s")  # noqa: E731
    tot["summary"] = (f"From {source}: {n('songs', 'song')} with plays, favourites or ratings, {n('stars', 'favourite')}, "
                      f"{n('playlists', 'playlist')}" + (f"; {tot['missed']:,} not in your folders" if tot["missed"] else ""))
    return tot


def ts(v):
    """Seconds since 1970 from a number, a datetime or an ISO date ("2024-05-01T10:00:00.0000000Z")."""
    if not v:
        return 0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, datetime.datetime):
        return (v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)).timestamp()
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", str(v))
    if not m:
        return 0
    return datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()


# --------------------------------------------------------------------------- Plex
def plex(url, token, say):
    h = {"X-Plex-Token": token, "Accept": "application/json"}
    get = lambda p: (core.http("GET", url + p, headers=h, timeout=120, retries=1) or {}).get("MediaContainer") or {}  # noqa: E731
    say("Reading Plex")
    secs = [d for d in get("/library/sections").get("Directory") or [] if d.get("type") == "artist"]
    if not secs:
        raise ValueError("Plex has no music library on that server")

    def rec(t):
        part = ((t.get("Media") or [{}])[0].get("Part") or [{}])[0]
        r = t.get("userRating") or 0
        return {"title": t.get("title"), "artist": t.get("originalTitle") or t.get("grandparentTitle"), "album": t.get("parentTitle"),
                "duration": (t.get("duration") or 0) / 1000, "path": part.get("file") or "", "plays": t.get("viewCount") or 0,
                "played": t.get("lastViewedAt") or 0, "rating": round(r / 2) if r else 0, "starred": (t.get("lastRatedAt") or time.time()) if r >= 10 else 0}
    songs = []
    for s in secs:
        off = 0
        while True:
            mc = get(f"/library/sections/{s['key']}/all?type=10&X-Plex-Container-Start={off}&X-Plex-Container-Size=1000")
            batch = mc.get("Metadata") or []
            songs += [rec(t) for t in batch]
            say(f"Reading Plex: {len(songs):,} songs")
            if len(batch) < 1000:
                break
            off += 1000
    pls = []
    for p in get("/playlists?playlistType=audio").get("Metadata") or []:
        if p.get("smart"):
            continue
        items = get(f"/playlists/{p['ratingKey']}/items").get("Metadata") or []
        pls.append({"name": p.get("title"), "songs": [rec(t) for t in items]})
    return songs, pls


# --------------------------------------------------------------------------- Jellyfin / Emby
def jellyfin(url, username, password, say):
    auth = 'MediaBrowser Client="Umculo", Device="Umculo", DeviceId="umculo-import", Version="2"'
    try:
        r = core.http("POST", url + "/Users/AuthenticateByName", body={"Username": username, "Pw": password},
                      headers={"X-Emby-Authorization": auth, "Authorization": auth}, timeout=30, retries=0)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Couldn't sign in to {url}: {e}")
    tok, uidj = r["AccessToken"], r["User"]["Id"]
    h = {"X-Emby-Token": tok, "X-MediaBrowser-Token": tok}
    get = lambda p: core.http("GET", url + p, headers=h, timeout=120, retries=1) or {}  # noqa: E731

    def rec(t):
        ud = t.get("UserData") or {}
        return {"title": t.get("Name"), "artist": ", ".join(t.get("Artists") or []) or t.get("AlbumArtist"), "album": t.get("Album"),
                "duration": (t.get("RunTimeTicks") or 0) / 1e7, "path": t.get("Path") or "", "plays": ud.get("PlayCount") or 0,
                "played": ts(ud.get("LastPlayedDate")), "starred": ts(ud.get("LastPlayedDate")) or time.time() if ud.get("IsFavorite") else 0,
                "rating": round((ud.get("Rating") or 0) / 2) if ud.get("Rating") else 0}
    songs, off = [], 0
    while True:
        d = get(f"/Users/{uidj}/Items?Recursive=true&IncludeItemTypes=Audio&Fields=Path,UserData&StartIndex={off}&Limit=2000")
        batch = d.get("Items") or []
        songs += [rec(t) for t in batch]
        say(f"Reading {username}'s songs: {len(songs):,}")
        if len(batch) < 2000:
            break
        off += 2000
    pls = []
    for p in (get(f"/Users/{uidj}/Items?Recursive=true&IncludeItemTypes=Playlist").get("Items") or []):
        items = get(f"/Playlists/{p['Id']}/Items?UserId={uidj}&Fields=Path").get("Items") or []
        if items and all((i.get("MediaType") or "Audio") == "Audio" for i in items):
            pls.append({"name": p.get("Name"), "songs": [rec(t) for t in items]})
    return songs, pls


# --------------------------------------------------------------------------- iTunes / Apple Music library file
def itunes(xml, say):
    say("Reading the iTunes library file")
    try:
        lib = plistlib.loads(xml.encode() if isinstance(xml, str) else xml)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"That isn't an iTunes / Music library file (File > Library > Export Library…): {e}")
    tracks = {}
    for tid, t in (lib.get("Tracks") or {}).items():
        loc = urllib.parse.unquote(urllib.parse.urlparse(t.get("Location") or "").path)
        r = t.get("Rating") or 0
        if t.get("Rating Computed"):
            r = 0
        tracks[str(tid)] = {"title": t.get("Name"), "artist": t.get("Artist"), "album": t.get("Album"), "duration": (t.get("Total Time") or 0) / 1000,
                            "path": loc, "plays": t.get("Play Count") or 0, "played": ts(t.get("Play Date UTC")),
                            "rating": round(r / 20) if r else 0, "starred": ts(t.get("Play Date UTC")) or time.time() if (t.get("Loved") or t.get("Favorited")) else 0}
    pls = []
    for p in lib.get("Playlists") or []:
        if p.get("Master") or p.get("Distinguished Kind") or p.get("Folder") or p.get("Smart Info") or p.get("Visible") is False:
            continue
        items = [tracks.get(str(i.get("Track ID"))) for i in p.get("Playlist Items") or []]
        items = [x for x in items if x]
        if items:
            pls.append({"name": p.get("Name"), "songs": items})
    return list(tracks.values()), pls


# --------------------------------------------------------------------------- jobs (shared with Move in)
def start(source, who, fn):
    job = core.migrate.JOB
    if job["state"] == "running":
        raise ValueError("An import is already running")
    if not core.builtin():
        raise ValueError("Make Umculo your music server first (step 2)")
    job.update(state="running", message=f"Starting {source}", at=time.time(), result=None)

    def run():
        say = lambda m: job.update(message=m, at=time.time())  # noqa: E731
        try:
            while core.musicdb.SCAN["running"] or not core.musicdb.SCAN["last"]:
                say("Waiting for Umculo to finish reading your music")
                time.sleep(3)
            songs, pls = fn(say)
            res = apply(who, songs, pls, say, source)
            job.update(state="done", message=res["summary"], at=time.time(), result=res)
            core.log("move in:", res["summary"])
        except Exception as e:  # noqa: BLE001
            job.update(state="failed", message=str(e)[:300], at=time.time())
            core.log("move in from", source, "failed:", e)
    threading.Thread(target=run, daemon=True).start()
    return job


def who_from(b):
    users = core.users
    t = b.get("person") or "admin"
    if t == "admin":
        return users.admin_user() or users.OPEN
    u = users.USERS.get(t)
    if not u:
        raise ValueError("Pick who it's for")
    return u


def handle_post(p, b):
    if p == "/api/migrate/plex":
        url, tok = (b.get("url") or "").rstrip("/"), (b.get("token") or "").strip()
        if not url.startswith("http") or not tok:
            raise ValueError("Plex's address (like http://192.168.1.10:32400) and your Plex token, please")
        return start("Plex", who_from(b), lambda say: plex(url, tok, say))
    if p == "/api/migrate/jellyfin":
        url = (b.get("url") or "").rstrip("/")
        if not url.startswith("http") or not b.get("username"):
            raise ValueError("The Jellyfin or Emby address, username and password, please")
        return start("Jellyfin", who_from(b), lambda say: jellyfin(url, b["username"], b.get("password") or "", say))
    if p == "/api/migrate/itunes":
        xml = b.get("xml") or ""
        if "<plist" not in xml[:2000]:
            raise ValueError("Choose the Library.xml you exported from iTunes or Music (File > Library > Export Library…)")
        return start("iTunes", who_from(b), lambda say: itunes(xml, say))
    return None


def init(umculo):
    global core
    core = umculo
