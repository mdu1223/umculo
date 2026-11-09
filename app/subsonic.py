"""Umculo as a music server for Subsonic apps (Amperfy, Symfonium, play:Sub, substreamer...).

Point the app at Umculo instead of Navidrome, with your Navidrome username and password.
Everything is passed straight through to Navidrome, except that Umculo
  * adds songs, albums and artists from the internet to search results (marked "Internet"),
  * streams those songs straight away (from YouTube or SoundCloud, cached on the server),
  * downloads the best copy (per your quality profile) when you star one,
  * serves virtual playlists other parts of Umculo publish (the weekly mix in stream mode).
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

core = None
E = os.environ.get
CFG = {
    "internet_results": int(E("INTERNET_RESULTS", "10")),
    "cache_mb": int(E("STREAM_CACHE_MB", "3000")),
    "cache_days": int(E("STREAM_CACHE_DAYS", "14")),
    "stream_quality": E("STREAM_QUALITY", "320"),      # best | 320 | 256 | 128
    "stream_wait_s": int(E("STREAM_WAIT_SECONDS", "25")),
    "internet_kbps": int(E("INTERNET_KBPS", "320")),    # apps over the internet get at most this (0 = the file as it is)
}
NS = "http://subsonic.org/restapi"
API_VERSION = "1.16.1"
AUTH = {}                       # auth tuple -> valid until
META = {}                       # deezer id -> normalised track
LOCKS = {}                      # deezer id -> lock while it's being fetched for streaming
LOCKS_GUARD = threading.Lock()
STARRED = {}                    # deezer id -> request id (downloads started from the app)
PLAYS = {}                      # deezer id -> plays of the internet copy
PLAYLISTS = {}                  # name -> provider() returning {"id","name","comment","songs":[deezer items]}
ON_PLAY = []                    # callbacks(meta, plays) - the weekly mix uses this


def log(*a):
    core.log(*a)


# --------------------------------------------------------------------------- responses
def to_xml(parent, key, value):
    if isinstance(value, list):
        for v in value:
            to_xml(parent, key, v)
        return
    el = ET.SubElement(parent, key)
    if isinstance(value, dict):
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                to_xml(el, k, v)
            elif k == "value":
                el.text = str(v)
            elif v is not None:
                el.set(k, ("true" if v else "false") if isinstance(v, bool) else str(v))
    elif value is not None:
        el.text = str(value)


def render(h, body, fmt, status=200):
    """body = the inside of "subsonic-response" (JSON convention), sent as JSON or XML."""
    body = {"status": "ok", "version": API_VERSION, "type": "umculo", "serverVersion": core.VERSION,
            "openSubsonic": True, **body}
    if fmt in ("json", "jsonp"):
        data = json.dumps({"subsonic-response": body}).encode()
        ctype = "application/json"
    else:
        root = ET.Element("subsonic-response", {"xmlns": NS})
        for k, v in body.items():
            if isinstance(v, (dict, list)):
                to_xml(root, k, v)
            elif v is not None:
                root.set(k, ("true" if v else "false") if isinstance(v, bool) else str(v))
        data = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root)
        ctype = "text/xml; charset=utf-8"
    h.send_response(status)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(data)))
    h.end_headers()
    h.wfile.write(data)


def error(h, fmt, code, msg):
    render(h, {"status": "failed", "error": {"code": code, "message": msg}}, fmt)


# --------------------------------------------------------------------------- Navidrome pass-through
AUTH_KEYS = ("u", "p", "t", "s", "apiKey")


def nd_url(path, query):
    return f"{core.CONFIG['navidrome']}{path}" + (f"?{query}" if query else "")


def authed(params):
    if core.builtin():
        try:
            core.users.LOCAL.user = core.server.check_login(params)      # whose playlists, stars and downloads
            return True
        except core.server.ApiError:
            return False
    key = tuple((k, params.get(k, [""])[0]) for k in AUTH_KEYS)
    if AUTH.get(key, 0) > time.time():
        return True
    q = {k: v for k, v in key if v}
    q.update(v=params.get("v", [API_VERSION])[0], c=params.get("c", ["umculo"])[0], f="json")
    try:
        res = core.http("GET", nd_url("/rest/ping", urllib.parse.urlencode(q)), timeout=15, retries=1)
        ok = ((res or {}).get("subsonic-response") or {}).get("status") == "ok"
    except Exception as e:  # noqa: BLE001
        log("auth check against Navidrome failed:", e)
        ok = False
    if ok:
        AUTH[key] = time.time() + 900
    return ok


def proxy(h, method, path, query, body=None):
    """Forward the request to Navidrome as it is and stream the answer back (keeps Range for seeking)."""
    req = urllib.request.Request(nd_url(path, query), data=body, method=method)
    for k in ("Range", "User-Agent", "Accept", "Content-Type", "If-None-Match", "If-Modified-Since"):
        if h.headers.get(k):
            req.add_header(k, h.headers[k])
    try:
        r = urllib.request.urlopen(req, timeout=120)
        status = r.status
    except urllib.error.HTTPError as e:
        r, status = e, e.code
    except Exception as e:  # noqa: BLE001
        log("navidrome unreachable:", e)
        h.send_response(502)
        h.send_header("Content-Length", "0")
        h.end_headers()
        return
    h.send_response(status)
    for k, v in r.headers.items():
        if k.lower() not in ("transfer-encoding", "connection", "server", "date"):
            h.send_header(k, v)
    h.end_headers()
    try:
        while True:
            chunk = r.read(65536)
            if not chunk:
                break
            h.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError):
        pass
    finally:
        r.close()


def nd_json(endpoint, params):
    if core.builtin():
        try:
            u = core.server.check_login(params)
        except core.server.ApiError as e:
            return {"status": "failed", "error": {"code": e.code, "message": str(e)}}
        rest = {k: v for k, v in params.items() if k not in ("u", "p", "t", "s", "apiKey", "f", "v", "c")}
        return core.server.as_user(u, endpoint, **rest)
    q = {k: v for k, v in params.items()}
    q["f"] = ["json"]
    res = core.http("GET", nd_url(f"/rest/{endpoint}", urllib.parse.urlencode(q, doseq=True)), timeout=30, retries=1)
    return (res or {}).get("subsonic-response") or {}


# --------------------------------------------------------------------------- internet catalogue (Deezer)
def dz(path):
    return core.http("GET", f"{core.CONFIG['deezer']}{path}", timeout=20) or {}


def norm_track(d, album=None):
    alb = d.get("album") or album or {}
    art = d.get("artist") or {}
    item = {"deezer_id": d.get("id"), "title": d.get("title") or "", "version": d.get("title_version") or "",
            "artist": art.get("name", ""), "artist_id": art.get("id"), "album": alb.get("title", ""),
            "album_id": alb.get("id"), "cover": alb.get("cover_medium", ""),
            "cover_big": alb.get("cover_xl") or alb.get("cover_big", ""), "duration": d.get("duration") or 0,
            "track": d.get("track_position"), "year": (alb.get("release_date") or "")[:4] or None}
    if item["deezer_id"]:
        META[str(item["deezer_id"])] = item
    return item


def meta(did):
    did = str(did)
    if did not in META:
        META[did] = norm_track(dz(f"/track/{did}"))
    m = META.get(did)
    if not m or not m.get("title"):
        raise LookupError("song not found")
    return m


def remember(items):
    for it in items:
        if it.get("deezer_id"):
            META[str(it["deezer_id"])] = it
    return items


def vsong(it):
    did = it["deezer_id"]
    title = it["title"] + (f" ({it['version']})" if it.get("version") and it["version"].lower() not in it["title"].lower() else "")
    dur = int(it.get("duration") or 0)
    s = {"id": f"umc-dz-{did}", "isDir": False, "title": title, "album": f"{it.get('album') or 'Internet'}",
         "artist": it["artist"], "coverArt": f"umc-dz-{did}", "duration": dur,
         "bitRate": 320 if CFG["stream_quality"] == "best" else int(CFG["stream_quality"]),
         "suffix": "mp3", "contentType": "audio/mpeg", "size": dur * 40000, "isVideo": False, "type": "music",
         "path": f"Internet/{core.safe(it['artist'])} - {core.safe(title)}.mp3",
         "comment": "Streams from the internet. Star it to download the best copy to your library.",
         "genre": "Internet"}
    if it.get("album_id"):
        s["parent"] = s["albumId"] = f"umc-al-{it['album_id']}"
    if it.get("artist_id"):
        s["artistId"] = f"umc-ar-{it['artist_id']}"
    if it.get("track"):
        s["track"] = it["track"]
    if it.get("year"):
        s["year"] = it["year"]
    if str(did) in STARRED:
        s["starred"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return s


def valbum(a, artist=None):
    art = a.get("artist") or artist or {}
    return {"id": f"umc-al-{a['id']}", "name": a.get("title", ""), "title": a.get("title", ""),
            "artist": art.get("name", ""), "artistId": f"umc-ar-{art['id']}" if art.get("id") else None,
            "coverArt": f"umc-al-{a['id']}", "songCount": a.get("nb_tracks") or 0,
            "duration": a.get("duration") or 0, "isDir": True, "genre": "Internet",
            "year": (a.get("release_date") or "")[:4] or None, "created": a.get("release_date")}


def vartist(a):
    return {"id": f"umc-ar-{a['id']}", "name": a.get("name", ""), "coverArt": f"umc-ar-{a['id']}",
            "albumCount": a.get("nb_album") or 0}


def search3(h, params, fmt):
    res = nd_json("search3", params)
    if res.get("status") != "ok":
        return render(h, res, fmt)
    sr = res.get("searchResult3") or {}
    q = (params.get("query", [""])[0] or "").strip().strip('"')
    want = CFG["internet_results"]
    if q and len(q) >= 2 and want:
        try:
            add_internet(sr, q, params)
        except Exception as e:  # noqa: BLE001
            log("internet search failed:", e)
    res["searchResult3"] = sr
    res.pop("type", None)
    res.pop("serverVersion", None)
    render(h, res, fmt)


def add_internet(sr, q, params):
    num = lambda k, d: int((params.get(k) or [d])[0] or d)   # noqa: E731
    songs = sr.get("song") or []
    if num("songCount", 20) > 0 and num("songOffset", 0) == 0:
        library = [{"artist": s.get("artist", ""), "title": s.get("title", "")} for s in songs]
        extra = []
        for it in remember(core.deezer_search(q, limit=25)):
            if not core.owned_in(library, it):
                extra.append(vsong(it))
            if len(extra) >= CFG["internet_results"]:
                break
        sr["song"] = songs + extra
    if num("albumCount", 20) > 0 and num("albumOffset", 0) == 0:
        have = {core.norm(a.get("name") or a.get("title") or "") for a in sr.get("album") or []}
        data = dz("/search/album?limit=8&q=" + urllib.parse.quote(q)).get("data") or []
        sr["album"] = (sr.get("album") or []) + [valbum(a) for a in data
                                                 if core.norm(a.get("title", "")) not in have][:5]
    if num("artistCount", 20) > 0 and num("artistOffset", 0) == 0:
        have = {core.norm(a.get("name", "")) for a in sr.get("artist") or []}
        data = dz("/search/artist?limit=5&q=" + urllib.parse.quote(q)).get("data") or []
        sr["artist"] = (sr.get("artist") or []) + [vartist(a) for a in data if core.norm(a.get("name", "")) not in have][:3]


def get_album(did):
    a = dz(f"/album/{did}")
    if not a.get("id"):
        raise LookupError("album not found")
    tracks = [vsong(norm_track(t, a)) for t in (a.get("tracks") or {}).get("data") or []]
    out = valbum(a)
    out["songCount"] = len(tracks)
    out["song"] = tracks
    return out


def get_artist(did):
    a = dz(f"/artist/{did}")
    if not a.get("id"):
        raise LookupError("artist not found")
    albums = dz(f"/artist/{did}/albums?limit=50").get("data") or []
    out = vartist(a)
    out["album"] = [valbum(x, a) for x in albums]
    out["albumCount"] = len(albums)
    return out


def cover_url(vid):
    kind, did = vid.split("-")[1], vid.split("-", 2)[2]
    if kind == "dz":
        m = meta(did)
        return m.get("cover_big") or m.get("cover")
    if kind == "al":
        a = dz(f"/album/{did}")
        return a.get("cover_xl") or a.get("cover_big") or a.get("cover_medium")
    if kind == "ar":
        a = dz(f"/artist/{did}")
        return a.get("picture_xl") or a.get("picture_big") or a.get("picture_medium")
    if kind == "pl":
        p = PLAYLISTS.get(did)
        p = p() if p else None
        return p.get("cover") if own_playlist(p) else None
    return None


# --------------------------------------------------------------------------- streaming internet songs
def cache_dir():
    d = os.path.join(core.CONFIG["staging"], "stream")
    os.makedirs(d, exist_ok=True)
    return d


def cached(did):
    for ext in ("m4a", "mp3"):
        p = os.path.join(cache_dir(), f"{did}.{ext}")
        if os.path.exists(p):
            return p
    return None


def local_copy(m):
    hit = core.index_get(m["artist"], m["title"])
    return hit["path"] if hit else None


def find_stream(m):
    """Fastest good-enough copy to play right now: YouTube first, SoundCloud second."""
    if E("UMCULO_TEST_STREAM_URL"):
        return E("UMCULO_TEST_STREAM_URL")
    target = core.make_target(m["artist"], m["title"], m.get("version", ""), m.get("duration") or 0)
    q = f"{m['artist']} {m['title']}" + (f" {m['version']}" if m.get("version") else "")
    best = None
    for prefix in ("ytsearch6:", "scsearch6:"):
        try:
            with core._ydl({"extract_flat": "in_playlist"}) as y:
                res = y.extract_info(prefix + q, download=False) or {}
        except Exception as e:  # noqa: BLE001
            log("stream search failed:", prefix, e)
            continue
        for e in res.get("entries") or []:
            text = f"{e.get('title', '')} {e.get('uploader') or e.get('channel') or ''}"
            song_ok, exact = core.match(text, target)
            if not song_ok:
                continue
            dur = int(e.get("duration") or 0)
            dur_ok = not (target["duration"] and dur) or abs(dur - target["duration"]) <= 20
            score = (exact, dur_ok)
            url = e.get("url") or e.get("webpage_url")
            if url and (not best or score > best[0]):
                best = (score, url)
            if exact and dur_ok:
                return url
        if best and best[0][0]:
            return best[1]
    return best[1] if best else None


def src_path(did):
    for f in os.listdir(cache_dir()):
        if f.startswith(f"src.{did}."):
            return os.path.join(cache_dir(), f)
    return None


def cached(did):
    """Any playable copy already on the server (kept for the web page)."""
    return src_path(did)


def want_score():
    q = CFG["stream_quality"]
    return 10000 if q == "best" else 315 if q == "320" else 0


def quick_soulseek(m, need, wait_s, did):
    """Try to get a high-quality copy from Soulseek within `wait_s` seconds (free upload slots only)."""
    deadline = time.time() + wait_s
    target = core.make_target(m["artist"], m["title"], m.get("version", ""), m.get("duration") or 0)
    q = core.query_variants(target)[0]
    found = []
    try:
        responses = core._slskd_search(q, seconds=max(5, min(10, wait_s // 3)))
    except Exception as e:  # noqa: BLE001
        log("quick soulseek search failed:", e)
        return None
    for r in responses or []:
        if not r.get("hasFreeUploadSlot"):
            continue
        speed = r.get("uploadSpeed") or 0
        for f in r.get("files") or []:
            name = f.get("filename", "")
            ext = name.rsplit(".", 1)[-1].lower()
            if ext not in core.AUDIO:
                continue
            ok, exact = core.match(name, target)
            if not exact:
                continue
            if target["duration"] and f.get("length") and abs(int(f["length"]) - target["duration"]) > 15:
                continue
            score, label, tier = core.quality(ext, f.get("bitRate"), f.get("bitDepth"), f.get("sampleRate"),
                                              f.get("isVariableBitRate"))
            if score < min(need, 315):
                continue
            size = f.get("size") or 0
            eta = size / speed if speed else 999
            found.append({"source": "soulseek", "exact": True, "score": score, "quality": label, "tier": tier,
                          "size": size, "eta_s": int(eta), "name": name,
                          "ref": {"username": r.get("username"), "filename": name, "size": size}})
    if need >= 10000:                                   # "best": lossless first, then the fastest
        found.sort(key=lambda c: (-min(c["score"], 10000), c["eta_s"]))
    else:
        found.sort(key=lambda c: (c["eta_s"], -c["score"]))
    for c in found[:2]:
        left = deadline - time.time()
        if left < 5 or c["eta_s"] > left:
            continue
        stop = threading.Event()
        box = {}
        req = {"id": "s" + did, "target": target, "candidate": c, "stall_s": 10, "stop": stop}

        def run():
            try:
                box["path"] = core.dl_soulseek(req)
            except Exception as e:  # noqa: BLE001
                box["error"] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(left)
        if t.is_alive():
            stop.set()
            t.join(20)
            continue
        if box.get("path"):
            ext = box["path"].rsplit(".", 1)[-1].lower()
            out = os.path.join(cache_dir(), f"src.{did}.{ext}")
            shutil.move(box["path"], out)
            FROM[did] = "Soulseek"
            log("stream from Soulseek:", m["artist"], "-", m["title"], c["quality"])
            return out
    return None


def fetch_youtube(m, did):
    url = find_stream(m)
    if not url:
        raise LookupError("Couldn't find this song to stream")
    tmp = os.path.join(cache_dir(), f".{did}")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        # Opus (~160 kbps) sounds better than YouTube's 128 kbps AAC; it's converted for players below.
        with core._ydl({"skip_download": False, "format": "bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio/best",
                        "outtmpl": os.path.join(tmp, "a.%(ext)s")}) as y:
            y.download([url])
        files = [os.path.join(tmp, f) for f in os.listdir(tmp) if not f.endswith(".part")]
        if not files:
            raise RuntimeError("stream download produced nothing")
        src = max(files, key=os.path.getsize)
        out = os.path.join(cache_dir(), f"src.{did}.{src.rsplit('.', 1)[-1].lower()}")
        os.replace(src, out)
        FROM[did] = "YouTube/SoundCloud"
        log("stream from the internet:", m["artist"], "-", m["title"])
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch_stream(did):
    """The quickest good copy (cached): YouTube/SoundCloud now, a better Soulseek copy found in the background
    for next time. Waiting for Soulseek first kept people listening to silence for 25 seconds."""
    with LOCKS_GUARD:
        lock = LOCKS.setdefault(did, threading.Lock())
    with lock:
        hit = src_path(did)
        need = want_score()
        if hit and (core.file_score(hit) >= min(need, 315) or not need or TRIED.get(did, 0) > time.time() - 86400):
            return hit
        m = meta(did)
        better = need and "soulseek" in core.CONFIG["sources"]
        if hit:
            if better:
                upgrade_later(m, need, did)
            return hit
        try:
            out = fetch_youtube(m, did)
        except Exception:                                  # not on YouTube/SoundCloud: Soulseek is all there is
            if not better:
                raise
            TRIED[did] = time.time()
            got = quick_soulseek(m, need, CFG["stream_wait_s"], did)
            if not got:
                raise
            return got
        if better:
            upgrade_later(m, need, did)
        trim_cache()
        return out


UPGRADING = set()


def upgrade_later(m, need, did):
    """Look for a better copy on Soulseek without making anyone wait; the next play uses it."""
    if did in UPGRADING or TRIED.get(did, 0) > time.time() - 86400:
        return
    UPGRADING.add(did)
    TRIED[did] = time.time()

    def go():
        try:
            old = src_path(did)
            got = quick_soulseek(m, need, CFG["stream_wait_s"] * 4, did)
            if got and old and old != got and os.path.exists(old):
                os.remove(old)
        except Exception as e:  # noqa: BLE001
            log("better stream copy failed:", e)
        finally:
            UPGRADING.discard(did)
    core.POOL.submit(go)


TRIED = {}
FROM = {}                       # deezer id -> where the cached source came from
SENT = {}                       # deezer id -> (path sent, note) for the web player's quality label
PLAYABLE = ("mp3", "m4a", "aac", "flac", "wav")


def note_internet(did, t0, cached, client, path):
    """Measure internet songs too: how long until they started."""
    try:
        m = meta(did)
        core.server.note_stream({"who": (core.users.current() or {}).get("username", ""), "client": client,
                                 "song": f"{m.get('artist')} - {m.get('title')}", "from": "internet",
                                 "sent": path.rsplit(".", 1)[-1].upper(), "mode": "internet, ready" if cached else "internet, fetched",
                                 "start_ms": int((time.time() - t0) * 1000), "range": ""})
    except Exception:  # noqa: BLE001
        pass


def deliver(did, max_kbps=0, raw=False):
    """Path to send to the player: the source as-is when it fits, otherwise an MP3 at the right bitrate."""
    m = meta(did)
    local = local_copy(m)
    src = local if local and os.path.exists(local) else fetch_stream(did)
    out = _deliver(did, src, max_kbps, raw)
    origin = "Your library" if src == local else FROM.get(did) or "Internet"
    SENT[did] = (out, origin if out == src else f"{origin}, converted from {core.file_quality(src)[1].replace(' ?', '').strip()}")
    return out


def stream_info(did):
    path, note = SENT.get(did) or ("", "")
    if not path or not os.path.exists(path):
        return {"quality": "", "from": ""}
    tier, label = core.file_quality(path)
    return {"quality": label, "tier": tier, "from": note}


def _deliver(did, src, max_kbps, raw):
    q = CFG["stream_quality"]
    kbps = None if q == "best" else int(q)
    if max_kbps and (kbps is None or max_kbps < kbps):
        kbps = max_kbps
    ext = src.rsplit(".", 1)[-1].lower()
    if raw and ext in PLAYABLE:
        return src
    if kbps is None and ext in PLAYABLE:
        return src
    score = core.file_score(src)
    if kbps and ext in ("mp3", "m4a", "aac") and score <= kbps * 1.08:
        return src
    kbps = kbps or 320
    out = os.path.join(cache_dir(), f"v{kbps}.{did}.mp3")
    if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src):
        return out
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a",
                        f"{kbps}k", out], capture_output=True, timeout=600)
    if p.returncode != 0:
        raise RuntimeError("couldn't prepare the stream: " + p.stderr.decode()[-200:])
    return out


def trim_cache():
    files = []
    for f in os.listdir(cache_dir()):
        p = os.path.join(cache_dir(), f)
        if os.path.isfile(p):
            files.append((os.path.getmtime(p), os.path.getsize(p), p))
    files.sort()
    total = sum(f[1] for f in files)
    for mtime, size, p in files:
        if total <= CFG["cache_mb"] * 1_000_000 and time.time() - mtime < CFG["cache_days"] * 86400:
            break
        try:
            os.remove(p)
            total -= size
        except OSError:
            pass


def serve_file(h, path, ctype=None):
    size = os.path.getsize(path)
    ctype = ctype or {"m4a": "audio/mp4", "mp3": "audio/mpeg", "flac": "audio/flac", "ogg": "audio/ogg",
                      "opus": "audio/ogg", "wav": "audio/wav", "aac": "audio/aac"}.get(path.rsplit(".", 1)[-1].lower(),
                                                                                      "application/octet-stream")
    start, end = 0, size - 1
    rng = re.match(r"bytes=(\d*)-(\d*)", h.headers.get("Range") or "")
    if rng and (rng.group(1) or rng.group(2)):
        if rng.group(1):
            start = int(rng.group(1))
            end = int(rng.group(2)) if rng.group(2) else size - 1
        else:
            start = max(0, size - int(rng.group(2)))
        end = min(end, size - 1)
        h.send_response(206)
        h.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    else:
        h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Accept-Ranges", "bytes")
    h.send_header("Content-Length", str(end - start + 1))
    h.end_headers()
    if h.command == "HEAD":
        return
    with open(path, "rb") as fh:
        fh.seek(start)
        left = end - start + 1
        try:
            while left > 0:
                chunk = fh.read(min(65536, left))
                if not chunk:
                    break
                h.wfile.write(chunk)
                left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


# --------------------------------------------------------------------------- stars, plays, downloads
def save_state():
    core._save_json("subsonic.json", {"starred": STARRED, "plays": PLAYS})


def as_person(u, fn, *a):
    """Run fn in the background as this person: their folder, their queue."""
    def go():
        core.users.LOCAL.user = u
        try:
            return fn(*a)
        finally:
            core.users.LOCAL.user = None
    return core.POOL.submit(go)


def star(did):
    """Starring an internet song downloads the best copy (per the quality profile), if that person may download."""
    did = str(did)
    u = core.users.current()
    m = meta(did)
    if u and not core.users.is_admin(u):
        core.users.fetch_for(u, m, "starred in your app")     # yes: theirs to keep; ask: you decide; off: just a star
        return
    if did in STARRED:
        return
    STARRED[did] = "pending"
    save_state()
    as_person(u, auto_get, m)


def auto_get(m, why="starred in your app"):
    """Search every source for this song and request the best copy, like 'Get best available'."""
    try:
        if local_copy(m):
            STARRED[str(m["deezer_id"])] = "owned"
            return
        target = core.make_target(m["artist"], m["title"], m.get("version", ""), m.get("duration") or 0,
                                  m.get("album", ""), m.get("cover_big") or m.get("cover", ""), m.get("deezer_id"))
        src = src_path(str(m["deezer_id"])) if m.get("deezer_id") else None
        if src and core.good_enough(core.file_score(src)):        # already streamed a good copy: keep that one
            tier, label = core.file_quality(src)
            req = {"id": "k" + str(m["deezer_id"]), "target": target,
                   "candidate": {"source": "soulseek", "quality": label, "tier": tier, "score": core.file_score(src),
                                 "exact": True, "name": os.path.basename(src)}}
            os.makedirs(os.path.join(core.CONFIG["staging"], req["id"]), exist_ok=True)
            copy = os.path.join(core.CONFIG["staging"], req["id"], os.path.basename(src))
            shutil.copy2(src, copy)
            final = core.file_into_library(copy, req)
            core.index_add(target, final, req["candidate"])
            core.request_scan()
            STARRED[str(m["deezer_id"])] = "owned"
            core.notify(f"Saved {m['artist']} - {m['title']} ({req['candidate']['quality']})")
            return
        cid = core.start_check(target)
        deadline = time.time() + core.CONFIG["search_seconds"] + 150
        while time.time() < deadline:
            time.sleep(3)
            view = core.check_view(core.CHECKS[cid])
            if view["done"]:
                break
        cands = core.CHECKS[cid]["candidates"]
        picks = core.pick_auto(cands, -1, 1)
        if not picks:
            exact = [c for c in cands if c["exact"]]
            picks = [max(exact, key=core.rank)] if exact else []
        if not picks:
            STARRED[str(m["deezer_id"])] = "not found"
            core.notify(f"Couldn't find a copy of {m['artist']} - {m['title']} to download")
            return
        rid = core.start_request(target, picks[0])
        STARRED[str(m["deezer_id"])] = rid
        log("downloading", m["artist"], "-", m["title"], "because it was", why)
    except Exception as e:  # noqa: BLE001
        log("auto download failed:", e)
        STARRED[str(m["deezer_id"])] = "failed"
    finally:
        save_state()


def played(did):
    did = str(did)
    PLAYS[did] = PLAYS.get(did, 0) + 1
    save_state()
    for cb in ON_PLAY:
        try:
            cb(meta(did), PLAYS[did])
        except Exception as e:  # noqa: BLE001
            log("play hook failed:", e)


# --------------------------------------------------------------------------- virtual playlists
def viewer():
    return core.server.uid(core.users.current()) if core.builtin() else "admin"


def own_playlist(p):
    """Umculo's playlists (recipes, the weekly mix) belong to whoever made them."""
    return bool(p) and (p.get("owner") or "admin") == viewer()


