"""For You: recommendations from what you actually play, and a weekly mix.

Taste comes from (strongest first): your plays and stars in Navidrome, your 👍/👎 in Umculo,
the artists in your playlists, and (optionally) your ListenBrainz account.
Shelves:
  * Heard in your DJs' mixes  - songs the song finder found in mixes you subscribe to
  * Because you play <artist>  - related artists' best songs
  * New from artists you play  - releases from the last 90 days
  * ListenBrainz picks         - your Weekly Exploration playlist (if a ListenBrainz user is set)
  * Rediscover                 - songs you starred but haven't played in months
The weekly mix (Mondays) takes the best of those. In "download" mode the songs are fetched
in your quality profile into a "Weekly Mix" playlist; in "stream" mode they stay on the
internet and play through Umculo in your app (Amperfy) and on this page.
"""
import datetime
import json
import os
import random
import threading
import time
import traceback
import urllib.parse

core = None
E = os.environ.get
CFG = {
    "weekly_mode": E("WEEKLY_MODE", "download"),      # download | stream
    "weekly_size": int(E("WEEKLY_SIZE", "40")),
    "lb_user": E("LISTENBRAINZ_USER", ""),
    "lb_api": E("LISTENBRAINZ_API", "https://api.listenbrainz.org").rstrip("/"),
    "weekly_day": int(E("WEEKLY_DAY", "0")),          # 0 = Monday
}
STATE = {"likes": {}, "dislikes": {}, "artist_ids": {}, "history": {}, "shelves": [], "built": 0,
         "weekly": None, "artists": {}}
LOCK = threading.RLock()            # S() is used inside "with LOCK" blocks
PEOPLE = {}                       # person id -> their own For You (yours is STATE)
BUILDS = {}                       # person -> lock while their shelves are being built
CUR = threading.local()           # whose shelves this thread is building


def blank():
    return {"likes": {}, "dislikes": {}, "history": {}, "shelves": [], "built": 0, "weekly": None, "artists": {}}


def who():
    return getattr(CUR, "uid", None) or core.users.me_key()


def S(uid=None):
    """This person's suggestions, likes and history."""
    uid = uid or who()
    if uid == "admin":
        return STATE
    with LOCK:
        return PEOPLE.setdefault(uid, blank())


def building(uid=None):
    return BUILDS.setdefault(uid or who(), threading.Lock())


def ask_server(endpoint, **params):
    """The music server, as the person whose shelves these are."""
    uid = who()
    if uid == "admin":
        return core.nd_call(endpoint, **params)
    return core.server.as_user(core.users.person(uid), endpoint, **params)


def log(*a):
    core.log(*a)


def save():
    with LOCK:
        data = dict(STATE)
        data["people"] = json.loads(json.dumps(PEOPLE))
    core._save_json("foryou.json", data)


def dz(path):
    return core.dz_get(path)


def key(artist, title):
    return core.song_key(artist, title)


# --------------------------------------------------------------------------- taste
def taste():
    """artist name -> weight"""
    w = {}

    def add(name, n):
        for a in core.split_artists(name or "")[:2]:
            a = a.strip()
            if a and a.lower() not in ("various artists", "unknown artist"):
                w[a] = w.get(a, 0) + n
    try:
        for al in ((ask_server("getAlbumList2", type="frequent", size=60) or {}).get("albumList2") or {}).get("album") or []:
            add(al.get("artist"), min(int(al.get("playCount") or 3), 30))
        for al in ((ask_server("getAlbumList2", type="recent", size=40) or {}).get("albumList2") or {}).get("album") or []:
            add(al.get("artist"), 3)
        st = (ask_server("getStarred2") or {}).get("starred2") or {}
        for s in st.get("song") or []:
            add(s.get("artist"), 4)
        for a in st.get("artist") or []:
            add(a.get("name"), 8)
    except Exception as e:  # noqa: BLE001
        log("for you: navidrome taste failed:", e)
    try:                                                   # your playlists, from file names (fast)
        for p in (core.local_playlists() if who() == "admin" else []):
            for e in core.m3u_entries(p["path"])[:400]:
                stem = os.path.splitext(os.path.basename(e))[0]
                if " - " in stem:
                    add(stem.split(" - ", 1)[0], 1)
    except Exception as e:  # noqa: BLE001
        log("for you: playlist taste failed:", e)
    for did, n in ((core.subsonic.PLAYS or {}) if who() == "admin" else {}).items():      # internet songs you played
        m = core.subsonic.META.get(did)
        if m:
            add(m["artist"], 2 * n)
    with LOCK:
        for it in S()["likes"].values():
            add(it.get("artist"), 6)
        for it in S()["dislikes"].values():
            add(it.get("artist"), -4)
    return w


