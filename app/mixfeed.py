"""Mixes for you: DJ mixes picked from what you download and play, so you never need YouTube to find them.

What it learns from (yours only): the shows you subscribe to and their back catalogue, the DJs and shows in
your mixes folder (weighted by how much you play them), and the styles in their titles (deep house, amapiano...).
Where it looks: YouTube (search and your shows' channels) and Hearthis.at (DJs' own uploads).
"Not interested" hides a mix; "less from this channel" hides the channel.

GET  /api/mixfeed                 -> {"shelves": [{"id", "title", "sub", "items": [mix]}], "building", "at"}
POST /api/mixfeed/refresh         -> build again now
POST /api/mixfeed/hide            {id, channel?}
POST /api/mixfeed/get             {url, title?, subscribe, find, video}   -> {"find" | "request", "subscribed"}
GET  /api/mixsearch?q=           -> {"items": [mix]}  any DJ, show or style: long sets only, yours marked
GET  /api/mixstream?u=<page>      -> the mix's audio, streamed (seekable) while it downloads
GET  /api/mixvideo?u=<page>       -> its video, streamed

a mix: {"id", "url", "title", "channel", "channel_url", "duration", "views", "thumb", "source", "why"}
"""
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

core = None
LOCK = threading.Lock()
CACHE = {}                 # user id -> {"at", "shelves", "building"}
PREFS = {}                 # user id -> {"hidden": [ids], "muted": [channels]}
FRESH = 6 * 3600
MIN_LEN = 20 * 60
HOSTS = ("youtube.com", "youtu.be", "hearthis.at", "hearthis.app", "soundcloud.com", "mixcloud.com")
STYLES = ["amapiano", "deep house", "afro house", "soulful house", "afro tech", "3 step", "gqom", "kwaito", "bacardi",
          "jazz", "lounge", "tech house", "techno", "house", "r&b", "hip hop", "piano", "deep tech", "private school"]


def uid():
    u = core.users.current()
    return (u or {}).get("id", "open")


def prefs_path():
    return os.path.join(core.CONFIG["config"], "mixfeed.json")


def load():
    try:
        with open(prefs_path()) as fh:
            PREFS.update(json.load(fh))
    except (OSError, ValueError):
        pass


def save():
    with LOCK:
        data = json.dumps(PREFS)
    with open(prefs_path(), "w") as fh:
        fh.write(data)


def norm(t):
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", (t or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


# --------------------------------------------------------------------------- what you like
def taste(u):
    """-> {"djs": [(name, weight)], "shows": [...], "styles": [...], "have": set of title keys, "subs": [...]}"""
    srv = core.server
    root = core.CONFIG["mixes"].rstrip("/") + "/"
    rows = srv.db.q(f"""SELECT song.title, song.album, song.artist, COALESCE(ann.plays, 0) AS plays FROM song
                        LEFT JOIN ann ON ann.item = song.id AND ann.user = ? AND ann.kind = 'song'
                        WHERE substr(song.path, 1, ?) = ? AND {srv.lib_sql(u)}""", (srv.uid(u), len(root), root))
    djs, shows, styles, have = {}, {}, {}, set()
    for r in rows:
        w = 1 + min(10, r["plays"] or 0)
        have.add(norm(r["title"]))
        for name, bag in ((r["album"], shows), (core.mixtidy.show_name(r["artist"] or ""), djs)):
            if name and name != core.mixtidy.LOOSE:
                bag[name] = bag.get(name, 0) + w
        text = f" {(r['title'] or '').lower()} {(r['album'] or '').lower()} "
        for st in STYLES:
            if f" {st} " in text or f" {st}," in text or f"{st} mix" in text:
                styles[st] = styles.get(st, 0) + w
                if st != "house":
                    break
    with core.links.LOCK:
        subs = [s for s in core.links.SUBS.values() if core.users.mine(s) and not s.get("podcast")]
        for f in core.links.FINDS.values():
            if core.users.mine(f):
                have.add(norm(f["title"]))
    for s in subs:
        shows[s["title"]] = shows.get(s["title"], 0) + 5
    top = lambda d, n: sorted(d.items(), key=lambda x: -x[1])[:n]
    return {"djs": top(djs, 4), "shows": top(shows, 4), "styles": top(styles, 3) or [("deep house", 1), ("afro house", 1)],
            "have": have, "subs": subs}


