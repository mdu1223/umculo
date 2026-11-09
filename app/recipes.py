"""Recipes: playlists Umculo cooks for you, on a schedule or right now.

A recipe is a list of ingredients, each with how many songs it adds:
  discover  - artists you don't play yet, close to the ones you do
  library   - songs you don't have yet from artists you play (or their deeper cuts)
  trending  - what's hot in your country this week (Deezer's chart for where your server is)
  focus     - artists close to the ones you name ("seeds")
  new       - recent releases from artists you play
  vibe      - any genre, mood or era in words: "amapiano", "sunday jazz", "90s kwaito", "gqom, 3-step"
Then flavour: popularity (hits / mix / hidden gems), deeper cuts, one song per artist, and what
happens to the result (stream-only playlist in your apps, or download into your library).
Recipes rotate: songs from the last two runs are left out so each run is fresh.
Explore stations are one-tap recipes ("Amapiano, hits") that play straight away.
"""
import concurrent.futures as cf
import datetime
import os
import random
import re
import threading
import time
import traceback
import urllib.parse
import uuid

core = None
LOCK = threading.Lock()
RECIPES = {}
RUNNING = set()
STATIONS = {}                     # vibe|popularity -> (built at, songs) for one-tap explore stations

INGREDIENTS = {
    "discover": "Artists you don't play yet, close to the ones you do",
    "library": "Songs you don't have yet from artists you play",
    "trending": "What's hot this week where you are",
    "focus": "Artists close to the ones you name",
    "new": "Recent releases from artists you play",
    "vibe": "A genre, mood or era, in your words",
}

TEMPLATES = [
    {"key": "fresh", "name": "Fresh Finds", "emoji": "🌱", "color": "#2fbf71",
     "about": "New artists that sound like your favourites, every Monday.",
     "ingredients": {"discover": 18, "focus": 0, "trending": 4, "new": 3}, "popularity": "mix", "days": [0]},
    {"key": "charts", "name": "Mzansi Heat", "emoji": "🔥", "color": "#ff7a3d",
     "about": "The biggest songs in the country right now, plus the hottest amapiano.",
     "ingredients": {"trending": 20, "vibe": 10}, "vibe": "amapiano", "popularity": "hits", "days": [4]},
    {"key": "deep", "name": "Deep Cuts", "emoji": "💎", "color": "#7c6cff",
     "about": "Album tracks from artists you love that you've never saved.",
     "ingredients": {"library": 25}, "deeper": True, "popularity": "gems", "days": [2]},
    {"key": "radar", "name": "Release Radar", "emoji": "📡", "color": "#2aa8ff",
     "about": "Fresh releases from artists you play, every Friday.",
     "ingredients": {"new": 25, "library": 5}, "popularity": "mix", "days": [4]},
    {"key": "explore", "name": "Something New", "emoji": "🧭", "color": "#e6b422",
     "about": "A genre you don't usually play. Change the vibe to steer it.",
     "ingredients": {"vibe": 25}, "vibe": "afrobeats, kwaito, gqom", "popularity": "mix", "days": [5]},
    {"key": "popular", "name": "All the Hits", "emoji": "🏆", "color": "#ff4d6d",
     "about": "The most popular songs of a sound, no deep cuts.",
     "ingredients": {"vibe": 20, "trending": 10}, "vibe": "afro house", "popularity": "hits", "days": [6]},
    {"key": "chill", "name": "Sunday Slow", "emoji": "☕", "color": "#b07b5a",
     "about": "Soulful house, jazz and neo soul for slow mornings.",
     "ingredients": {"vibe": 25}, "vibe": "soulful house, jazz, neo soul", "popularity": "mix", "days": [6]},
]

GENRES = ["Amapiano", "Afro House", "Deep House", "Soulful House", "Gqom", "3-Step", "Kwaito", "Afro Tech",
          "Maskandi", "Gospel", "Jazz", "Neo Soul", "R&B", "Hip Hop", "SA Hip Hop", "Afrobeats", "Bacardi",
          "Lekompo", "Dancehall", "Old School", "90s R&B", "Disco", "Reggae", "Lo-fi", "Piano", "Workout"]