def artist_id(name):
    with LOCK:
        if name in STATE["artist_ids"]:
            return STATE["artist_ids"][name]
    aid = None
    for a in dz("/search/artist?limit=5&q=" + urllib.parse.quote(name)).get("data") or []:
        if core.norm(a.get("name", "")).replace(" ", "") == core.norm(name).replace(" ", ""):
            aid = a["id"]
            break
    with LOCK:
        STATE["artist_ids"][name] = aid
    return aid


def item_from_dz(d, why, album=None):
    it = core.subsonic.norm_track(d, album)
    it.update(why=why, key=key(it["artist"], it["title"]), preview=d.get("preview") or "")
    return it


def lookup(artist, title, why):
    """A song name (from a tracklist or ListenBrainz) -> catalogue item, if the catalogue has it."""
    tgt = core.make_target(artist, title)
    try:
        for d in core.deezer_search(f"{artist} {title}", limit=5):
            if core.match(f"{d['artist']} {d['title']} {d.get('version', '')}", tgt)[0]:
                it = dict(d)
                core.subsonic.remember([it])
                it.update(why=why, key=key(d["artist"], d["title"]))
                return it
    except Exception:  # noqa: BLE001
        pass
    return {"deezer_id": None, "artist": artist, "title": title, "why": why, "key": key(artist, title),
            "cover": "", "preview": "", "duration": 0}


# --------------------------------------------------------------------------- shelves
def shelf_djs():
    out, seen = [], set()
    cutoff = time.time() - 21 * 86400
    finds = sorted((f for f in core.links.FINDS.values() if (f.get("owner") or "admin") == who()),
                   key=lambda f: f["created"], reverse=True)
    for f in finds:
        if f.get("state") != "done" or f["created"] < cutoff:
            continue
        for t in f.get("tracks") or []:
            k = key(t["artist"], t["title"])
            if t.get("owned") or k in seen:
                continue
            seen.add(k)
            out.append(lookup(t["artist"], t["title"], f"Heard in {f.get('show') or f['title']}"))
            if len(out) >= 30:
                return out
    return out


def shelf_because(top):
    shelves = []
    for name in top[:3]:
        aid = artist_id(name)
        if not aid:
            continue
        items = []
        for rel in (dz(f"/artist/{aid}/related?limit=8").get("data") or [])[:6]:
            for d in (dz(f"/artist/{rel['id']}/top?limit=3").get("data") or [])[:2]:
                items.append(item_from_dz(d, f"Because you play {name}"))
        if items:
            shelves.append({"id": f"because-{aid}", "title": f"Because you play {name}", "items": items})
    return shelves


def shelf_new(top):
    out = []
    since = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    for name in top[:12]:
        aid = artist_id(name)
        if not aid:
            continue
        albums = dz(f"/artist/{aid}/albums?limit=10").get("data") or []
        for al in albums:
            if (al.get("release_date") or "") < since:
                continue
            full = dz(f"/album/{al['id']}")
            for d in ((full.get("tracks") or {}).get("data") or [])[:2]:
                out.append(item_from_dz(d, f"New from {name} · {al.get('release_date')}", full))
    out.sort(key=lambda i: i.get("why", ""), reverse=True)
    return out[:30]


