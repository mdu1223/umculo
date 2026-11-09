"""Umculo's built-in music server: the Subsonic / OpenSubsonic API that Arpeggi, Amperfy,
Substreamer, Symfonium, Feishin and friends speak, served straight from Umculo's own library
(musicdb). Same logins as Umculo, the household's folders and sharing, stars, ratings, play
counts, playlists, play queues, cover art, lyrics and streaming with on-the-fly conversion.

call(user, endpoint, params) -> the inside of "subsonic-response" (used by Umculo itself too)
handle(h, endpoint, params, fmt) -> answers an app's request (including binary ones)
"""
import datetime
import hashlib
import json
import collections
import ipaddress
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

core = None
db = None
REQ = threading.local()           # this request: the address it came in on, the music folder it asked for
NOW_PLAYING = {}                  # user id -> (song id, when, player)
LOSSLESS = {"flac", "wav", "aiff", "aif", "alac", "ape", "wv", "dsf"}
CTYPE = {"mp3": "audio/mpeg", "flac": "audio/flac", "m4a": "audio/mp4", "mp4": "audio/mp4", "aac": "audio/aac",
         "ogg": "audio/ogg", "opus": "audio/ogg", "wav": "audio/wav", "aiff": "audio/aiff", "aif": "audio/aiff",
         "wma": "audio/x-ms-wma", "ape": "audio/ape", "wv": "audio/wavpack", "dsf": "audio/dsf"}
EXT = [{"name": "apiKeyAuthentication", "versions": [1]}, {"name": "formPost", "versions": [1]},
       {"name": "songLyrics", "versions": [1]}, {"name": "transcodeOffset", "versions": [1]}]


class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def p1(params, k, d=""):
    v = params.get(k)
    if isinstance(v, list):
        return v[0] if v else d
    return d if v is None else v


def pint(params, k, d=0):
    try:
        return int(p1(params, k, d) or d)
    except (TypeError, ValueError):
        return d


def plist(params, k):
    v = params.get(k) or []
    return v if isinstance(v, list) else [v]


def iso(t):
    return datetime.datetime.utcfromtimestamp(t or 0).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# --------------------------------------------------------------------------- who is asking
def check_login(params):
    """-> Umculo user dict. Password (plain or enc:), token+salt, or an OpenSubsonic apiKey (Umculo access key)."""
    users = core.users
    key = p1(params, "apiKey")
    if key:
        t = core.share.token_info(key)
        if not t:
            raise ApiError(44, "Invalid API key")
        return users.USERS.get(t.get("user")) or users.admin_user() or users.OPEN
    name, pw, tok, salt = p1(params, "u"), p1(params, "p"), p1(params, "t"), p1(params, "s")
    if not name:
        raise ApiError(10, "Required parameter is missing: u")
    if users.locked_out(name):
        raise ApiError(40, users.LOCKED_MSG)
    if pw.startswith("enc:"):
        try:
            pw = bytes.fromhex(pw[4:]).decode()
        except ValueError:
            pw = ""
    candidates = []
    u = users.by_name(name)
    main = core.security.main_password_allowed()     # from the internet with approved devices on: app passwords only
    if u and not u.get("disabled"):
        if main:
            candidates.append((u, users.subsonic_secret(u)))
        for did, sec in core.security.app_secrets(u):
            candidates.append((u, sec, did))
    if main and name == core.CONFIG["nd_user"] and core.CONFIG["nd_password"]:       # the old Navidrome login keeps working
        candidates.append((users.admin_user() or users.OPEN, core.CONFIG["nd_password"]))
    for cand in candidates:
        user, secret = cand[0], cand[1]
        if len(cand) > 2 and ((pw and pw == secret) or (tok and salt and hashlib.md5((secret + salt).encode()).hexdigest() == tok.lower())):
            core.security.used(user, cand[2])
            return user
        if not secret:
            if pw and user is u and users.check_pw(pw, u.get("pw", "")):
                users.remember_secret(u, pw)
                return user
            continue
        if pw and pw == secret:
            return user
        if tok and salt and hashlib.md5((secret + salt).encode()).hexdigest() == tok.lower():
            return user
    if not users.USERS and not core.CONFIG["nd_password"]:
        return users.OPEN                                          # nothing set up yet: open, like the web page
    users.failed_login(name)
    if not main:
        raise ApiError(40, "From the internet, use an app password (Umculo: your account → Devices)")
    raise ApiError(40, "Wrong username or password")


def uid(u):
    """Whose stars, plays and playlists: the admin is always "admin" (stable even before accounts existed)."""
    if not u or core.users.is_admin(u):
        return "admin"
    return u["id"]


def set_request(h, params):
    proto = h.headers.get("X-Forwarded-Proto") or "http"
    host = h.headers.get("X-Forwarded-Host") or h.headers.get("Host") or ""
    REQ.base = f"{proto}://{host}" if host else ""
    f = p1(params, "musicFolderId")
    REQ.folder = int(f) if str(f).lstrip("-").isdigit() else None


def libs(u):
    mine = db.libraries_for(u) or [-1]
    f = getattr(REQ, "folder", None)
    if f is not None and f in mine:
        return [f]
    return mine


def lib_sql(u, col="lib"):
    ids = libs(u)
    return f"{col} IN ({','.join(str(int(i)) for i in ids)})"


# --------------------------------------------------------------------------- shaping answers
def anns(u, ids):
    if not ids:
        return {}
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in db.q(f"SELECT * FROM ann WHERE user=? AND item IN ({','.join('?' * len(chunk))})", [uid(u)] + chunk):
            out[r["item"]] = r
    return out


def put_ann(d, a):
    if not a:
        return d
    if a["starred"]:
        d["starred"] = iso(a["starred"])
    if a["rating"]:
        d["userRating"] = a["rating"]
    if a["plays"]:
        d["playCount"] = a["plays"]
    if a["played"]:
        d["played"] = iso(a["played"])
    return d


def song(r, a=None):
    suffix = r["suffix"] or ""
    d = {"id": r["id"], "parent": r["album_id"], "isDir": False, "title": r["title"], "album": r["album"],
         "artist": r["artist"], "track": r["track"] or None, "year": r["year"] or None, "genre": r["genre"] or None,
         "coverArt": r["album_id"], "size": r["size"], "contentType": CTYPE.get(suffix, "audio/" + suffix),
         "suffix": suffix, "duration": int(r["duration"] or 0), "bitRate": r["bitrate"], "path": r["rel"],
         "discNumber": r["disc"] or None, "created": iso(r["created"]), "albumId": r["album_id"],
         "artistId": r["artist_id"], "type": "music", "mediaType": "song", "isVideo": False,
         "samplingRate": r["samplerate"] or None, "bitDepth": r["bitdepth"] or None, "channelCount": r["channels"] or None,
         "displayArtist": r["artist"], "displayAlbumArtist": r["albumartist"],
         "artists": [{"id": r["artist_id"], "name": db.primary(r["artist"])}], "albumArtists": [{"id": r["albumartist_id"], "name": r["albumartist"]}],
         "genres": [{"name": g} for g in genres(r["genre"])], "musicBrainzId": r["mbid"] or None}
    ex = extra(r)
    if ex.get("rg"):
        d["replayGain"] = ex["rg"]
    for k, key in (("bpm", "bpm"), ("sort", "sortName"), ("comment", "comment")):
        if ex.get(k):
            d[key] = ex[k]
    d = {k: v for k, v in d.items() if v is not None}
    return put_ann(d, a)


def extra(r):
    try:
        return json.loads(r["extra"] or "{}") if "extra" in r.keys() else {}
    except ValueError:
        return {}


def genres(g):
    return [x.strip() for x in re.split(r"[;/]|\x00", g or "") if x.strip()]


def album(r, a=None, plays=None):
    d = {"id": r["id"], "name": r["name"], "title": r["name"], "album": r["name"], "artist": r["artist"],
         "artistId": r["artist_id"], "coverArt": r["id"], "songCount": r["songs"], "duration": int(r["duration"] or 0),
         "created": iso(r["created"]), "year": r["year"] or None, "genre": r["genre"] or None, "isDir": True,
         "parent": r["artist_id"], "isCompilation": bool(r["compilation"]),
         "artists": [{"id": r["artist_id"], "name": r["artist"]}], "displayArtist": r["artist"],
         "genres": [{"name": g} for g in genres(r["genre"])], "sortName": db.sort_name(r["name"])}
    d = {k: v for k, v in d.items() if v is not None}
    return put_ann(d, a)


def artist(r, a=None):
    d = {"id": r["id"], "name": r["name"], "albumCount": r["albums"], "coverArt": r["id"],
         "artistImageUrl": cover_link(r["id"]), "sortName": r["sort"]}
    return put_ann(d, a)


def cover_link(cid):
    base = getattr(REQ, "base", "") or core.CONFIG.get("public_url") or ""     # the address this app used: home, Tailscale or the internet
    return f"{base}/rest/getCoverArt?id={cid}" if base else None


def songs_out(u, rows):
    a = anns(u, [r["id"] for r in rows])
    return [song(r, a.get(r["id"])) for r in rows]


