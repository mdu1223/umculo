"""Links, podcasts, subscriptions and the song finder.

Paste a link from SoundCloud, YouTube, Mixcloud, Audiomack, Hearthis, Bandcamp, PodOmatic,
Apple Podcasts or any podcast RSS feed. Umculo can then
  * download the track, mix or episode (mixes and episodes go to MIXES_DIR/<show>/),
  * find the songs inside a mix (the published tracklist plus audio recognition),
    and fetch those songs in good quality as a playlist,
  * subscribe to the show or channel and download each new episode as it comes out.
"""
import asyncio
import email.utils
import html
import json
import os
import re
import subprocess
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import concurrent.futures as cf

core = None            # the umculo module, set by init()
E = os.environ.get
CFG = {
    "itunes": E("ITUNES_API", "https://itunes.apple.com").rstrip("/"),
    "podomatic_feed": E("PODOMATIC_FEED", "https://{channel}.podomatic.com/rss2.xml"),
    "sub_hours": float(E("SUBSCRIPTION_CHECK_HOURS", "6")),
    "sub_max_new": int(E("SUBSCRIPTION_MAX_NEW", "3")),
    "finder_step": int(E("FINDER_STEP_SECONDS", "60")),
    "finder_pause": float(E("FINDER_PAUSE_SECONDS", "1.5")),
    "test_shazam": E("UMCULO_TEST_SHAZAM", ""),
    "auto_find": E("AUTO_FIND_SONGS", "1") == "1",
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"

SUBS, FINDS = {}, {}
LOCK = threading.Lock()
FIND_POOL = cf.ThreadPoolExecutor(max_workers=1)      # one song finder at a time (be polite to Shazam)
SHOWS = {}                                            # resolved links, short-lived cache for the UI


def log(*a):
    core.log(*a)


# --------------------------------------------------------------------------- helpers
def fetch(url, timeout=40):
    data, _h = core.http("GET", url, raw=True, timeout=timeout, headers={"User-Agent": UA}, retries=1)
    return data


def strip_html(text):
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>", "\n", text or "")
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("\r", "")


def secs(value):
    """'1:02:03', '62:03', '3723' -> seconds."""
    if value in (None, ""):
        return 0
    try:
        parts = [float(p) for p in str(value).strip().split(":")]
    except ValueError:
        return 0
    total = 0
    for p in parts:
        total = total * 60 + p
    return int(total)


def iso_date(value):
    if not value:
        return ""
    if re.match(r"^\d{8}$", str(value)):                      # yt-dlp upload_date
        v = str(value)
        return f"{v[:4]}-{v[4:6]}-{v[6:]}"
    try:
        return email.utils.parsedate_to_datetime(value).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        m = re.match(r"(\d{4}-\d{2}-\d{2})", str(value))
        return m.group(1) if m else ""


def entry(**kw):
    e = {"id": "", "title": "", "uploader": "", "date": "", "duration": 0, "media": "", "page": "", "description": "",
         "cover": "", "quality": "", "tier": "unknown", "chapters": []}
    e.update({k: v for k, v in kw.items() if v is not None})
    e["id"] = str(e["id"] or e["media"] or e["page"] or e["title"])
    return e


# --------------------------------------------------------------------------- RSS feeds
def parse_feed(data, feed_url):
    root = ET.fromstring(data)
    ch = root.find("channel")
    if ch is None:
        raise ValueError("That address isn't a podcast feed")
    img = ch.find(ITUNES_NS + "image")
    cover = img.get("href") if img is not None else (ch.findtext("image/url") or "")
    show = {"kind": "show", "podcast": True, "title": (ch.findtext("title") or "Podcast").strip(),
            "author": (ch.findtext(ITUNES_NS + "author") or "").strip(), "cover": cover,
            "feed": feed_url, "url": feed_url, "description": strip_html(ch.findtext("description"))[:600],
            "entries": []}
    for it in ch.findall("item"):
        enc = it.find("enclosure")
        media = enc.get("url") if enc is not None else ""
        if not media:
            continue
        im = it.find(ITUNES_NS + "image")
        desc = it.findtext(CONTENT_NS + "encoded") or it.findtext("description") or it.findtext(ITUNES_NS + "summary") or ""
        ext = media.split("?")[0].rsplit(".", 1)[-1].lower()
        show["entries"].append(entry(
            id=(it.findtext("guid") or media).strip(), title=(it.findtext("title") or "").strip(),
            date=iso_date(it.findtext("pubDate")), duration=secs(it.findtext(ITUNES_NS + "duration")),
            media=media, page=(it.findtext("link") or "").strip(), description=strip_html(desc)[:8000],
            cover=im.get("href") if im is not None else cover,
            quality=f"{ext.upper()} (podcast)" if ext in core.AUDIO else "Podcast audio"))
    return show


def load_feed(feed_url):
    return parse_feed(fetch(feed_url), feed_url)


def discover_feed(page_url):
    """Find <link rel="alternate" type="application/rss+xml"> on a web page."""
    page = fetch(page_url).decode("utf-8", "replace")
    for m in re.finditer(r"<link[^>]+>", page, re.I):
        tag = m.group(0)
        if re.search(r"rss\+xml|atom\+xml", tag, re.I):
            h = re.search(r'href=["\']([^"\']+)', tag)
            if h:
                return urllib.parse.urljoin(page_url, html.unescape(h.group(1)))
    return ""


# --------------------------------------------------------------------------- per-site resolvers
def resolve_apple(url):
    sid = re.search(r"/id(\d+)", url)
    if not sid:
        raise ValueError("Couldn't read the show number from that Apple Podcasts link")
    ep = re.search(r"[?&]i=(\d+)", url)
    res = core.http("GET", f"{CFG['itunes']}/lookup?id={sid.group(1)}&entity=podcastEpisode&limit=200",
                    timeout=30) or {}
    items = res.get("results") or []
    if not items:
        raise ValueError("Apple doesn't list that podcast")
    info = items[0]
    eps = [r for r in items[1:] if r.get("wrapperType") == "podcastEpisode" or r.get("kind") == "podcast-episode"]
    show = None
    if info.get("feedUrl"):
        try:
            show = load_feed(info["feedUrl"])
        except Exception as e:  # noqa: BLE001
            log("apple feed failed, using Apple's episode list:", e)
    if not show:                                   # Apple-only show: build from the lookup itself
        show = {"kind": "show", "podcast": True, "title": info.get("collectionName", "Podcast"),
                "author": info.get("artistName", ""), "cover": info.get("artworkUrl600", ""), "feed": "",
                "url": url, "description": "", "entries": [entry(
                    id=r.get("episodeGuid") or r.get("trackId"), title=r.get("trackName", ""),
                    date=iso_date((r.get("releaseDate") or "")[:10]), duration=int((r.get("trackTimeMillis") or 0) / 1000),
                    media=r.get("episodeUrl", ""), page=r.get("trackViewUrl", ""),
                    description=strip_html(r.get("description") or ""), cover=r.get("artworkUrl600", ""),
                    quality="Podcast audio") for r in eps if r.get("episodeUrl")]}
    show["url"] = url
    show["cover"] = show.get("cover") or info.get("artworkUrl600", "")
    if ep:
        hit = next((r for r in eps if str(r.get("trackId")) == ep.group(1)), None)
        if hit:
            guid, name, media = hit.get("episodeGuid"), hit.get("trackName", ""), hit.get("episodeUrl", "")
            focus = next((e for e in show["entries"] if (guid and e["id"] == guid) or (media and e["media"] == media)
                          or core.norm(e["title"]) == core.norm(name)), None)
            if not focus and media:
                focus = entry(id=guid or media, title=name, media=media, date=iso_date((hit.get("releaseDate") or "")[:10]),
                              duration=int((hit.get("trackTimeMillis") or 0) / 1000),
                              description=strip_html(hit.get("description") or ""), cover=show["cover"],
                              quality="Podcast audio")
                show["entries"].insert(0, focus)
            show["focus"] = focus["id"] if focus else None
    return show


def resolve_podomatic(url):
    m = (re.match(r"https?://(?:www\.)?podomatic\.com/podcasts/([^/?#]+)(?:/episodes/([^/?#]+))?", url) or
         re.match(r"https?://([^./]+)\.podomatic\.com(?:/entry/([^/?#]+))?", url))
    if not m or m.group(1) in ("www", "feeds"):
        raise ValueError("Couldn't read the show name from that PodOmatic link")
    channel, ep = m.group(1), m.group(2)
    show = load_feed(CFG["podomatic_feed"].format(channel=channel))
    show["url"] = url
    if ep:
        focus = next((e for e in show["entries"] if ep in e["page"] or ep in e["id"] or ep in e["media"]), None)
        show["focus"] = focus["id"] if focus else None
    return show


def resolve_ytdlp(url):
    with core._ydl({"extract_flat": "in_playlist"}) as y:
        info = y.extract_info(url, download=False) or {}
    if info.get("_type") in ("playlist", "multi_video") or info.get("entries") is not None:
        ents = [e for e in (info.get("entries") or []) if e]
        return {"kind": "show", "podcast": False, "title": info.get("title") or info.get("uploader") or "Channel",
                "author": info.get("uploader") or info.get("channel") or "", "cover": thumb(info),
                "feed": "", "url": info.get("webpage_url") or url, "description": "",
                "entries": [entry(id=e.get("id") or e.get("url"), title=e.get("title") or "",
                                  uploader=e.get("uploader") or e.get("channel") or e.get("artist") or "",
                                  duration=int(e.get("duration") or 0), page=e.get("url") or e.get("webpage_url") or "",
                                  date=iso_date(e.get("upload_date")), cover=thumb(e) or thumb(info),
                                  quality="Best available") for e in ents[:200]]}
    _s, label, tier, fmt, _ext = core._best_audio(info)
    e = entry(id=info.get("id"), title=info.get("title") or "", duration=int(info.get("duration") or 0),
              page=info.get("webpage_url") or url, date=iso_date(info.get("upload_date")),
              description=(info.get("description") or "")[:8000], cover=thumb(info), quality=label, tier=tier,
              chapters=[{"t": int(c.get("start_time") or 0), "title": c.get("title") or ""}
                        for c in info.get("chapters") or []])
    e["format"] = fmt
    channel = info.get("uploader_url") or info.get("channel_url") or ""
    return {"kind": "single", "podcast": False, "title": info.get("title") or "", "cover": e["cover"],
            "author": info.get("uploader") or info.get("channel") or info.get("artist") or "",
            "feed": "", "url": e["page"], "channel_url": channel, "description": "", "entries": [e], "focus": e["id"]}


def thumb(info):
    if info.get("thumbnail"):
        return info["thumbnail"]
    th = info.get("thumbnails") or []
    return (th[-1] or {}).get("url", "") if th else ""


def resolve(url):
    url = (url or "").strip()
    if not re.match(r"^https?://", url):
        raise ValueError("Paste a full link starting with https://")
    host = urllib.parse.urlparse(url).netloc.lower()
    if host.endswith("podcasts.apple.com") or host.endswith("itunes.apple.com"):
        show = resolve_apple(url)
    elif host.endswith("podomatic.com"):
        show = resolve_podomatic(url)
    elif re.search(r"(\.xml|\.rss|/rss|/feed)(/|$|\?)", url, re.I) or host.startswith("feeds."):
        show = load_feed(url)
    else:
        try:
            show = resolve_ytdlp(url)
        except Exception as e:  # noqa: BLE001
            log("yt-dlp could not read link, looking for a feed:", e)
            feed = ""
            try:
                feed = discover_feed(url)
            except Exception:  # noqa: BLE001
                pass
            if not feed:
                raise ValueError("Umculo can't read that link. Try the show's page or its RSS feed.") from e
            show = load_feed(feed)
            show["url"] = url
    show["id"] = uuid.uuid4().hex[:12]
    show["subscribable"] = bool(show.get("feed") or show["kind"] == "show" or show.get("channel_url"))
    with LOCK:
        SHOWS[show["id"]] = {"at": time.time(), "show": show}
        for k in [k for k, v in SHOWS.items() if time.time() - v["at"] > 6 * 3600]:
            SHOWS.pop(k, None)
    with LOCK:
        sub = next((s for s in SUBS.values() if s["url"] in (show["url"], url) or (show.get("feed") and s.get("feed") == show["feed"])), None)
    show["subscribed"] = sub["id"] if sub else None
    return show


def get_show(show_id):
    with LOCK:
        hit = SHOWS.get(show_id)
    if not hit:
        raise ValueError("That link has expired; paste it again")
    return hit["show"]


def find_entry(show, entry_id):
    e = next((x for x in show["entries"] if x["id"] == entry_id), None)
    if not e:
        raise ValueError("Episode not found")
    return e


# --------------------------------------------------------------------------- downloading
def candidate_for(show, e):
    long = (e.get("duration") or 0) > core.CONFIG["mix_seconds"]
    folder = show["title"] if show.get("podcast") or show["kind"] == "show" else (show.get("author") or "Mixes")
    return {"source": "link", "exact": True, "score": 0, "quality": e.get("quality") or "Best available",
            "tier": e.get("tier") or "unknown", "eta": "", "name": e["title"], "detail": show.get("author") or show["title"],
            "duration": e.get("duration"), "podcast": bool(show.get("podcast")), "folder": folder,
            "filename": f"{e['date']} - {e['title']}" if (show.get("podcast") and e.get("date")) else "",
            "long": long, "ref": {"url": e.get("media") or e.get("page"), "direct": bool(e.get("media")),
                                  "format": e.get("format") or ""}}


def target_for(show, e):
    artist = show.get("author") or show["title"] if show.get("podcast") else (show.get("author") or "")
    title = e["title"]
    if not show.get("podcast") and " - " in title and not artist:
        artist, title = [x.strip() for x in title.split(" - ", 1)]
    return core.make_target(artist or show["title"], title, "", e.get("duration") or 0,
                            show["title"] if show.get("podcast") else "", e.get("cover") or show.get("cover") or "")


JUNK_TITLE = re.compile(r"[\(\[](?:official[^\)\]]*|lyrics?[^\)\]]*|audio|visuali[sz]er|hd|hq|4k|music video|"
                        r"video|clip officiel|free download|out now)[\)\]]", re.I)


def song_from_title(title, uploader=""):
    """'Kabza De Small - Track A (Official Video) [HD]' -> ('Kabza De Small', 'Track A')."""
    t = JUNK_TITLE.sub("", title or "")
    t = re.sub(r"\s*[|｜].*$", "", t)                  # "Song - Artist | Label Name"
    t = re.sub(r"\s+", " ", t).strip(" -–—")
    a, s = split_song(t)
    if not a:
        a = re.sub(r"\s*-\s*Topic$|VEVO$|\s+Official$", "", uploader or "", flags=re.I).strip()
    return a, s


def import_show(show, name=""):
    """Every item in a YouTube/SoundCloud playlist becomes a song to fetch in the best quality."""
    tracks = []
    for e in show["entries"]:
        a, t = song_from_title(e["title"], e.get("uploader") or show.get("author", ""))
        if t:
            tracks.append({"artist": a, "title": t, "duration": e.get("duration") or 0})
    if not tracks:
        raise ValueError("No songs found in that playlist")
    name = core.safe(name or show["title"])
    return core.new_job(name, "import", os.path.join(core.CONFIG["playlists"], name + ".m3u8"), tracks)


def get_all(show):
    return [get_entry(show, e) for e in show["entries"][:300]]


def get_entry(show, e):
    return core.start_request(target_for(show, e), candidate_for(show, e))


def dl_link(req):
    cand = req["candidate"]
    ref = cand["ref"]
    out = os.path.join(core.CONFIG["staging"], req["id"])
    os.makedirs(out, exist_ok=True)
    if ref.get("direct"):
        return download_file(req, ref["url"], out)
    with core._ydl() as y:
        info = y.extract_info(ref["url"], download=False)
    _s, label, tier, fmt, _ext = core._best_audio(info)
    cand.update(quality=label, tier=tier)
    ref["format"] = fmt
    if not req["target"].get("cover") and thumb(info):
        req["target"]["cover"] = thumb(info)
    return core.dl_ytdlp(req)


def download_file(req, url, out):
    r = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=60)
    ctype = (r.headers.get("Content-Type") or "").split(";")[0]
    ext = urllib.parse.urlparse(r.geturl()).path.rsplit(".", 1)[-1].lower()
    if ext not in core.AUDIO:
        ext = {"audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "m4a",
               "audio/ogg": "ogg", "audio/flac": "flac", "audio/wav": "wav"}.get(ctype, "mp3")
    total = int(r.headers.get("Content-Length") or 0)
    path = os.path.join(out, f"episode.{ext}")
    done, last = 0, 0
    stop = req.get("stop")
    with open(path, "wb") as fh:
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if stop and stop.is_set():
                raise core.Stopped()
            if time.time() - last > 2:
                last = time.time()
                pct = int(done * 100 / total) if total else 0
                core.update(req, progress=pct, message=f"Downloading ({pct}%)" if total else f"Downloading ({done // 1_000_000} MB)")
    r.close()
    if done < 10_000:
        raise RuntimeError("The episode file came back empty")
    return path


