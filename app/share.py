"""Share to Umculo, music recognition, Apple Music playlists and followed playlists.

  * Access keys (tokens) for the iPhone Shortcut and, later, the Umculo phone app
  * /api/share: any song link (Spotify, Apple Music, Shazam, Deezer, YouTube, YouTube Music,
    TikTok, SoundCloud, Audiomack...) or "Artist - Title" -> the best copy in your quality profile.
    Album and playlist links become playlist imports.
  * /api/recognize: a few seconds of microphone audio -> the song (Shazam)
  * Apple Music public playlists and albums
  * Follow a Spotify / Apple Music / YouTube playlist: new songs added to it are fetched too
    (what spotDL's "sync" did, in your quality profile)
"""
import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid

core = None
E = os.environ.get
CFG = {"itunes": E("ITUNES_API", "https://itunes.apple.com").rstrip("/"),
       "follow_hours": float(E("FOLLOW_CHECK_HOURS", "12"))}
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
      "Version/18.0 Mobile/15E148 Safari/604.1")
TOKENS, FOLLOWS, SHARES = {}, {}, []
LOCK = threading.Lock()


def log(*a):
    core.log(*a)


# --------------------------------------------------------------------------- access keys
def h(tok):
    return hashlib.sha256(tok.encode()).hexdigest()


def save_tokens():
    with LOCK:
        data = list(TOKENS.values())
    core._save_json("tokens.json", data)


def new_token(name):
    tok = "umc_" + secrets.token_urlsafe(24)
    tid = uuid.uuid4().hex[:8]
    with LOCK:
        TOKENS[tid] = {"id": tid, "name": (name or "Key").strip()[:60], "hash": h(tok), "created": time.time(),
                       "last_used": None, "user": core.users.current_id()}
    save_tokens()
    return tid, tok


def token_info(tok):
    if not tok or not tok.startswith("umc_"):
        return None
    hh = h(tok)
    with LOCK:
        for t in TOKENS.values():
            if secrets.compare_digest(t["hash"], hh):
                t["last_used"] = time.time()
                return t
    return None


def token_ok(tok):
    return bool(token_info(tok))


# --------------------------------------------------------------------------- page helpers
def fetch(url, timeout=30):
    data, _h = core.http("GET", url, raw=True, timeout=timeout, headers={"User-Agent": UA}, retries=1)
    return data.decode("utf-8", "replace")


