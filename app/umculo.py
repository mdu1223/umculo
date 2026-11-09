#!/usr/bin/env python3
"""Umculo - search it, see it, get it, keep it.

Phase 1: one web page that searches Deezer's catalogue and your Navidrome
library, checks Soulseek, Usenet, SoundCloud and YouTube for a song (with
format, bitrate, size and wait shown up front), downloads the one you pick,
tags it, files it into your library and asks Navidrome to rescan.

Standard library HTTP server + yt-dlp + mutagen. All settings come from
environment variables (see CONFIG below).
"""
import base64
import concurrent.futures as cf
import hashlib
import json
import mimetypes
import os
import random
import re
import shutil
import string
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # our own modules (python -I drops this)
import links  # noqa: E402
import subsonic  # noqa: E402
import foryou  # noqa: E402
import library  # noqa: E402
import status  # noqa: E402
import share  # noqa: E402
import playlists  # noqa: E402
import recipes  # noqa: E402
import artists  # noqa: E402
import users  # noqa: E402
import libview  # noqa: E402
import gzip  # noqa: E402
import musicdb  # noqa: E402
import server  # noqa: E402
import connectors  # noqa: E402
import selftest  # noqa: E402
import migrate  # noqa: E402
import listening  # noqa: E402
import importers  # noqa: E402
import security  # noqa: E402
import jellyfin  # noqa: E402

# --------------------------------------------------------------------------- config
VERSION = "2.3.1"
E = os.environ.get
CONFIG = {
    "port": int(E("PORT", "8095")),
    "user": E("UMCULO_USER", "admin"),
    "password": E("UMCULO_PASSWORD", ""),
    "navidrome": E("NAVIDROME_URL", "http://localhost:4533").rstrip("/"),
    "nd_user": E("NAVIDROME_USER", ""),
    "nd_password": E("NAVIDROME_PASSWORD", ""),
    "slskd": E("SLSKD_URL", "http://localhost:5030").rstrip("/"),
    "slskd_key": E("SLSKD_KEY", ""),
    "slskd_downloads": E("SLSKD_DOWNLOADS", "/data/Music/Soulseek"),
    "prowlarr": E("PROWLARR_URL", "http://localhost:9696").rstrip("/"),
    "prowlarr_key": E("PROWLARR_KEY", ""),
    "sab": E("SABNZBD_URL", "http://localhost:8081").rstrip("/"),
    "sab_key": E("SABNZBD_KEY", ""),
    "sab_category": E("SABNZBD_CATEGORY", ""),
    "library": E("LIBRARY_DIR", "/data/Music/Umculo"),
    "mixes": E("MIXES_DIR", "/data/Music/meta_podcasts"),
    "staging": E("STAGING_DIR", "/data/downloads/umculo"),
    "config": E("CONFIG_DIR", "/config"),
    "mix_seconds": int(E("MIX_THRESHOLD", "1200")),
    "search_seconds": int(E("SEARCH_SECONDS", "20")),
    "deezer": E("DEEZER_API", "https://api.deezer.com").rstrip("/"),
    "sources": [s.strip() for s in E("SOURCES", "soulseek,usenet,soundcloud,youtube").split(",") if s.strip()],
    "ntfy": E("NTFY_URL", ""),
    "music": E("MUSIC_DIR", "/data/Music"),
    "family": E("FAMILY_DIR", "/data/Family"),              # separate households: one folder per person
    "nd_music_path": E("NAVIDROME_MUSIC_PATH", "/music"),     # the music folder as Navidrome sees it
    "nd_family_path": E("NAVIDROME_FAMILY_PATH", "/family"),  # the family folder as Navidrome sees it
    "navidrome_public": E("NAVIDROME_PUBLIC_URL", ""),        # the address people put in their music app
    "share_scan_wait": int(E("SHARE_SCAN_WAIT", "20")),
    "playlists": E("PLAYLISTS_DIR", "/data/Music/_playlists"),
    "spotify_dir": E("SPOTIFY_DIR", "/data/Music/Spotify Playlists"),
    "sab_complete": E("SAB_COMPLETE_DIR", ""),
    "batch_workers": int(E("BATCH_WORKERS", "3")),
    "batch_sources": [s.strip() for s in E("BATCH_SOURCES", "soulseek,usenet,soundcloud,youtube").split(",") if s.strip()],
    "usenet_max_mb": int(E("USENET_MAX_MB", "150")),
    "retry_days": int(E("RETRY_DAYS", "7")),
    "slsk_stall_s": int(E("SLSK_STALL_SECONDS", "600")),
    "stream_after_s": int(E("STREAM_FALLBACK_SECONDS", "300")),
    "slsk_empty_limit": int(E("SLSK_EMPTY_LIMIT", "8")),
    "slsk_pause_s": int(E("SLSK_PAUSE_SECONDS", "900")),
    "slsk_gap_s": float(E("SLSK_GAP_SECONDS", "3")),
    "quality": E("QUALITY_PROFILE", "best"),          # best | 320 | 256 | 128 (changeable in Settings)
    "best_effort": E("BEST_EFFORT", "1") not in ("0", "false", "no"),   # take a lower copy when yours isn't there
    "music_server": E("MUSIC_SERVER", "builtin"),   # navidrome | builtin (Umculo serves your apps itself)
    "scan_minutes": int(E("SCAN_MINUTES", "15")),
    "public_url": E("PUBLIC_URL", ""),                # e.g. https://music.example.com (for artist pictures in apps)
    "static": E("STATIC_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")),
}

AUDIO = {"flac", "mp3", "m4a", "aac", "ogg", "opus", "wav", "aiff", "aif", "alac", "wma"}
LOSSLESS = {"flac", "wav", "aiff", "aif", "alac"}
GENERIC = {"feat", "ft", "featuring", "mix", "remix", "original", "bootleg", "dub", "edit",
           "extended", "radio", "version", "the", "and", "x", "s", "vs", "a",
           "dj", "djs", "deejay", "sa", "hd", "mr"}


def plural(n, word):
    """plural(1, "song") -> "1 song"; plural(3, "song") -> "3 songs"."""
    return f"{n:,} {word}" + ("" if n == 1 else "es" if word.endswith(("x", "ch")) else "s")


def log(*a):
    line = " ".join(str(x) for x in a)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), line, flush=True)
    try:
        status.record(line)
    except NameError:
        pass


# --------------------------------------------------------------------------- text matching
def norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"['’]s\b", "", text)
    text = text.replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def tokens(text):
    return [t for t in norm(text).split() if len(t) > 1 or t.isdigit()]


def split_artists(artist):
    return [n.strip() for n in re.split(r"\s+(?:x|&|and|feat\.?|ft\.?|vs\.?)\s+|,", artist or "", flags=re.I) if n.strip()]


def make_target(artist, title, version="", duration=0, album="", cover="", deezer_id=None):
    """Everything the matchers need about the song being looked for."""
    full_title = title.strip()
    if version and version.lower() not in full_title.lower():
        full_title = f"{full_title} ({version})"
    core = re.split(r"[\(\[]", full_title, maxsplit=1)[0].strip()
    brackets = re.findall(r"[\(\[]([^\)\]]*)[\)\]]", full_title)
    vtok = []
    for b in brackets:
        if re.match(r"^(feat|ft|featuring)\b", norm(b)):
            continue
        vtok += [t for t in tokens(b) if t not in GENERIC]
    names = split_artists(artist)
    return {
        "artist": artist.strip(), "title": full_title, "core": core,
        "core_tokens": [t for t in tokens(core) if t not in {"the", "a"}],
        "version_tokens": vtok,
        "first_artist": names[0] if names else "",
        "artists": [c for c in (norm(n).replace(" ", "") for n in names) if len(c) >= 3],
        "duration": int(duration or 0), "album": album or "", "cover": cover or "",
        "deezer_id": deezer_id,
    }


def parse_free_text(q):
    """'Artist - Title (Version)' typed by the user -> target."""
    q = q.strip()
    if " - " in q:
        artist, title = q.split(" - ", 1)
    else:
        artist, title = "", q
    return make_target(artist, title)


def resolve_free_text(q):
    """'FreakSA Ride' has no ' - ', so ask the catalogue who the artist is.
    Falls back to the plain parse when nothing in the catalogue fits the words typed."""
    q = q.strip()
    if " - " in q:
        return parse_free_text(q)
    words = set(tokens(q))
    compact = norm(q).replace(" ", "")
    version = ""
    m = re.search(r"[\(\[]([^\)\]]+)[\)\]]", q)
    if m:
        version = m.group(1)
    try:
        hits = deezer_search(re.sub(r"[\(\[].*?[\)\]]", " ", q), limit=10)
    except Exception as e:  # noqa: BLE001
        log("free-text lookup failed:", e)
        hits = []
    for d in hits:
        artist_ok = any(a in compact for a in
                        [c for c in (norm(n).replace(" ", "") for n in split_artists(d["artist"])) if len(c) >= 3])
        title_tokens = [t for t in tokens(re.split(r"[\(\[]", d["title"])[0]) if t not in {"the", "a"}]
        if artist_ok and title_tokens and all(_has(t, words, compact) for t in title_tokens):
            return make_target(d["artist"], d["title"], d["version"] or version, d["duration"],
                               d["album"], d.get("cover_big") or d["cover"], d["deezer_id"])
    return parse_free_text(q)


def _has(tok, words, compact):
    return tok in words or (len(tok) >= 4 and tok in compact)


def match(text, t):
    """-> (song_matches, exact_version_matches)."""
    words = set(tokens(text))
    compact = norm(text).replace(" ", "")
    core = t["core_tokens"]
    core_ok = bool(core) and all(_has(tok, words, compact) for tok in core)
    if core_ok and t["artists"]:
        core_ok = any(a in compact for a in t["artists"])
    ver = t["version_tokens"]
    missing = sum(not _has(tok, words, compact) for tok in ver)
    return core_ok, core_ok and missing <= (1 if len(ver) >= 3 else 0)


def split_name(name):
    out = [name]
    v = re.sub(r"(?<=[a-z])(SA)\b", r" \1", name)
    v = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", v)
    if v != name:
        out.append(v)
    nosa = re.sub(r"\s*\bSA\b", "", v).strip()
    if nosa and nosa != v and len(nosa) >= 4:
        out.append(nosa)
    return out


def query_variants(t):
    core = " ".join(tokens(t["core"]))
    ver = " ".join(t["version_tokens"])
    qs = [" ".join(filter(None, [" ".join(tokens(a)), core])) for a in (split_name(t["first_artist"]) if t["first_artist"] else [""])]
    if not t["first_artist"]:                      # "FreakSA Ride" -> also try "Freak SA Ride"
        qs += [" ".join(tokens(v)) for v in split_name(t["core"])[1:]]
    if ver:
        qs.append(f"{core} {ver}")
    if len(core.split()) >= 2 or len(core) >= 7:
        qs.append(core)
    seen, out = set(), []
    for q in qs:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


# --------------------------------------------------------------------------- quality
def quality(ext, bitrate=None, bit_depth=None, sample_rate=None, vbr=False):
    """-> (score, label, tier). tier: lossless | high | ok | low | unknown"""
    ext = (ext or "").lower().lstrip(".")
    if ext in LOSSLESS:
        bd = bit_depth or 16
        sr = (sample_rate or 44100) / 1000
        sr = int(sr) if float(sr).is_integer() else round(sr, 1)
        return 10000 + bd * 100 + int(sample_rate or 44100) // 1000, f"{ext.upper()} {bd}/{sr}", "lossless"
    br = int(bitrate or 0)
    if not br:
        return 1, f"{ext.upper()} ?", "unknown"
    tier = "high" if br >= 315 else "ok" if br >= 250 else "low"
    return br, f"{ext.upper()} {br}{' VBR' if vbr else ''}", tier


def quality_from_title(title):
    t = title.lower()
    if re.search(r"\b(flac|lossless|24bit|24-bit|16bit|wav|aiff)\b", t):
        return 10000, "FLAC (release)", "lossless"
    m = re.search(r"\b(320|256|192|128)\s*(k|kbps)?\b", t)
    if m:
        br = int(m.group(1))
        return quality("mp3", br)
    if re.search(r"\bv0\b", t):
        return 300, "MP3 V0", "ok"
    return 1, "MP3 (release)", "unknown"


def fmt_eta(seconds):
    if seconds is None:
        return ""
    seconds = int(max(seconds, 5))
    return f"~{seconds // 60} min" if seconds >= 90 else f"~{seconds} s"


# --------------------------------------------------------------------------- http helpers
def http(method, url, body=None, headers=None, timeout=60, raw=False, retries=2):
    data = None
    if body is not None:
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("User-Agent", "Umculo/" + VERSION)
        if data is not None and not (headers or {}).get("Content-Type"):
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                if raw:
                    return payload, r.headers
                return json.loads(payload) if payload.strip() else None
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:   # DNS blips etc.
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


# --------------------------------------------------------------------------- metadata (Deezer)
DZ_CACHE = {}
DZ_LOCK = threading.Lock()


def dz_get(path, ttl=6 * 3600):
    """Cached Deezer GET (catalogue answers barely change; For You and recipes ask a lot)."""
    now = time.time()
    with DZ_LOCK:
        hit = DZ_CACHE.get(path)
        if hit and now - hit[0] < ttl:
            return hit[1]
    data = http("GET", f"{CONFIG['deezer']}{path}", timeout=20) or {}
    with DZ_LOCK:
        if len(DZ_CACHE) > 5000:
            for k in sorted(DZ_CACHE, key=lambda k: DZ_CACHE[k][0])[:1000]:
                DZ_CACHE.pop(k, None)
        DZ_CACHE[path] = (now, data)
    return data