def after_filing(req, final):
    """Called by run_request once a link download is in the library."""
    tier, label = core.file_quality(final)
    req["candidate"].update(quality=label, tier=tier)


# --------------------------------------------------------------------------- subscriptions
def save_subs():
    with LOCK:
        data = list(SUBS.values())
    core._save_json("subscriptions.json", data)


def subscribe(show, latest=False, owner=None):
    owner = owner or core.users.me_key()
    url = show.get("feed") or (show.get("channel_url") if show["kind"] == "single" else show["url"])
    with LOCK:
        for s in SUBS.values():
            if s["url"] == url and (s.get("owner") or "admin") == owner:
                return s["id"]
    if show["kind"] == "single" and show.get("channel_url"):         # subscribed from one upload: follow the channel
        show = resolve_ytdlp(show["channel_url"])
        url = show["url"]
    sid = uuid.uuid4().hex[:12]
    ents = show["entries"]
    sub = {"id": sid, "title": show["title"], "author": show.get("author", ""), "cover": show.get("cover", ""),
           "url": url, "feed": show.get("feed", ""), "podcast": bool(show.get("podcast")),
           "seen": [e["id"] for e in ents[1:]] if latest else [e["id"] for e in ents],
           "created": time.time(), "last_check": time.time(), "last_new": None, "error": "", "fetched": 0, "owner": owner}
    with LOCK:
        SUBS[sid] = sub
    save_subs()
    if latest and ents:
        run_sub(sub)
    return sid