def shelf_listenbrainz():
    user = CFG["lb_user"] if who() == "admin" else ""        # your ListenBrainz account is yours
    if not user:
        return []
    try:
        res = core.http("GET", f"{CFG['lb_api']}/1/user/{urllib.parse.quote(user)}/playlists/createdfor?count=10",
                        timeout=30) or {}
        pls = [p.get("playlist") or {} for p in res.get("playlists") or []]
        pick = next((p for p in pls if "exploration" in (p.get("title") or "").lower()), None) or (pls[0] if pls else None)
        if not pick:
            return []
        pid = (pick.get("identifier") or "").rstrip("/").rsplit("/", 1)[-1]
        full = (core.http("GET", f"{CFG['lb_api']}/1/playlist/{pid}", timeout=30) or {}).get("playlist") or {}
        out = []
        for t in (full.get("track") or [])[:30]:
            out.append(lookup(t.get("creator") or "", t.get("title") or "", pick.get("title") or "ListenBrainz"))
        return out
    except Exception as e:  # noqa: BLE001
        log("for you: listenbrainz failed:", e)
        return []


def shelf_rediscover():
    out = []
    try:
        st = (ask_server("getStarred2") or {}).get("starred2") or {}
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=90)).isoformat()
        for s in st.get("song") or []:
            if (s.get("played") or "") < cutoff:
                out.append({"nd_id": s["id"], "artist": s.get("artist", ""), "title": s.get("title", ""),
                            "cover": "", "duration": s.get("duration") or 0, "owned": True,
                            "why": "Starred, not played in a while", "key": key(s.get("artist", ""), s.get("title", ""))})
    except Exception as e:  # noqa: BLE001
        log("for you: rediscover failed:", e)
    random.shuffle(out)
    return out[:20]


def clean(items, limit=24):
    """Drop what you own, disliked, or already saw here recently; at most 2 songs per artist."""
    with LOCK:
        banned = set(S()["dislikes"])
    out, per_artist, keys = [], {}, set()
    for it in items:
        k = it["key"]
        a = core.norm((core.split_artists(it["artist"]) or [it["artist"]])[0])
        if k in banned or k in keys or per_artist.get(a, 0) >= 2:
            continue
        if not it.get("owned") and core.links.owned(it["artist"], it["title"]):
            continue
        keys.add(k)
        per_artist[a] = per_artist.get(a, 0) + 1
        out.append(it)
        if len(out) >= limit:
            break
    return out


def build(uid=None):
    """Rebuild every shelf (a few minutes: lots of small catalogue lookups), for one person."""
    uid = uid or who()
    lock = building(uid)
    if not lock.acquire(blocking=False):
        return
    CUR.uid = uid
    try:
        w = taste()
        top = [a for a, n in sorted(w.items(), key=lambda x: -x[1]) if n > 0]
        with LOCK:
            S()["artists"] = dict(sorted(w.items(), key=lambda x: -x[1])[:30])
        shelves = []
        djs = clean(shelf_djs(), 30)
        if djs:
            shelves.append({"id": "djs", "title": "Heard in your DJs' mixes", "items": djs})
        lb = clean(shelf_listenbrainz())
        if lb:
            shelves.append({"id": "lb", "title": "ListenBrainz picks", "items": lb})
        new = clean(shelf_new(top))
        if new:
            shelves.append({"id": "new", "title": "New from artists you play", "items": new})
        for sh in shelf_because(top):
            sh["items"] = clean(sh["items"], 16)
            if sh["items"]:
                shelves.append(sh)
        red = shelf_rediscover()
        if red:
            shelves.append({"id": "rediscover", "title": "Rediscover", "items": red})
        with LOCK:
            S()["shelves"], S()["built"] = shelves, time.time()
        save()
        log(f"for you: built {len(shelves)} shelves from {len(top)} artists" + ("" if uid == "admin" else f" for {(core.users.person(uid) or {}).get('name', uid)}"))
    except Exception:  # noqa: BLE001
        log("for you: build failed:", traceback.format_exc())
    finally:
        CUR.uid = None
        lock.release()