def deezer_search(q, limit=20):
    res = http("GET", f"{CONFIG['deezer']}/search?limit={limit}&q=" + urllib.parse.quote(q), timeout=20) or {}
    out = []
    for d in res.get("data") or []:
        out.append({
            "deezer_id": d.get("id"),
            "artist_id": (d.get("artist") or {}).get("id"),
            "album_id": (d.get("album") or {}).get("id"),
            "title": d.get("title") or "",
            "version": d.get("title_version") or "",
            "artist": (d.get("artist") or {}).get("name", ""),
            "album": (d.get("album") or {}).get("title", ""),
            "cover": (d.get("album") or {}).get("cover_medium", ""),
            "cover_big": (d.get("album") or {}).get("cover_xl") or (d.get("album") or {}).get("cover_big", ""),
            "duration": d.get("duration") or 0,
            "preview": d.get("preview") or "",
        })
    return out


def deezer_related(deezer_track_id, limit=12):
    """More like this: the artist's related artists' top tracks."""
    tr = http("GET", f"{CONFIG['deezer']}/track/{deezer_track_id}", timeout=20) or {}
    aid = (tr.get("artist") or {}).get("id")
    if not aid:
        return []
    rel = http("GET", f"{CONFIG['deezer']}/artist/{aid}/related?limit=8", timeout=20) or {}
    out = []
    for a in (rel.get("data") or [])[:6]:
        top = http("GET", f"{CONFIG['deezer']}/artist/{a['id']}/top?limit=2", timeout=20) or {}
        for d in top.get("data") or []:
            out.append({
                "deezer_id": d.get("id"), "title": d.get("title") or "", "version": d.get("title_version") or "",
                "artist_id": (d.get("artist") or {}).get("id"), "album_id": (d.get("album") or {}).get("id"),
                "artist": (d.get("artist") or {}).get("name", ""), "album": (d.get("album") or {}).get("title", ""),
                "cover": (d.get("album") or {}).get("cover_medium", ""),
                "cover_big": (d.get("album") or {}).get("cover_xl", ""),
                "duration": d.get("duration") or 0, "preview": d.get("preview") or "",
            })
    return out[:limit]


# --------------------------------------------------------------------------- Navidrome (Subsonic API)
def nd_params():
    salt = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    token = hashlib.md5((CONFIG["nd_password"] + salt).encode()).hexdigest()
    return {"u": CONFIG["nd_user"], "t": token, "s": salt, "v": "1.16.1", "c": "umculo", "f": "json"}


def builtin():
    return CONFIG.get("music_server") == "builtin"


def nd_call(endpoint, **params):
    """Ask the music server (as the admin): Umculo's own, or Navidrome."""
    if builtin():
        return server.as_user(users.admin_user() or users.OPEN, endpoint, **params)
    return navidrome_call(endpoint, **params)


def navidrome_call(endpoint, **params):
    if not CONFIG["nd_user"]:
        return None
    p = nd_params()
    p.update(params)
    res = http("GET", f"{CONFIG['navidrome']}/rest/{endpoint}?" + urllib.parse.urlencode(p), timeout=20)
    return (res or {}).get("subsonic-response")


def nd_search(q):
    try:
        r = nd_call("search3", query=q, songCount=25, artistCount=0, albumCount=0) or {}
    except Exception as e:  # noqa: BLE001
        log("navidrome search failed:", e)
        return []
    songs = ((r.get("searchResult3") or {}).get("song")) or []
    return [{"id": s.get("id"), "title": s.get("title", ""), "artist": s.get("artist", ""),
             "album": s.get("album", ""), "duration": s.get("duration") or 0,
             "suffix": s.get("suffix", ""), "bitrate": s.get("bitRate") or 0,
             "path": s.get("path", ""), "cover": s.get("coverArt") or ""} for s in songs]


def nd_scan():
    if builtin():
        musicdb.request_scan()
        return True
    try:
        nd_call("startScan")
        return True
    except Exception as e:  # noqa: BLE001
        log("navidrome scan failed:", e)
        return False


def owned_in(library, item):
    t = make_target(item["artist"], item["title"], item.get("version", ""))
    for s in library:
        song_ok, exact = match(f"{s['artist']} {s['title']}", t)
        if exact:
            return s
    return None


# --------------------------------------------------------------------------- source: Soulseek
def slskd_headers():
    return {"X-API-Key": CONFIG["slskd_key"]} if CONFIG["slskd_key"] else {}


SLSK = {"empty_streak": 0, "pause_until": 0, "searches": 0, "empty": 0, "last_ok": 0, "last_empty": 0}


def slskd_search(query):
    res = _slskd_search(query)
    SLSK["searches"] += 1
    if res:
        SLSK["empty_streak"], SLSK["last_ok"] = 0, time.time()
    else:
        SLSK["empty_streak"] += 1
        SLSK["empty"] += 1
        SLSK["last_empty"] = time.time()
    return res


def _slskd_search(query, seconds=None):
    sid = None
    seconds = seconds or CONFIG["search_seconds"]
    try:
        s = http("POST", f"{CONFIG['slskd']}/api/v0/searches",
                 {"searchText": query, "searchTimeout": seconds * 1000,
                  "responseLimit": 200, "fileLimit": 5000}, slskd_headers())
        sid = s["id"]
        deadline = time.time() + seconds + 15
        while time.time() < deadline:
            time.sleep(2)
            st = http("GET", f"{CONFIG['slskd']}/api/v0/searches/{sid}", headers=slskd_headers())
            if st.get("isComplete"):
                break
        return http("GET", f"{CONFIG['slskd']}/api/v0/searches/{sid}/responses", headers=slskd_headers()) or []
    finally:
        if sid:
            try:
                http("DELETE", f"{CONFIG['slskd']}/api/v0/searches/{sid}", headers=slskd_headers(), retries=0)
            except Exception:  # noqa: BLE001
                pass


REFUSED = {}                                     # (user, file) or user -> when they said no


def slsk_refused(user, remote):
    now = time.time()
    REFUSED[(user, remote)] = now
    REFUSED[user] = [x for x in (REFUSED.get(user) or []) if now - x < 86400] + [now]


def slsk_skip(user, remote):
    now = time.time()
    if now - REFUSED.get((user, remote), 0) < 86400:
        return True
    return len([x for x in (REFUSED.get(user) or []) if now - x < 86400]) >= 2   # this peer keeps saying no


def soulseek_candidates(t, emit):
    seen = set()
    found_exact = False
    answered = 0
    for q in query_variants(t):
        emit("status", f"Soulseek: searching “{q}”")
        responses = slskd_search(q)
        answered += len(responses)
        emit("raw", len(responses))
        for r in responses:
            user = r.get("username")
            speed = r.get("uploadSpeed") or 0
            queue = r.get("queueLength") or 0
            free = bool(r.get("hasFreeUploadSlot"))
            for f in r.get("files") or []:
                name = f.get("filename", "")
                ext = (f.get("extension") or name.rsplit(".", 1)[-1]).lower().lstrip(".")
                if ext not in AUDIO or (user, name) in seen or slsk_skip(user, name):
                    continue
                song_ok, exact = match(name, t)
                if not song_ok:
                    continue
                seen.add((user, name))
                score, label, tier = quality(ext, f.get("bitRate"), f.get("bitDepth"), f.get("sampleRate"), f.get("isVariableBitRate"))
                size = f.get("size") or 0
                wait = (size / speed if speed else size / 400_000) + (0 if free else 60 + queue * 20)
                found_exact |= exact
                emit("candidate", {
                    "source": "soulseek", "exact": exact, "score": score, "quality": label, "tier": tier,
                    "size": size, "eta": fmt_eta(wait), "eta_s": int(wait),
                    "detail": f"{user} · {'free slot' if free else f'queue {queue}'}", "free": free,
                    "name": name.replace("\\", "/").split("/")[-1], "duration": f.get("length"),
                    "ref": {"username": user, "filename": name, "size": size},
                })
        if found_exact:
            break


# --------------------------------------------------------------------------- source: Usenet (Prowlarr -> SABnzbd)
def usenet_candidates(t, emit):
    emit("status", "Usenet: searching indexers")
    q = " ".join(tokens(" ".join(filter(None, [t["first_artist"], t["core"]]))))
    url = (f"{CONFIG['prowlarr']}/api/v1/search?type=search&limit=100&categories=3000&query=" + urllib.parse.quote(q))
    res = http("GET", url, headers={"X-Api-Key": CONFIG["prowlarr_key"]}, timeout=120) or []
    for r in res:
        if (r.get("protocol") or "").lower() != "usenet":
            continue
        title = r.get("title") or ""
        song_ok, exact = match(title, t)
        if not song_ok:
            continue
        score, label, tier = quality_from_title(title)
        size = r.get("size") or 0
        emit("candidate", {
            "source": "usenet", "exact": exact, "score": score, "quality": label, "tier": tier,
            "size": size, "eta": fmt_eta(size / 4_000_000 + 60), "eta_s": int(size / 4_000_000 + 60),
            "detail": f"{r.get('indexer', '')} · {r.get('grabs') or 0} grabs",
            "name": title, "duration": None,
            "ref": {"guid": r.get("guid"), "indexerId": r.get("indexerId"), "downloadUrl": r.get("downloadUrl"),
                    "title": title},
        })


# --------------------------------------------------------------------------- source: SoundCloud / YouTube (yt-dlp)
class QuietLog:
    def debug(self, msg):
        pass

    warning = info = error = debug


def _ydl(opts=None):
    from yt_dlp import YoutubeDL
    base = {"quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
            "socket_timeout": 20, "retries": 2,
            "js_runtimes": {"node": {}},          # YouTube needs a JS runtime (Node ships in the image)
            "logger": QuietLog()}                  # errors still raise; just don't print them twice
    cookies = os.path.join(CONFIG["config"], "youtube-cookies.txt")
    if os.path.isfile(cookies):                       # optional: helps when YouTube asks "are you a bot?"
        base["cookiefile"] = cookies
    base.update(opts or {})
    return YoutubeDL(base)


def _best_audio(info):
    """-> (score, label, tier, format_spec, ext)"""
    fmts = info.get("formats") or []
    dl = [f for f in fmts if f.get("format_id") == "download"]
    if dl:                                    # SoundCloud: uploader's original file
        ext = (dl[0].get("ext") or "").lower()
        score, label, tier = quality(ext, dl[0].get("abr"))
        if tier == "lossless":
            label = f"{ext.upper()} original"
        elif tier == "unknown":
            label, tier, score = f"Original {ext.upper()}", "high", 400
        return score, label, tier, "download", ext
    audio = [f for f in fmts if f.get("acodec") not in (None, "none") and f.get("vcodec") in (None, "none")]
    if not audio:
        return 1, "audio ?", "unknown", "bestaudio/best", ""
    best = max(audio, key=lambda f: (f.get("abr") or f.get("tbr") or 0))
    abr = int(best.get("abr") or best.get("tbr") or 0)
    codec = (best.get("acodec") or best.get("ext") or "").split(".")[0].upper().replace("MP4A", "AAC")
    # Opus is far more efficient than MP3: Opus 160 sounds about like MP3 256, so rate it that way
    score, label, tier = quality("mp3", int(abr * {"OPUS": 1.6, "AAC": 1.25}.get(codec, 1)))
    return score, f"{codec} {abr}" if abr else f"{codec} ?", tier, "bestaudio/best", best.get("ext") or ""


def ytdlp_candidates(t, emit, source):
    prefix = {"soundcloud": "scsearch10:", "youtube": "ytsearch8:"}[source]
    q = " ".join(filter(None, [t["artist"], t["title"]]))
    emit("status", "searching")
    with _ydl({"extract_flat": "in_playlist"}) as y:
        res = y.extract_info(prefix + q, download=False) or {}
    hits = []
    for e in res.get("entries") or []:
        text = f"{e.get('title', '')} {e.get('uploader', '') or e.get('channel', '')}"
        song_ok, exact = match(text, t)
        if song_ok:
            hits.append((exact, e))
    hits.sort(key=lambda h: not h[0])
    for exact, e in hits[:4]:
        url = e.get("url") or e.get("webpage_url")
        try:
            with _ydl() as y:
                info = y.extract_info(url, download=False)
        except Exception as ex:  # noqa: BLE001
            if "DRM" not in str(ex):                 # some SoundCloud/YouTube uploads are locked; just skip them
                log(source, "detail failed:", ex)
            continue
        score, label, tier, fmt, ext = _best_audio(info)
        dur = info.get("duration") or 0
        emit("candidate", {
            "source": source, "exact": exact, "score": score, "quality": label, "tier": tier,
            "size": info.get("filesize") or info.get("filesize_approx") or 0,
            "eta": fmt_eta(30 + dur / 20), "eta_s": int(30 + dur / 20),
            "detail": info.get("uploader") or info.get("channel") or "",
            "name": info.get("title") or e.get("title", ""), "duration": dur,
            "ref": {"url": info.get("webpage_url") or url, "format": fmt},
        })


SOURCE_NAMES = {"soulseek": "Soulseek", "usenet": "Usenet", "soundcloud": "SoundCloud", "youtube": "YouTube",
                "link": "the link"}
SOURCES = {
    "soulseek": soulseek_candidates,
    "usenet": usenet_candidates,
    "soundcloud": lambda t, emit: ytdlp_candidates(t, emit, "soundcloud"),
    "youtube": lambda t, emit: ytdlp_candidates(t, emit, "youtube"),
}