def albums_out(u, rows):
    a = anns(u, [r["id"] for r in rows])
    return [album(r, a.get(r["id"])) for r in rows]


def artists_out(u, rows):
    a = anns(u, [r["id"] for r in rows])
    return [{k: v for k, v in artist(r, a.get(r["id"])).items() if v is not None} for r in rows]


def web_params(sq):
    return ("raw", 0) if sq == "best" else ("mp3", int(sq))


def warm(u, sid, sq, can, net="home"):
    """Convert the next song now if the player will need it converted, so it starts instantly."""
    try:
        r = get_song_row(u, sid)
    except ApiError:
        return
    fmt, want = web_params(sq)
    convert, kbps, out_fmt = plan(r, fmt, want, set(filter(None, can.lower().split(","))) or None, net)
    if convert:
        prepare(r, kbps, out_fmt)


def stream_label(u, sid, sq, can):
    """What the web player is playing and in what quality."""
    try:
        r = get_song_row(u, sid)
    except ApiError:
        return {}
    fmt, want = web_params(sq)
    convert, kbps, out_fmt = plan(r, fmt, want, set(filter(None, can.lower().split(","))) or None, network())
    lossless = r["suffix"] in LOSSLESS
    orig = f"{r['suffix'].upper()} {r['bitrate'] or ''}".strip() + (" kbps" if lossless else "")
    if not convert:
        return {"quality": orig, "tier": "lossless" if lossless else "", "from": "Your library"}
    return {"quality": f"{out_fmt.upper()} {kbps}", "from": f"Your library, converted from {orig}"}


PREP_LOCKS = {}