PENDING = {}                   # url -> title, subscriptions being set up in the background


def subscribe_async(show, latest=False):
    """Subscribing can take a while (a whole channel has to be read): answer at once, work behind."""
    url = show.get("feed") or (show.get("channel_url") if show["kind"] == "single" else show["url"])
    owner = core.users.me_key()
    with LOCK:
        for s in SUBS.values():
            if s["url"] in (url, show.get("url")) and (s.get("owner") or "admin") == owner:
                return {"id": s["id"], "state": "done"}
        if (owner, url) in PENDING:
            return {"id": None, "state": "pending"}
        PENDING[(owner, url)] = show.get("author") or show["title"]
    url = (owner, url)

    def run():
        try:
            subscribe(show, latest, owner)
        except Exception as e:  # noqa: BLE001
            log("subscribe failed:", show["title"], e)
        finally:
            with LOCK:
                PENDING.pop(url, None)
    threading.Thread(target=run, daemon=True).start()
    return {"id": None, "state": "pending"}


def run_sub(sub):
    """Check a subscription in the background as the person who subscribed: their folder, their queue."""
    core.subsonic.as_person(core.users.person(sub.get("owner") or "admin"), check_sub, sub)


def check_sub(sub):
    try:
        show = load_feed(sub["feed"]) if sub.get("feed") else resolve_ytdlp(sub["url"])
        if not sub.get("feed"):
            show["kind"] = "show"
        new = [e for e in show["entries"] if e["id"] not in set(sub["seen"])]
        for e in new[:CFG["sub_max_new"]]:
            sh = {**show, "title": sub["title"], "podcast": sub["podcast"] or show.get("podcast")}
            long = (e.get("duration") or 0) > core.CONFIG["mix_seconds"] or sh["podcast"]
            if CFG["auto_find"] and long:
                start_find(sh, e, keep=True, auto=True, owner=sub.get("owner") or "admin")   # downloads it, lists its songs
            else:
                get_entry(sh, e)
            sub["fetched"] = sub.get("fetched", 0) + 1
            sub["last_new"] = time.time()
            log("subscription", sub["title"], "new:", e["title"])
        sub["seen"] = list(dict.fromkeys(sub["seen"] + [e["id"] for e in new]))[-2000:]
        sub["error"] = ""
    except Exception as e:  # noqa: BLE001
        sub["error"] = str(e)[:200]
        log("subscription check failed:", sub["title"], e)
    sub["last_check"] = time.time()
    save_subs()