# --------------------------------------------------------------------------- jobs (availability checks)
CHECKS = {}
CHECK_LOCK = threading.Lock()
POOL = cf.ThreadPoolExecutor(max_workers=16)


def start_check(target):
    cid = uuid.uuid4().hex[:12]
    job = {"id": cid, "target": target, "candidates": [], "status": {}, "errors": {},
           "done": {}, "started": time.time()}
    with CHECK_LOCK:
        CHECKS[cid] = job
        for old in [k for k, v in CHECKS.items() if time.time() - v["started"] > 3600]:
            CHECKS.pop(old, None)
    for name in CONFIG["sources"]:
        if name not in SOURCES:
            continue
        job["done"][name] = False

        def run(name=name):
            def emit(kind, payload):
                with CHECK_LOCK:
                    if kind == "status":
                        job["status"][name] = payload
                    elif kind == "candidate":
                        payload["id"] = uuid.uuid4().hex[:10]
                        dur = payload.get("duration")
                        if target["duration"] and dur:
                            payload["duration_ok"] = abs(int(dur) - target["duration"]) <= 15
                        job["candidates"].append(payload)
            try:
                SOURCES[name](target, emit)
            except Exception as e:  # noqa: BLE001
                job["errors"][name] = str(e)[:200]
                log("source", name, "failed:", e)
            finally:
                with CHECK_LOCK:
                    job["done"][name] = True
                    job["status"][name] = "done"
        POOL.submit(run)
    return cid


def rank(c):
    return (c["exact"], c.get("duration_ok", True), c["score"], -c.get("eta_s", 0))


def check_view(job):
    with CHECK_LOCK:
        cands = sorted(job["candidates"], key=rank, reverse=True)
        return {"id": job["id"], "target": job["target"], "candidates": cands[:60],
                "status": dict(job["status"]), "errors": dict(job["errors"]),
                "done": all(job["done"].values()), "sources": dict(job["done"]),
                "profile": CONFIG["quality"], "cap": cap() if CONFIG["quality"] != "best" else 0}


# --------------------------------------------------------------------------- requests (downloads)
REQS = {}
REQ_LOCK = threading.Lock()


def req_path():
    return os.path.join(CONFIG["config"], "requests.json")


def save_reqs():
    with REQ_LOCK:
        data = list(REQS.values())
    _save_json("requests.json", data[-300:])


def load_reqs():
    try:
        with open(req_path(), encoding="utf-8") as fh:
            for r in json.load(fh):
                if r["state"] not in ("ready", "failed"):
                    r["state"], r["message"] = "failed", "Umculo restarted during this request"
                REQS[r["id"]] = r
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        log("could not load requests:", e)


def update(req, **kw):
    if req.get("cancel") and kw.get("state") != "failed":
        raise RuntimeError("Stopped by you")
    with REQ_LOCK:
        req.update(kw)
        req["updated"] = time.time()
        tracked = req.get("id") in REQS
    if tracked:                 # batch (playlist) downloads use throwaway request records
        save_reqs()


def start_request(target, cand):
    rid = uuid.uuid4().hex[:12]
    req = {"id": rid, "target": target, "candidate": cand, "state": "queued", "progress": 0,
           "message": "Queued", "created": time.time(), "updated": time.time(), "file": "", "user": users.current_id()}
    with REQ_LOCK:
        REQS[rid] = req
    save_reqs()
    POOL.submit(run_request, req)
    return rid


def run_request(req):
    cand = req["candidate"]
    try:
        update(req, state="downloading", message=f"Downloading from {SOURCE_NAMES.get(cand['source'], cand['source'])}")
        path = DL[cand["source"]](req)
        update(req, state="filing", progress=100, message="Checking and filing")
        final = file_into_library(path, req)
        cleanup_download(req, path)
        if cand["source"] == "link":
            links.after_filing(req, final)
        if not (cand.get("podcast") or within(final, CONFIG["mixes"])):
            index_add(req["target"], final, cand)
        update(req, file=final)
        update(req, state="scanning", message="Asking Navidrome to rescan")
        nd_scan()
        update(req, state="ready", message="Ready in your library")
        notify(f"Ready: {req['target']['artist']} - {req['target']['title']}")
    except Exception as e:  # noqa: BLE001
        if req.get("cancel"):
            update(req, state="failed", progress=0, message="Stopped")
            return
        log("request failed:", traceback.format_exc())
        update(req, state="failed", message=str(e)[:300])


def notify(text):
    if CONFIG["ntfy"]:
        try:
            http("POST", CONFIG["ntfy"], text.encode(), {"Content-Type": "text/plain", "Title": "Umculo"}, retries=0)
        except Exception as e:  # noqa: BLE001
            log("ntfy failed:", e)


# ---- Soulseek download
def dl_soulseek(req):
    ref = req["candidate"]["ref"]
    user, remote = ref["username"], ref["filename"]
    http("POST", f"{CONFIG['slskd']}/api/v0/transfers/downloads/{urllib.parse.quote(user)}",
         [{"filename": remote, "size": ref.get("size") or 0}], slskd_headers())
    stall = req.get("stall_s") or 0                 # batch jobs give up on a peer that doesn't move
    deadline = time.time() + 3 * 3600
    last_move, last_bytes, f = time.time(), -1, None
    stop = req.get("stop")
    while time.time() < deadline:
        if stop and stop.wait(3):
            slsk_cancel(user, f)
            raise Stopped()
        if not stop:
            time.sleep(3)
        try:
            data = http("GET", f"{CONFIG['slskd']}/api/v0/transfers/downloads/{urllib.parse.quote(user)}",
                        headers=slskd_headers()) or {}
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
            data = {}                                    # slskd has no transfer listed for this peer (yet)
        f = next((f for d in data.get("directories") or [] for f in d.get("files") or []
                  if f.get("filename") == remote), None) or f
        if f is None or not data:
            if time.time() - last_move > 120:
                raise RuntimeError("Soulseek peer rejected the download or went offline")
            continue
        state = f.get("state") or ""
        pct = int(f.get("percentComplete") or 0)
        if "Succeeded" in state:
            break
        if state.startswith("Completed"):
            if "Rejected" in state or "Errored" in state:
                slsk_refused(user, remote)
                raise RuntimeError("Soulseek peer said no" + (" (" + state.split(",")[-1].strip() + ")" if "," in state else ""))
            raise RuntimeError(f"Soulseek transfer ended: {state}")
        got = int(f.get("bytesTransferred") or 0) or pct
        if got > last_bytes:
            last_bytes, last_move = got, time.time()
        if stall and time.time() - last_move > stall:
            slsk_cancel(user, f)
            raise RuntimeError("Peer too slow" if got else "Stuck in the peer's queue")
        msg = "Waiting in the peer's queue" if "Queued" in state else f"Downloading from Soulseek ({pct}%)"
        update(req, progress=pct, message=msg)
    else:
        slsk_cancel(user, f)
        raise RuntimeError("Soulseek download timed out")
    base = remote.replace("\\", "/").split("/")[-1]
    stem, ext = os.path.splitext(base)
    key = re.sub(r"[^a-z0-9]", "", stem.lower())
    size = ref.get("size") or 0
    for i in range(30):                          # slskd may still be moving the file into place
        hits, near = [], []
        for root, _d, files in os.walk(CONFIG["slskd_downloads"]):
            for n in files:
                p = os.path.join(root, n)
                if n == base:
                    hits.append((os.path.getmtime(p), p))
                elif n.lower().endswith(ext.lower()):
                    # slskd swaps characters it can't save (?, :, ") and adds _<number> when the name is taken
                    s2 = re.sub(r"_\d{8,}$", "", os.path.splitext(n)[0])
                    if re.sub(r"[^a-z0-9]", "", s2.lower()) == key and time.time() - os.path.getmtime(p) < 3600:
                        near.append((os.path.getmtime(p), p))
                    elif size and i >= 5 and time.time() - os.path.getmtime(p) < 600 and os.path.getsize(p) == size:
                        near.append((os.path.getmtime(p), p))
        if hits or near:
            return max(hits or near)[1]
        time.sleep(2)
    raise RuntimeError(f"Downloaded file not found in {CONFIG['slskd_downloads']}")


class Stopped(Exception):
    """Another source won the race for this song."""


def slsk_cancel(user, f):
    if not f or not f.get("id"):
        return
    try:
        http("DELETE", f"{CONFIG['slskd']}/api/v0/transfers/downloads/{urllib.parse.quote(user)}/{f['id']}"
             "?remove=true", headers=slskd_headers(), retries=0)
    except Exception as e:  # noqa: BLE001
        log("slskd cancel failed:", e)


# ---- Usenet download
def multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (fname, content) in files.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: application/x-nzb\r\n\r\n").encode() + content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


def sab(mode, **params):
    p = {"mode": mode, "output": "json", "apikey": CONFIG["sab_key"]}
    p.update(params)
    return http("GET", f"{CONFIG['sab']}/api?" + urllib.parse.urlencode(p), timeout=60) or {}


def dl_usenet(req):
    ref = req["candidate"]["ref"]
    nzb, _h = http("GET", ref["downloadUrl"], headers={"X-Api-Key": CONFIG["prowlarr_key"]}, raw=True, timeout=120)
    if b"<nzb" not in nzb[:2000].lower():
        raise RuntimeError("Indexer did not return an NZB file")
    fields = {"mode": "addfile", "output": "json", "apikey": CONFIG["sab_key"], "nzbname": ref["title"][:200]}
    if CONFIG["sab_category"]:
        fields["cat"] = CONFIG["sab_category"]
    body, ctype = multipart(fields, {"name": (re.sub(r"[^\w.-]+", "_", ref["title"])[:100] + ".nzb", nzb)})
    res = http("POST", f"{CONFIG['sab']}/api", body, {"Content-Type": ctype}, timeout=60)
    ids = (res or {}).get("nzo_ids") or []
    if not ids:
        raise RuntimeError(f"SABnzbd refused the NZB: {res}")
    nzo = ids[0]
    req["nzo"] = nzo
    stop = req.get("stop")
    deadline = time.time() + 6 * 3600
    while time.time() < deadline:
        if stop and stop.wait(5):
            try:
                sab("queue", name="delete", value=nzo, del_files=1)
                sab("history", name="delete", value=nzo, del_files=1)
            except Exception as e:  # noqa: BLE001
                log("SABnzbd delete failed:", e)
            raise Stopped()
        if not stop:
            time.sleep(5)
        q = (sab("queue").get("queue") or {}).get("slots") or []
        slot = next((s for s in q if s.get("nzo_id") == nzo), None)
        if slot:
            pct = int(float(slot.get("percentage") or 0))
            update(req, progress=pct, message=f"Downloading from Usenet ({pct}%)")
            continue
        h = (sab("history", nzo_ids=nzo).get("history") or {}).get("slots") or []
        slot = next((s for s in h if s.get("nzo_id") == nzo), None)
        if not slot:
            continue
        status = slot.get("status")
        if status == "Completed":
            folder = slot.get("storage") or ""
            req["download_folder"] = folder
            return pick_track(folder, req["target"])
        if status == "Failed":
            raise RuntimeError(f"Usenet download failed: {slot.get('fail_message', '')}")
        update(req, message=f"Usenet: {status}")
    raise RuntimeError("Usenet download timed out")


def pick_track(folder, t):
    """From a downloaded release (folder or file) pick the file that is this song."""
    if os.path.isfile(folder):
        return folder
    best = None
    for root, _d, files in os.walk(folder):
        for f in files:
            ext = f.rsplit(".", 1)[-1].lower()
            if ext not in AUDIO:
                continue
            song_ok, exact = match(f, t)
            if not song_ok:                                         # track files often omit the artist
                song_ok, exact = match(f, {**t, "artists": []})
            if song_ok:
                key = (exact, quality(ext)[0])
                if not best or key > best[0]:
                    best = (key, os.path.join(root, f))
    if not best:
        raise RuntimeError(f"Release downloaded to {folder}, but no file in it matches the song")
    return best[1]


# ---- SoundCloud / YouTube download
def dl_ytdlp(req):
    ref = req["candidate"]["ref"]
    out = os.path.join(CONFIG["staging"], req["id"])
    os.makedirs(out, exist_ok=True)

    def hook(d):
        if d.get("status") == "downloading":
            tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            pct = int(d.get("downloaded_bytes", 0) * 100 / tot) if tot else 0
            update(req, progress=pct, message=f"Downloading ({pct}%)")

    opts = {"skip_download": False, "format": ref.get("format") or "bestaudio/best",
            "outtmpl": os.path.join(out, "%(title).150B.%(ext)s"), "progress_hooks": [hook],
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}]}
    if ref.get("format") == "download":
        opts["postprocessors"] = []
    with _ydl(opts) as y:
        y.download([ref["url"]])
    files = [os.path.join(out, f) for f in os.listdir(out) if f.rsplit(".", 1)[-1].lower() in AUDIO]
    if not files:
        raise RuntimeError("Download finished but no audio file was produced")
    return max(files, key=os.path.getsize)


# ---- filing
def safe(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "").strip(" .")
    return re.sub(r"\s+", " ", name)[:150] or "Unknown"


def audio_duration(path):
    try:
        import mutagen
        m = mutagen.File(path)
        return int(m.info.length) if m and m.info else 0
    except Exception:  # noqa: BLE001
        return 0