def playlist_entries():
    out = []
    for pid, provider in PLAYLISTS.items():
        try:
            p = provider()
        except Exception as e:  # noqa: BLE001
            log("virtual playlist failed:", pid, e)
            continue
        if p and p.get("songs") and own_playlist(p):
            out.append((pid, p))
    return out


def vplaylist(pid, p, with_songs=False):
    songs = [vsong(remember([s])[0]) if s.get("deezer_id") else s for s in p["songs"]]
    out = {"id": f"umc-pl-{pid}", "name": p["name"], "comment": p.get("comment", ""), "owner": "umculo",
           "public": False, "songCount": len(songs), "duration": sum(s.get("duration") or 0 for s in songs),
           "created": p.get("created"), "changed": p.get("changed"), "coverArt": f"umc-pl-{pid}"}
    if with_songs:
        out["entry"] = songs
    return out


# --------------------------------------------------------------------------- dispatcher
def handle(h, method):
    u = urllib.parse.urlparse(h.path)
    body = None
    if method == "POST":
        n = int(h.headers.get("Content-Length") or 0)
        body = h.rfile.read(n) if n else b""
    params = urllib.parse.parse_qs(u.query, keep_blank_values=True)
    if body and "x-www-form-urlencoded" in (h.headers.get("Content-Type") or ""):
        for k, v in urllib.parse.parse_qs(body.decode(), keep_blank_values=True).items():
            params.setdefault(k, []).extend(v)
    endpoint = u.path[len("/rest/"):].split("/")[0]
    endpoint = endpoint[:-5] if endpoint.endswith(".view") else endpoint
    fmt = params.get("f", ["xml"])[0]
    if core.builtin():
        core.server.set_request(h, params)
    vid = params.get("id", [""])[0]
    ours = vid.startswith("umc-") or endpoint in ("search3", "getPlaylists", "star", "unstar", "scrobble")
    if not ours and core.builtin():
        return core.server.handle(h, endpoint, params, fmt)
    if not ours:
        return proxy(h, method, u.path, urllib.parse.urlencode(params, doseq=True) if body else u.query,
                     None if body is not None and "x-www-form-urlencoded" in (h.headers.get("Content-Type") or "") else body)
    if not authed(params):
        return error(h, fmt, 40, "Wrong username or password")
    try:
        if endpoint == "search3":
            return search3(h, params, fmt)
        if endpoint == "getPlaylists":
            res = nd_json("getPlaylists", params)
            pls = (res.get("playlists") or {}).get("playlist") or []
            res["playlists"] = {"playlist": [vplaylist(pid, p) for pid, p in playlist_entries()] + pls}
            res.pop("type", None)
            return render(h, res, fmt)
        if endpoint in ("star", "unstar", "scrobble"):
            ids = params.get("id", []) + params.get("albumId", []) + params.get("artistId", [])
            mine = [i for i in ids if i.startswith("umc-dz-")]
            if endpoint == "star":
                for i in mine:
                    star(i[7:])
            elif endpoint == "scrobble" and params.get("submission", ["true"])[0] != "false":
                for i in mine:
                    played(i[7:])
            rest = {k: [x for x in v if not x.startswith("umc-")] if k in ("id", "albumId", "artistId") else v
                    for k, v in params.items()}
            if any(rest.get(k) for k in ("id", "albumId", "artistId")):
                res = nd_json(endpoint, rest)
                if res.get("status") != "ok":
                    return render(h, res, fmt)
            return render(h, {}, fmt)
        kind = vid.split("-")[1] if vid.count("-") >= 2 else ""
        did = vid.split("-", 2)[2] if kind else ""
        if endpoint in ("stream", "download") and kind == "dz":
            mk = params.get("maxBitRate", ["0"])[0]
            t0, cached = time.time(), bool(src_path(did))
            path = deliver(did, int(mk) if mk.isdigit() else 0,
                           raw=endpoint == "download" or params.get("format", [""])[0] == "raw")
            os.utime(path, None) if path.startswith(cache_dir()) else None
            note_internet(did, t0, cached, params.get("c", [""])[0], path)
            return serve_file(h, path)
        if endpoint == "getCoverArt":
            url = cover_url(vid)
            if not url:
                raise LookupError("no cover")
            data, hdr = core.http("GET", url, raw=True, timeout=20, retries=1)
            h.send_response(200)
            h.send_header("Content-Type", hdr.get("Content-Type") or "image/jpeg")
            h.send_header("Content-Length", str(len(data)))
            h.send_header("Cache-Control", "max-age=604800")
            h.end_headers()
            return h.wfile.write(data)
        if endpoint == "getSong" and kind == "dz":
            return render(h, {"song": vsong(meta(did))}, fmt)
        if endpoint in ("getAlbum", "getMusicDirectory") and kind == "al":
            al = get_album(did)
            if endpoint == "getMusicDirectory":
                return render(h, {"directory": {"id": al["id"], "name": al["name"], "child": al["song"]}}, fmt)
            return render(h, {"album": al}, fmt)
        if endpoint in ("getArtist", "getMusicDirectory") and kind == "ar":
            ar = get_artist(did)
            if endpoint == "getMusicDirectory":
                return render(h, {"directory": {"id": ar["id"], "name": ar["name"], "child": ar["album"]}}, fmt)
            return render(h, {"artist": ar}, fmt)
        if endpoint == "getPlaylist" and kind == "pl":
            p = PLAYLISTS.get(did)
            p = p() if p else None
            if not own_playlist(p):
                raise LookupError("playlist not found")
            return render(h, {"playlist": vplaylist(did, p, True)}, fmt)
        if endpoint in ("getAlbumInfo", "getAlbumInfo2"):
            return render(h, {"albumInfo": {}}, fmt)
        if endpoint in ("getArtistInfo", "getArtistInfo2"):
            return render(h, {"artistInfo2" if endpoint.endswith("2") else "artistInfo": {}}, fmt)
        if endpoint in ("getLyrics", "getLyricsBySongId"):
            return render(h, {"lyrics": {}}, fmt)
        if endpoint in ("getSimilarSongs", "getSimilarSongs2", "getTopSongs"):
            return render(h, {"similarSongs2" if endpoint.endswith("2") else "similarSongs": {}}, fmt)
        return error(h, fmt, 70, "Not available for internet items")
    except LookupError as e:
        return error(h, fmt, 70, str(e))
    except Exception as e:  # noqa: BLE001
        log("subsonic", endpoint, "failed:", e)
        return error(h, fmt, 0, str(e)[:200])


def settings_view():
    return {"stream_quality": CFG["stream_quality"], "stream_wait_s": CFG["stream_wait_s"], "internet_kbps": CFG["internet_kbps"]}


def apply_setting(k, v):
    if k == "stream_quality" and str(v) in ("best", "320", "256", "128"):
        CFG["stream_quality"] = str(v)
        return True
    if k == "stream_wait_s":
        CFG["stream_wait_s"] = max(0, min(90, int(v)))
        return True
    if k == "internet_kbps" and str(v) in ("0", "320", "256", "192", "128"):
        CFG["internet_kbps"] = int(v)
        return True
    return False


def init(umculo):
    global core
    core = umculo
    st = core._load_json("subsonic.json", {})
    STARRED.update(st.get("starred") or {})
    PLAYS.update(st.get("plays") or {})