# --------------------------------------------------------------------------- where to look
def fake(q):
    """Tests: pretend YouTube results from UMCULO_TEST_MIXES (a JSON list), the ones sharing a word with the search."""
    with open(os.environ["UMCULO_TEST_MIXES"]) as fh:
        items = json.load(fh)
    words = set(norm(q).split()) - {"mix", "live", "set", "south", "africa", str(time.gmtime().tm_year)}
    return [yt_item(e) for e in items if not words or words & set(norm(e["title"] + " " + e.get("channel", "")).split())]


def yt_search(q, n=14):
    if os.environ.get("UMCULO_TEST_MIXES"):
        return fake(q)
    with core._ydl({"extract_flat": True}) as y:
        info = y.extract_info(f"ytsearch{n}:{q}", download=False) or {}
    return [yt_item(e) for e in info.get("entries") or [] if e and e.get("id")]


def yt_channel(url, n=30):
    if os.environ.get("UMCULO_TEST_MIXES"):
        return []
    page = url.rstrip("/") + ("/videos" if "youtube.com" in url and not url.rstrip("/").endswith("/videos") else "")
    with core._ydl({"extract_flat": "in_playlist", "playlistend": n}) as y:
        info = y.extract_info(page, download=False) or {}
    return [yt_item(e, info.get("uploader") or info.get("channel") or "", url) for e in info.get("entries") or [] if e and e.get("id")]


def yt_item(e, channel="", channel_url=""):
    vid = e["id"]
    return {"id": "yt:" + vid, "url": e.get("url") if str(e.get("url", "")).startswith("http") else f"https://www.youtube.com/watch?v={vid}",
            "title": e.get("title") or "", "channel": e.get("channel") or e.get("uploader") or channel,
            "channel_url": e.get("channel_url") or e.get("uploader_url") or channel_url,
            "duration": int(e.get("duration") or 0), "views": int(e.get("view_count") or 0),
            "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "source": "YouTube"}


def hearthis_search(q, n=20):
    url = f"{core.HEARTHIS_API}/search/?{urllib.parse.urlencode({'t': q, 'type': 'tracks', 'count': n})}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Umculo/" + core.VERSION}),
                                timeout=20) as r:
        res = json.loads(r.read() or b"[]")
    out = []
    for e in res if isinstance(res, list) else []:
        user = e.get("user") or {}
        out.append({"id": f"ht:{e.get('id')}", "url": e.get("permalink_url") or "", "title": e.get("title") or "",
                    "channel": user.get("username") or "", "channel_url": user.get("permalink_url") or "",
                    "duration": int(e.get("duration") or 0), "views": int(e.get("playback_count") or 0),
                    "thumb": e.get("artwork_url_retina") or e.get("artwork_url") or "", "source": "Hearthis.at"})
    return out