def read_tags(path):
    try:
        import mutagen
        m = mutagen.File(path, easy=True)
        if not m or not m.tags:
            return {}
        get = lambda k: (m.tags.get(k) or [""])[0] if hasattr(m.tags, "get") else ""  # noqa: E731
        return {"artist": get("artist"), "title": get("title"), "album": get("album")}
    except Exception:  # noqa: BLE001
        return {}


def identity(t, src, cand):
    """Artist and title to file under. Uses, in order: the target, the file's own tags,
    'Artist - Title' in the source's name, then the file name."""
    artist, title = t.get("artist") or "", t.get("title") or ""
    if artist and title:
        return artist, title
    tags = read_tags(src)
    if tags.get("artist") and tags.get("title"):
        return artist or tags["artist"], title if artist else tags["title"]
    name = os.path.splitext(cand.get("name") or os.path.basename(src))[0]
    name = re.sub(r"^\s*\d{1,3}\s*[-.]\s*", "", name)
    if " - " in name:
        a, n = name.split(" - ", 1)
        return artist or a.strip(), title if artist else n.strip()
    if cand.get("source") in ("youtube", "soundcloud") and cand.get("detail"):
        return artist or cand["detail"], title or name
    return artist or "Unknown Artist", title or name


def file_into_library(src, req):
    t = req["target"]
    dur = audio_duration(src)
    cand0 = req.get("candidate") or {}
    if not (cand0.get("podcast") or (dur and dur > CONFIG["mix_seconds"])):
        src = apply_profile(src, req)
    ext = src.rsplit(".", 1)[-1].lower()
    if t["duration"] and dur and abs(dur - t["duration"]) > 20:
        update(req, warning=f"Length {dur // 60}:{dur % 60:02d} differs from the catalogue "
                            f"({t['duration'] // 60}:{t['duration'] % 60:02d}); check it is the right version")
    artist, title = identity(t, src, req.get("candidate") or {})
    if artist != t["artist"] or title != t["title"]:
        t = {**t, "artist": artist, "title": title}
        req["target"] = t
    cand = req.get("candidate") or {}
    if (dur and dur > CONFIG["mix_seconds"]) or cand.get("podcast"):
        dest_dir = CONFIG["mixes"]
        if cand.get("folder"):
            dest_dir = os.path.join(dest_dir, safe(cand["folder"]))
    else:
        dest_dir = os.path.join(users.music_root(req.get("user")),
                                safe(split_artists(artist)[0] if split_artists(artist) else artist))
    os.makedirs(dest_dir, exist_ok=True)
    stem = safe(cand["filename"]) if cand.get("filename") else f"{safe(artist)} - {safe(title)}"
    dest = os.path.join(dest_dir, f"{stem}.{ext}")
    if cand.get("filename") and os.path.exists(dest):          # this episode is already on the server
        os.remove(src)
        shutil.rmtree(os.path.join(CONFIG["staging"], req["id"]), ignore_errors=True)
        return dest
    same = [os.path.join(dest_dir, f) for f in (os.listdir(dest_dir) if os.path.isdir(dest_dir) else [])
            if os.path.splitext(f)[0] == stem and os.path.splitext(f)[1].lower().lstrip(".") in AUDIO]
    if same and not cand.get("podcast") and req["id"].startswith("b"):   # batch: the same song from two albums/jobs
        have = max(same, key=file_score_cached)
        if file_score_cached(have) >= file_score(src):
            os.remove(src)
            shutil.rmtree(os.path.join(CONFIG["staging"], req["id"]), ignore_errors=True)
            log("already in the library in this quality or better, kept:", have)
            return have
        for old in same:                                 # the new copy is better: it replaces the old one
            replace_everywhere(old, dest)
            try:
                os.remove(old)
            except OSError:
                pass
    if os.path.exists(dest):
        base, n = dest.rsplit(".", 1)[0], 2
        while os.path.exists(f"{base} ({n}).{ext}"):
            n += 1
        dest = f"{base} ({n}).{ext}"
    shutil.move(src, dest)
    try:
        tag_file(dest, t)
    except Exception as e:  # noqa: BLE001
        log("tagging failed (file kept):", e)
    staging = os.path.join(CONFIG["staging"], req["id"])
    shutil.rmtree(staging, ignore_errors=True)
    library.after_filing(dest)
    return dest


def tag_file(path, t):
    """Write artist/title/album and, where we have it, cover art."""
    import mutagen
    ext = path.rsplit(".", 1)[-1].lower()
    tags = {"artist": t["artist"], "title": t["title"]}
    if t.get("album"):
        tags["album"] = t["album"]
    cover = None
    if t.get("cover"):
        try:
            cover, _h = http("GET", t["cover"], raw=True, timeout=20, retries=1)
        except Exception:  # noqa: BLE001
            cover = None
    if ext in ("mp3",):
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3, APIC, ID3NoHeaderError
        try:
            e = EasyID3(path)
        except ID3NoHeaderError:
            e = mutagen.File(path, easy=True)
            e.add_tags()
        for k, v in tags.items():
            if v and not e.get(k):
                e[k] = v
        e.save()
        if cover:
            i = ID3(path)
            if not i.getall("APIC"):
                i.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover))
                i.save()
    elif ext in ("flac",):
        from mutagen.flac import FLAC, Picture
        f = FLAC(path)
        for k, v in tags.items():
            if v and not f.get(k):
                f[k] = v
        if cover and not f.pictures:
            p = Picture()
            p.type, p.mime, p.data = 3, "image/jpeg", cover
            f.add_picture(p)
        f.save()
    elif ext in ("m4a", "aac", "alac"):
        from mutagen.mp4 import MP4, MP4Cover
        f = MP4(path)
        keys = {"artist": "\xa9ART", "title": "\xa9nam", "album": "\xa9alb"}
        for k, v in tags.items():
            if v and not f.get(keys[k]):
                f[keys[k]] = [v]
        if cover and not f.get("covr"):
            f["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        f.save()
    elif ext in ("ogg", "opus"):
        f = mutagen.File(path)
        for k, v in tags.items():
            if v and not f.get(k):
                f[k] = [v]
        if cover and "metadata_block_picture" not in f:
            from mutagen.flac import Picture
            p = Picture()
            p.type, p.mime, p.data = 3, "image/jpeg", cover
            f["metadata_block_picture"] = [base64.b64encode(p.write()).decode()]
        f.save()
    # wav / aiff: left as delivered (usually already tagged by the sharer)


# --------------------------------------------------------------------------- after a download
def within(path, root):
    if not root:
        return False
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def cleanup_download(req, src):
    """Remove what a download left behind: the rest of a Usenet release, empty Soulseek folders."""
    folder = req.get("download_folder")
    if folder and CONFIG["sab_complete"] and within(folder, CONFIG["sab_complete"]) \
            and os.path.realpath(folder) != os.path.realpath(CONFIG["sab_complete"]):
        shutil.rmtree(folder, ignore_errors=True)
    parent = os.path.dirname(src)
    if within(parent, CONFIG["slskd_downloads"]) and os.path.realpath(parent) != os.path.realpath(CONFIG["slskd_downloads"]):
        try:
            os.rmdir(parent)          # only succeeds when empty
        except OSError:
            pass


# --------------------------------------------------------------------------- library index
INDEX = {}
INDEX_LOCK = threading.Lock()
TIER_RANK = {"unknown": 0, "low": 1, "ok": 2, "high": 3, "lossless": 4}
TIER_SCORE = {"lossless": 10000, "high": 320, "ok": 256, "low": 128, "unknown": 1}
PROFILES = {"best": 10 ** 9, "320": 315, "256": 250, "128": 125}


def cap():
    """Highest quality the user wants to keep (scores: MP3 kbps, lossless = 10000+)."""
    return PROFILES.get(str(CONFIG["quality"]), PROFILES["best"])


def pv(score):
    """Quality as far as the profile cares: anything above the profile counts as the profile."""
    return min(score or 0, cap())


def cscore(c):
    return c.get("score") or TIER_SCORE.get(c.get("tier") or "", 0)


def good_enough(score):
    """No need to keep looking: meets the profile (or 320 when the profile is 'best')."""
    return pv(score) >= min(cap(), 315)


FS_CACHE = {}


def file_score_cached(path):
    try:
        st = os.stat(path)
        sig = (st.st_mtime, st.st_size)
    except OSError:
        return 0
    hit = FS_CACHE.get(path)
    if hit and hit[0] == sig:
        return hit[1]
    sc = file_score(path)
    FS_CACHE[path] = (sig, sc)
    return sc


def file_score(path):
    ext = path.rsplit(".", 1)[-1].lower()
    try:
        import mutagen
        m = mutagen.File(path)
        info = m.info if m else None
    except Exception:  # noqa: BLE001
        info = None
    if ext in LOSSLESS:
        return quality(ext, None, getattr(info, "bits_per_sample", None), getattr(info, "sample_rate", None))[0]
    br = int((getattr(info, "bitrate", 0) or 0) / 1000)
    if ext == "opus":
        br = int(br * 1.6)
    elif ext in ("m4a", "aac"):
        br = int(br * 1.25)
    return quality(ext, br)[0]


def apply_profile(src, req):
    """Convert a download that's better than the user's profile down to MP3 at the profile's bitrate.
    'best' keeps every file exactly as delivered."""
    if CONFIG["quality"] == "best":
        return src
    target = int(CONFIG["quality"])
    if file_score(src) <= cap() * 1.08:
        return src                                           # already at or below the profile
    out = os.path.splitext(src)[0] + ".profile.mp3"
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-map", "0:a:0", "-map", "0:v:0?", "-c:v", "copy",
                        "-c:a", "libmp3lame", "-b:a", f"{target}k", "-map_metadata", "0", "-id3v2_version", "3", out],
                       capture_output=True, timeout=900)
    if p.returncode != 0 or not os.path.exists(out):
        log("profile conversion failed, keeping original:", p.stderr.decode()[-300:])
        return src
    cand = req.get("candidate") or {}
    was = cand.get("quality") or src.rsplit(".", 1)[-1].upper()
    try:
        os.remove(src)
    except OSError:
        pass
    cand.update(quality=f"MP3 {target} (from {was})", score=target, tier=quality("mp3", target)[2], converted=True)
    return out


def song_key(artist, title):
    """Same song = same first artist, same title words, same remix words ("Original Mix" ignored)."""
    t = make_target(artist, title)
    first = split_artists(artist)
    return f"{norm(first[0] if first else artist)}|{' '.join(t['core_tokens'])}|{' '.join(sorted(t['version_tokens']))}"


def index_add(t, path, cand):
    with INDEX_LOCK:
        INDEX[song_key(t["artist"], t["title"])] = {"path": path, "tier": cand.get("tier", "unknown"),
                                                      "score": cscore(cand),
                                                      "quality": cand.get("quality", "")}
        data = dict(INDEX)
    _save_json("index.json", data)


def delete_song_file(path):
    """Delete a song from the server (only inside the music folder), and forget it everywhere."""
    if not (within(path, CONFIG["music"]) or within(path, CONFIG["family"])) or not os.path.isfile(path):
        return False
    try:
        os.remove(path)
    except OSError as e:
        log("couldn't delete", path, e)
        return False
    for side in (os.path.splitext(path)[0] + ".lrc",):
        try:
            os.remove(side)
        except OSError:
            pass
    parent = os.path.dirname(path)
    for leftover in ("cover.jpg", "folder.jpg"):
        if os.path.isdir(parent) and os.listdir(parent) == [leftover]:
            try:
                os.remove(os.path.join(parent, leftover))
            except OSError:
                pass
    try:
        os.rmdir(parent)
    except OSError:
        pass
    with INDEX_LOCK:
        for k in [k for k, v in INDEX.items() if v.get("path") == path]:
            INDEX.pop(k, None)
        data = dict(INDEX)
    _save_json("index.json", data)
    FQ_CACHE.pop(path, None)
    log("deleted from the server:", path)
    return True


def index_get(artist, title):
    with INDEX_LOCK:
        hit = INDEX.get(song_key(artist, title))
    return hit if hit and os.path.exists(hit["path"]) else None


SAVE_LOCKS = {}
SAVE_LOCKS_GUARD = threading.Lock()


def _save_json(name, data):
    """Write a state file atomically. One writer per file at a time, each with its own temp file."""
    with SAVE_LOCKS_GUARD:
        lock = SAVE_LOCKS.setdefault(name, threading.Lock())
    with lock:
        p = os.path.join(CONFIG["config"], name)
        tmp = f"{p}.{threading.get_ident()}.tmp"
        try:
            os.makedirs(CONFIG["config"], exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, p)
        except Exception as e:  # noqa: BLE001
            log("could not save", name, e)
            try:
                os.remove(tmp)
            except OSError:
                pass


