"""Listening: plays from the web player, synced lyrics, instant mixes and your listening summary ("Wrapped").

POST /api/listen          a song was played (library or internet)
GET  /api/lyrics/<kind>/<id>   synced lyrics: the file's own, else LRCLIB (and kept for next time)
GET  /api/mix/<kind>/<id>      an instant mix from any song
GET  /api/wrapped?period=week|month|year|all&back=0
POST /api/pl/fav          favourite playlists
"""
import collections
import datetime
import json
import re
import time

core = None
LYR = {}                 # (artist, title) -> (at, text) for internet songs


def me():
    return core.users.current()


def uid():
    return core.server.uid(me())


# --------------------------------------------------------------------------- plays
def listen(b):
    kind, sid = b.get("kind"), str(b.get("id") or "")
    played = float(b.get("played") or 0)
    if played < 30 and played < float(b.get("duration") or 0) * 0.5:
        return {"counted": False}                     # skips aren't plays
    if kind == "lib":
        if core.builtin():
            core.server.scrobble(me(), sid, None, True)
        else:
            core.users.nd_as(me(), "scrobble", id=sid, submission="true")
        return {"counted": True}
    meta = {"id": "umc-dz-" + sid, "title": b.get("title") or "", "artist": b.get("artist") or "", "album": b.get("album") or "",
            "duration": int(b.get("duration") or 0)}
    if core.builtin() and meta["title"]:
        core.server.log_listen(me(), meta, time.time(), source="internet")
    return {"counted": True}


# --------------------------------------------------------------------------- lyrics
def lyrics(kind, sid, qs):
    text, title, artist = "", (qs.get("title") or [""])[0], (qs.get("artist") or [""])[0]
    if kind == "lib" and core.builtin():
        r = core.musicdb.q("SELECT * FROM song WHERE id=?", (sid,), one=True)
        if r:
            title, artist = r["title"], r["artist"]
            text = core.server.lyrics_text(r["path"])
            if not text:
                text = core.library.fetch_lyrics(core.musicdb.primary(r["artist"]), r["title"], r["album"], r["duration"]) or ""
                if text:
                    try:                                  # keep them in the file for every app
                        core.library.write_lyrics(r["path"], text)
                    except Exception:  # noqa: BLE001
                        pass
    elif title:
        k = (core.norm(artist), core.norm(title))
        hit = LYR.get(k)
        if hit and time.time() - hit[0] < 86400:
            text = hit[1]
        else:
            text = core.library.fetch_lyrics(re.split(r",| feat| & ", artist, flags=re.I)[0].strip(), title,
                                             duration=float((qs.get("duration") or [0])[0] or 0)) or ""
            LYR[k] = (time.time(), text)
    if not text:
        return {"lines": [], "synced": False}
    s = core.server.structured(text, {"artist": artist, "title": title})
    return {"lines": s["line"], "synced": s["synced"]}


# --------------------------------------------------------------------------- instant mix
def mix(kind, sid):
    if kind == "lib":
        if not core.builtin():
            r = core.users.nd_as(me(), "getSimilarSongs2", id=sid, count=50) or {}
            return {"songs": [core.libview.song_view(s) for s in (r.get("similarSongs2") or {}).get("song") or []]}
        rows = core.server.instant_mix(me(), sid, 60)
        a = core.server.anns(me(), [r["id"] for r in rows])
        return {"songs": [core.libview.song_view(core.server.song(r, a.get(r["id"]))) for r in rows]}
    return {"songs": core.deezer_related(sid), "internet": True}