# --------------------------------------------------------------------------- weekly mix
def build_weekly():
    build("admin")
    with LOCK:
        shelves = list(STATE["shelves"])
        recent = {k for k, t in STATE["history"].items() if time.time() - t < 28 * 86400}
    size = CFG["weekly_size"]
    pools = {s["id"]: [i for i in s["items"] if not i.get("owned") and i["key"] not in recent] for s in shelves}
    if sum(len(v) for v in pools.values()) < min(10, size):        # made one already this week: allow repeats
        pools = {s["id"]: [i for i in s["items"] if not i.get("owned")] for s in shelves}
    plan = [("djs", 0.3), ("lb", 0.2), ("new", 0.15)]
    picked = []
    for sid, share in plan:
        picked += pools.get(sid, [])[:int(size * share)]
    because = [i for sid, items in pools.items() if sid.startswith("because") for i in items]
    random.shuffle(because)
    picked += because
    for sid in ("djs", "lb", "new"):
        picked += pools.get(sid, [])
    songs = clean(picked, size)
    if CFG["weekly_mode"] == "stream":
        songs = [s for s in songs if s.get("deezer_id")]
    random.shuffle(songs)
    week = {"created": time.time(), "mode": CFG["weekly_mode"], "songs": songs, "job": None,
            "name": "Weekly Mix", "week": datetime.date.today().isocalendar()[1]}
    if CFG["weekly_mode"] == "download" and songs:
        tracks = [{"artist": s["artist"], "title": s["title"], "duration": s.get("duration") or 0} for s in songs]
        week["job"] = core.new_job("Weekly Mix", "import", os.path.join(core.CONFIG["playlists"], "Weekly Mix.m3u8"), tracks)
    with LOCK:
        STATE["weekly"] = week
        for s in songs:
            STATE["history"][s["key"]] = time.time()
        STATE["history"] = {k: t for k, t in STATE["history"].items() if time.time() - t < 90 * 86400}
    save()
    core.notify(f"Your Weekly Mix is ready: {core.plural(len(songs), 'song')}")
    return week


def weekly_playlist():
    """For Subsonic apps: the stream-mode weekly mix as a playlist."""
    with LOCK:
        w = STATE["weekly"]
    if not w or w.get("mode") != "stream":
        return None
    return {"owner": "admin", "name": "Umculo Weekly Mix", "comment": "Streams from the internet. Star a song to keep it.",
            "songs": [s for s in w["songs"] if s.get("deezer_id")], "created": None,
            "cover": (w["songs"][0].get("cover_big") or w["songs"][0].get("cover")) if w["songs"] else None}


def loop():
    time.sleep(120)
    while True:
        try:
            with LOCK:
                built, w = STATE["built"], STATE["weekly"]
            today = datetime.date.today()
            this_week = today.isocalendar()[1]
            if (not w or (w.get("week") != this_week and today.weekday() >= CFG["weekly_day"])):
                build_weekly()
            elif time.time() - built > 24 * 3600:
                build("admin")
            for p in list(core.users.USERS.values()):         # everyone else who's been around lately
                if p.get("role") == "admin" or time.time() - (p.get("last_seen") or 0) > 14 * 86400:
                    continue
                if time.time() - S(p["id"])["built"] > 24 * 3600:
                    build(p["id"])
        except Exception:  # noqa: BLE001
            log("for you loop:", traceback.format_exc())
        time.sleep(3600)


# --------------------------------------------------------------------------- feedback
def find_item(k):
    with LOCK:
        for sh in S()["shelves"]:
            for it in sh["items"]:
                if it["key"] == k:
                    return it
        for it in (S()["weekly"] or {}).get("songs") or []:
            if it["key"] == k:
                return it
    return None