def _load_json(name, default):
    try:
        with open(os.path.join(CONFIG["config"], name), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except Exception as e:  # noqa: BLE001
        log("could not load", name, e)
        return default


FQ_CACHE = {}


def file_quality(path):
    """-> (tier, label) of a file already on disk (cached until the file changes)."""
    try:
        st = os.stat(path)
        sig = (st.st_mtime, st.st_size)
    except OSError:
        sig = None
    hit = FQ_CACHE.get(path)
    if sig and hit and hit[0] == sig:
        return hit[1]
    out = _file_quality(path)
    if sig:
        FQ_CACHE[path] = (sig, out)
    return out


def _file_quality(path):
    ext = path.rsplit(".", 1)[-1].lower()
    try:
        import mutagen
        m = mutagen.File(path)
        info = m.info if m else None
    except Exception:  # noqa: BLE001
        info = None
    if ext in LOSSLESS:
        _s, label, tier = quality(ext, None, getattr(info, "bits_per_sample", None), getattr(info, "sample_rate", None))
        return tier, label
    br = int((getattr(info, "bitrate", 0) or 0) / 1000)
    _s, label, tier = quality(ext, br)
    return tier, label


# --------------------------------------------------------------------------- playlists on disk
def m3u_entries(path):
    """-> list of absolute paths, in order, for an .m3u/.m3u8 file."""
    base = os.path.dirname(path)
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip().lstrip("﻿")
            if not line or line.startswith("#") or not os.path.splitext(line)[1]:
                continue                  # blank, comment, or not a file (spotDL writes "*" for some songs)
            out.append(os.path.normpath(line if os.path.isabs(line) else os.path.join(base, line)))
    return out


def write_m3u(path, files):
    base = os.path.dirname(path)
    os.makedirs(base, exist_ok=True)
    lines = ["#EXTM3U"] + [os.path.relpath(f, base) for f in files]
    tmp = os.path.join(base, "." + os.path.basename(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


M3U_LOCK = threading.Lock()


def replace_everywhere(old, new):
    """Point every playlist that lists `old` at `new` instead."""
    old = os.path.normpath(old)
    changed = 0
    with M3U_LOCK:
        for root in (CONFIG["playlists"], CONFIG["spotify_dir"]):
            if not os.path.isdir(root):
                continue
            for d, _s, files in os.walk(root):
                for f in files:
                    if not f.lower().endswith((".m3u", ".m3u8")) or f.startswith("."):
                        continue
                    p = os.path.join(d, f)
                    try:
                        entries = m3u_entries(p)
                    except Exception:  # noqa: BLE001
                        continue
                    if old in entries:
                        write_m3u(p, [new if e == old else e for e in entries])
                        changed += 1
    return changed


SCAN_WANTED = threading.Event()


def request_scan():
    SCAN_WANTED.set()


def scan_loop():
    while True:
        SCAN_WANTED.wait()
        time.sleep(60)            # batch several changes into one rescan
        SCAN_WANTED.clear()
        nd_scan()


# --------------------------------------------------------------------------- list readers
def parse_list(text):
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\s*\d{1,3}\s*[.)\-:]?\s+", "", line)        # "01 ", "1. ", "12) "
        line = re.sub(r"\)\s*\d+\s*$", ")", line)                   # "(feat. Dutch)12"
        line = line.rstrip("# ").strip()
        if " - " in line:
            a, t = line.split(" - ", 1)
        else:
            a, t = "", line
        if t.strip():
            out.append({"artist": a.strip(), "title": t.strip(), "duration": 0})
    return out


def parse_csv(text):
    import csv
    import io
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return []
    cols = {k.lower().strip(): k for k in rows[0].keys() if k}

    def col(*names):
        return next((cols[n] for n in names if n in cols), None)
    c_title = col("track name", "title", "name", "song", "track")
    c_artist = col("artist name(s)", "artist name", "artist", "artists")
    c_dur = col("duration (ms)", "duration_ms", "duration")
    if not c_title:
        raise ValueError("CSV needs a track/title column")
    out = []
    for r in rows:
        dur = r.get(c_dur) if c_dur else 0
        try:
            dur = int(float(dur or 0))
            dur = dur // 1000 if dur > 10000 else dur
        except ValueError:
            dur = 0
        title = (r.get(c_title) or "").strip()
        if title:
            out.append({"artist": (r.get(c_artist) or "").replace(";", ", ").strip() if c_artist else "",
                        "title": title, "duration": dur})
    return out


def spotify_tracks(url):
    """Best effort: read a public playlist from Spotify's embed page (no account needed).
    Spotify changes this page without notice, so failures are expected from time to time."""
    m = re.search(r"playlist[/:]([A-Za-z0-9]{22})", url or "")
    if not m:
        raise ValueError("That doesn't look like a Spotify playlist link")
    html, _h = http("GET", f"https://open.spotify.com/embed/playlist/{m.group(1)}", raw=True, timeout=30,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                           "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"})
    s = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html.decode("utf-8", "replace"), re.S)
    if not s:
        raise ValueError("Spotify didn't return the playlist (it may be private or Spotify changed its page)")
    data = json.loads(s.group(1))
    found = {"tracks": None, "name": ""}

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("trackList"), list) and found["tracks"] is None:
                found["tracks"] = o["trackList"]
                found["name"] = o.get("name") or o.get("title") or ""
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    if not found["tracks"]:
        raise ValueError("No tracks found on Spotify's page; export the playlist to CSV instead")
    tracks = [{"artist": (t.get("subtitle") or "").replace(" ", " "), "title": t.get("title") or "",
               "duration": int((t.get("duration") or 0) / 1000)} for t in found["tracks"] if t.get("title")]
    return found["name"], tracks


# --------------------------------------------------------------------------- playlist jobs
JOBS = {}
JOB_LOCK = threading.Lock()
BATCH = None          # BatchQueue, created in main()
SLSK_GATE = threading.Lock()


def job_priority(job):
    """Higher runs first. Things you asked for directly beat the bulk 'Upgrade all'."""
    return job.get("priority", 0 if job.get("kind") == "upgrade" else 10)


class BatchQueue:
    """Playlist work queue: the highest-priority playlist goes first, paused playlists wait,
    otherwise first come, first served."""

    def __init__(self, workers):
        self.items, self.seq, self.cv = [], 0, threading.Condition()
        for n in range(max(1, workers)):
            threading.Thread(target=self._work, daemon=True, name=f"batch-{n}").start()

    def submit(self, fn, job, tr):
        with self.cv:
            self.seq += 1
            self.items.append((self.seq, fn, job, tr))
            self.cv.notify()

    def wake(self):
        with self.cv:
            self.cv.notify_all()

    def waiting(self):
        with self.cv:
            return len(self.items)

    def _next(self):
        with self.cv:
            while True:
                ready = [i for i in self.items if not i[2].get("paused") or i[2].get("cancel")]
                if ready:
                    item = min(ready, key=lambda i: (-job_priority(i[2]), i[0]))
                    self.items.remove(item)
                    return item
                self.cv.wait(10)

    def _work(self):
        while True:
            _seq, fn, job, tr = self._next()
            try:
                fn(job, tr)
            except Exception:  # noqa: BLE001
                log("batch worker error:", traceback.format_exc())


def save_jobs():
    with JOB_LOCK:
        data = list(JOBS.values())
    _save_json("playlist_jobs.json", data)


def tr_set(job, tr, **kw):
    with JOB_LOCK:
        tr.update(kw)
        job["updated"] = time.time()
    save_jobs()


def describe_file(path):
    tags = read_tags(path)
    artist, title = tags.get("artist", ""), tags.get("title", "")
    if not (artist and title):
        stem = os.path.splitext(os.path.basename(path))[0]
        if " - " in stem:
            artist, title = [x.strip() for x in stem.split(" - ", 1)]
        else:
            title = title or stem
    artist = artist.replace("/", ", ")
    tier, label = file_quality(path) if os.path.exists(path) else ("unknown", "missing")
    return {"artist": artist, "title": title, "duration": audio_duration(path) if os.path.exists(path) else 0,
            "file": path, "quality": label, "tier": tier}


def new_job(name, kind, playlist_path, tracks):
    jid = uuid.uuid4().hex[:12]
    for i, tr in enumerate(tracks):
        tr.update({"i": i, "state": "pending", "message": "", "new_file": "", "new_quality": "", "source": ""})
        tr.setdefault("file", "")
        tr.setdefault("quality", "")
        tr.setdefault("tier", "")
    uid = users.current_id()
    if uid and kind == "import" and os.path.dirname(playlist_path) == CONFIG["playlists"]:
        playlist_path = os.path.join(users.playlist_dir(uid), os.path.basename(playlist_path))   # their own list
        os.makedirs(os.path.dirname(playlist_path), exist_ok=True)
    job = {"id": jid, "name": name, "kind": kind, "m3u": playlist_path, "tracks": tracks,
           "created": time.time(), "updated": time.time(), "cancel": False, "user": uid}
    keep = {f.get("job") for f in share.FOLLOWS.values()} | {(foryou.STATE.get("weekly") or {}).get("job")}
    with JOB_LOCK:
        for old in [j for j in JOBS.values() if j["m3u"] == playlist_path and j["id"] not in keep
                    and not any(t["state"] in ("pending", "searching", "downloading") for t in j["tracks"])]:
            JOBS.pop(old["id"], None)          # one playlist, one card: the older finished run goes
        JOBS[jid] = job
    try:
        if not playlists.watching(playlist_path):
            playlists.set_watch(playlist_path, True)
    except Exception:  # noqa: BLE001
        pass
    save_jobs()
    for tr in tracks:
        BATCH.submit(process_track, job, tr)
    return jid


def pick_auto(cands, current_val, limit=8):
    """Exact version only, better than what we have (as far as the quality profile cares).
    Best first; with a capped profile, a copy that already matches the profile beats one that needs
    converting. Returns up to `limit` candidates to try in order, at most one per Soulseek user."""
    ok = [c for c in cands if c["exact"] and c.get("duration_ok") is not False
          and pv(cscore(c)) > current_val
          and not (c["source"] == "usenet" and (c.get("size") or 0) > CONFIG["usenet_max_mb"] * 1_000_000)]
    best_profile = CONFIG["quality"] == "best"
    ok.sort(key=lambda c: (pv(cscore(c)), best_profile or cscore(c) <= cap() * 1.08,
                           cscore(c) if best_profile else -cscore(c), -c.get("eta_s", 0)), reverse=True)
    out, users, per_lane = [], set(), {}
    for c in ok:
        u = (c.get("ref") or {}).get("username") if c["source"] == "soulseek" else None
        lane = "stream" if c["source"] in ("soundcloud", "youtube") else c["source"]
        if (u and u in users) or per_lane.get(lane, 0) >= 3:
            continue
        users.add(u)
        per_lane[lane] = per_lane.get(lane, 0) + 1
        out.append(c)
    return out[:limit]


def kept_reason(cands, current_val):
    better = [c for c in cands if c["exact"] and pv(cscore(c)) > current_val]
    if not better:
        seen = next((c for c in cands if c["exact"]), None)
        return f"Kept: best found was {seen['quality']}" if seen else "Kept: no better copy found"
    c = better[0]
    if c.get("duration_ok") is False:
        return f"Kept: {c['quality']} found, but its length differs (likely another edit)"
    return f"Kept: {c['quality']} found, but the release is too big to fetch for one song"


def slsk_wait():
    """Soulseek stops answering when it gets too many searches. If a run of searches came back
    completely empty, rest the batch for a while instead of marking songs as missing."""
    if SLSK["empty_streak"] >= CONFIG["slsk_empty_limit"]:
        SLSK["pause_until"] = time.time() + CONFIG["slsk_pause_s"]
        SLSK["empty_streak"] = 0
        log(f"Soulseek gave {CONFIG['slsk_empty_limit']} empty answers in a row; resting playlist searches "
            f"for {CONFIG['slsk_pause_s'] // 60} minutes")
    while time.time() < SLSK["pause_until"]:
        time.sleep(15)


def gather(target, sources, heard=None):
    cands, lock = [], threading.Lock()
    heard = {} if heard is None else heard

    def run(name):
        def emit(kind, payload):
            if kind == "raw":
                with lock:
                    heard[name] = heard.get(name, 0) + payload
                return
            if kind != "candidate":
                return
            dur = payload.get("duration")
            if target["duration"] and dur:
                payload["duration_ok"] = abs(int(dur) - target["duration"]) <= 15
            with lock:
                cands.append(payload)
        try:
            if name == "soulseek":
                with SLSK_GATE:              # one Soulseek search at a time, so the network isn't hammered
                    slsk_wait()
                    SOURCES[name](target, emit)
                    time.sleep(CONFIG["slsk_gap_s"])
            else:
                SOURCES[name](target, emit)
        except Exception as e:  # noqa: BLE001
            log("batch source", name, "failed:", e)
    names = [s for s in sources if s in SOURCES]
    with cf.ThreadPoolExecutor(max(1, len(names))) as ex:
        list(ex.map(run, names))
    return sorted(cands, key=rank, reverse=True)


def adopt(job, tr, path, label, how, tier=None, later=False):
    """Make `path` the playlist's copy of this track, retiring the old file if Umculo may delete it."""
    old = tr.get("new_file") or tr.get("file") or ""
    if old and os.path.normpath(old) != os.path.normpath(path):
        if os.path.exists(old) and (within(old, CONFIG["spotify_dir"]) or within(old, CONFIG["library"])
                                    or within(old, CONFIG["family"])):
            os.remove(old)
        replace_everywhere(old, path)
    tr_set(job, tr, state="done", new_file=path, new_quality=label, message=how,
           new_tier=tier, better_later=later, last_try=time.time())
    if writes_m3u(job):
        write_job_playlist(job)
    request_scan()


def writes_m3u(job):
    """Imports own their playlist file; upgrades only rewrite theirs when a followed playlist added songs."""
    return job["kind"] == "import" or bool(job.get("sync"))


def add_tracks(job, tracks):
    """Append songs to an existing playlist job (a followed playlist grew)."""
    with JOB_LOCK:
        start = len(job["tracks"])
        for n, tr in enumerate(tracks):
            tr.update({"i": start + n, "state": "pending", "message": "New in the playlist", "new_file": "",
                       "new_quality": "", "source": "", "file": "", "quality": "", "tier": ""})
            job["tracks"].append(tr)
        job["notified"] = False
        job["cancel"] = False
    save_jobs()
    for tr in tracks:
        BATCH.submit(process_track, job, tr)