def sub_loop():
    while True:
        time.sleep(300)
        with LOCK:
            due = [s for s in SUBS.values() if time.time() - s["last_check"] >= CFG["sub_hours"] * 3600]
        for s in due:
            core.subsonic.as_person(core.users.person(s.get("owner") or "admin"), check_sub, s).result()


# --------------------------------------------------------------------------- song finder
DASH = re.compile(r"\s+[-–—]\s+")
STAMP = re.compile(r"^\s*[\[(]?((?:\d{1,2}:)?\d{1,2}:\d{2})[\])]?\s*[-–—.:)|]?\s*")
NUM = re.compile(r"^\s*\d{1,3}\s*[.)\-:]\s*|^\s*\d{1,3}\s+(?=\S)")
JUNK = re.compile(r"https?://|www\.|@|\bfollow|\bsubscribe|instagram|facebook|twitter|tiktok|\bbookings?\b|"
                  r"\bcontact\b|e-?mail", re.I)


def split_song(text):
    text = re.sub(r"\s+", " ", text).strip(" -–—*•·")
    parts = DASH.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    return "", text


def parse_tracklist(text, chapters=None):
    """Published tracklist from a description (and YouTube chapters) -> [{t, artist, title}]."""
    out = []
    for c in chapters or []:
        a, t = split_song(c["title"])
        if a and t:
            out.append({"t": c["t"], "artist": a, "title": t})
    if len(out) >= 2:
        return out
    out = []
    for line in strip_html(text).splitlines():
        line = line.strip()
        if not line or len(line) > 140 or JUNK.search(line):
            continue
        t = None
        m = STAMP.match(line)
        if m:
            t = secs(m.group(1))
            line = line[m.end():]
        else:
            line = NUM.sub("", line, count=1)
        a, title = split_song(line)
        if a and title and len(a) < 80:
            out.append({"t": t, "artist": a, "title": title})
    return out if len(out) >= 2 else []