def log(*a):
    core.log(*a)


def dz(path):
    try:
        return core.dz_get(path)
    except Exception as e:  # noqa: BLE001
        log("recipes: catalogue call failed:", path, e)
        return {}


def save():
    with LOCK:
        data = [{k: v for k, v in r.items()} for r in RECIPES.values()]
    core._save_json("recipes.json", data)


def item(d, why, album=None):
    it = core.foryou.item_from_dz(d, why, album)
    it["rank"] = d.get("rank") or 0
    return it


# --------------------------------------------------------------------------- ingredients
def top_artists(n=15):
    w = core.foryou.taste()
    return [a for a, v in sorted(w.items(), key=lambda x: -x[1]) if v > 0][:n]


def artist_tracks(aid, deeper, why):
    data = dz(f"/artist/{aid}/top?limit={30 if deeper else 10}").get("data") or []
    if deeper and len(data) > 12:
        data = data[10:30]
    return [item(d, why) for d in data]


def src_trending(r, n):
    out = [item(d, "Trending in your country this week") for d in (dz("/chart/0/tracks?limit=100").get("data") or [])]
    return out


def src_library(r, n):
    out = []
    names = top_artists(12)
    with cf.ThreadPoolExecutor(6) as ex:
        ids = list(ex.map(core.foryou.artist_id, names))
        lists = list(ex.map(lambda p: artist_tracks(p[1], r.get("deeper"), ("Deeper cut from " if r.get("deeper")
                                                                            else "From ") + p[0]) if p[1] else [],
                            zip(names, ids)))
    for lst in lists:
        out += lst
    return out


def related_tracks(seed_names, why_fmt, exclude, deeper=False, depth="mix"):
    with cf.ThreadPoolExecutor(6) as ex:
        ids = list(ex.map(core.foryou.artist_id, seed_names))
    rel = []
    for name, aid in zip(seed_names, ids):
        if not aid:
            continue
        for a in (dz(f"/artist/{aid}/related?limit=12").get("data") or [])[:8]:
            if core.norm(a["name"]) not in exclude:
                rel.append((name, a))
    if depth == "gems":                             # one hop further: friends of friends
        more = []
        for name, a in rel[:6]:
            for b in (dz(f"/artist/{a['id']}/related?limit=6").get("data") or [])[:3]:
                if core.norm(b["name"]) not in exclude:
                    more.append((name, b))
        rel += more
    seen, uniq = set(), []
    for name, a in rel:
        if a["id"] not in seen:
            seen.add(a["id"])
            uniq.append((name, a))
    with cf.ThreadPoolExecutor(6) as ex:
        lists = list(ex.map(lambda p: artist_tracks(p[1]["id"], deeper, why_fmt.format(p[0]))[:5], uniq[:24]))
    return [i for lst in lists for i in lst]


def src_discover(r, n):
    top = top_artists(10)
    return related_tracks(top[:6], "Because you play {}", {core.norm(a) for a in top_artists(60)},
                          r.get("deeper"), r.get("popularity"))


def src_focus(r, n):
    seeds = [s.strip() for s in (r.get("seeds") or []) if s.strip()] or top_artists(3)
    out = related_tracks(seeds[:5], "Close to {}", set(), r.get("deeper"), r.get("popularity"))
    with cf.ThreadPoolExecutor(4) as ex:                        # and the seeds themselves
        ids = list(ex.map(core.foryou.artist_id, seeds[:5]))
    for name, aid in zip(seeds, ids):
        if aid:
            out += artist_tracks(aid, r.get("deeper"), f"From {name}")[:4]
    return out


def src_new(r, n):
    return core.foryou.shelf_new(top_artists(20))


def vibe_terms(text):
    return [t.strip() for t in re.split(r"[,;/]+", text or "") if t.strip()][:5]