def final_url(url):
    """Follow short links (link.deezer.com, spotify.link, vm.tiktok.com...)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="GET")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.geturl()
    except Exception:  # noqa: BLE001
        return url


def walk(o, test):
    if isinstance(o, dict):
        if test(o):
            yield o
        for v in o.values():
            yield from walk(v, test)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v, test)


def song(artist, title, duration=0, album="", cover="", source=""):
    return {"artist": (artist or "").strip(), "title": (title or "").strip(), "duration": int(duration or 0),
            "album": album or "", "cover": cover or "", "from": source}


# --------------------------------------------------------------------------- Spotify
def spotify_embed(kind, sid):
    html = fetch(f"{E('SPOTIFY_EMBED', 'https://open.spotify.com/embed')}/{kind}/{sid}")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("Spotify didn't return that page (private, or Spotify changed its page)")
    return json.loads(m.group(1))


def spotify_track(sid):
    data = spotify_embed("track", sid)
    for o in walk(data, lambda d: d.get("type") == "track" and d.get("name") and d.get("artists")):
        return song(", ".join(a.get("name", "") for a in o["artists"]), o["name"], (o.get("duration") or 0) / 1000,
                    source="Spotify")
    for o in walk(data, lambda d: d.get("title") and d.get("subtitle")):
        return song(o["subtitle"], o["title"], (o.get("duration") or 0) / 1000, source="Spotify")
    raise ValueError("Couldn't read that Spotify song")


def spotify_list(kind, sid):
    data = spotify_embed(kind, sid)
    for o in walk(data, lambda d: isinstance(d.get("trackList"), list)):
        tracks = [song((t.get("subtitle") or "").replace(" ", " "), t.get("title"), (t.get("duration") or 0) / 1000)
                  for t in o["trackList"] if t.get("title")]
        return o.get("name") or o.get("title") or "Spotify", tracks
    raise ValueError("No songs found on that Spotify page")


# --------------------------------------------------------------------------- Apple Music
def itunes(path):
    return core.http("GET", f"{CFG['itunes']}{path}", timeout=20) or {}


def art(url):
    return re.sub(r"/\d+x\d+bb\.", "/600x600bb.", url or "")


def apple_song(song_id):
    r = (itunes(f"/lookup?id={song_id}").get("results") or [])
    t = next((x for x in r if x.get("wrapperType") == "track"), None)
    if not t:
        raise ValueError("Apple Music didn't find that song")
    return song(t.get("artistName"), t.get("trackName"), (t.get("trackTimeMillis") or 0) / 1000,
                t.get("collectionName"), art(t.get("artworkUrl100")), "Apple Music")


def apple_album(album_id):
    r = itunes(f"/lookup?id={album_id}&entity=song&limit=200").get("results") or []
    alb = next((x for x in r if x.get("wrapperType") == "collection"), {})
    tracks = [song(t.get("artistName"), t.get("trackName"), (t.get("trackTimeMillis") or 0) / 1000,
                   t.get("collectionName"), art(t.get("artworkUrl100"))) for t in r if t.get("wrapperType") == "track"]
    if not tracks:
        raise ValueError("Apple Music didn't return that album")
    return f"{alb.get('artistName', '')} - {alb.get('collectionName', 'Album')}".strip(" -"), tracks


def apple_playlist(url):
    """Public Apple Music playlist: read the songs from the web page (no account needed)."""
    html = fetch(url)
    name = ""
    og = re.search(r'<meta property="og:title" content="([^"]+)"', html)
    if og:
        name = re.sub(r"\s+on Apple Music$", "", og.group(1)).strip()
    tracks = []
    m = re.search(r'<script[^>]+id="serialized-server-data"[^>]*>(.*?)</script>', html, re.S)
    if m:
        data = json.loads(m.group(1))
        for sec in walk(data, lambda d: "track-list" in str(d.get("id", "")) and isinstance(d.get("items"), list)):
            for it in sec["items"]:
                artist = it.get("artistName") or ((it.get("subtitleLinks") or [{}])[0].get("title")) or it.get("subtitle") or ""
                title = it.get("title") or ((it.get("titleLinks") or [{}])[0].get("title")) or ""
                if title:
                    tracks.append(song(artist, title, (it.get("duration") or 0) / 1000))
            if tracks:
                break
    if not tracks:                                       # fallback: the page's schema.org data
        for ld in re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
            try:
                data = json.loads(ld)
            except ValueError:
                continue
            for o in walk(data, lambda d: isinstance(d.get("track"), list)):
                for t in o["track"]:
                    by = t.get("byArtist")
                    artist = ", ".join(a.get("name", "") for a in by) if isinstance(by, list) else (by or {}).get("name", "")
                    tracks.append(song(artist, t.get("name")))
                name = name or o.get("name", "")
                break
    if not tracks:
        raise ValueError("Couldn't read that Apple Music playlist (is it public?)")
    return name or "Apple Music playlist", tracks


# --------------------------------------------------------------------------- Shazam
def shazam_track(track_id):
    from shazamio import Shazam

    async def go():
        return await Shazam().track_about(track_id=int(track_id))
    t = asyncio.run(go()) or {}
    if not t.get("title"):
        raise ValueError("Shazam didn't find that song")
    return song(t.get("subtitle"), t.get("title"), 0, cover=(t.get("images") or {}).get("coverarthq", ""),
                source="Shazam")


# --------------------------------------------------------------------------- any link
def resolve(text):
    """-> ("song", song) or ("list", (name, [songs]))"""
    text = (text or "").strip()
    url = re.search(r"https?://\S+", text)
    if not url:
        if not text:
            raise ValueError("Send a link or 'Artist - Title'")
        text = text.strip().splitlines()[-1]
        if " - " in text and ":" in text.split(" - ")[0]:
            text = text.split(":", 1)[1].strip()               # "Listen to this: Artist - Title"
        t = core.resolve_free_text(text)
        return "song", song(t["artist"], t["title"] + (f" ({t['version']})" if t.get("version") else ""), t.get("duration"))
    url = url.group(0).rstrip(").,")
    host = urllib.parse.urlparse(url).netloc.lower()
    if any(x in host for x in ("spotify.link", "link.deezer.com", "deezer.page.link", "vm.tiktok.com", "vt.tiktok.com",
                               "shz.am", "on.soundcloud.com", "apple.co")):
        url = final_url(url)
        host = urllib.parse.urlparse(url).netloc.lower()
    if "spotify.com" in host:
        m = re.search(r"(track|album|playlist)/([A-Za-z0-9]{22})", url)
        if not m:
            raise ValueError("That Spotify link isn't a song, album or playlist")
        if m.group(1) == "track":
            return "song", spotify_track(m.group(2))
        return "list", spotify_list(m.group(1), m.group(2))
    if "music.apple.com" in host or "itunes.apple.com" in host:
        i = re.search(r"[?&]i=(\d+)", url)
        if i or "/song/" in url:
            return "song", apple_song(i.group(1) if i else re.search(r"/(\d+)(?:\?|$)", url).group(1))
        if "/playlist/" in url:
            return "list", apple_playlist(url)
        a = re.search(r"/album/[^/]*/?(\d+)", url) or re.search(r"/id(\d+)", url)
        if a:
            return "list", apple_album(a.group(1))
        raise ValueError("That Apple Music link isn't a song, album or playlist")
    if "shazam.com" in host:
        m = re.search(r"/(?:track|song)/(\d+)", url)
        if not m:
            raise ValueError("That Shazam link has no song in it")
        return "song", shazam_track(m.group(1))
    if "deezer.com" in host:
        m = re.search(r"/(track|album|playlist)/(\d+)", url)
        if not m:
            raise ValueError("That Deezer link isn't a song, album or playlist")
        d = core.http("GET", f"{core.CONFIG['deezer']}/{m.group(1)}/{m.group(2)}", timeout=20) or {}
        if m.group(1) == "track":
            return "song", song((d.get("artist") or {}).get("name"), d.get("title"), d.get("duration"),
                                (d.get("album") or {}).get("title"), (d.get("album") or {}).get("cover_xl"), "Deezer")
        tracks = [song((t.get("artist") or {}).get("name"), t.get("title"), t.get("duration"))
                  for t in (d.get("tracks") or {}).get("data") or []]
        return "list", (d.get("title") or "Deezer", tracks)
    # YouTube, YouTube Music, TikTok, SoundCloud, Audiomack, Instagram...: ask yt-dlp what it is
    with core._ydl({"extract_flat": "in_playlist"}) as y:
        info = y.extract_info(url, download=False) or {}
    if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
        tracks = []
        for e in info.get("entries") or []:
            if e:
                a, t = core.links.song_from_title(e.get("title") or "", e.get("uploader") or e.get("channel") or "")
                if t:
                    tracks.append(song(a, t, e.get("duration")))
        return "list", (info.get("title") or "Playlist", tracks)
    if info.get("track") and info.get("artist") and "original sound" not in info["track"].lower():
        return "song", song(info["artist"], info["track"], info.get("duration"), info.get("album"), "", host)
    a, t = core.links.song_from_title(info.get("title") or "", info.get("uploader") or info.get("channel") or "")
    if not t:
        raise ValueError("Couldn't tell which song that is")
    return "song", song(a, t, info.get("duration"), "", info.get("thumbnail") or "", host)


def catalogue_match(s):
    """Fill in a catalogue entry (cover, exact title, version) so the download and tags are right."""
    tgt = core.make_target(s["artist"], s["title"])
    try:
        for d in core.deezer_search(f"{s['artist']} {s['title']}", limit=5):
            if core.match(f"{d['artist']} {d['title']} {d.get('version', '')}", tgt)[0]:
                core.subsonic.remember([d])
                return d
    except Exception:  # noqa: BLE001
        pass
    return None


def take(text, name=""):
    """Resolve and start fetching. Returns a short message for the phone."""
    kind, data = resolve(text)
    if kind == "song":
        s = data
        d = catalogue_match(s) or {}
        m = {"artist": d.get("artist") or s["artist"], "title": d.get("title") or s["title"],
             "version": d.get("version", ""), "duration": d.get("duration") or s["duration"],
             "album": d.get("album") or s["album"], "cover": d.get("cover") or s["cover"],
             "cover_big": d.get("cover_big") or s["cover"], "deezer_id": d.get("deezer_id")}
        core.POOL.submit(core.subsonic.auto_get, m, "you shared it")
        msg = f"Getting {m['artist']} - {m['title']}"
        record(text, msg, m)
        return {"ok": True, "kind": "song", "message": msg, "song": m}
    pname, tracks = data
    tracks = [t for t in tracks if t["title"]]
    if not tracks:
        raise ValueError("No songs found there")
    pname = core.safe(name or pname)
    jid = core.new_job(pname, "import", os.path.join(core.CONFIG["playlists"], pname + ".m3u8"),
                       [{"artist": t["artist"], "title": t["title"], "duration": t["duration"]} for t in tracks])
    msg = f"Getting {core.plural(len(tracks), 'song')} into the playlist “{pname}”"
    record(text, msg, None)
    return {"ok": True, "kind": "list", "message": msg, "job": jid, "count": len(tracks), "name": pname}


def record(text, msg, m):
    with LOCK:
        SHARES.insert(0, {"at": time.time(), "text": text[:300], "message": msg,
                          "cover": (m or {}).get("cover", "")})
        del SHARES[30:]


# --------------------------------------------------------------------------- music recognition
def recognize(audio):
    """A few seconds of microphone audio (any format the browser records) -> the song."""
    if core.links.CFG["test_shazam"]:
        table = json.load(open(core.links.CFG["test_shazam"]))
        return {"found": True, "artist": table[1][2], "title": table[1][3], "album": "", "cover": "", "shazam": ""}
    from shazamio import Shazam
    with tempfile.TemporaryDirectory() as d:
        src, wav = os.path.join(d, "in"), os.path.join(d, "clip.wav")
        with open(src, "wb") as fh:
            fh.write(audio)
        p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", "16000", "-t", "15", wav],
                           capture_output=True, timeout=60)
        if p.returncode != 0:
            raise ValueError("Couldn't read the recording")
        with open(wav, "rb") as fh:
            data = fh.read()

    async def go():
        return await Shazam().recognize(data)
    res = asyncio.run(go()) or {}
    t = res.get("track") or {}
    if not t.get("title"):
        return {"found": False}
    album = ""
    for sec in t.get("sections") or []:
        for md in sec.get("metadata") or []:
            if md.get("title") == "Album":
                album = md.get("text", "")
    return {"found": True, "artist": t.get("subtitle", ""), "title": t["title"], "album": album,
            "cover": (t.get("images") or {}).get("coverarthq") or (t.get("images") or {}).get("coverart", ""),
            "shazam": t.get("url", "")}


# --------------------------------------------------------------------------- followed playlists
def save_follows():
    with LOCK:
        data = list(FOLLOWS.values())
    core._save_json("follows.json", data)


def list_for(url):
    kind, data = resolve(url)
    if kind != "list":
        raise ValueError("That link is a single song; follow a playlist instead")
    return data


def follow(url, name=""):
    pname, tracks = list_for(url)
    pname = core.safe(name or pname)
    with LOCK:
        for f in FOLLOWS.values():
            if f["url"] == url and core.users.mine(f):
                return f["id"]
    job = None
    for j in core.JOBS.values():                     # already have it (e.g. the old spotDL playlist)? use that one
        if j.get("user") in ((None, core.users.current_id()) if core.users.is_admin() else (core.users.current_id(),)) and os.path.splitext(os.path.basename(j["m3u"]))[0].lower() == pname.lower():
            job = j
    if job:
        job["sync"] = True
        have = {core.song_key(t["artist"], t["title"]) for t in job["tracks"]}
        new = [t for t in tracks if core.song_key(t["artist"], t["title"]) not in have]
        if new:
            core.add_tracks(job, [{"artist": t["artist"], "title": t["title"], "duration": t["duration"]} for t in new])
        jid = job["id"]
    else:
        jid = core.new_job(pname, "import", os.path.join(core.CONFIG["playlists"], pname + ".m3u8"),
                           [{"artist": t["artist"], "title": t["title"], "duration": t["duration"]} for t in tracks])
    fid = uuid.uuid4().hex[:10]
    with LOCK:
        FOLLOWS[fid] = {"id": fid, "url": url, "name": pname, "job": jid, "m3u": core.JOBS[jid]["m3u"], "created": time.time(),
                        "last_check": time.time(), "count": len(tracks), "added": 0, "error": "",
                        "owner": core.users.me_key()}
    save_follows()
    return fid


def check_follow(f):
    try:
        _n, tracks = list_for(f["url"])
        job = core.JOBS.get(f["job"]) or (core.playlists.job_for(f["m3u"]) if f.get("m3u") else None)
        if not job:
            raise ValueError("Its playlist job was removed")
        have = {core.song_key(t["artist"], t["title"]) for t in job["tracks"]}
        new = [t for t in tracks if core.song_key(t["artist"], t["title"]) not in have]
        if new:
            job["sync"] = True
            core.add_tracks(job, [{"artist": t["artist"], "title": t["title"], "duration": t["duration"]} for t in new])
            f["added"] = f.get("added", 0) + len(new)
            core.notify(f"{f['name']}: {len(new)} new songs added, fetching them now")
        f["count"], f["error"] = len(tracks), ""
    except Exception as e:  # noqa: BLE001
        f["error"] = str(e)[:200]
        log("followed playlist check failed:", f["name"], e)
    f["last_check"] = time.time()
    save_follows()


def follow_loop():
    while True:
        time.sleep(600)
        with LOCK:
            due = [f for f in FOLLOWS.values() if time.time() - f["last_check"] >= CFG["follow_hours"] * 3600]
        for f in due:
            check_follow(f)


# --------------------------------------------------------------------------- web
def handle_get(path, qs):
    if path == "/api/tokens":
        me = core.users.current()
        with LOCK:
            mine = [t for t in TOKENS.values() if core.users.is_admin(me) or t.get("user") == (me or {}).get("id")]
            return {"tokens": [{k: t[k] for k in ("id", "name", "created", "last_used")} for t in mine]}
    if path == "/api/share/recent":
        with LOCK:
            return {"shares": SHARES[:20]}
    if path == "/api/follows":
        with LOCK:
            return {"follows": sorted((f for f in FOLLOWS.values() if core.users.mine(f)), key=lambda f: f["name"].lower())}
    if path == "/api/share":                       # simple GET form for Shortcuts: /api/share?url=...
        return take(qs.get("url", qs.get("text", [""]))[0])
    return None


def handle_post(path, b):
    if path == "/api/share":
        return take(b.get("url") or b.get("text") or "", b.get("name", ""))
    if path == "/api/getsong":                       # "download this" from the player / a recognised song
        m = {k: b.get(k) for k in ("artist", "title", "version", "duration", "album", "cover", "cover_big", "deezer_id")}
        if not m["title"]:
            raise ValueError("Which song?")
        core.subsonic.as_person(core.users.current(), core.subsonic.auto_get, m, "you asked for it")
        return {"ok": True, "message": f"Getting {m['artist']} - {m['title']}"}
    if path == "/api/tokens":
        tid, tok = new_token(b.get("name", ""))
        return {"id": tid, "token": tok}
    m = re.match(r"^/api/tokens/([0-9a-f]+)/revoke$", path)
    if m:
        with LOCK:
            TOKENS.pop(m.group(1), None)
        save_tokens()
        return {"ok": True}
    if path == "/api/follows":
        return {"id": follow(b.get("url", ""), b.get("name", ""))}
    m = re.match(r"^/api/follows/([0-9a-f]+)/(check|remove)$", path)
    if m:
        f = FOLLOWS.get(m.group(1))
        if not core.users.mine(f):
            raise LookupError("unknown playlist")
        if m.group(2) == "check":
            core.POOL.submit(check_follow, f)
        else:
            with LOCK:
                FOLLOWS.pop(f["id"], None)
            save_follows()
        return {"ok": True}
    return None


def init(umculo):
    global core
    core = umculo
    for t in core._load_json("tokens.json", []):
        TOKENS[t["id"]] = t
    for f in core._load_json("follows.json", []):
        FOLLOWS[f["id"]] = f
    threading.Thread(target=follow_loop, daemon=True).start()