def write_job_playlist(job):
    if job["kind"] == "album":               # albums go to the library, not into a playlist
        return
    with JOB_LOCK:
        files = [t["new_file"] or t["file"] for t in job["tracks"]
                 if (t["new_file"] or t["file"]) and os.path.exists(t["new_file"] or t["file"])]
    with M3U_LOCK:
        write_m3u(job["m3u"], files)


DL = {"soulseek": lambda r: dl_soulseek(r), "usenet": lambda r: dl_usenet(r),
      "soundcloud": lambda r: dl_ytdlp(r), "youtube": lambda r: dl_ytdlp(r),
      "link": lambda r: links.dl_link(r)}


def discard(req, path):
    """Throw away a download that lost the race."""
    try:
        if path and (within(path, CONFIG["slskd_downloads"]) or within(path, CONFIG["staging"])):
            os.remove(path)
    except OSError:
        pass
    shutil.rmtree(os.path.join(CONFIG["staging"], req["id"]), ignore_errors=True)
    cleanup_download(req, path or os.path.join(CONFIG["slskd_downloads"], "x"))


def race(job, tr, target, picks):
    """Download from Soulseek and Usenet at the same time; streaming sites join after a head start.
    The first copy to arrive wins and the others are cancelled. -> (req, path, cand) or None."""
    lanes = {}
    for c in picks:
        lanes.setdefault("stream" if c["source"] in ("soundcloud", "youtube") else c["source"], []).append(c)
    stop, slow_done, lock = threading.Event(), threading.Event(), threading.Lock()
    won, errors, doing = {}, [], {}

    def show():
        msg = " · ".join(f"{q} from {w}" for w, q in doing.values())
        if msg and not stop.is_set():
            tr_set(job, tr, state="downloading", message="Getting " + msg)

    def run(lane, cands):
        try:
            if lane == "stream" and len(lanes) > 1:          # give the better sources a head start
                until = time.time() + CONFIG["stream_after_s"]
                while time.time() < until and not slow_done.is_set() and not stop.is_set():
                    time.sleep(1)
            for c in cands:
                if stop.is_set() or job.get("cancel"):
                    return
                where = SOURCE_NAMES.get(c["source"], c["source"])
                with lock:
                    doing[lane] = (where, c["quality"])
                show()
                req = {"id": "b" + uuid.uuid4().hex[:11], "target": target, "candidate": c,
                       "stall_s": CONFIG["slsk_stall_s"], "stop": stop, "user": job.get("user")}
                try:
                    path = DL[c["source"]](req)
                except Stopped:
                    return
                except Exception as e:  # noqa: BLE001
                    log("batch download failed:", tr.get("title"), "-", where, ":", e)
                    errors.append(f"{where}: {e}")
                    continue
                with lock:
                    if won or stop.is_set():
                        lost = True
                    else:
                        lost = False
                        won.update(req=req, path=path, cand=c)
                        stop.set()
                if lost:
                    discard(req, path)
                return
        finally:
            with lock:
                doing.pop(lane, None)
            if lane != "stream":
                with lock:
                    if not any(t.is_alive() for n, t in threads.items() if n not in ("stream", lane)):
                        slow_done.set()
                show()

    threads = {lane: threading.Thread(target=run, args=(lane, cands), daemon=True) for lane, cands in lanes.items()}
    if "stream" not in lanes or len(lanes) == 1:
        slow_done.set()
    for t in threads.values():
        t.start()
    while any(t.is_alive() for t in threads.values()):
        if job.get("cancel"):
            stop.set()
        time.sleep(1)
    if won:
        return won["req"], won["path"], won["cand"]
    if job.get("cancel"):
        tr_set(job, tr, state="cancelled")
        return None
    raise RuntimeError("; ".join(errors[-2:]) or "Download failed")


def job_finished(job):
    """Send one phone notification when a playlist job has nothing left to do."""
    with JOB_LOCK:
        if job.get("notified") or job_summary_running(job) or job.get("cancel"):
            return
        job["notified"] = True
        c = {}
        for t in job["tracks"]:
            c[t["state"]] = c.get(t["state"], 0) + 1
    save_jobs()
    verb = "upgraded" if job["kind"] == "upgrade" else "fetched"
    parts = [f"{c.get('done', 0)} {verb}"] + [f"{c[k]} {w}" for k, w in
                                              (("kept", "kept"), ("not_found", "not found"), ("failed", "failed")) if c.get(k)]
    notify(f"{job['name']}: " + ", ".join(parts))
    try:
        playlists.on_job_finished(job)
    except Exception as e:  # noqa: BLE001
        log("playlist finish hook failed:", e)


def manual_pick(job, tr, target, cand):
    """The user chose this exact copy for a playlist song."""
    try:
        where = SOURCE_NAMES.get(cand["source"], cand["source"])
        tr_set(job, tr, state="downloading", source=cand["source"],
               message=f"Getting {cand['quality']} from {where} (your choice)")
        req = {"id": "m" + uuid.uuid4().hex[:11], "target": target, "candidate": cand, "user": job.get("user")}
        path = DL[cand["source"]](req)
        final = file_into_library(path, req)
        cleanup_download(req, path)
        index_add(req["target"], final, cand)
        adopt(job, tr, final, cand["quality"], "Your choice", cand.get("tier"), False)
    except Exception as e:  # noqa: BLE001
        log("manual pick failed:", traceback.format_exc())
        tr_set(job, tr, state="failed", message=str(e)[:200], last_try=time.time())
    finally:
        job_finished(job)


def process_track(job, tr):
    try:
        _process_track(job, tr)
    finally:
        job_finished(job)


def _process_track(job, tr):
    if job.get("cancel"):
        tr_set(job, tr, state="cancelled")
        return
    try:
        tr_set(job, tr, state="searching", message="Looking for the best copy")
        cur = tr.get("new_file") or tr.get("file") or ""
        cur_tier = (tr.get("new_tier") if tr.get("new_file") else tr.get("tier")) or ""
        cur_rank = pv(file_score(cur)) if cur and os.path.exists(cur) else -1
        if tr.get("new_file"):                       # a "better later" re-check: keep the stand-in as the base
            tr_set(job, tr, file=cur, tier=cur_tier, quality=tr.get("new_quality") or tr.get("quality"))

        # 1. already upgraded through another playlist?
        hit = index_get(tr["artist"], tr["title"])
        hit_score = (hit.get("score") or TIER_SCORE.get(hit["tier"], 0)) if hit else 0
        if hit and pv(hit_score) > cur_rank:
            return adopt(job, tr, hit["path"], hit["quality"], "Already in your library", hit["tier"],
                         not good_enough(hit_score))
        if cur_rank >= min(cap(), 10000):            # lossless is the top for "best": no 24-bit hunting
            tr_set(job, tr, state="kept", message="Already lossless" if cur_rank >= 10000 else "Already meets your quality profile",
                   last_try=time.time())
            if writes_m3u(job):
                write_job_playlist(job)
            return

        # 2. import lists: is a good copy already in Navidrome?
        if not cur:
            target0 = make_target(tr["artist"], tr["title"])
            for s in nd_search(f"{tr['artist']} {tr['title']}".strip()):
                if match(f"{s['artist']} {s['title']}", target0)[1]:
                    p = os.path.join(CONFIG["music"], s.get("path") or "")
                    if s.get("path") and os.path.exists(p):
                        tier, label = file_quality(p)
                        tr_set(job, tr, file=p, quality=label, tier=tier)
                        cur, cur_rank = p, pv(file_score(p))
                        break
            if cur and good_enough(cur_rank):
                tr_set(job, tr, state="kept", message="Already in your library in good quality")
                write_job_playlist(job)
                return

        # 3. search every batch source, pick automatically
        target = make_target(tr["artist"], tr["title"], "", tr.get("duration") or 0, tr.get("album", ""), tr.get("cover", ""))
        heard = {}
        cands = gather(target, CONFIG["batch_sources"], heard)
        picks = pick_auto(cands, cur_rank)
        below = []
        if not CONFIG["best_effort"]:                # only your quality: wait for it rather than settle
            below = [c for c in picks if not good_enough(cscore(c))]
            picks = [c for c in picks if good_enough(cscore(c))]
        if job.get("cancel"):
            tr_set(job, tr, state="cancelled")
            return
        silent = "soulseek" in CONFIG["batch_sources"] and not heard.get("soulseek")
        if silent and not any(c["source"] == "soulseek" for c in cands) and not tr.get("silent_retry"):
            # Soulseek didn't answer at all (not "no copies": no answer). Try once more in an hour
            # instead of settling for a worse copy or calling the song missing.
            tr_set(job, tr, state="not_found", silent_retry=True, retry_at=time.time() + 3600, last_try=time.time(),
                   message="Soulseek didn't answer; trying again in an hour")
            return
        tr.pop("silent_retry", None)
        if not picks and below:
            tr_set(job, tr, state="kept" if cur else "not_found", last_try=time.time(),
                   message=f"Waiting for your quality: only {below[0]['quality']} found (best effort is off)")
            if cur and writes_m3u(job):
                write_job_playlist(job)
            return
        if not picks:
            if cur:
                tr_set(job, tr, state="kept", message=kept_reason(cands, cur_rank), last_try=time.time())
                if writes_m3u(job):
                    write_job_playlist(job)
            else:
                tr_set(job, tr, state="not_found", last_try=time.time(),
                       message=f"Only other versions found" if cands else "Not found on any source")
            return

        # 4. race the sources, file the winner, retire the old copy
        won = race(job, tr, target, picks)
        if not won:
            return
        req, path, best = won
        final = file_into_library(path, req)
        cleanup_download(req, path)
        index_add(req["target"], final, best)
        later = not good_enough(cscore(best))
        adopt(job, tr, final, best["quality"], f"Upgraded from {tr.get('quality') or 'nothing'}"
              + (" · will look for a better copy later" if later else ""), best["tier"], later)
    except Exception as e:  # noqa: BLE001
        log("playlist track failed:", tr.get("artist"), "-", tr.get("title"), ":", e)
        tr_set(job, tr, state="failed", message=str(e)[:200], last_try=time.time())


def job_summary(job, with_tracks=False):
    with JOB_LOCK:
        counts = {}
        for t in job["tracks"]:
            counts[t["state"]] = counts.get(t["state"], 0) + 1
        done = sum(v for k, v in counts.items() if k not in ("pending", "searching", "downloading"))
        out = {"id": job["id"], "name": job["name"], "kind": job["kind"], "total": len(job["tracks"]),
               "settled": done, "counts": counts, "created": job["created"], "updated": job["updated"],
               "running": done < len(job["tracks"]) and not job.get("cancel"), "cancelled": job.get("cancel", False),
               "paused": bool(job.get("paused")), "priority": job_priority(job),
               "resting_until": SLSK["pause_until"] if SLSK["pause_until"] > time.time() else 0}
        if with_tracks:
            out["tracks"] = [dict(t) for t in job["tracks"]]
    return out


def requeue(job, states, older_than=0):
    n = 0
    job["notified"] = False
    for tr in job["tracks"]:
        if tr["state"] in states and time.time() - (tr.get("last_try") or 0) >= older_than:
            tr_set(job, tr, state="pending", message="Waiting to retry")
            BATCH.submit(process_track, job, tr)
            n += 1
    return n


def retry_loop():
    last_long = time.time()
    while True:
        time.sleep(600)
        with JOB_LOCK:
            jobs = [j for j in JOBS.values() if not j.get("cancel")]
        jobs = [j for j in jobs if playlists.watching(j["m3u"])]      # you stopped watching the others
        for j in jobs:                                        # quick retries (Soulseek was silent)
            for tr in j["tracks"]:
                if tr["state"] == "not_found" and tr.get("retry_at") and time.time() >= tr["retry_at"]:
                    tr.pop("retry_at", None)
                    tr_set(j, tr, state="pending", message="Trying again")
                    BATCH.submit(process_track, j, tr)
        if time.time() - last_long < 6 * 3600:
            continue
        last_long = time.time()
        for j in jobs:
            n = requeue(j, ("not_found", "failed"), CONFIG["retry_days"] * 86400)
            for tr in j["tracks"]:
                if tr["state"] in ("done", "kept") and tr.get("better_later") \
                        and time.time() - (tr.get("last_try") or 0) >= CONFIG["retry_days"] * 86400:
                    tr_set(j, tr, state="pending", message="Looking for a better copy")
                    BATCH.submit(process_track, j, tr)
                    n += 1
            if n:
                log(f"retrying {n} tracks in playlist {j['name']}")


LOCAL_CACHE = {"at": 0, "data": []}


def local_playlists():
    if time.time() - LOCAL_CACHE["at"] < 60:
        return LOCAL_CACHE["data"]
    out = []
    root = CONFIG["spotify_dir"]
    if os.path.isdir(root):
        for d, _s, files in os.walk(root):
            for f in files:
                if f.lower().endswith((".m3u", ".m3u8")) and not f.startswith("."):
                    p = os.path.join(d, f)
                    entries = m3u_entries(p)
                    tiers = {}
                    for e in entries:
                        tier = file_quality(e)[0] if os.path.exists(e) else "missing"
                        tiers[tier] = tiers.get(tier, 0) + 1
                    out.append({"path": p, "name": os.path.splitext(f)[0], "tracks": len(entries), "tiers": tiers})
    out.sort(key=lambda x: x["name"].lower())
    LOCAL_CACHE.update(at=time.time(), data=out)
    return out