def src_vibe(r, n):
    out = []
    for term in vibe_terms(r.get("vibe")):
        pls = dz("/search/playlist?limit=10&q=" + urllib.parse.quote(term)).get("data") or []
        pls = [p for p in pls if (p.get("nb_tracks") or 0) >= 10]
        pls.sort(key=lambda p: -(p.get("nb_tracks") or 0) if r.get("popularity") == "gems" else -(p.get("fans") or 0))
        with cf.ThreadPoolExecutor(3) as ex:
            lists = list(ex.map(lambda p: dz(f"/playlist/{p['id']}/tracks?limit=100").get("data") or [], pls[:3]))
        label = term if term.isupper() else term.title()
        for lst in lists:
            out += [item(d, f"{label}") for d in lst if d.get("readable", True)]
        art = (dz("/search/artist?limit=5&q=" + urllib.parse.quote(term)).get("data") or [])   # "kabza" works too
        if art and core.norm(art[0]["name"]) == core.norm(term):
            out += artist_tracks(art[0]["id"], r.get("deeper"), f"From {art[0]['name']}")
    return out


SOURCES = {"discover": src_discover, "library": src_library, "trending": src_trending, "focus": src_focus,
           "new": src_new, "vibe": src_vibe}


# --------------------------------------------------------------------------- cooking
def flavour(items, popularity):
    if popularity == "hits":
        return sorted(items, key=lambda i: -(i.get("rank") or 0))
    if popularity == "gems":
        ranked = sorted(items, key=lambda i: i.get("rank") or 0)
        low = ranked[:max(len(ranked) * 2 // 3, 1)]
        random.shuffle(low)
        return low + ranked[len(low):]
    items = list(items)
    random.shuffle(items)
    return items


def cook(r, exclude_keys=()):
    """-> list of songs for this recipe."""
    ing = {k: max(0, min(100, int(v or 0))) for k, v in (r.get("ingredients") or {}).items() if k in SOURCES}
    ing = {k: v for k, v in ing.items() if v}
    if not ing:
        raise ValueError("Add at least one ingredient")
    with core.foryou.LOCK:
        banned = set(core.foryou.STATE["dislikes"])
    pools = {}
    with cf.ThreadPoolExecutor(len(ing)) as ex:
        futs = {k: ex.submit(SOURCES[k], r, n) for k, n in ing.items()}
        for k, f in futs.items():
            try:
                pools[k] = flavour([i for i in f.result() if i.get("deezer_id")], r.get("popularity", "mix"))
            except Exception:  # noqa: BLE001
                log("recipe ingredient failed:", k, traceback.format_exc())
                pools[k] = []
    def not_owned(lst, n):
        """Drop songs already in your library (checked for the front of each pool, in parallel)."""
        head = lst[:n * 2 + 10]
        with cf.ThreadPoolExecutor(8) as ex:
            have = list(ex.map(lambda i: bool(core.links.owned(i["artist"], i["title"])), head))
        return [i for i, h in zip(head, have) if not h] + lst[len(head):]
    for k in pools:
        if not (k == "trending" and r.get("popularity") == "hits"):
            pools[k] = not_owned(pools[k], ing[k])
    per_artist = 1 if r.get("one_per_artist", True) else 3
    picked, keys, artists = [], set(), {}

    def take(it, keep_owned):
        k = it["key"]
        a = core.norm((core.split_artists(it["artist"]) or [it["artist"]])[0])
        if k in keys or k in banned or k in exclude_keys or artists.get(a, 0) >= per_artist:
            return False
        if not keep_owned and core.index_get(it["artist"], it["title"]):
            return False
        keys.add(k)
        artists[a] = artists.get(a, 0) + 1
        picked.append(it)
        return True

    left = {}
    for k, n in ing.items():
        got, rest = 0, []
        for it in pools[k]:
            if got < n and take(it, k == "trending" and r.get("popularity") == "hits"):
                got += 1
            else:
                rest.append(it)
        left[k] = (n - got, rest)
    short = sum(s for s, _r in left.values())
    for k, (_s, rest) in left.items():             # fill the gaps from the other ingredients
        for it in rest:
            if short <= 0:
                break
            if take(it, False):
                short -= 1
    if r.get("popularity") != "hits":
        random.shuffle(picked)
    return picked


def run(rid, notify=True):
    with LOCK:
        r = RECIPES.get(rid)
        if not r or rid in RUNNING:
            return
        RUNNING.add(rid)
        r["message"] = "Cooking…"
    try:
        recent = set(k for run_keys in (r.get("history") or [])[-2:] for k in run_keys)
        songs = cook(r, recent)
        if len(songs) < 5:                                   # rotation left too little: allow repeats
            songs = cook(r)
        job = None
        if r.get("mode") == "download" and songs:
            name = core.safe(r["name"])
            job = core.new_job(name, "import", os.path.join(core.CONFIG["playlists"], name + ".m3u8"),
                               [{"artist": s["artist"], "title": s["title"], "duration": s.get("duration") or 0}
                                for s in songs])
        with LOCK:
            r.update(songs=songs, job=job, last_run=time.time(), message="",
                     history=((r.get("history") or []) + [[s["key"] for s in songs]])[-4:])
        save()
        core.subsonic.PLAYLISTS[pl_id(rid)] = provider(rid)
        log(f"recipe {r['name']}: {len(songs)} songs ({r.get('mode')})")
        if notify:
            core.notify(f"{r['emoji']} {r['name']} is ready: {core.plural(len(songs), 'song')}")
    except Exception as e:  # noqa: BLE001
        log("recipe failed:", r.get("name"), traceback.format_exc())
        with LOCK:
            r["message"] = f"Couldn't cook it: {e}"[:200]
        save()
    finally:
        RUNNING.discard(rid)


def pl_id(rid):
    return "r" + rid


def owner(r):
    return r.get("owner") or "admin"            # recipes from before 2.2 are yours


def me():
    return core.server.uid(core.users.current())


def mine(r):
    return r is not None and owner(r) == me()


def provider(rid):
    def p():
        with LOCK:
            r = RECIPES.get(rid)
        if not r or not r.get("songs") or r.get("mode") == "download":
            return None
        return {"owner": owner(r), "name": f"{r['name']} {r.get('emoji', '')}".strip(), "comment": "An Umculo recipe. Star a song to keep it.",
                "songs": r["songs"], "created": None,
                "cover": r["songs"][0].get("cover_big") or r["songs"][0].get("cover")}
    return p


# --------------------------------------------------------------------------- schedule
def due(r, now=None):
    now = now or datetime.datetime.now()
    if not r.get("enabled") or now.weekday() not in (r.get("days") or []) or now.hour < int(r.get("hour", 6)):
        return False
    last = r.get("last_run")
    return not last or datetime.datetime.fromtimestamp(last).date() != now.date()


def loop():
    time.sleep(90)
    while True:
        try:
            with LOCK:
                todo = [rid for rid, r in RECIPES.items() if due(r)]
            for rid in todo:
                run(rid)
        except Exception:  # noqa: BLE001
            log("recipes loop:", traceback.format_exc())
        time.sleep(600)


# --------------------------------------------------------------------------- web
def clean_recipe(b, old=None):
    r = dict(old or {})
    for k in ("name", "emoji", "color", "vibe", "popularity", "mode", "about"):
        if k in b:
            r[k] = str(b[k] or "")[:120]
    if "ingredients" in b:
        r["ingredients"] = {k: max(0, min(100, int(v or 0))) for k, v in (b["ingredients"] or {}).items() if k in SOURCES}
    if "seeds" in b:
        seeds = b["seeds"] if isinstance(b["seeds"], list) else str(b["seeds"]).split(",")
        r["seeds"] = [s.strip() for s in seeds if s.strip()][:10]
    for k in ("deeper", "enabled", "one_per_artist"):
        if k in b:
            r[k] = bool(b[k])
    if "days" in b:
        r["days"] = sorted({int(d) for d in b["days"] if 0 <= int(d) <= 6})
    if "hour" in b:
        r["hour"] = max(0, min(23, int(b["hour"])))
    r.setdefault("name", "My recipe")
    r.setdefault("emoji", "🎧")
    r.setdefault("color", "#ff7a3d")
    r.setdefault("popularity", "mix")
    r.setdefault("mode", "stream")
    r.setdefault("days", [0])
    r.setdefault("hour", 6)
    r.setdefault("enabled", True)
    r.setdefault("one_per_artist", True)
    if r["popularity"] not in ("hits", "mix", "gems"):
        r["popularity"] = "mix"
    if r["mode"] not in ("stream", "download"):
        r["mode"] = "stream"
    return r


def view(r, songs=False):
    out = {k: v for k, v in r.items() if k not in ("songs", "history")}
    out["count"] = len(r.get("songs") or [])
    out["running"] = r["id"] in RUNNING
    out["covers"] = [s.get("cover") for s in (r.get("songs") or []) if s.get("cover")][:4]
    out["size"] = sum((r.get("ingredients") or {}).values())
    if r.get("job"):
        job = core.JOBS.get(r["job"])
        out["progress"] = core.job_summary(job) if job else None
    if songs:
        out["songs"] = r.get("songs") or []
    return out


def station(vibe, popularity="mix"):
    k = f"{vibe.lower()}|{popularity}"
    hit = STATIONS.get(k)
    if hit and time.time() - hit[0] < 3 * 3600:
        return hit[1]
    songs = cook({"ingredients": {"vibe": 30}, "vibe": vibe, "popularity": popularity, "one_per_artist": False})
    STATIONS[k] = (time.time(), songs)
    return songs


def handle_get(path, qs):
    if path == "/api/recipes":
        with LOCK:
            rs = sorted((r for r in RECIPES.values() if mine(r)), key=lambda r: r.get("created", 0))
        return {"recipes": [view(r) for r in rs], "templates": TEMPLATES, "ingredients": INGREDIENTS, "genres": GENRES}
    m = re.match(r"^/api/recipes/([0-9a-f]+)$", path)
    if m:
        r = RECIPES.get(m.group(1))
        if not mine(r):                            # each person's recipes are their own
            raise LookupError("Recipe not found")
        return view(r, True)
    if path == "/api/station":
        vibe = (qs.get("vibe") or [""])[0].strip()
        if not vibe:
            raise ValueError("Pick a sound")
        return {"songs": station(vibe, (qs.get("popularity") or ["mix"])[0])}
    return None


def handle_post(path, b):
    if path == "/api/recipes":
        tpl = next((t for t in TEMPLATES if t["key"] == b.get("template")), None)
        base = {k: v for k, v in (tpl or {}).items() if k != "key"}
        r = clean_recipe({**base, **{k: v for k, v in b.items() if k != "template"}})
        r.update(id=uuid.uuid4().hex[:10], created=time.time(), songs=[], history=[], last_run=None, job=None, message="", owner=me())
        with LOCK:
            RECIPES[r["id"]] = r
        save()
        threading.Thread(target=run, args=(r["id"], False), daemon=True).start()
        return view(r)
    if path == "/api/recipes/preview":
        return {"songs": cook(clean_recipe(b))}
    m = re.match(r"^/api/recipes/([0-9a-f]+)/(save|run|delete|keep)$", path)
    if not m:
        return None
    rid, act = m.groups()
    with LOCK:
        r = RECIPES.get(rid)
    if not mine(r):
        raise LookupError("Recipe not found")
    if act == "keep":                      # this run's songs, downloaded into a playlist
        if not r.get("songs"):
            raise ValueError("Nothing cooked yet")
        name = core.safe(f"{r['name']} {time.strftime('%d %b')}")
        jid = core.new_job(name, "import", os.path.join(core.CONFIG["playlists"], name + ".m3u8"),
                           [{"artist": s["artist"], "title": s["title"], "duration": s.get("duration") or 0} for s in r["songs"]])
        return {"job": jid}
    if act == "delete":
        with LOCK:
            RECIPES.pop(rid, None)
        core.subsonic.PLAYLISTS.pop(pl_id(rid), None)
        save()
        return {"ok": True}
    if act == "save":
        with LOCK:
            RECIPES[rid] = clean_recipe(b, r)
        save()
        if b.get("rerun"):
            threading.Thread(target=run, args=(rid, False), daemon=True).start()
        return view(RECIPES[rid])
    threading.Thread(target=run, args=(rid, False), daemon=True).start()
    return {"ok": True}


def init(umculo):
    global core
    core = umculo
    for r in core._load_json("recipes.json", []):
        RECIPES[r["id"]] = r
        core.subsonic.PLAYLISTS[pl_id(r["id"])] = provider(r["id"])
    threading.Thread(target=loop, daemon=True).start()