def recognizer():
    """-> function(wav_bytes, offset) -> (artist, title) or None"""
    if CFG["test_shazam"]:
        table = json.load(open(CFG["test_shazam"]))
        return lambda _b, off: next(((r[2], r[3]) for r in table if r[0] <= off < r[1]), None)
    try:
        from shazamio import Shazam
    except ImportError:
        return None
    loop = asyncio.new_event_loop()
    shz = loop.run_until_complete(_make_shazam(Shazam))

    def rec(wav, _off):
        res = loop.run_until_complete(shz.recognize(wav, max_skew=0.01))
        tr = res.get("track") or {}
        if tr.get("title") and tr.get("subtitle"):
            return tr["subtitle"], tr["title"]
        return None
    rec.close = lambda: (loop.run_until_complete(shz.close()), loop.close())
    return rec


async def _make_shazam(cls):
    return cls()


def segment(path, offset):
    """12 seconds of the mix as a small WAV file (a real file: Shazam's reader needs the WAV sizes filled in)."""
    tmp = f"{path}.{offset}.wav"
    try:
        p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(offset), "-t", "12", "-i", path, "-ac", "1",
                            "-ar", "16000", tmp], capture_output=True, timeout=60)
        if p.returncode != 0:
            return b""
        with open(tmp, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def save_finds():
    with LOCK:
        data = list(FINDS.values())[-100:]
    core._save_json("finder.json", data)


def start_find(show, e, keep=False, auto=False, owner=None):
    owner = owner or core.users.me_key()
    with LOCK:                                     # the same episode only once (tapped twice, or a subscription)
        for f in FINDS.values():
            if f["state"] != "failed" and f["show"] == show["title"] and (f.get("owner") or "admin") == owner and \
                    ((f.get("entry") or {}).get("id") == e.get("id") or f["title"] == e["title"]):
                if keep and not f.get("keep"):
                    f["keep"] = True
                return f["id"]
    fid = uuid.uuid4().hex[:12]
    f = {"id": fid, "title": e["title"], "show": show["title"], "cover": e.get("cover") or show.get("cover", ""),
         "duration": e.get("duration") or 0, "state": "queued", "progress": 0, "message": "Waiting its turn",
         "tracks": [], "created": time.time(), "keep": keep, "auto": auto, "show_data": {k: show.get(k) for k in
         ("kind", "podcast", "title", "author", "cover", "feed", "url")}, "entry": e, "owner": owner}
    with LOCK:
        FINDS[fid] = f
    save_finds()
    FIND_POOL.submit(run_find, f)
    return fid


def fset(f, **kw):
    with LOCK:
        f.update(kw)
    save_finds()


RUNNING_REQ = {}                    # find id -> its download, so Stop can reach it


class Stopped(Exception):
    pass


def stop_find(fid):
    """Stop a song finder run (queued, downloading or listening) and forget it."""
    with LOCK:
        f = FINDS.pop(fid, None)
    if f:
        f["cancel"] = True
        r = RUNNING_REQ.get(fid)
        if r:
            r["cancel"] = True
            if r.get("stop") is not None:
                r["stop"].set()
        log("song finder stopped:", f["title"])
    save_finds()
    return bool(f)


def run_find(f):
    e, show = f["entry"], f["show_data"]
    if f.get("cancel"):
        return
    who = core.users.person(f.get("owner") or "admin")
    req = {"id": "f" + uuid.uuid4().hex[:11], "target": target_for(show, e), "candidate": candidate_for(show, e),
           "stop": threading.Event(), "user": None if not who or core.users.is_admin(who) else who["id"]}
    RUNNING_REQ[f["id"]] = req
    path = ""
    rec = None
    try:
        listed = parse_tracklist(e.get("description", ""), e.get("chapters"))
        fset(f, state="downloading", message="Downloading the mix to listen to it",
             tracks=[{**t, "from": "tracklist", "hits": 0} for t in listed])
        path = dl_link(req)
        dur = core.audio_duration(path) or e.get("duration") or 0
        rec = recognizer()
        heard, fails, rec_error = [], 0, ""
        if rec and dur:
            offsets = list(range(min(30, max(dur - 12, 0)), max(dur - 12, 1), max(CFG["finder_step"], 15)))
            for n, off in enumerate(offsets, 1):
                if f.get("cancel"):
                    raise Stopped()
                fset(f, state="listening", progress=int(n * 100 / len(offsets)),
                     message=f"Listening: {n} of {len(offsets)} samples · {len(heard)} heard so far")
                wav = segment(path, off)
                if not wav:
                    continue
                try:
                    hit = rec(wav, off)
                    fails = 0
                except Exception as ex:  # noqa: BLE001
                    log("recognition failed at", off, ":", ex)
                    hit, fails = None, fails + 1
                    if fails >= 5:
                        rec_error = f"Audio recognition stopped working ({type(ex).__name__}); showing what was found"
                        break
                if hit:
                    heard.append({"t": off, "artist": hit[0], "title": hit[1]})
                if not CFG["test_shazam"]:
                    time.sleep(CFG["finder_pause"])
        if f.get("cancel"):
            raise Stopped()
        tracks = merge_tracks(listed, heard)
        fset(f, message="Checking your library")
        for t in tracks:
            t["owned"] = owned(t["artist"], t["title"])
        if f.get("keep"):
            final = core.file_into_library(path, req)
            core.request_scan()
            f["file"] = final
            path = ""
        how = ("audio recognition" if heard else "") + (" and " if heard and listed else "") + ("the published tracklist" if listed else "")
        msg = (f"Found {core.plural(len(tracks), 'song')}" + (f" using {how}" if how else "") if tracks else
               ("No songs recognised" if rec else "No tracklist published, and audio recognition isn't installed"))
        fset(f, state="done", progress=100, tracks=tracks, message=rec_error or msg)
        core.notify(f"Song finder: {core.plural(len(tracks), 'song')} in {f['title']}")
    except Exception as ex:  # noqa: BLE001
        if f.get("cancel"):
            return
        log("song finder failed:", traceback.format_exc())
        fset(f, state="failed", message=str(ex)[:200])
    finally:
        RUNNING_REQ.pop(f["id"], None)
        if rec is not None and hasattr(rec, "close"):
            try:
                rec.close()
            except Exception:  # noqa: BLE001
                pass
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        import shutil
        shutil.rmtree(os.path.join(core.CONFIG["staging"], req["id"]), ignore_errors=True)


def title_key(title):
    """Same title words and remix words, whoever is credited ('DeepSon' vs 'Kat La Kat')."""
    t = core.make_target("", title)
    return f"{' '.join(t['core_tokens'])}|{' '.join(sorted(t['version_tokens']))}"


def merge_tracks(listed, heard):
    out, by_key = [], {}
    for t in heard:
        k = core.song_key(t["artist"], t["title"])
        same = by_key.get(k) or next((o for o in out if title_key(o["title"]) == title_key(t["title"])
                                      and len(title_key(t["title"])) > 6), None)
        if same:
            same["hits"] += 1
            continue
        item = {"t": t["t"], "artist": t["artist"], "title": t["title"], "from": "heard", "hits": 1}
        by_key[k] = item
        out.append(item)
    for t in listed:
        k = core.song_key(t["artist"], t["title"])
        tgt = core.make_target(t["artist"], t["title"])
        same = by_key.get(k) or next((o for o in out if core.match(f"{o['artist']} {o['title']}", tgt)[0]
                                      or (title_key(o["title"]) == title_key(t["title"]) and len(title_key(t["title"])) > 6)), None)
        if same:
            same["from"] = "both"
            same["artist"], same["title"] = t["artist"], t["title"]       # the DJ's own naming is usually best
            continue
        item = {"t": t["t"], "artist": t["artist"], "title": t["title"], "from": "tracklist", "hits": 0}
        by_key[k] = item
        out.append(item)
    out.sort(key=lambda x: (x["t"] is None, x["t"] or 0))
    for i, t in enumerate(out):
        t["i"] = i
    return out


def owned(artist, title):
    hit = core.index_get(artist, title)
    if hit:
        return hit["quality"]
    tgt = core.make_target(artist, title)
    for s in core.nd_search(f"{artist} {title}")[:10]:
        if core.match(f"{s.get('artist', '')} {s.get('title', '')}", tgt)[1]:
            return core.quality(s.get("suffix"), s.get("bitrate"))[1]
    return ""


def get_found(fid, indexes, name=""):
    f = FINDS.get(fid)
    if not f:
        raise ValueError("Unknown mix")
    want = [t for t in f["tracks"] if t["i"] in set(indexes)]
    if not want:
        raise ValueError("Choose at least one song")
    tracks = [{"artist": t["artist"], "title": t["title"], "duration": 0} for t in want]
    name = core.safe(name or f["title"])
    return core.new_job(name, "import", os.path.join(core.CONFIG["playlists"], name + ".m3u8"), tracks)


# --------------------------------------------------------------------------- web routes
def view_sub(s):
    return {k: s.get(k) for k in ("id", "title", "author", "cover", "url", "podcast", "last_check", "last_new",
                                  "error", "fetched", "created")}


def view_find(f, full=False):
    keys = ("id", "title", "show", "cover", "duration", "state", "progress", "message", "created", "file")
    out = {k: f.get(k) for k in keys}
    out["count"] = len(f.get("tracks") or [])
    if full:
        out["tracks"] = f.get("tracks") or []
    return out


def handle_get(path, qs):
    if path == "/api/mixes":
        with LOCK:
            subs = sorted((s for s in SUBS.values() if core.users.mine(s)), key=lambda s: s["title"].lower())
            finds = sorted((f for f in FINDS.values() if core.users.mine(f)), key=lambda f: f["created"], reverse=True)
            me = core.users.me_key()
            pending = [t for (o, _u), t in PENDING.items() if o == me]
        return {"subscriptions": [view_sub(s) for s in subs], "finds": [view_find(f) for f in finds[:30]],
                "recognition": recognition_available(), "pending": pending}
    m = re.match(r"^/api/finds/([0-9a-f]+)$", path)
    if m:
        f = FINDS.get(m.group(1))
        if not core.users.mine(f):
            raise LookupError("unknown mix")
        return view_find(f, True)
    return None


def handle_post(path, b):
    if path == "/api/link":
        return resolve(b.get("url", ""))
    if path == "/api/link/get":
        show = get_show(b.get("show", ""))
        return {"id": get_entry(show, find_entry(show, b.get("entry", "")))}
    if path == "/api/link/find":
        show = get_show(b.get("show", ""))
        return {"id": start_find(show, find_entry(show, b.get("entry", "")), bool(b.get("keep")))}
    if path == "/api/link/import":
        return {"job": import_show(get_show(b.get("show", "")), b.get("name", ""))}
    if path == "/api/link/getall":
        return {"ids": get_all(get_show(b.get("show", "")))}
    if path == "/api/link/subscribe":
        return subscribe_async(get_show(b.get("show", "")), bool(b.get("latest")))
    m = re.match(r"^/api/subscriptions/([0-9a-f]+)/(check|remove)$", path)
    if m:
        s = SUBS.get(m.group(1))
        if not core.users.mine(s):
            raise LookupError("unknown subscription")
        if m.group(2) == "check":
            run_sub(s)
        else:
            with LOCK:
                SUBS.pop(s["id"], None)
            save_subs()
        return {"ok": True}
    m = re.match(r"^/api/finds/([0-9a-f]+)/(get|remove)$", path)
    if m:
        if not core.users.mine(FINDS.get(m.group(1))):
            raise LookupError("unknown mix")
        if m.group(2) == "get":
            return {"job": get_found(m.group(1), b.get("tracks") or [], b.get("name", ""))}
        stop_find(m.group(1))
        return {"ok": True}
    return None


_REC = {"ok": None}


def recognition_available():
    if _REC["ok"] is None:
        try:
            import shazamio  # noqa: F401
            _REC["ok"] = True
        except ImportError:
            _REC["ok"] = bool(CFG["test_shazam"])
    return _REC["ok"]


def settings_view():
    return {"sub_hours": CFG["sub_hours"], "finder_step": CFG["finder_step"], "auto_find": CFG["auto_find"]}


def apply_setting(k, v):
    if k == "sub_hours":
        CFG["sub_hours"] = max(1.0, float(v))
        return True
    if k == "auto_find":
        CFG["auto_find"] = bool(v)
        return True
    if k == "finder_step":
        CFG["finder_step"] = max(20, int(v))
        return True
    return False


def init(umculo):
    global core
    core = umculo
    for s in core._load_json("subscriptions.json", []):
        SUBS[s["id"]] = s
    for f in core._load_json("finder.json", []):
        if f["state"] not in ("done", "failed"):
            f["state"], f["message"] = "queued", "Resumed after restart"
            FIND_POOL.submit(run_find, f)
        FINDS[f["id"]] = f
    threading.Thread(target=sub_loop, daemon=True).start()