# --------------------------------------------------------------------------- the shelves
def build(user_id, u):
    t = taste(u)
    p = PREFS.get(user_id) or {}
    hidden, muted = set(p.get("hidden") or []), {c.lower() for c in p.get("muted") or []}
    seen = set()

    def keep(items, why, need_style=False, limit=12):
        out = []
        for m in items:
            k = norm(m["title"])
            if (m["id"] in seen or m["id"] in hidden or m["duration"] < MIN_LEN or not m["url"] or k in t["have"]
                    or (m["channel"] or "").lower() in muted):
                continue
            if any(k and (k in h or h in k) for h in t["have"] if len(h) > 12):
                continue
            seen.add(m["id"])
            out.append({**m, "why": why})
            if len(out) >= limit:
                break
        return out

    def safe(fn, *a):
        try:
            return fn(*a)
        except Exception as ex:  # noqa: BLE001
            core.log("mixes for you:", fn.__name__, a[:1], ex)
            return []

    shelves = []
    back = []
    for s in t["subs"][:4]:                          # older uploads of shows you follow that you don't have
        if s.get("url") and "youtube.com" in s["url"]:
            back += keep(safe(yt_channel, s["url"]), f"From {s['title']}", limit=6)
    if back:
        shelves.append({"id": "shows", "title": "More from your shows", "sub": "Mixes from channels you follow that you don't have yet", "items": back})
    names = []
    for name, _w in t["djs"][:2] + t["shows"][:3]:       # one shelf per DJ: "Noxious Deejay" and "Noxious Deejay Podcast" are one
        k = norm(name)
        if k and not any(k in o or o in k for o in map(norm, names)):
            names.append(name)
    for name in names[:3]:
        items = keep(safe(yt_search, f"{name} mix") + safe(hearthis_search, name), f"Because you play {name}")
        if len(items) >= 2:
            shelves.append({"id": "dj-" + norm(name).replace(" ", "-"), "title": f"Because you play {name}", "sub": "Their sets, and DJs like them", "items": items})
    year = time.gmtime().tm_year
    for st, _w in t["styles"][:2]:
        items = keep(safe(yt_search, f"{st} mix {year} south africa") + safe(hearthis_search, f"{st} mix"), st.title())
        if len(items) >= 3:
            shelves.append({"id": "style-" + st.replace(" ", "-"), "title": f"{st.title()} mixes", "sub": "New sets in the style you play most", "items": items})
    st = t["styles"][0][0]
    live = keep(safe(yt_search, f"{st} live set"), "Live set", limit=10)
    if len(live) >= 3:
        shelves.append({"id": "live", "title": "Live sets", "sub": f"Filmed {st} sets: great with video on", "items": live})
    ht = keep(safe(hearthis_search, f"{st} south africa"), "Free on Hearthis.at", limit=10)
    if len(ht) >= 3:
        shelves.append({"id": "hearthis", "title": "On Hearthis.at", "sub": "DJs' own uploads, free to download", "items": ht})
    return shelves


def refresh(user_id, u):
    with LOCK:
        c = CACHE.setdefault(user_id, {"at": 0, "shelves": [], "building": False})
        if c["building"]:
            return
        c["building"] = True

    def go():
        core.users.LOCAL.user = u                    # this thread runs as the person: their library, their subscriptions
        try:
            shelves = build(user_id, u)
            with LOCK:
                CACHE[user_id].update(shelves=shelves, at=time.time())
        except Exception as ex:  # noqa: BLE001
            core.log("mixes for you failed:", ex)
        finally:
            core.users.LOCAL.user = None
            with LOCK:
                CACHE[user_id]["building"] = False
    threading.Thread(target=go, daemon=True).start()


def feed():
    i, u = uid(), core.users.current()
    c = CACHE.get(i)
    if not c or time.time() - c["at"] > FRESH:
        refresh(i, u)
        c = CACHE[i]
    p = PREFS.get(i) or {}
    hidden, muted = set(p.get("hidden") or []), {x.lower() for x in p.get("muted") or []}
    shelves = [{**s, "items": [m for m in s["items"] if m["id"] not in hidden and (m["channel"] or "").lower() not in muted]}
               for s in c["shelves"]]
    return {"shelves": [s for s in shelves if s["items"]], "building": c["building"], "at": c["at"]}


def search(q):
    q = (q or "").strip()[:120]
    if not q:
        raise ValueError("Search for a DJ, a show or a style")
    t = taste(core.users.current())
    found, seen = [], set()
    yt = []
    try:
        yt = yt_search(q if re.search(r"\b(mix|set|session|podcast|episode)\b", q, re.I) else q + " mix", 25)
    except Exception as ex:  # noqa: BLE001
        core.log("mix search (YouTube):", ex)
    ht = []
    try:
        ht = hearthis_search(q, 20)
    except Exception as ex:  # noqa: BLE001
        core.log("mix search (Hearthis):", ex)
    for m in yt + ht:
        if m["id"] in seen or m["duration"] < MIN_LEN or not m["url"]:
            continue
        seen.add(m["id"])
        found.append({**m, "have": norm(m["title"]) in t["have"]})
    found.sort(key=lambda m: (m["source"] != "YouTube" and m["views"] < 50, 0))
    return {"items": found[:40]}