def start_upgrade(path):
    if not within(path, CONFIG["spotify_dir"]) or not os.path.isfile(path):
        raise ValueError("Not a playlist in the Spotify Playlists folder")
    with JOB_LOCK:
        for j in JOBS.values():
            if j["m3u"] == path and job_summary_running(j):
                return j["id"]
    tracks = [describe_file(e) for e in m3u_entries(path)]
    LOCAL_CACHE["at"] = 0
    return new_job(os.path.splitext(os.path.basename(path))[0], "upgrade", path, tracks)


def job_summary_running(j):
    return not j.get("cancel") and any(t["state"] in ("pending", "searching", "downloading") for t in j["tracks"])


def start_import(name, text="", csv_text="", url=""):
    if url:
        sp_name, tracks = share.list_for(url)
        name = name or sp_name or "Imported playlist"
    elif csv_text:
        tracks = parse_csv(csv_text)
    else:
        tracks = parse_list(text)
    if not tracks:
        raise ValueError("No songs found in that list")
    name = safe(name or "Imported playlist")
    return new_job(name, "import", os.path.join(CONFIG["playlists"], name + ".m3u8"), tracks)


def load_jobs():
    junk = 0
    for j in _load_json("playlist_jobs.json", []):
        keep = [t for t in j["tracks"] if not (t.get("file") and not os.path.splitext(t["file"])[1])]
        junk += len(j["tracks"]) - len(keep)
        j["tracks"] = keep                    # spotDL's "*" lines are not songs
        JOBS[j["id"]] = j
    if junk:
        log(f"dropped {junk} '*' entries from playlist jobs")
    resume = 0
    for j in JOBS.values():
        if j.get("cancel"):
            continue
        for tr in j["tracks"]:
            if tr["state"] in ("pending", "searching", "downloading"):
                tr["state"], tr["message"] = "pending", "Resumed after restart"
                BATCH.submit(process_track, j, tr)
                resume += 1
    if resume:
        log(f"resuming {resume} playlist tracks")


# --------------------------------------------------------------------------- settings (changeable in the app)
SETTINGS = {   # key: (type, choices or None, description)
    "quality": (str, list(PROFILES), "Quality profile"),
    "best_effort": (bool, None, "Take the best copy there is when your quality isn't available"),
    "sources": (list, ["soulseek", "usenet", "soundcloud", "youtube"], "Sources for searches"),
    "batch_sources": (list, ["soulseek", "usenet", "soundcloud", "youtube"], "Sources for playlists"),
    "slsk_stall_s": (int, None, "Drop a Soulseek peer that doesn't move after (seconds)"),
    "stream_after_s": (int, None, "Streaming sites join a playlist download after (seconds)"),
    "retry_days": (int, None, "Retry songs that weren't found after (days)"),
    "ntfy": (str, None, "Notifications address (ntfy)"),
}


# Where people can support the project. Forks can point these elsewhere (or blank them) with the env vars.
SUPPORT_LINKS = [
    ("GitHub Sponsors", E("SUPPORT_GITHUB", "")),
    ("Ko-fi", E("SUPPORT_KOFI", "https://ko-fi.com/mdz10111")),
]


def settings_view():
    out = {k: CONFIG.get(k) for k in SETTINGS}
    out["support"] = [{"name": n, "url": u} for n, u in SUPPORT_LINKS if u.startswith("https://")]
    for k, v in links.settings_view().items():
        out[k] = v
    for mod in EXTRA_SETTINGS:
        out.update(mod.settings_view())
    return out


EXTRA_SETTINGS = []      # other modules that keep their own settings


def apply_settings(new, save=True):
    """Validate and apply settings from the app; unknown keys go to the module that owns them."""
    changed = {}
    for k, v in (new or {}).items():
        if k in SETTINGS:
            typ, choices, _d = SETTINGS[k]
            if typ is list:
                v = [x for x in (v or []) if not choices or x in choices]
            elif typ is int:
                v = max(0, int(v))
            elif typ is bool:
                v = v in (True, 1, "1", "true", "on", "yes")
            else:
                v = str(v or "").strip()
                if choices and v not in choices:
                    raise ValueError(f"{k} must be one of {', '.join(choices)}")
            CONFIG[k] = v
            changed[k] = v
        else:
            for mod in [links] + EXTRA_SETTINGS:
                if mod.apply_setting(k, v):
                    changed[k] = v
                    break
    if save:
        stored = _load_json("settings.json", {})
        stored.update(changed)
        _save_json("settings.json", stored)
    return changed


def load_settings():
    try:
        apply_settings(_load_json("settings.json", {}), save=False)
    except Exception as e:  # noqa: BLE001
        log("settings file ignored:", e)