# --------------------------------------------------------------------------- wrapped
def period_bounds(period, back):
    now = datetime.datetime.now()
    if period == "week":
        start = (now - datetime.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        start -= datetime.timedelta(weeks=back)
        end = start + datetime.timedelta(weeks=1)
        label = "This week" if not back else "Last week" if back == 1 else start.strftime("Week of %-d %b")
    elif period == "month":
        y, m = now.year, now.month - back
        while m <= 0:
            m, y = m + 12, y - 1
        start = datetime.datetime(y, m, 1)
        end = datetime.datetime(y + (m == 12), m % 12 + 1, 1)
        label = start.strftime("%B %Y")
    elif period == "year":
        start = datetime.datetime(now.year - back, 1, 1)
        end = datetime.datetime(now.year - back + 1, 1, 1)
        label = str(now.year - back)
    else:
        start, end, label = datetime.datetime(1970, 1, 2), now + datetime.timedelta(days=1), "All time"
    return start.timestamp(), end.timestamp(), label


def mins(secs):
    return -(-int(secs or 0) // 60)            # a few seconds of music is still a minute, not zero


def wrapped(period="month", back=0):
    db = core.musicdb
    t0, t1, label = period_bounds(period, back)
    rows = [dict(r) for r in db.q("SELECT * FROM listen WHERE user=? AND at >= ? AND at < ? ORDER BY at", (uid(), t0, t1))]
    out = {"period": period, "back": back, "label": label, "plays": len(rows), "minutes": mins(sum(r["secs"] or 0 for r in rows))}
    if not rows:
        out["empty"] = True
        tot = db.q("SELECT SUM(plays) n FROM ann WHERE user=? AND kind='song'", (uid(),), one=True)
        out["all_time_plays"] = (tot["n"] if tot else 0) or 0
        return out
    by = lambda key: collections.Counter(key(r) for r in rows if key(r))  # noqa: E731
    songs = by(lambda r: (r["song"], r["title"], r["artist"]))
    artists = by(lambda r: core.musicdb.primary(r["artist"]))
    albums = by(lambda r: (r["album_id"] or r["album"], r["album"], core.musicdb.primary(r["artist"])) if r["album"] else None)
    genres = by(lambda r: (r["genre"] or "").split(";")[0].strip())
    days = collections.Counter(datetime.datetime.fromtimestamp(r["at"]).strftime("%Y-%m-%d") for r in rows)
    hours = collections.Counter(datetime.datetime.fromtimestamp(r["at"]).hour for r in rows)
    weekdays = collections.Counter(datetime.datetime.fromtimestamp(r["at"]).strftime("%A") for r in rows)
    # longest run of days in a row
    ds = sorted(datetime.date.fromisoformat(d) for d in days)
    best = run = 1
    for a, b in zip(ds, ds[1:]):
        run = run + 1 if (b - a).days == 1 else 1
        best = max(best, run)
    earlier = {core.musicdb.primary(r["artist"]).lower() for r in db.q("SELECT artist FROM listen WHERE user=? AND at < ?", (uid(), t0))}
    new_artists = [a for a in artists if a.lower() not in earlier]
    first = {}
    for r in rows:
        first.setdefault(core.musicdb.primary(r["artist"]), r["at"])
    covers = {}
    for (sid, _t, _a), _n in songs.most_common(10):
        s = db.q("SELECT album_id FROM song WHERE id=?", (sid,), one=True)
        covers[sid] = s["album_id"] if s else ""
    art_ids = {}
    for name, _n in artists.most_common(10):
        a = db.q("SELECT id FROM artist WHERE lower(name)=lower(?)", (name,), one=True)
        art_ids[name] = a["id"] if a else ""
    out.update({
        "top_songs": [{"id": k[0], "title": k[1], "artist": k[2], "plays": n, "cover": covers.get(k[0], "")} for k, n in songs.most_common(10)],
        "top_artists": [{"name": k, "plays": n, "id": art_ids.get(k, ""),
                         "minutes": mins(sum(r["secs"] or 0 for r in rows if core.musicdb.primary(r["artist"]) == k))}
                        for k, n in artists.most_common(10)],
        "top_albums": [{"id": k[0], "name": k[1], "artist": k[2], "plays": n} for k, n in albums.most_common(6)],
        "top_genres": [{"name": k, "plays": n} for k, n in genres.most_common(5)],
        "songs": len(songs), "artists": len(artists), "new_artists": len(new_artists), "new_artist_names": new_artists[:8],
        "days": len(days), "streak": best, "busiest_day": max(days.items(), key=lambda kv: kv[1]) if days else None,
        "hour": max(hours.items(), key=lambda kv: kv[1])[0], "weekday": max(weekdays.items(), key=lambda kv: kv[1])[0],
        "internet": sum(1 for r in rows if r["source"] == "internet"),
        "hours": [hours.get(h, 0) for h in range(24)],
    })
    return out


# --------------------------------------------------------------------------- favourite playlists
def favs():
    r = core.musicdb.q("SELECT v FROM kv WHERE k=?", ("plfav:" + uid(),), one=True) if core.musicdb.DB is not None else None
    return set(json.loads(r["v"])) if r else set()


def set_fav(pid, on):
    f = favs()
    (f.add if on else f.discard)(pid)
    core.musicdb.x("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", ("plfav:" + uid(), json.dumps(sorted(f))))
    return {"favs": sorted(f)}


# --------------------------------------------------------------------------- web
def handle_get(p, qs):
    m = re.match(r"^/api/lyrics/(lib|dz)/([^/]+)$", p)
    if m:
        return lyrics(m.group(1), m.group(2), qs)
    m = re.match(r"^/api/mix/(lib|dz)/([^/]+)$", p)
    if m:
        return mix(m.group(1), m.group(2))
    if p == "/api/wrapped":
        return wrapped((qs.get("period") or ["month"])[0], int((qs.get("back") or ["0"])[0] or 0))
    if p == "/api/pl/favs":
        return {"favs": sorted(favs())}
    return None


def handle_post(p, b):
    if p == "/api/listen":
        return listen(b)
    if p == "/api/pl/fav":
        return set_fav(str(b.get("id") or ""), bool(b.get("on")))
    return None


def init(umculo):
    global core
    core = umculo