def hide(b):
    i = uid()
    with LOCK:
        p = PREFS.setdefault(i, {"hidden": [], "muted": []})
        if b.get("id"):
            p.setdefault("hidden", []).append(str(b["id"])[:80])
            p["hidden"] = p["hidden"][-2000:]
        if b.get("channel"):
            p.setdefault("muted", []).append(str(b["channel"])[:120])
    save()
    return {"ok": True}


# --------------------------------------------------------------------------- get it
def check_url(url):
    url = (url or "").strip()
    host = urllib.parse.urlparse(url).netloc.lower()
    test = os.environ.get("UMCULO_TEST_MIXES") and host.startswith("localhost")
    if not re.match(r"^https?://", url) or not (test or any(host == h or host.endswith("." + h) for h in HOSTS)):
        raise ValueError("Mixes from YouTube, Hearthis.at, SoundCloud or Mixcloud")
    return url


def get(b):
    if not core.users.can("download"):
        raise PermissionError("Downloading needs download permission")
    show = core.links.resolve(check_url(b.get("url")))
    e = next((x for x in show["entries"] if x["id"] == show.get("focus")), None) or show["entries"][0]
    video = bool(b.get("video"))
    out = {"show": show["id"], "subscribed": bool(show.get("subscribed"))}
    if b.get("find", True):                         # into the library first, then the song finder listens to it
        out["find"] = core.links.start_find({**show, "video": video}, e, keep=True)
    else:
        out["request"] = core.links.get_entry(show, e, video)
    if b.get("subscribe") and show.get("subscribable") and not show.get("subscribed"):
        if show.get("channel_url"):
            channel = core.links.resolve(show["channel_url"])
            core.links.subscribe_async(channel, False, video)
        else:
            core.links.subscribe_async(show, False, video)
        out["subscribed"] = True
    return out


# --------------------------------------------------------------------------- stream it now
AUDIO_FMT = "bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio[ext=mp3]/bestaudio/best"


def stream(h, qs, video=False):
    page = check_url((qs.get("u") or [""])[0])
    core.video.proxy(h, page, core.video.vfmt(core.video.quality(qs)) if video else AUDIO_FMT, "video/mp4" if video else None)
    return True


def _warm(page):
    try:
        if not os.environ.get("UMCULO_TEST_MIXES"):
            core.video.direct(page, AUDIO_FMT)
    except Exception as ex:  # noqa: BLE001
        core.log("mix warm-up:", ex)


def handle_get(h, path, qs):
    """Called from do_GET with the handler (two of these stream). -> True when handled."""
    if path == "/api/mixfeed":
        return h.send_json(feed()) or True
    if path == "/api/mixsearch":
        try:
            return h.send_json(search((qs.get("q") or [""])[0])) or True
        except ValueError as ex:
            return h.send_json({"error": str(ex)}, 400) or True
    if path == "/api/mixstream":
        return stream(h, qs)
    if path == "/api/mixwarm":                     # get a mix's stream address ready before Play is pressed
        page = check_url((qs.get("u") or [""])[0])
        threading.Thread(target=lambda: _warm(page), daemon=True).start()
        return h.send_json({"ok": True}) or True
    if path == "/api/mixvideo":
        return stream(h, qs, True)
    return None


def handle_post(path, b):
    if path == "/api/mixfeed/refresh":
        refresh(uid(), core.users.current())
        return {"ok": True}
    if path == "/api/mixfeed/hide":
        return hide(b)
    if path == "/api/mixfeed/get":
        return get(b)
    return None


def init(umculo):
    global core
    core = umculo
    load()