def prepare(r, kbps, out_fmt="mp3"):
    """Convert a song whole into the cache (once, even if asked twice at the same time). -> path or None"""
    cache = os.path.join(core.CONFIG["staging"], "transcode")
    os.makedirs(cache, exist_ok=True)
    final = os.path.join(cache, f"{r['id']}.{int(r['mtime'])}.{kbps}.{out_fmt}")
    lock = PREP_LOCKS.setdefault(final, threading.Lock())
    with lock:
        if os.path.exists(final):
            return final
        tmp = final + ".prep"
        args = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", r["path"], "-map", "0:a:0", "-vn"] + \
            (["-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-f", "mp3"] if out_fmt == "mp3" else ["-c:a", "libopus", "-b:a", f"{kbps}k", "-f", "ogg"]) + [tmp]
        try:
            subprocess.run(args, timeout=180, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.replace(tmp, final)
            return final
        except Exception as e:  # noqa: BLE001
            log("converting for the player failed:", r["path"], e)
            if os.path.exists(tmp):
                os.remove(tmp)
            return None
    


def get_song_row(u, sid):
    r = db.q(f"SELECT * FROM song WHERE id=? AND {lib_sql(u)}", (sid,), one=True)
    if not r:
        raise ApiError(70, "Song not found")
    return r


# --------------------------------------------------------------------------- browsing
def ignored_articles():
    return "The El La Los Las Le Les A An"


def artist_index(u):
    rows = sorted(db.q(f"SELECT * FROM artist WHERE {lib_sql(u)} AND albums > 0"), key=lambda r: db.natural(r["name"]))
    idx = {}
    for r in rows:
        ch = (r["sort"][:1] or "#").upper()
        ch = ch if ch.isalpha() else "#"
        idx.setdefault(ch, []).append(r)
    return [{"name": k, "artist": artists_out(u, v)} for k, v in sorted(idx.items())]


def get_artist(u, aid):
    r = db.q(f"SELECT * FROM artist WHERE id=? AND {lib_sql(u)}", (aid,), one=True)
    if not r:
        raise ApiError(70, "Artist not found")
    als = db.q(f"SELECT * FROM album WHERE artist_id=? AND {lib_sql(u)} ORDER BY year DESC, name", (aid,))
    if not als:                                              # a featured artist: albums they appear on
        als = db.q(f"SELECT * FROM album WHERE id IN (SELECT album_id FROM song WHERE artist_id=?) AND {lib_sql(u)}", (aid,))
    d = artists_out(u, [r])[0]
    d["album"] = albums_out(u, als)
    d["albumCount"] = len(als)
    return d


def get_album(u, alid):
    r = db.q(f"SELECT * FROM album WHERE id=? AND {lib_sql(u)}", (alid,), one=True)
    if not r:
        raise ApiError(70, "Album not found")
    rows = db.q("SELECT * FROM song WHERE album_id=? ORDER BY disc, track, title", (alid,))
    d = albums_out(u, [r])[0]
    d["song"] = songs_out(u, rows)
    return d


def music_directory(u, i):
    """Folder-style browsing, the way older apps ask for it: artist -> albums -> songs."""
    r = db.q(f"SELECT * FROM artist WHERE id=? AND {lib_sql(u)}", (i,), one=True)
    if r:
        a = get_artist(u, i)
        return {"id": i, "name": r["name"], "child": [{**al, "title": al["name"], "isDir": True, "parent": i} for al in a["album"]]}
    r = db.q(f"SELECT * FROM album WHERE id=? AND {lib_sql(u)}", (i,), one=True)
    if r:
        a = get_album(u, i)
        return {"id": i, "name": r["name"], "parent": r["artist_id"], "child": a["song"]}
    if i.startswith("lib"):
        return {"id": i, "name": "Music", "child": [{**x, "isDir": True, "title": x["name"], "parent": i}
                                                     for g in artist_index(u) for x in g["artist"]]}
    raise ApiError(70, "Directory not found")


def album_list(u, params):
    t = p1(params, "type", "newest")
    size, off = min(pint(params, "size", 10), 500), pint(params, "offset", 0)
    me = uid(u)
    base = f"SELECT album.* FROM album LEFT JOIN ann ON ann.item = album.id AND ann.user = ? WHERE {lib_sql(u, 'album.lib')}"
    args = [me]
    order = {"newest": "album.created DESC", "recent": "ann.played DESC", "frequent": "ann.plays DESC",
             "highest": "ann.rating DESC", "alphabeticalByName": "lower(album.name)", "alphabeticalByArtist": "lower(album.artist), album.year",
             "starred": "ann.starred DESC", "byYear": "album.year", "byGenre": "lower(album.name)"}.get(t)
    if t == "random":
        order = "RANDOM()"
    if t == "recent":
        base += " AND ann.played > 0"
    if t == "frequent":
        base += " AND ann.plays > 0"
    if t == "highest":
        base += " AND ann.rating > 0"
    if t == "starred":
        base += " AND ann.starred > 0"
    if t == "byYear":
        a, b = pint(params, "fromYear", 0), pint(params, "toYear", 3000)
        base += f" AND album.year BETWEEN {min(a, b)} AND {max(a, b)}"
        order = "album.year" + (" DESC" if a > b else "")
    if t == "byGenre":
        base += " AND lower(album.genre) = lower(?)"
        args.append(p1(params, "genre"))
    if not order:
        raise ApiError(0, f"Unknown list type {t}")
    if t in ("alphabeticalByName", "alphabeticalByArtist"):      # natural order: "2 Pac" before "10 Years"
        rows = db.q(f"{base}", args)
        key = (lambda r: db.natural(r["name"])) if t == "alphabeticalByName" else (lambda r: (db.natural(r["artist"]), r["year"] or 0))
        rows = sorted(rows, key=key)[off:off + size]
    else:
        rows = db.q(f"{base} ORDER BY {order} LIMIT ? OFFSET ?", args + [size, off])
    return albums_out(u, rows)


def search(u, params, v2=False):
    query = (p1(params, "query") or "").strip().strip('"')
    words = [w for w in core_fold(query).split() if w]
    def where(col):
        if not words:
            return "1=1", []
        return " AND ".join(f"{col} LIKE ?" for _ in words), [f"%{w}%" for w in words]
    out = {}
    n, o = pint(params, "artistCount", 20), pint(params, "artistOffset", 0)
    if n:
        w, a = where("search")
        out["artist"] = artists_out(u, db.q(f"SELECT * FROM artist WHERE {lib_sql(u)} AND {w} ORDER BY albums DESC, sort LIMIT ? OFFSET ?", a + [n, o]))
    n, o = pint(params, "albumCount", 20), pint(params, "albumOffset", 0)
    if n:
        w, a = where("search")
        out["album"] = albums_out(u, db.q(f"SELECT * FROM album WHERE {lib_sql(u)} AND {w} ORDER BY lower(name) LIMIT ? OFFSET ?", a + [n, o]))
    n, o = pint(params, "songCount", 20), pint(params, "songOffset", 0)
    if n:
        w, a = where("search")
        out["song"] = songs_out(u, db.q(f"SELECT * FROM song WHERE {lib_sql(u)} AND {w} ORDER BY {'title' if words else 'lower(albumartist), album_id, disc, track'} LIMIT ? OFFSET ?", a + [n, o]))
    if v2:
        out["artist"] = [{**x, "isDir": True} for x in out.get("artist", [])]
        out["album"] = [{**x, "isDir": True, "title": x["name"]} for x in out.get("album", [])]
    return out


def core_fold(s):
    return db.fold(s)


# --------------------------------------------------------------------------- playlists
def visible_playlists(u):
    me = uid(u)
    rows = [dict(r) for r in db.q("SELECT * FROM playlist ORDER BY lower(name)")]
    shared = set((u or {}).get("shared_playlists") or [])
    out = []
    for r in rows:
        if r["owner"] == me or r["public"] or (r["path"] and r["path"] in shared):
            out.append(r)
    return out


def pl_songs(u, pid):
    rows = db.q("""SELECT song.* FROM plsong JOIN song ON song.id = plsong.song WHERE plsong.pid=? ORDER BY plsong.pos""", (pid,))
    allowed = set(libs(u))
    out, missing = [], []
    for r in rows:
        if r["lib"] in allowed:
            out.append(r)
        else:
            missing.append(r)
    if missing and core.users.HOUSE.get("mode") == "separate":          # shared: use their own linked copy
        for r in missing:
            copy = core.users.shared_copy(u, r["path"])
            c = db.q("SELECT * FROM song WHERE path=?", (copy,), one=True)
            if c and c["lib"] in allowed:
                out.append(c)
    return out


def playlist_out(u, r, entries=False):
    rows = pl_songs(u, r["id"])
    owner = core.users.USERS.get(r["owner"], {}).get("username") or (core.users.admin_user() or {}).get("username") or "admin"
    d = {"id": r["id"], "name": r["name"], "comment": r["comment"] or "", "owner": owner, "public": bool(r["public"]),
         "songCount": len(rows), "duration": int(sum(x["duration"] or 0 for x in rows)), "created": iso(r["created"]),
         "changed": iso(r["changed"]), "coverArt": rows[0]["album_id"] if rows else r["id"],
         "readonly": r["owner"] != uid(u)}
    if entries:
        d["entry"] = songs_out(u, rows)
    return d


def write_playlist_file(r, song_ids):
    """Playlists that live in files (yours from Spotify, imports...) stay in step with the app's edits."""
    if not r["path"]:
        return
    paths = []
    for sid in song_ids:
        s = db.q("SELECT path FROM song WHERE id=?", (sid,), one=True)
        if s:
            paths.append(s["path"])
    with core.M3U_LOCK:
        core.write_m3u(r["path"], paths)
    db.x("UPDATE playlist SET mtime=? WHERE id=?", (os.path.getmtime(r["path"]), r["id"]))


def set_pl_songs(pid, ids):
    with db.LOCK:
        db.DB.execute("DELETE FROM plsong WHERE pid=?", (pid,))
        for i, sid in enumerate(ids):
            s = db.DB.execute("SELECT path FROM song WHERE id=?", (sid,)).fetchone()
            db.DB.execute("INSERT INTO plsong (pid, pos, song, path) VALUES (?,?,?,?)", (pid, i, sid, s["path"] if s else ""))
        db.DB.execute("UPDATE playlist SET changed=? WHERE id=?", (time.time(), pid))
        db.DB.commit()


def my_playlist(u, pid):
    r = db.q("SELECT * FROM playlist WHERE id=?", (pid,), one=True)
    if not r:
        raise ApiError(70, "Playlist not found")
    if r["owner"] != uid(u) and not core.users.is_admin(u):
        raise ApiError(50, "This playlist is shared with you; only its owner can change it")
    return r


# --------------------------------------------------------------------------- stars, ratings, plays
def touch_ann(u, item, kind, **kw):
    me = uid(u)
    with db.LOCK:
        r = db.DB.execute("SELECT * FROM ann WHERE user=? AND item=?", (me, item)).fetchone()
        cur = dict(r) if r else {"user": me, "item": item, "kind": kind, "starred": 0, "rating": 0, "plays": 0, "played": 0}
        if "plays" in kw:
            cur["plays"] = (cur["plays"] or 0) + kw.pop("plays")
        cur.update(kw)
        db.DB.execute("INSERT OR REPLACE INTO ann (user, item, kind, starred, rating, plays, played) VALUES (?,?,?,?,?,?,?)",
                      (me, item, kind, cur["starred"], cur["rating"], cur["plays"], cur["played"]))
        db.DB.commit()


def kind_of(i):
    if db.q("SELECT 1 FROM song WHERE id=?", (i,), one=True):
        return "song"
    if db.q("SELECT 1 FROM album WHERE id=?", (i,), one=True):
        return "album"
    return "artist"


def scrobble(u, sid, when, submission):
    r = db.q("SELECT * FROM song WHERE id=?", (sid,), one=True)
    if not r:
        return
    if not submission:
        NOW_PLAYING[uid(u)] = (sid, time.time())
        return
    t = when or time.time()
    log_listen(u, dict(r), t)
    touch_ann(u, sid, "song", plays=1, played=t)
    touch_ann(u, r["album_id"], "album", plays=1, played=t)
    touch_ann(u, r["albumartist_id"], "artist", plays=1, played=t)
    lb_token = (u or {}).get("listenbrainz") or (core.foryou.CFG.get("lb_token") if uid(u) == "admin" else "")
    if lb_token:
        threading.Thread(target=listenbrainz, args=(lb_token, r, t), daemon=True).start()


def log_listen(u, r, t, source="library"):
    """Every play, kept: the listening summaries are made from this."""
    try:
        db.x("""INSERT INTO listen (user, song, title, artist, album, genre, secs, at, source, artist_id, album_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (uid(u), r.get("id") or "", r.get("title") or "", r.get("artist") or "",
                                                     r.get("album") or "", r.get("genre") or "", int(r.get("duration") or 0), t, source,
                                                     r.get("albumartist_id") or r.get("artist_id") or "", r.get("album_id") or ""))
    except Exception as e:  # noqa: BLE001
        core.log("listen log:", e)


def instant_mix(u, seed, n=50):
    """Songs from your library that go with this one: the artist, artists like them, the same sound and era."""
    r = db.q(f"SELECT * FROM song WHERE id=? AND {lib_sql(u)}", (seed,), one=True) or \
        db.q(f"SELECT * FROM song WHERE album_id=? AND {lib_sql(u)} ORDER BY RANDOM() LIMIT 1", (seed,), one=True) or \
        db.q(f"SELECT * FROM song WHERE (albumartist_id=? OR artist_id=?) AND {lib_sql(u)} ORDER BY RANDOM() LIMIT 1", (seed, seed), one=True)
    if not r:
        return []
    picked, seen = [r], {r["id"]}

    def add(rows, limit):
        k = 0
        for x in rows:
            if x["id"] not in seen and k < limit:
                seen.add(x["id"]); picked.append(x); k += 1
    add(db.q(f"SELECT * FROM song WHERE {lib_sql(u)} AND (albumartist_id=? OR artist_id=?) ORDER BY RANDOM() LIMIT 8",
             (r["albumartist_id"], r["artist_id"])), max(3, n // 6))
    names = []
    try:
        aid = dz_artist(db.primary(r["artist"]))
        if aid:
            names = [core.norm(x["name"]) for x in (core.dz_get(f"/artist/{aid}/related?limit=25", 86400 * 7) or {}).get("data") or []]
    except Exception:  # noqa: BLE001
        names = []
    if names:
        rel = [x for x in db.q(f"SELECT id, name FROM artist WHERE {lib_sql(u)}") if core.norm(x["name"]) in names]
        if rel:
            ids = [x["id"] for x in rel]
            add(db.q(f"SELECT * FROM song WHERE {lib_sql(u)} AND (albumartist_id IN ({','.join('?' * len(ids))}) OR artist_id IN ({','.join('?' * len(ids))})) "
                     "ORDER BY RANDOM() LIMIT ?", ids + ids + [n]), n // 2)
    if r["genre"]:
        y = r["year"] or 0
        add(db.q(f"""SELECT * FROM song WHERE {lib_sql(u)} AND lower(genre)=lower(?) ORDER BY
                     CASE WHEN ? > 0 AND year BETWEEN ? AND ? THEN 0 ELSE 1 END, RANDOM() LIMIT ?""", (r["genre"], y, y - 6, y + 6, n)), n)
    if len(picked) < n // 2:                       # thin tags: fill with what you play most
        add(db.q(f"""SELECT song.* FROM song LEFT JOIN ann ON ann.item = song.id AND ann.user = ? WHERE {lib_sql(u, 'song.lib')}
                     ORDER BY ann.plays DESC, RANDOM() LIMIT ?""", (uid(u), n)), n - len(picked))
    head, rest = picked[:1], picked[1:]
    random.shuffle(rest)                           # mixed, but never the same artist three in a row
    out, last = list(head), [head[0]["albumartist_id"]]
    while rest:
        i = next((k for k, x in enumerate(rest) if x["albumartist_id"] not in last[-2:]), 0)
        x = rest.pop(i); out.append(x); last.append(x["albumartist_id"])
    return out[:n]


def listenbrainz(token, r, t):
    try:
        core.http("POST", core.foryou.CFG["lb_api"] + "/1/submit-listens", headers={"Authorization": "Token " + token},
                  body={"listen_type": "single", "payload": [{"listened_at": int(t), "track_metadata": {
                      "artist_name": r["artist"], "track_name": r["title"], "release_name": r["album"]}}]}, timeout=15, retries=1)
    except Exception as e:  # noqa: BLE001
        core.log("ListenBrainz scrobble failed:", e)


# --------------------------------------------------------------------------- lyrics
def lyrics_text(path):
    lrc = os.path.splitext(path)[0] + ".lrc"
    if os.path.exists(lrc):
        with open(lrc, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    try:
        import mutagen
        f = mutagen.File(path)
        tags = getattr(f, "tags", None) or {}
        if hasattr(tags, "getall"):
            u = tags.getall("USLT")
            return str(u[0].text) if u else ""
        for k in ("lyrics", "LYRICS", "UNSYNCEDLYRICS", "\xa9lyr"):
            v = tags.get(k) if hasattr(tags, "get") else None
            if v:
                return str(v[0] if isinstance(v, list) else v)
    except Exception:  # noqa: BLE001
        pass
    return ""


def structured(text, r):
    lines, synced = [], False
    for ln in text.splitlines():
        m = re.match(r"^\[(\d+):(\d+)(?:[.:](\d+))?\](.*)$", ln.strip())
        if m:
            synced = True
            ms = int(m.group(1)) * 60000 + int(m.group(2)) * 1000 + int((m.group(3) or "0").ljust(3, "0")[:3])
            lines.append({"start": ms, "value": m.group(4).strip()})
        elif ln.strip() and not re.match(r"^\[[a-z]+:", ln.strip()):
            lines.append({"value": ln.strip()})
    if not synced:
        lines = [{"value": x["value"]} for x in lines]
    return {"displayArtist": r["artist"], "displayTitle": r["title"], "lang": "xxx", "synced": synced, "line": lines}


# --------------------------------------------------------------------------- the API
def call(u, endpoint, params):
    """Everything that answers with data (not audio or images)."""
    me = uid(u)
    users = core.users
    if endpoint == "ping":
        return {}
    if endpoint == "getLicense":
        return {"license": {"valid": True, "email": "", "licenseExpires": "2099-01-01T00:00:00.000Z"}}
    if endpoint == "getOpenSubsonicExtensions":
        return {"openSubsonicExtensions": EXT}
    if endpoint == "getMusicFolders":
        rows = [r for r in db.q("SELECT * FROM library ORDER BY id") if r["id"] in libs(u)]
        return {"musicFolders": {"musicFolder": [{"id": r["id"], "name": r["name"]} for r in rows]}}
    if endpoint in ("getIndexes", "getArtists"):
        key = "indexes" if endpoint == "getIndexes" else "artists"
        d = {"ignoredArticles": ignored_articles(), "index": artist_index(u)}
        if endpoint == "getIndexes":
            d["lastModified"] = int((db.SCAN["last"] or time.time()) * 1000)
        return {key: d}
    if endpoint == "getArtist":
        return {"artist": get_artist(u, p1(params, "id"))}
    if endpoint == "getAlbum":
        return {"album": get_album(u, p1(params, "id"))}
    if endpoint == "getSong":
        return {"song": songs_out(u, [get_song_row(u, p1(params, "id"))])[0]}
    if endpoint == "getMusicDirectory":
        return {"directory": music_directory(u, p1(params, "id"))}
    if endpoint == "getGenres":
        rows = db.q(f"SELECT genre, COUNT(*) n, COUNT(DISTINCT album_id) a FROM song WHERE {lib_sql(u)} AND genre != '' GROUP BY lower(genre) ORDER BY genre")
        return {"genres": {"genre": [{"value": r["genre"], "songCount": r["n"], "albumCount": r["a"]} for r in rows]}}
    if endpoint in ("getAlbumList", "getAlbumList2"):
        als = album_list(u, params)
        if endpoint == "getAlbumList":
            als = [{**a, "title": a["name"], "isDir": True} for a in als]
        return {"albumList2" if endpoint.endswith("2") else "albumList": {"album": als}}
    if endpoint == "getRandomSongs":
        sql, args = f"SELECT * FROM song WHERE {lib_sql(u)}", []
        if p1(params, "genre"):
            sql += " AND lower(genre) = lower(?)"
            args.append(p1(params, "genre"))
        if p1(params, "fromYear"):
            sql += f" AND year >= {pint(params, 'fromYear')}"
        if p1(params, "toYear"):
            sql += f" AND year <= {pint(params, 'toYear')}"
        return {"randomSongs": {"song": songs_out(u, db.q(sql + " ORDER BY RANDOM() LIMIT ?", args + [min(pint(params, "size", 10), 500)]))}}
    if endpoint == "getSongsByGenre":
        rows = db.q(f"SELECT * FROM song WHERE {lib_sql(u)} AND lower(genre) = lower(?) ORDER BY albumartist, album, track LIMIT ? OFFSET ?",
                    (p1(params, "genre"), min(pint(params, "count", 10), 500), pint(params, "offset", 0)))
        return {"songsByGenre": {"song": songs_out(u, rows)}}
    if endpoint in ("getStarred", "getStarred2"):
        st = lambda t: f"SELECT {t}.* FROM {t} JOIN ann ON ann.item = {t}.id AND ann.user = ? WHERE ann.starred > 0 AND {lib_sql(u, t + '.lib')} ORDER BY ann.starred DESC"  # noqa: E731
        d = {"artist": artists_out(u, db.q(st("artist"), (me,))), "album": albums_out(u, db.q(st("album"), (me,))),
             "song": songs_out(u, db.q(st("song"), (me,)))}
        return {"starred2" if endpoint.endswith("2") else "starred": d}
    if endpoint in ("search2", "search3"):
        return {"searchResult3" if endpoint == "search3" else "searchResult2": search(u, params, endpoint == "search2")}
    if endpoint == "search":
        return {"searchResult": {"offset": 0, "totalHits": 0, "match": []}}
    if endpoint in ("getArtistInfo", "getArtistInfo2"):
        aid = p1(params, "id")
        r = db.q("SELECT * FROM artist WHERE id=?", (aid,), one=True) or {"name": ""}
        info = {"biography": "", "musicBrainzId": "", "lastFmUrl": "",
                "smallImageUrl": cover_link(aid), "mediumImageUrl": cover_link(aid), "largeImageUrl": cover_link(aid)}
        sim = []
        if r["name"]:
            names = [core.norm(x["name"]) for x in (core.dz_get("/artist/" + str(dz_artist(r["name"]) or 0) + "/related?limit=10") or {}).get("data") or []] if dz_artist(r["name"]) else []
            if names:
                rows = db.q(f"SELECT * FROM artist WHERE {lib_sql(u)} AND albums > 0")
                sim = artists_out(u, [x for x in rows if core.norm(x["name"]) in names][:pint(params, "count", 20)])
        info["similarArtist"] = sim
        return {"artistInfo2" if endpoint.endswith("2") else "artistInfo": {k: v for k, v in info.items() if v is not None}}
    if endpoint in ("getAlbumInfo", "getAlbumInfo2"):
        aid = p1(params, "id")
        return {"albumInfo": {"notes": "", "smallImageUrl": cover_link(aid), "mediumImageUrl": cover_link(aid),
                              "largeImageUrl": cover_link(aid)}}
    if endpoint == "getTopSongs":
        name = p1(params, "artist")
        rows = db.q(f"""SELECT song.* FROM song LEFT JOIN ann ON ann.item = song.id AND ann.user = ?
                        WHERE {lib_sql(u, 'song.lib')} AND (lower(song.artist) = lower(?) OR lower(song.albumartist) = lower(?))
                        ORDER BY ann.plays DESC, song.year DESC LIMIT ?""", (me, name, name, pint(params, "count", 50)))
        return {"topSongs": {"song": songs_out(u, rows)}}
    if endpoint in ("getSimilarSongs", "getSimilarSongs2"):
        rows = instant_mix(u, p1(params, "id"), pint(params, "count", 50) + 1)[1:]
        return {"similarSongs2" if endpoint.endswith("2") else "similarSongs": {"song": songs_out(u, rows)}}
    if endpoint == "getNowPlaying":
        out = []
        for who, (sid, t) in list(NOW_PLAYING.items()):
            r = db.q("SELECT * FROM song WHERE id=?", (sid,), one=True)
            if r and time.time() - t < 1200 and r["lib"] in libs(u):
                usr = users.USERS.get(who, {}).get("username", "admin")
                out.append({**song(r), "username": usr, "minutesAgo": int((time.time() - t) / 60), "playerId": 0})
        return {"nowPlaying": {"entry": out}}
    if endpoint in ("star", "unstar"):
        when = time.time() if endpoint == "star" else 0
        for k, kind in (("id", None), ("albumId", "album"), ("artistId", "artist")):
            for i in plist(params, k):
                touch_ann(u, i, kind or kind_of(i), starred=when)
        return {}
    if endpoint == "setRating":
        i = p1(params, "id")
        touch_ann(u, i, kind_of(i), rating=max(0, min(5, pint(params, "rating", 0))))
        return {}
    if endpoint == "scrobble":
        times = plist(params, "time")
        sub = p1(params, "submission", "true") != "false"
        for n, i in enumerate(plist(params, "id")):
            t = int(times[n]) / 1000 if n < len(times) and str(times[n]).isdigit() else None
            scrobble(u, i, t, sub)
        return {}
    if endpoint == "getPlaylists":
        return {"playlists": {"playlist": [playlist_out(u, r) for r in visible_playlists(u)]}}
    if endpoint == "getPlaylist":
        pid = p1(params, "id")
        r = next((x for x in visible_playlists(u) if x["id"] == pid), None)
        if not r:
            raise ApiError(70, "Playlist not found")
        return {"playlist": playlist_out(u, r, True)}
    if endpoint == "createPlaylist":
        ids = plist(params, "songId")
        pid = p1(params, "playlistId")
        if pid:
            r = my_playlist(u, pid)
            set_pl_songs(pid, ids)
            write_playlist_file(r, ids)
        else:
            pid = "p" + db.h("api", me, p1(params, "name"), time.time())
            db.x("INSERT INTO playlist (id, name, owner, public, comment, created, changed, path, mtime) VALUES (?,?,?,?,?,?,?,?,?)",
                 (pid, p1(params, "name") or "New playlist", me, 0, "", time.time(), time.time(), "", 0))
            set_pl_songs(pid, ids)
        r = db.q("SELECT * FROM playlist WHERE id=?", (pid,), one=True)
        return {"playlist": playlist_out(u, dict(r), True)}
    if endpoint == "updatePlaylist":
        pid = p1(params, "playlistId")
        r = my_playlist(u, pid)
        if p1(params, "name"):
            db.x("UPDATE playlist SET name=? WHERE id=?", (p1(params, "name"), pid))
        if "comment" in params:
            db.x("UPDATE playlist SET comment=? WHERE id=?", (p1(params, "comment"), pid))
        if "public" in params:
            db.x("UPDATE playlist SET public=? WHERE id=?", (int(p1(params, "public") == "true"), pid))
        cur = [x["song"] for x in db.q("SELECT song FROM plsong WHERE pid=? ORDER BY pos", (pid,))]
        rm = sorted({int(i) for i in plist(params, "songIndexToRemove") if str(i).isdigit()}, reverse=True)
        for i in rm:
            if i < len(cur):
                cur.pop(i)
        cur += plist(params, "songIdToAdd")
        if rm or plist(params, "songIdToAdd"):
            set_pl_songs(pid, cur)
            write_playlist_file(r, cur)
        return {}
    if endpoint == "deletePlaylist":
        pid = p1(params, "id")
        r = my_playlist(u, pid)
        db.x("DELETE FROM plsong WHERE pid=?", (pid,))
        db.x("DELETE FROM playlist WHERE id=?", (pid,))
        if r["path"] and os.path.isfile(r["path"]):
            os.remove(r["path"])
        return {}
    if endpoint == "getUser":
        name = p1(params, "username") or (u or {}).get("username")
        t = users.by_name(name) if name else u
        if not t or (t is not u and not users.is_admin(u) and t.get("id") != (u or {}).get("id")):
            t = u
        return {"user": user_out(t)}
    if endpoint == "getUsers":
        if not users.is_admin(u):
            raise ApiError(50, "Only the admin can see everyone")
        return {"users": {"user": [user_out(x) for x in users.USERS.values()]}}
    if endpoint in ("createUser", "updateUser", "deleteUser"):
        raise ApiError(50, "Add and manage people in Umculo: tap your initial at the top of the page")
    if endpoint == "changePassword":
        if p1(params, "username") and p1(params, "username") != (u or {}).get("username") and not users.is_admin(u):
            raise ApiError(50, "You can only change your own password")
        t = users.by_name(p1(params, "username")) or u
        pw = p1(params, "password")
        if pw.startswith("enc:"):
            pw = bytes.fromhex(pw[4:]).decode()
        users.set_password(t, pw)
        return {}
    if endpoint == "getPlayQueue":
        st = json.loads((db.q("SELECT v FROM kv WHERE k=?", ("queue:" + me,), one=True) or {"v": "{}"})["v"])
        if not st.get("ids"):
            return {}
        rows = {r["id"]: r for r in db.q(f"SELECT * FROM song WHERE id IN ({','.join('?' * len(st['ids']))})", st["ids"])}
        ordered = [rows[i] for i in st["ids"] if i in rows]
        return {"playQueue": {"entry": songs_out(u, ordered), "current": st.get("current"), "position": st.get("position", 0),
                              "username": (u or {}).get("username", "admin"), "changed": iso(st.get("changed")), "changedBy": st.get("by", "")}}
    if endpoint == "savePlayQueue":
        st = {"ids": plist(params, "id"), "current": p1(params, "current"), "position": pint(params, "position", 0),
              "changed": time.time(), "by": p1(params, "c")}
        db.x("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", ("queue:" + me, json.dumps(st)))
        return {}
    if endpoint == "getScanStatus":
        return {"scanStatus": {"scanning": db.SCAN["running"], "count": db.q("SELECT COUNT(*) n FROM song", one=True)["n"],
                               "lastScan": iso(db.SCAN["last"])}}
    if endpoint == "startScan":
        db.request_scan()
        if p1(params, "fullScan") == "true":
            threading.Thread(target=db.scan, args=(True,), daemon=True).start()
        return {"scanStatus": {"scanning": True, "count": db.SCAN["count"]}}
    if endpoint == "getLyrics":
        a, t = p1(params, "artist"), p1(params, "title")
        r = db.q(f"SELECT * FROM song WHERE {lib_sql(u)} AND lower(title)=lower(?) AND (?='' OR lower(artist) LIKE lower(?)) LIMIT 1",
                 (t, a, f"%{a}%"), one=True)
        text = lyrics_text(r["path"]) if r else ""
        text = re.sub(r"^\[[\d:.]+\]\s?", "", text, flags=re.M) if text else ""
        return {"lyrics": {"artist": a, "title": t, "value": text} if text else {}}
    if endpoint == "getLyricsBySongId":
        r = get_song_row(u, p1(params, "id"))
        text = lyrics_text(r["path"])
        return {"lyricsList": {"structuredLyrics": [structured(text, r)] if text else []}}
    if endpoint == "getBookmarks":
        marks = kv_get("bookmarks:" + me, {})
        out = []
        for sid, b in sorted(marks.items(), key=lambda kv: -kv[1]["changed"]):
            r = db.q(f"SELECT * FROM song WHERE id=? AND {lib_sql(u)}", (sid,), one=True)
            if r:
                out.append({"position": b["position"], "username": (u or {}).get("username", "admin"), "comment": b.get("comment", ""),
                            "created": iso(b["created"]), "changed": iso(b["changed"]), "entry": songs_out(u, [r])[0]})
        return {"bookmarks": {"bookmark": out}}
    if endpoint in ("createBookmark", "deleteBookmark"):
        sid = p1(params, "id")
        marks = kv_get("bookmarks:" + me, {})
        if endpoint == "deleteBookmark":
            marks.pop(sid, None)
        else:
            get_song_row(u, sid)
            old = marks.get(sid) or {"created": time.time()}
            marks[sid] = {**old, "position": pint(params, "position", 0), "comment": p1(params, "comment"), "changed": time.time()}
        kv_set("bookmarks:" + me, marks)
        return {}
    if endpoint == "getShares":
        return {"shares": {}}
    if endpoint == "getInternetRadioStations":
        st = kv_get("radio", [])
        return {"internetRadioStations": {"internetRadioStation": st}}
    if endpoint in ("createInternetRadioStation", "updateInternetRadioStation", "deleteInternetRadioStation"):
        if not users.is_admin(u):
            raise ApiError(50, "Only the admin can change radio stations")
        st = kv_get("radio", [])
        sid = p1(params, "id")
        if endpoint == "deleteInternetRadioStation":
            st = [x for x in st if x["id"] != sid]
        else:
            url = p1(params, "streamUrl")
            if not url.startswith(("http://", "https://")):
                raise ApiError(10, "streamUrl must be a web address")
            item = {"id": sid or "r" + db.h("radio", url, time.time())[:10], "name": p1(params, "name") or url,
                    "streamUrl": url, "homePageUrl": p1(params, "homepageUrl") or p1(params, "homePageUrl")}
            st = [x for x in st if x["id"] != item["id"]] + [item]
        kv_set("radio", st)
        return {}
    if endpoint == "getPodcasts":
        return {"podcasts": {}}
    if endpoint == "getNewestPodcasts":
        return {"newestPodcasts": {}}
    if endpoint == "getVideos":
        return {"videos": {}}
    if endpoint == "getChatMessages":
        return {"chatMessages": {}}
    if endpoint == "addChatMessage":
        return {}
    raise ApiError(0, f"{endpoint} isn't supported")


def kv_get(k, default):
    r = db.q("SELECT v FROM kv WHERE k=?", (k,), one=True)
    try:
        return json.loads(r["v"]) if r else default
    except ValueError:
        return default


def kv_set(k, v):
    db.x("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", (k, json.dumps(v)))


def user_out(t):
    users = core.users
    t = t or users.OPEN
    admin = users.is_admin(t)
    p = t.get("perms") or {}
    return {"username": t.get("username", "admin"), "email": "", "scrobblingEnabled": True, "adminRole": admin,
            "settingsRole": True, "downloadRole": True, "uploadRole": False, "playlistRole": admin or bool(p.get("playlists", True)),
            "coverArtRole": True, "commentRole": False, "podcastRole": False, "streamRole": True, "jukeboxRole": False,
            "shareRole": False, "videoConversionRole": False, "folder": db.libraries_for(t)}


DZ_ARTIST = {}


def dz_artist(name):
    k = core.norm(name)
    if k not in DZ_ARTIST:
        try:
            hit = (core.dz_get("/search/artist?limit=3&q=" + urllib.parse.quote(name)) or {}).get("data") or []
            ex = [a for a in hit if core.norm(a["name"]) == k]
            DZ_ARTIST[k] = (ex or [None])[0] and ex[0]["id"]
        except Exception:  # noqa: BLE001
            DZ_ARTIST[k] = None
    return DZ_ARTIST[k]


# --------------------------------------------------------------------------- audio and pictures
def zip_download(h, u, i):
    """Download an album, an artist or a playlist in one go (a zip, like Navidrome)."""
    import zipfile
    rows, name = [], "music"
    a = db.q(f"SELECT * FROM album WHERE id=? AND {lib_sql(u)}", (i,), one=True)
    if a:
        rows, name = db.q("SELECT * FROM song WHERE album_id=? ORDER BY disc, track", (i,)), f"{a['artist']} - {a['name']}"
    ar = None if a else db.q(f"SELECT * FROM artist WHERE id=? AND {lib_sql(u)}", (i,), one=True)
    if ar:
        rows, name = db.q(f"SELECT * FROM song WHERE albumartist_id=? AND {lib_sql(u)} ORDER BY album, disc, track", (i,)), ar["name"]
    if not rows:
        pl = next((x for x in visible_playlists(u) if x["id"] == i), None)
        if pl:
            rows, name = pl_songs(u, i), pl["name"]
    if not rows:
        return False
    safe = re.sub(r'[\\/:*?"<>|]+', "_", name)[:120] or "music"
    h.send_response(200)
    h.send_header("Content-Type", "application/zip")
    h.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{urllib.parse.quote(safe)}.zip")
    h.send_header("Connection", "close")
    h.end_headers()
    h.close_connection = True

    class Out:                                    # zipfile wants tell(); count what went out
        def __init__(self):
            self.n = 0

        def write(self, b):
            h.wfile.write(b)
            self.n += len(b)
            return len(b)

        def tell(self):
            return self.n

        def flush(self):
            pass
    try:
        with zipfile.ZipFile(Out(), "w", zipfile.ZIP_STORED) as z:
            used = set()
            for r in rows:
                arc = os.path.basename(r["path"]) if not ar else os.path.join(re.sub(r'[\\/:*?"<>|]+', "_", r["album"]), os.path.basename(r["path"]))
                while arc in used:
                    arc = "_" + arc
                used.add(arc)
                if os.path.exists(r["path"]):
                    z.write(r["path"], arc)
    except (BrokenPipeError, ConnectionResetError):
        pass
    return True


STREAMS = collections.deque(maxlen=300)        # the last streams: how long they took to start, how they were sent


def network():
    """Where this request comes from: home, tailscale or internet."""
    ip = getattr(core.users.LOCAL, "ip", "") or ""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return "home"
    if a in ipaddress.ip_network("100.64.0.0/10"):
        return "tailscale"
    return "home" if (a.is_private or a.is_loopback) else "internet"


def plan(r, fmt="", want=0, can=None, net="home"):
    """-> (convert, kbps, out_fmt). Send the file as it is whenever the player can play it and it isn't too big."""
    suf, br = r["suffix"], r["bitrate"] or 0
    lossless = suf in LOSSLESS
    cap = int(core.subsonic.CFG.get("internet_kbps") or 0) if net == "internet" else 0
    if cap:
        want = min(want or cap, cap)                 # over the internet: no 2-3 Mbps FLAC stalling a phone on mobile data
    if can is not None:                              # the web player told us what this browser plays
        playable = suf in can
    else:                                            # an app: it plays the file unless it asked for another format
        playable = fmt in ("", "raw") or fmt == suf
    fits = not want or (not lossless and br <= want + 8) or (fmt == "raw" and not cap)
    if playable and fits:
        return False, 0, ""
    kbps = min(want or 320, 320)
    if not lossless and br:
        kbps = min(kbps, max(128, br + 32))          # never blow a 128 kbps file up to 320
    return True, kbps, "opus" if fmt == "opus" else "mp3"


def note_stream(rec):
    if rec.get("range") and not rec["range"].startswith("bytes=0-"):
        return                                       # a seek or a browser's follow-up request: not a new play
    rec.update(at=time.time(), net=network())
    try:
        rec["load"] = round(os.getloadavg()[0] / (os.cpu_count() or 1), 2)
    except OSError:
        pass
    STREAMS.append(rec)


def stream_report():
    """The last streams, for the Status sheet: how quickly they started and how they were sent."""
    rs = list(STREAMS)[-80:][::-1]
    if not rs:
        return {"streams": [], "summary": {}}
    starts = sorted(r["start_ms"] for r in rs)
    med = starts[len(starts) // 2]
    slow = [r for r in rs if r["start_ms"] > 3000]
    live = [r for r in rs if r["mode"] == "live"]
    big = [r for r in rs if r["net"] == "internet" and r["mode"] == "file" and re.match(r"(FLAC|WAV|AIFF|ALAC)", r.get("sent", ""))]
    tips = []
    if slow:
        tips.append(f"{len(slow)} of the last {len(rs)} songs took over 3 s to start (mostly: {collections.Counter(r['mode'] for r in slow).most_common(1)[0][0]}).")
    if live:
        tips.append(f"{core.plural(len(live), 'song')} converted while playing (apps that ask for MP3 get this). Setting the app's quality to 'original' starts them faster.")
    if big:
        tips.append(f"{core.plural(len(big), 'lossless file')} went out over the internet as they are; 'Over the internet, at most' in Settings sends them smaller.")
    busy = [r for r in rs if (r.get("load") or 0) > 0.9]
    if busy:
        tips.append(f"The server's processor was busy during {core.plural(len(busy), 'song')}: something else on it is working hard.")
    return {"streams": rs, "summary": {"count": len(rs), "median_start_ms": med, "slow": len(slow),
                                       "by_mode": dict(collections.Counter(r["mode"] for r in rs)),
                                       "by_net": dict(collections.Counter(r["net"] for r in rs))}, "tips": tips}


def stream(h, u, params, download=False):
    t0 = time.time()
    if download and not db.q("SELECT 1 FROM song WHERE id=?", (p1(params, "id"),), one=True):
        if zip_download(h, u, p1(params, "id")):
            return
    r = get_song_row(u, p1(params, "id"))
    path = r["path"]
    if not os.path.exists(path):
        raise ApiError(70, "The file is gone; a rescan will tidy this up")
    fmt = (p1(params, "format") or "").lower()
    want = pint(params, "maxBitRate", 0)
    offset = pint(params, "timeOffset", 0)
    can = set(filter(None, (p1(params, "can") or "").lower().split(","))) or None
    rec = {"who": u.get("username", ""), "client": p1(params, "c") or "", "song": f"{r['artist']} - {r['title']}",
           "from": f"{r['suffix'].upper()} {r['bitrate'] or ''}".strip(), "range": h.headers.get("Range") or ""}
    if download:
        convert, kbps, out_fmt = False, 0, ""
    else:
        convert, kbps, out_fmt = plan(r, fmt, want, can, network())
    if not convert and not offset:
        rec.update(mode="file", sent=rec["from"], start_ms=int((time.time() - t0) * 1000))
        note_stream(rec)
        return core.subsonic.serve_file(h, path, CTYPE.get(r["suffix"]))
    kbps = kbps or 320
    out_fmt = out_fmt or "mp3"
    cache = os.path.join(core.CONFIG["staging"], "transcode")
    os.makedirs(cache, exist_ok=True)
    key = f"{r['id']}.{int(r['mtime'])}.{kbps}.{out_fmt}"
    final = os.path.join(cache, key)
    ctype = "audio/mpeg" if out_fmt == "mp3" else "audio/ogg"
    rec["sent"] = f"{out_fmt.upper()} {kbps}"
    if os.path.exists(final) and not offset:
        os.utime(final, None)
        rec.update(mode="ready", start_ms=int((time.time() - t0) * 1000))
        note_stream(rec)
        return core.subsonic.serve_file(h, final, ctype)
    args = ["ffmpeg", "-v", "error", "-nostdin"] + (["-ss", str(offset)] if offset else []) + \
        ["-i", path, "-map", "0:a:0", "-vn"] + \
        (["-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-f", "mp3"] if out_fmt == "mp3" else ["-c:a", "libopus", "-b:a", f"{kbps}k", "-f", "ogg"]) + ["-"]
    if p1(params, "prepare") and not offset and h.command != "HEAD":
        # the web player: convert it whole first, then send it with a length, so the browser can buffer and seek
        if prepare(r, kbps, out_fmt):
            rec.update(mode="prepared", start_ms=int((time.time() - t0) * 1000))
            note_stream(rec)
            return core.subsonic.serve_file(h, final, ctype)
    rec.update(mode="live", start_ms=int((time.time() - t0) * 1000))
    note_stream(rec)
    if h.command == "HEAD":
        h.send_response(200)
        h.send_header("Content-Type", "audio/mpeg" if out_fmt == "mp3" else "audio/ogg")
        h.end_headers()
        return
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    h.send_response(200)
    h.send_header("Content-Type", "audio/mpeg" if out_fmt == "mp3" else "audio/ogg")
    if p1(params, "estimateContentLength") == "true":
        h.send_header("Content-Length", str(int((r["duration"] - offset) * kbps * 125)))
    h.send_header("Connection", "close")
    h.end_headers()
    h.close_connection = True
    tmp = final + f".part{os.getpid()}{threading.get_ident()}"
    keep = open(tmp, "wb") if not offset else None
    ok = True
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            if keep:
                keep.write(chunk)
            try:
                h.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                ok = False
                break
    finally:
        if keep:
            keep.close()
        if not ok:
            proc.kill()
        rc = proc.wait()
        if keep:
            if ok and rc == 0:
                os.replace(tmp, final)            # next time: instant, and seekable
            else:
                os.remove(tmp)


def prune_cache(max_mb=3000, days=30):
    """Converted copies are kept for quick replays and seeking; old ones go."""
    d = os.path.join(core.CONFIG["staging"], "transcode")
    if not os.path.isdir(d):
        return
    files = []
    for f in os.listdir(d):
        p = os.path.join(d, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if ".part" in f and time.time() - st.st_mtime > 3600 or time.time() - st.st_atime > days * 86400 and time.time() - st.st_mtime > days * 86400:
            os.remove(p)
            continue
        files.append((st.st_mtime, st.st_size, p))
    total = sum(x[1] for x in files)
    for _m, size, p in sorted(files):
        if total <= max_mb * 1_000_000:
            break
        os.remove(p)
        total -= size


def cover_bytes(cid):
    """-> (bytes, content type) for a song, album, artist or playlist id."""
    r = db.q("SELECT * FROM album WHERE id=?", (cid,), one=True)
    if not r:
        s = db.q("SELECT album_id FROM song WHERE id=?", (cid,), one=True)
        if s:
            r = db.q("SELECT * FROM album WHERE id=?", (s["album_id"],), one=True)
    if not r:
        pl = db.q("SELECT song.album_id FROM plsong JOIN song ON song.id = plsong.song WHERE pid=? ORDER BY pos LIMIT 1", (cid,), one=True)
        if pl:
            r = db.q("SELECT * FROM album WHERE id=?", (pl["album_id"],), one=True)
    if not r:
        ar = db.q("SELECT * FROM artist WHERE id=?", (cid,), one=True)
        if ar:
            pic = artist_folder_picture(cid) or artist_picture(ar["name"])
            if pic:
                return pic
            r = db.q("SELECT * FROM album WHERE artist_id=? ORDER BY year DESC LIMIT 1", (cid,), one=True)
    if not r:
        return None
    folder = os.path.dirname(r["cover"] or "")
    for name in db.COVERS:
        for f in (os.listdir(folder) if os.path.isdir(folder) else []):
            if f.lower() == name:
                with open(os.path.join(folder, f), "rb") as fh:
                    return fh.read(), "image/png" if f.lower().endswith("png") else "image/jpeg"
    songs = db.q("SELECT path FROM song WHERE album_id=? ORDER BY disc, track LIMIT 3", (r["id"],))
    for s in songs:
        data = embedded(s["path"])
        if data:
            return data
    got = album_picture(r["artist"], r["name"])      # nothing in the files: the catalogue's cover
    if not got:                                      # a single filed without an album: the song's own cover
        s = db.q("SELECT artist, title FROM song WHERE album_id=? ORDER BY disc, track LIMIT 1", (r["id"],), one=True)
        if s:
            got = album_picture(s["artist"], s["title"], track=True)
    return got


def album_picture(artist_name, album_name, track=False):
    if not artist_name or artist_name == "Unknown artist":
        return None
    d = os.path.join(core.CONFIG["config"], "albumart")
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, db.h("albumpic", core.norm(artist_name), core.norm(album_name), "t" if track else "") + ".jpg")
    if os.path.exists(f):
        if os.path.getsize(f) == 0 and time.time() - os.path.getmtime(f) < 7 * 86400:
            return None
        if os.path.getsize(f):
            with open(f, "rb") as fh:
                return fh.read(), "image/jpeg"
    data = None
    try:
        kind = "track" if track else "album"
        hits = (core.dz_get(f"/search/{kind}?limit=5&q=" + urllib.parse.quote(f"{db.primary(artist_name)} {album_name}"), 86400) or {}).get("data") or []
        want_a, want_b = core.norm(db.primary(artist_name)), core.norm(album_name)
        for x in hits:
            if core.norm(x.get("artist", {}).get("name", "")) == want_a and (core.norm(x.get("title", "")) == want_b
                                                                            or want_b in core.norm(x.get("title", ""))
                                                                            or core.norm(x.get("title", "")) in want_b):
                al = x.get("album") or x if track else x
                url = al.get("cover_xl") or al.get("cover_big")
                if url:
                    data, _hdr = core.http("GET", url, raw=True, timeout=20, retries=1)
                break
    except Exception:  # noqa: BLE001
        data = None
    with open(f, "wb") as fh:
        fh.write(data or b"")
    return (data, "image/jpeg") if data else None


def embedded(path):
    try:
        import mutagen
        f = mutagen.File(path)
        if f is None:
            return None
        if getattr(f, "pictures", None):
            p = f.pictures[0]
            return p.data, p.mime or "image/jpeg"
        tags = f.tags or {}
        if hasattr(tags, "getall"):
            ap = tags.getall("APIC")
            if ap:
                return ap[0].data, ap[0].mime or "image/jpeg"
        if "covr" in tags:
            return bytes(tags["covr"][0]), "image/jpeg"
        if "metadata_block_picture" in tags or "METADATA_BLOCK_PICTURE" in tags:
            import base64
            from mutagen.flac import Picture
            v = tags.get("metadata_block_picture") or tags.get("METADATA_BLOCK_PICTURE")
            p = Picture(base64.b64decode(v[0]))
            return p.data, p.mime or "image/jpeg"
    except Exception:  # noqa: BLE001
        pass
    return None


def artist_folder_picture(aid):
    """artist.jpg (or folder.jpg) in the artist's own folder, like Navidrome reads."""
    s = db.q("SELECT path, lib FROM song WHERE albumartist_id=? LIMIT 1", (aid,), one=True)
    if not s:
        return None
    lib = db.q("SELECT path FROM library WHERE id=?", (s["lib"],), one=True)
    d = os.path.dirname(os.path.dirname(s["path"]))
    for folder in (d, os.path.dirname(s["path"])):
        if lib and not core.within(folder, lib["path"]) or (lib and os.path.normpath(folder) == os.path.normpath(lib["path"])):
            continue
        for f in (os.listdir(folder) if os.path.isdir(folder) else []):
            if f.lower() in ("artist.jpg", "artist.png", "artist.jpeg"):
                with open(os.path.join(folder, f), "rb") as fh:
                    return fh.read(), "image/png" if f.lower().endswith("png") else "image/jpeg"
    return None


def artist_picture(name):
    d = os.path.join(core.CONFIG["config"], "artists")
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, db.h("artistpic", core.norm(name)) + ".jpg")
    if os.path.exists(f):
        if os.path.getsize(f) == 0:
            return None
        with open(f, "rb") as fh:
            return fh.read(), "image/jpeg"
    data = None
    try:
        aid = dz_artist(name)
        if aid:
            a = core.dz_get(f"/artist/{aid}", 86400 * 7) or {}
            url = a.get("picture_xl") or a.get("picture_big")
            if url and "/artist//" not in url:
                data, _hdr = core.http("GET", url, raw=True, timeout=20, retries=1)
    except Exception:  # noqa: BLE001
        data = None
    with open(f, "wb") as fh:
        fh.write(data or b"")
    return (data, "image/jpeg") if data else None


def cover(h, params):
    cid = p1(params, "id")
    size = pint(params, "size", 0)
    cache = os.path.join(core.CONFIG["config"], "covers")
    os.makedirs(cache, exist_ok=True)
    f = os.path.join(cache, f"{db.h('cover', cid)}_{size}.jpg")
    data, ctype = None, "image/jpeg"
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < 86400:
        with open(f, "rb") as fh:
            data = fh.read()
    else:
        got = cover_bytes(cid)
        if got:
            data, ctype = got
            if size and size < 1200:
                try:
                    p = subprocess.run(["ffmpeg", "-v", "error", "-i", "-", "-vf", f"scale='min({size},iw)':-2", "-f", "mjpeg", "-"],
                                       input=data, capture_output=True, timeout=20)
                    if p.returncode == 0 and p.stdout:
                        data, ctype = p.stdout, "image/jpeg"
                except Exception:  # noqa: BLE001
                    pass
            with open(f, "wb") as fh:
                fh.write(data)
    if not data:
        raise ApiError(70, "No cover art")
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.send_header("Cache-Control", "public, max-age=86400")
    h.end_headers()
    h.wfile.write(data)


def handle(h, endpoint, params, fmt):
    render, error = core.subsonic.render, core.subsonic.error
    try:
        u = check_login(params)
    except ApiError as e:
        return error(h, fmt, e.code, str(e))
    try:
        if endpoint in ("stream", "download"):
            return stream(h, u, params, endpoint == "download")
        if endpoint == "getCoverArt":
            return cover(h, params)
        if endpoint == "getAvatar":
            raise ApiError(70, "No avatar")
        return render(h, call(u, endpoint, params), fmt)
    except ApiError as e:
        return error(h, fmt, e.code, str(e))
    except Exception as e:  # noqa: BLE001
        core.log("music server", endpoint, "failed:", e)
        return error(h, fmt, 0, str(e)[:200])


def as_user(u, endpoint, **params):
    """Umculo's own calls (Library tab, ownership checks...): same answers as the apps get."""
    try:
        res = call(u, endpoint, {k: (v if isinstance(v, list) else [str(v)]) for k, v in params.items()})
        return {"status": "ok", **res}
    except ApiError as e:
        return {"status": "failed", "error": {"code": e.code, "message": str(e)}}



# --------------------------------------------------------------------------- moving over from Navidrome
RAW_BASE = threading.local()


def nd_raw(name, pw, endpoint, **params):
    base = getattr(RAW_BASE, "url", "") or core.CONFIG["navidrome"]
    salt = "%08x" % random.getrandbits(32)
    q = {"u": name, "t": hashlib.md5((pw + salt).encode()).hexdigest(), "s": salt, "v": "1.16.1", "c": "umculo", "f": "json"}
    q.update(params)
    res = core.http("GET", f"{base}/rest/{endpoint}?" + urllib.parse.urlencode(q, doseq=True), timeout=60, retries=1)
    return (res or {}).get("subsonic-response") or {}


def import_navidrome(say=lambda m: None, base=None, accounts=None):
    """Stars, ratings, play counts and app-made playlists, for you and everyone with a Navidrome account.
    base + accounts: any Subsonic server (Navidrome, Gonic, Airsonic, Ampache, LMS...) and whose data to bring."""
    users = core.users
    RAW_BASE.url = (base or "").rstrip("/")
    if accounts is None:
        accounts = [(None, core.CONFIG["nd_user"], core.CONFIG["nd_password"])]
        for u in list(users.USERS.values()):
            nd = u.get("nd") or {}
            if u.get("role") != "admin" and nd.get("id") and nd.get("password"):
                accounts.append((u, nd["user"], users.unkeep(nd["password"])))
    by_rel, by_tag = {}, {}
    for r in db.q("SELECT id, lib, rel, title, artist, album FROM song"):
        by_rel.setdefault(r["rel"], []).append((r["lib"], r["id"]))
        by_tag.setdefault((db.fold(r["title"]), db.fold(r["artist"]), db.fold(r["album"])), []).append((r["lib"], r["id"]))
    albums = {(db.fold(r["name"]), db.fold(r["artist"])): r["id"] for r in db.q("SELECT id, name, artist FROM album")}
    artists_ = {db.fold(r["name"]): r["id"] for r in db.q("SELECT id, name FROM artist")}
    tot = {"songs": 0, "stars": 0, "playlists": 0, "missed": 0, "people": 0}

    def find(s, mine):
        for key, table in ((s.get("path") or "", by_rel), ((db.fold(s.get("title")), db.fold(s.get("artist")), db.fold(s.get("album"))), by_tag)):
            hits = table.get(key) or []
            if not hits and isinstance(key, str) and key:
                for pre in (core.CONFIG["nd_music_path"].strip("/") + "/", ):
                    hits = table.get(key[len(pre):] if key.startswith(pre) else key.split("/", 1)[-1]) or []
            pick = [i for lib, i in hits if lib in mine] or [i for _l, i in hits]
            if pick:
                return pick[0]
        return None

    for u, name, pw in accounts:
        if not name or not pw:
            continue
        who = u or users.admin_user() or users.OPEN
        mine = set(libs(who))
        say(f"Reading {name}'s plays and stars")
        if nd_raw(name, pw, "ping").get("status") != "ok":
            core.log("import: couldn't sign in as", name)
            tot.setdefault("failed", []).append(name)
            continue
        tot["people"] += 1
        off, rows = 0, []
        while True:
            r = nd_raw(name, pw, "search3", query="", songCount=500, songOffset=off, artistCount=0, albumCount=0)
            batch = (r.get("searchResult3") or {}).get("song") or []
            rows += batch
            if len(batch) < 500 or off > 300000:
                break
            off += 500
        for s in rows:
            if not (s.get("playCount") or s.get("starred") or s.get("userRating")):
                continue
            sid = find(s, mine)
            if not sid:
                tot["missed"] += 1
                continue
            kw = {}
            if s.get("starred"):
                kw["starred"] = parse_time(s["starred"])
                tot["stars"] += 1
            if s.get("userRating"):
                kw["rating"] = int(s["userRating"])
            if s.get("playCount"):
                kw["plays"] = 0
                set_plays(who, sid, int(s["playCount"]), parse_time(s.get("played")) or time.time())
            touch_ann(who, sid, "song", **{k: v for k, v in kw.items() if k != "plays"})
            tot["songs"] += 1
        st = nd_raw(name, pw, "getStarred2").get("starred2") or {}
        for a in st.get("album") or []:
            i = albums.get((db.fold(a.get("name")), db.fold(a.get("artist"))))
            if i:
                touch_ann(who, i, "album", starred=parse_time(a.get("starred")))
                tot["stars"] += 1
        for a in st.get("artist") or []:
            i = artists_.get(db.fold(a.get("name")))
            if i:
                touch_ann(who, i, "artist", starred=parse_time(a.get("starred")))
                tot["stars"] += 1
        roll_up_plays(who)
        say(f"Bringing over {name}'s playlists")
        have = {(r["owner"], r["name"].lower()) for r in db.q("SELECT owner, name FROM playlist")}
        for p in (nd_raw(name, pw, "getPlaylists").get("playlists") or {}).get("playlist") or []:
            if p.get("owner") != name or (uid(who), (p.get("name") or "").lower()) in have:
                continue
            full = nd_raw(name, pw, "getPlaylist", id=p["id"]).get("playlist") or {}
            ids = [i for i in (find(e, mine) for e in full.get("entry") or []) if i]
            if not ids:
                continue
            pid = "p" + db.h("nd", uid(who), p["id"])
            db.x("INSERT OR REPLACE INTO playlist (id, name, owner, public, comment, created, changed, path, mtime) VALUES (?,?,?,?,?,?,?,?,?)",
                 (pid, p["name"], uid(who), int(bool(p.get("public"))), p.get("comment") or "", parse_time(p.get("created")) or time.time(),
                  time.time(), "", 0))
            set_pl_songs(pid, ids)
            tot["playlists"] += 1
    pl = lambda n, w: f"{n:,} {w}" + ("" if n == 1 else "s")  # noqa: E731
    tot["summary"] = (f"{pl(tot['songs'], 'song')} with plays or stars, {pl(tot['stars'], 'star')} and {pl(tot['playlists'], 'playlist')} "
                      f"brought over for {pl(tot['people'], 'account')}" + (f"; {pl(tot['missed'], 'song')} not in your folders any more" if tot["missed"] else ""))
    return tot


def parse_time(v):
    if not v:
        return 0
    try:
        return datetime.datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return time.time()


def set_plays(u, sid, n, when):
    me = uid(u)
    with db.LOCK:
        r = db.DB.execute("SELECT plays, played FROM ann WHERE user=? AND item=?", (me, sid)).fetchone()
        if r:
            db.DB.execute("UPDATE ann SET plays=MAX(plays, ?), played=MAX(played, ?) WHERE user=? AND item=?", (n, when, me, sid))
        else:
            db.DB.execute("INSERT INTO ann (user, item, kind, starred, rating, plays, played) VALUES (?,?,?,?,?,?,?)",
                          (me, sid, "song", 0, 0, n, when))
        db.DB.commit()


def roll_up_plays(u):
    """Album and artist play counts from the songs' (what Navidrome shows for them too)."""
    me = uid(u)
    for col, kind in (("album_id", "album"), ("albumartist_id", "artist")):
        rows = db.q(f"""SELECT song.{col} item, SUM(ann.plays) n, MAX(ann.played) t FROM ann JOIN song ON song.id = ann.item
                        WHERE ann.user=? AND ann.kind='song' AND ann.plays > 0 GROUP BY song.{col}""", (me,))
        for r in rows:
            set_plays(u, r["item"], r["n"], r["t"])
            db.x("UPDATE ann SET kind=? WHERE user=? AND item=?", (kind, me, r["item"]))

def init(umculo):
    global core, db
    core = umculo
    db = core.musicdb