# --------------------------------------------------------------------------- web server
class Handler(BaseHTTPRequestHandler):
    server_version = "Umculo/" + VERSION

    def end_headers(self):
        # security headers on every answer: no framing (clickjacking), no type sniffing, nothing loaded from strange places
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=(), payment=()")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data: blob: https:; media-src 'self' blob: https:; connect-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
        if self.headers.get("X-Forwarded-Proto") == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        super().end_headers()

    def parse_request(self):
        ok = super().parse_request()
        users.LOCAL.ip = users.client_ip(self) if ok else ""     # who's knocking (the real address behind Cloudflare)
        users.LOCAL.dev = None
        if ok:
            users.note_address(self)
        return ok

    def log_message(self, fmt, *args):   # quiet access log
        pass

    # -- helpers
    def send_json(self, obj, code=200):
        headers = {"Cache-Control": "no-store"}
        secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") == "https") else ""
        cookies = []
        if isinstance(obj, dict) and "_cookie" in obj:
            tok = obj.pop("_cookie")
            cookies.append(f"umc_s={tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age={users.SESSION_DAYS * 86400}{secure}"
                           if tok else "umc_s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
        if isinstance(obj, dict) and "_device" in obj:
            dtok = obj.pop("_device")                # this browser's device (approved devices, remembered 2-step)
            if dtok:
                cookies.append(f"umc_d={dtok}; Path=/; HttpOnly; SameSite=Lax; Max-Age={400 * 86400}{secure}")
        if cookies:
            headers["Set-Cookie"] = cookies
        self.send_bytes(json.dumps(obj).encode(), "application/json", code, headers)

    def send_bytes(self, body, ctype, code=200, headers=None):
        """Compressed when the browser accepts it (pages and lists shrink 4-8x)."""
        if len(body) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or "") and \
                (ctype.startswith("text/") or ctype in ("application/json", "application/javascript", "image/svg+xml")):
            body = gzip.compress(body, 6)
            headers = {**(headers or {}), "Content-Encoding": "gzip", "Vary": "Accept-Encoding"}
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            for one in (v if isinstance(v, list) else [v]):
                self.send_header(k, one)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def authed(self):
        users.LOCAL.user = None
        h = self.headers.get("Authorization") or ""
        tok = h[7:].strip() if h.startswith("Bearer ") else \
            urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("token", [""])[0]
        if tok:                                        # access keys: the Shortcut and the phone app
            t = share.token_info(tok)
            if not t:
                return False
            users.LOCAL.user = users.USERS.get(t.get("user")) or users.admin_user() or users.OPEN
            return True
        users.LOCAL.user = users.resolve(self)
        return users.LOCAL.user is not None

    def deny(self):
        body = b'{"error": "Sign in first", "login": true}'
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes
    def do_DELETE(self):  # noqa: N802
        if jellyfin.is_jellyfin(self.path):
            return jellyfin.handle(self, "DELETE")
        self.send_response(405)
        self.end_headers()

    def do_HEAD(self):  # noqa: N802
        if jellyfin.is_jellyfin(self.path):
            return jellyfin.handle(self, "GET")
        if self.path.startswith("/rest/"):
            return subsonic.handle(self, "GET")
        self.send_response(200)
        self.end_headers()

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            return self.send_json({"ok": True})
        if self.path.startswith("/rest/"):           # Subsonic apps (Amperfy...) use Navidrome's login
            return subsonic.handle(self, "GET")
        if jellyfin.is_jellyfin(self.path):          # Jellyfin music apps (Finamp...)
            return jellyfin.handle(self, "GET")
        if self.path in ("/manifest.json", "/apple-touch-icon.png", "/icon-192.png", "/icon-512.png", "/", "/index.html"):
            return self.static(self.path)            # the page itself shows the sign-in when needed
        if not self.authed():
            return self.deny()
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        try:
            g = users.gate("GET", u.path, {})
        except Exception as e:  # noqa: BLE001
            g = (400, {"error": str(e)})
        if g:
            return self.send_json(g[1], g[0])
        try:
            if u.path == "/api/search":
                return self.send_json(api_search(qs.get("q", [""])[0]))
            if u.path.startswith("/api/check/"):
                job = CHECKS.get(u.path.rsplit("/", 1)[-1])
                return self.send_json(check_view(job)) if job else self.send_json({"error": "unknown check"}, 404)
            if u.path == "/api/requests":
                me = users.current()
                with REQ_LOCK:
                    items = sorted(REQS.values(), key=lambda r: r["created"], reverse=True)
                if not users.is_admin(me):
                    items = [r for r in items if r.get("user") == me["id"]]
                names = {u["id"]: u["name"] for u in users.USERS.values()}
                items = [{**{k: v for k, v in r.items() if k != "stop"}, "who": names.get(r.get("user"), "")} for r in items[:100]]
                return self.send_json({"requests": items})
            if u.path == "/api/related":
                return self.send_json({"tracks": deezer_related(qs.get("id", [""])[0])})
            if u.path == "/api/playlists":
                with JOB_LOCK:
                    jobs = sorted((j for j in JOBS.values() if users.sees_job(j)), key=lambda j: j["created"], reverse=True)
                return self.send_json({"local": local_playlists() if users.is_admin() else [],
                                       "jobs": [job_summary(j) for j in jobs]})
            if u.path.startswith("/api/playlists/jobs/"):
                job = JOBS.get(u.path.rsplit("/", 1)[-1])
                return self.send_json(job_summary(job, True)) if job and users.sees_job(job) else self.send_json({"error": "unknown"}, 404)
            if u.path == "/api/settings":
                return self.send_json(settings_view())
            if u.path == "/api/config":
                return self.send_json({"sources": CONFIG["sources"], "navidrome": bool(CONFIG["nd_user"]),
                                       "ntfy": bool(CONFIG["ntfy"]), "version": VERSION,
                                       "quality": CONFIG["quality"]})
            if u.path.startswith("/api/warm/"):               # get the next songs ready while this one plays
                kind, sid = u.path.split("/")[-2:]
                who = users.current() or users.admin_user() or users.OPEN
                if kind == "d":
                    POOL.submit(subsonic.deliver, sid)
                elif builtin():
                    POOL.submit(server.warm, who, sid, subsonic.CFG["stream_quality"], qs.get("can", [""])[0], server.network())
                return self.send_json({"ok": True})
            if u.path.startswith("/api/stream/"):             # full song from the internet, for this page
                did = u.path.rsplit("/", 1)[-1]
                t0, cached = time.time(), bool(subsonic.src_path(did))
                path = subsonic.deliver(did)
                if not self.headers.get("Range", "").startswith("bytes=") or self.headers.get("Range") == "bytes=0-":
                    subsonic.note_internet(did, t0, cached, "Umculo web", path)
                return subsonic.serve_file(self, path)
            if builtin() and u.path.startswith(("/api/ndcover/", "/api/ndstream/")):   # Umculo serves these itself
                sid, sq = u.path.rsplit("/", 1)[-1], subsonic.CFG["stream_quality"]
                try:
                    if u.path.startswith("/api/ndcover/"):
                        sz = qs.get("s", ["300"])[0]
                        return server.cover(self, {"id": [sid], "size": [sz if sz.isdigit() and 8 <= int(sz) <= 1200 else "300"]})
                    p = {"id": [sid], "c": ["Umculo web"], "can": qs.get("can", [""]), "prepare": ["1"]}
                    p.update({"format": ["raw"]} if sq == "best" else {"format": ["mp3"], "maxBitRate": [str(sq)]})
                    return server.stream(self, users.current() or users.admin_user() or users.OPEN, p)
                except server.ApiError as e:
                    return self.send_json({"error": str(e)}, 404)
            if u.path.startswith("/api/ndcover/"):            # cover art from your library, for this page
                q = {**nd_params(), "id": u.path.rsplit("/", 1)[-1], "size": qs.get("s", ["300"])[0]}
                return subsonic.proxy(self, "GET", "/rest/getCoverArt", urllib.parse.urlencode(q))
            if u.path.startswith("/api/ndstream/"):           # a song from your library, for this page
                sq = subsonic.CFG["stream_quality"]          # your streaming quality setting
                q = {**nd_params(), "id": u.path.rsplit("/", 1)[-1]}
                q.update({"format": "raw"} if sq == "best" else {"format": "mp3", "maxBitRate": sq})
                return subsonic.proxy(self, "GET", "/rest/stream", urllib.parse.urlencode(q))
            if u.path.startswith("/api/streaminfo/"):         # what the web player is playing, and in what quality
                kind, sid = u.path.split("/")[-2:]
                if kind == "d":
                    return self.send_json(subsonic.stream_info(sid))
                if builtin():
                    return self.send_json(server.stream_label(users.current() or users.admin_user() or users.OPEN, sid,
                                                              subsonic.CFG["stream_quality"], qs.get("can", [""])[0]))
                try:
                    s = (users.nd_as(users.current(), "getSong", id=sid) or {}).get("song") or {}
                except Exception:  # noqa: BLE001
                    s = {}
                if not s:
                    return self.send_json({})
                suf, br, sq = (s.get("suffix") or "").lower(), int(s.get("bitRate") or 0), subsonic.CFG["stream_quality"]
                orig = f"{suf.upper()} {br}" if suf not in LOSSLESS else f"{suf.upper()} {br} kbps"
                if sq == "best" or (suf == "mp3" and br <= int(sq) + 8):
                    return self.send_json({"quality": orig, "tier": "lossless" if suf in LOSSLESS else "", "from": "Your library"})
                return self.send_json({"quality": f"MP3 {sq}", "from": f"Your library, converted from {orig}"})
            out = links.handle_get(u.path, qs)
            if out is None:
                out = listening.handle_get(u.path, qs)
            if out is None:
                out = (foryou.handle_get(u.path, qs) or library.handle_get(u.path, qs) or status.handle_get(u.path, qs)
                       or share.handle_get(u.path, qs) or playlists.handle_get(u.path, qs) or recipes.handle_get(u.path, qs)
                       or artists.handle_get(u.path, qs) or users.handle_get(u.path, qs) or libview.handle_get(u.path, qs)
                       or connectors.handle_get(u.path, qs))
            if out is not None:
                return self.send_json(out)
            return self.static(u.path)
        except PermissionError as e:
            return self.send_json({"error": str(e)}, 403)
        except LookupError as e:
            return self.send_json({"error": str(e)}, 404)
        except Exception as e:  # noqa: BLE001
            log("GET failed:", traceback.format_exc())
            return self.send_json({"error": str(e)}, 500)

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/rest/"):
            return subsonic.handle(self, "POST")
        if jellyfin.is_jellyfin(self.path):
            return jellyfin.handle(self, "POST")
        p0 = self.path.split("?")[0]
        if p0 in ("/api/login", "/api/setup"):          # signing in needs no session
            users.LOCAL.user = None
            try:
                return self.send_json(users.handle_post(p0, self.body(), self))
            except security.Step as e:              # one more step: the 2-step code, or the admin's OK for this device
                return self.send_json({"error": str(e), **{k: v for k, v in e.data.items() if k != "cookie"},
                                       "_device": e.data.get("cookie")}, 401)
            except PermissionError as e:
                return self.send_json({"error": str(e)}, 401)
            except ValueError as e:
                return self.send_json({"error": str(e)}, 400)
        if not self.authed():
            return self.deny()
        if p0 != "/api/recognize":
            self._b = self.body()
            try:
                g = users.gate("POST", p0, self._b)
            except PermissionError as e:
                g = (403, {"error": str(e)})
            except Exception as e:  # noqa: BLE001  a bad request is an answer, never a dropped line
                g = (400, {"error": str(e)})
            if g:
                return self.send_json(g[1], g[0])
        return self.route_post()

    def run_post(self, path, body):
        """Run a POST as if it came from the app (approved requests); -> its answer."""
        fake = object.__new__(Handler)
        fake.path, fake._b, fake.headers, fake.out = path, body, {}, []
        fake.send_json = lambda obj, code=200: fake.out.append((code, obj))
        fake.route_post()
        return fake.out[0] if fake.out else (200, {})

    def route_post(self):
        try:
            if self.path.split("?")[0] == "/api/recognize":
                n = int(self.headers.get("Content-Length") or 0)
                if not n or n > 8_000_000:
                    return self.send_json({"error": "Send 5 to 15 seconds of audio"}, 400)
                return self.send_json(share.recognize(self.rfile.read(n)))
            b = self._b
            if self.path == "/api/check":
                if b.get("text"):
                    target = resolve_free_text(b["text"])
                else:
                    tr = b.get("track") or {}
                    target = make_target(tr.get("artist", ""), tr.get("title", ""), tr.get("version", ""),
                                         tr.get("duration", 0), tr.get("album", ""),
                                         tr.get("cover_big") or tr.get("cover", ""), tr.get("deezer_id"))
                if not target["core_tokens"]:
                    return self.send_json({"error": "Type at least a song title"}, 400)
                return self.send_json({"id": start_check(target)})
            if self.path == "/api/get":
                job = CHECKS.get(b.get("check_id", ""))
                if not job:
                    return self.send_json({"error": "That availability check has expired; check again"}, 404)
                with CHECK_LOCK:
                    cands = job["candidates"]
                    if b.get("candidate_id") == "best":
                        picks = pick_auto(cands, -1, 1)
                        pool = [c for c in cands if c["exact"]] or cands
                        cand = picks[0] if picks else (max(pool, key=rank) if pool else None)
                    else:
                        cand = next((c for c in cands if c["id"] == b.get("candidate_id")), None)
                if not cand:
                    return self.send_json({"error": "Nothing to download yet"}, 400)
                return self.send_json({"id": start_request(job["target"], cand)})
            if self.path == "/api/playlists/upgrade":
                if b.get("all"):
                    ids = playlists.upgrade_all()
                    return self.send_json({"ids": ids})
                return self.send_json({"id": start_upgrade(b.get("path", ""))})
            if self.path == "/api/playlists/import":
                return self.send_json({"id": start_import(b.get("name", ""), b.get("text", ""),
                                                          b.get("csv", ""), b.get("url", ""))})
            if self.path == "/api/settings":
                apply_settings(b)
                return self.send_json(settings_view())
            m = re.match(r"^/api/playlists/jobs/([0-9a-f]+)/", self.path)
            if m and not users.sees_job(JOBS.get(m.group(1))):          # someone else's download
                return self.send_json({"error": "unknown"}, 404)
            m = re.match(r"^/api/playlists/jobs/([0-9a-f]+)/tracks/(\d+)/(check|pick)$", self.path)
            if m:
                job = JOBS.get(m.group(1))
                tr = next((t for t in (job or {}).get("tracks", []) if t["i"] == int(m.group(2))), None)
                if not tr:
                    return self.send_json({"error": "unknown song"}, 404)
                target = make_target(tr["artist"], tr["title"], "", tr.get("duration") or 0)
                if m.group(3) == "check":
                    return self.send_json({"id": start_check(target)})
                chk = CHECKS.get(b.get("check_id", ""))
                if not chk:
                    return self.send_json({"error": "That availability check has expired; check again"}, 404)
                with CHECK_LOCK:
                    cand = next((c for c in chk["candidates"] if c["id"] == b.get("candidate_id")), None)
                if not cand:
                    return self.send_json({"error": "Choose a copy"}, 400)
                if tr["state"] in ("searching", "downloading"):
                    return self.send_json({"error": "Umculo is still working on this song"}, 409)
                tr_set(job, tr, state="downloading", message="Starting your choice")
                POOL.submit(manual_pick, job, tr, chk["target"], cand)
                return self.send_json({"ok": True})
            out = links.handle_post(self.path, b)
            if out is None:
                out = listening.handle_post(self.path.split("?")[0], b)
            if out is None:
                out = (foryou.handle_post(self.path, b) or library.handle_post(self.path, b)
                       or share.handle_post(self.path.split("?")[0], b) or playlists.handle_post(self.path, b)
                       or recipes.handle_post(self.path, b) or artists.handle_post(self.path, b)
                       or users.handle_post(self.path.split("?")[0], b, self) or libview.handle_post(self.path, b)
                       or connectors.handle_post(self.path.split("?")[0], b))
            if out is not None:
                return self.send_json(out)
            m = re.match(r"^/api/playlists/jobs/([0-9a-f]+)/(prioritise|pause|resume)$", self.path)
            if m:
                job = JOBS.get(m.group(1))
                if not job:
                    return self.send_json({"error": "unknown"}, 404)
                with JOB_LOCK:
                    if m.group(2) == "prioritise":
                        job["priority"] = max([job_priority(j) for j in JOBS.values()] + [10]) + 1
                        job["paused"] = False
                    else:
                        job["paused"] = m.group(2) == "pause"
                save_jobs()
                BATCH.wake()
                return self.send_json({"ok": True})
            m = re.match(r"^/api/playlists/jobs/([0-9a-f]+)/recheck$", self.path)
            if m and JOBS.get(m.group(1)):
                n = requeue(JOBS[m.group(1)], ("kept",))
                return self.send_json({"ok": True, "count": n})
            m = re.match(r"^/api/playlists/jobs/([0-9a-f]+)/(cancel|retry|forget)$", self.path)
            if m:
                job = JOBS.get(m.group(1))
                if not job:
                    return self.send_json({"error": "unknown"}, 404)
                if m.group(2) == "cancel":
                    job["cancel"] = True
                    save_jobs()
                elif m.group(2) == "retry":
                    job["cancel"] = False
                    return self.send_json({"requeued": requeue(job, ("not_found", "failed", "cancelled"))})
                else:
                    with JOB_LOCK:
                        JOBS.pop(job["id"], None)
                    save_jobs()
                return self.send_json({"ok": True})
            m = re.match(r"^/api/requests/([0-9a-f]+)/delete$", self.path)
            if m:
                with REQ_LOCK:
                    req = REQS.get(m.group(1))
                if not req or not users.sees_job(req):
                    return self.send_json({"error": "unknown"}, 404)
                deleted = False
                if req["state"] in ("ready", "failed"):
                    if b.get("file") and req.get("file"):
                        deleted = delete_song_file(req["file"])
                        nd_scan()
                    with REQ_LOCK:
                        REQS.pop(req["id"], None)
                else:
                    update(req, message="Stopping…")
                    req["cancel"] = True             # the download stops at its next progress update
                save_reqs()
                return self.send_json({"ok": True, "deleted": deleted})
            if self.path.startswith("/api/requests/") and self.path.endswith("/dismiss"):
                rid = self.path.split("/")[3]
                with REQ_LOCK:
                    if users.sees_job(REQS.get(rid)):
                        REQS.pop(rid, None)
                save_reqs()
                return self.send_json({"ok": True})
            return self.send_json({"error": "not found"}, 404)
        except PermissionError as e:
            return self.send_json({"error": str(e)}, 403)
        except (LookupError, ValueError) as e:
            return self.send_json({"error": str(e)}, 404 if isinstance(e, LookupError) else 400)
        except Exception as e:  # noqa: BLE001
            log("POST failed:", traceback.format_exc())
            return self.send_json({"error": str(e)}, 500)

    def static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(CONFIG["static"], path.lstrip("/")))
        if not full.startswith(os.path.realpath(CONFIG["static"])) or not os.path.isfile(full):
            full = os.path.join(CONFIG["static"], "index.html")
        st = os.stat(full)
        etag = f'"{int(st.st_mtime)}-{st.st_size}"'
        if self.headers.get("If-None-Match") == etag:           # the browser already has it
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        with open(full, "rb") as fh:
            data = fh.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        self.send_bytes(data, ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""), 200,
                        {"ETag": etag, "Cache-Control": "no-cache"})


def api_search(q):
    q = (q or "").strip()
    if not q:
        return {"catalog": [], "library": []}
    with cf.ThreadPoolExecutor(2) as ex:
        f_cat = ex.submit(deezer_search, q)
        f_lib = ex.submit(nd_search, q)
        try:
            catalog = f_cat.result()
            cat_error = ""
        except Exception as e:  # noqa: BLE001
            catalog, cat_error = [], f"Catalogue search failed: {e}"
        library = f_lib.result()
    for item in catalog:
        own = owned_in(library, item)
        item["owned"] = bool(own)
        if own:
            item["owned_quality"] = quality(own.get("suffix"), own.get("bitrate"))[1]
    return {"catalog": catalog, "library": library[:10], "error": cat_error}


def main():
    for d in (CONFIG["staging"], CONFIG["config"]):
        os.makedirs(d, exist_ok=True)
    load_reqs()
    global BATCH
    BATCH = BatchQueue(CONFIG["batch_workers"])
    INDEX.update(_load_json("index.json", {}))
    links.init(sys.modules[__name__])
    subsonic.init(sys.modules[__name__])
    foryou.init(sys.modules[__name__])
    library.init(sys.modules[__name__])
    status.init(sys.modules[__name__])
    share.init(sys.modules[__name__])
    playlists.init(sys.modules[__name__])
    recipes.init(sys.modules[__name__])
    artists.init(sys.modules[__name__])
    users.init(sys.modules[__name__])
    libview.init(sys.modules[__name__])
    EXTRA_SETTINGS.extend([foryou, library, subsonic])
    load_settings()
    connectors.init(sys.modules[__name__])        # saved connections override the compose file
    musicdb.init(sys.modules[__name__])
    server.init(sys.modules[__name__])
    selftest.init(sys.modules[__name__])
    migrate.init(sys.modules[__name__])
    security.init(sys.modules[__name__])
    listening.init(sys.modules[__name__])
    importers.init(sys.modules[__name__])
    jellyfin.init(sys.modules[__name__])
    load_jobs()
    threading.Thread(target=scan_loop, daemon=True).start()
    threading.Thread(target=retry_loop, daemon=True).start()
    if not CONFIG["password"]:
        log("WARNING: UMCULO_PASSWORD is not set - the page is open to anyone on the network")
    log(f"Umculo listening on :{CONFIG['port']} - sources: {', '.join(CONFIG['sources'])}")
    ThreadingHTTPServer(("0.0.0.0", CONFIG["port"]), Handler).serve_forever()


if __name__ == "__main__":
    main()