def get_item(it, why="you asked for it"):
    m = {"artist": it["artist"], "title": it["title"], "version": it.get("version", ""), "duration": it.get("duration") or 0,
         "album": it.get("album", ""), "cover": it.get("cover", ""), "cover_big": it.get("cover_big", ""),
         "deezer_id": it.get("deezer_id")}
    return core.users.fetch_for(core.users.current(), m, why)


def feedback(k, like):
    it = find_item(k)
    if not it:
        raise LookupError("That suggestion has gone; refresh the page")
    with LOCK:
        if like:
            S()["likes"][k] = {"artist": it["artist"], "title": it["title"], "at": time.time()}
            S()["dislikes"].pop(k, None)
        else:
            S()["dislikes"][k] = {"artist": it["artist"], "title": it["title"], "at": time.time()}
            S()["likes"].pop(k, None)
            for sh in S()["shelves"]:
                sh["items"] = [i for i in sh["items"] if i["key"] != k]
            if S()["weekly"]:
                S()["weekly"]["songs"] = [i for i in S()["weekly"]["songs"] if i["key"] != k]
    save()
    if like:
        get_item(it, "you gave it a thumbs up")


# --------------------------------------------------------------------------- web
def view():
    with LOCK:
        likes, dislikes = set(S()["likes"]), set(S()["dislikes"])
        shelves = json.loads(json.dumps(S()["shelves"]))
        weekly = json.loads(json.dumps(S()["weekly"])) if S()["weekly"] else None
        artists = list(S()["artists"])[:12]
    for sh in shelves + ([{"items": weekly["songs"]}] if weekly else []):
        for it in sh["items"]:
            it["liked"] = it["key"] in likes
    if weekly and weekly.get("job"):
        job = core.JOBS.get(weekly["job"])
        weekly["progress"] = core.job_summary(job) if job else None
    admin = who() == "admin"
    return {"shelves": shelves, "weekly": weekly, "built": S()["built"], "building": building().locked(),
            "mode": CFG["weekly_mode"] if admin else "stream", "artists": artists, "lb_user": CFG["lb_user"] if admin else "",
            "hint": "" if admin else "Play and star some music: your suggestions grow from what you like."}


def handle_get(path, qs):
    if path == "/api/foryou":
        return view()
    return None


def handle_post(path, b):
    if path == "/api/foryou/refresh":
        threading.Thread(target=build, args=(who(),), daemon=True).start()
        return {"ok": True}
    if path == "/api/foryou/weekly":
        if who() != "admin":
            raise PermissionError("The weekly mix is the admin's")
        threading.Thread(target=build_weekly, daemon=True).start()
        return {"ok": True}
    if path == "/api/foryou/feedback":
        feedback(b.get("key", ""), bool(b.get("like")))
        return {"ok": True}
    if path == "/api/foryou/get":
        it = find_item(b.get("key", ""))
        if not it:
            raise LookupError("That suggestion has gone; refresh the page")
        r = get_item(it)
        return {"ok": True, "state": r, "message": {"asked": "Sent to the admin for an OK", "off": "Downloading isn't switched on for you"}.get(r, "")}
    return None


def settings_view():
    return {"weekly_mode": CFG["weekly_mode"], "weekly_size": CFG["weekly_size"], "lb_user": CFG["lb_user"]}


def apply_setting(k, v):
    if k == "weekly_mode" and v in ("download", "stream"):
        CFG["weekly_mode"] = v
        return True
    if k == "weekly_size":
        CFG["weekly_size"] = max(10, min(100, int(v)))
        return True
    if k == "lb_user":
        CFG["lb_user"] = str(v or "").strip()
        return True
    return False


def init(umculo):
    global core
    core = umculo
    st = core._load_json("foryou.json", {})
    for k in STATE:
        if k in st:
            STATE[k] = st[k]
    PEOPLE.update(st.get("people") or {})
    core.subsonic.PLAYLISTS["weekly"] = weekly_playlist
    threading.Thread(target=loop, daemon=True).start()
