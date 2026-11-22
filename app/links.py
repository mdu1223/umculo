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
    "auto_get": E("AUTO_GET_SONGS", "1") == "1",             # get each song from your sources the moment it's heard
    "listeners": max(1, min(16, int(E("FINDER_LISTENERS", "4")))),   # lookups at once (Shazam sets the real pace)
}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")
ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"

SUBS, FINDS = {}, {}
LOCK = threading.Lock()
FIND_POOL = cf.ThreadPoolExecutor(max_workers=3)      # up to three mixes listened to at once
FETCH_POOL = cf.ThreadPoolExecutor(max_workers=3)     # but downloads don't wait for someone else's listening
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
def candidate_for(show, e, video=False):
    long = (e.get("duration") or 0) > core.CONFIG["mix_seconds"]
    folder = core.mixtidy.show_name(show["title"] if show.get("podcast") else (show.get("author") or show["title"] or "Mixes"))
    return {"source": "link", "exact": True, "score": 0, "quality": e.get("quality") or "Best available",
            "tier": e.get("tier") or "unknown", "eta": "", "name": e["title"], "detail": show.get("author") or show["title"],
            "duration": e.get("duration"), "podcast": bool(show.get("podcast")), "folder": folder,
            "filename": f"{e['date']} - {e['title']}" if (show.get("podcast") and e.get("date")) else "",
            "long": long, "video": bool(video), "page": e.get("page") or "", "sub_owner": show.get("sub_owner") or "",
            "ref": {"url": e.get("media") or e.get("page"), "direct": bool(e.get("media")), "format": e.get("format") or ""}}


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


def get_all(show, video=False):
    return [get_entry(show, e, video) for e in show["entries"][:300]]


def get_entry(show, e, video=False):
    return core.start_request(target_for(show, e), candidate_for(show, e, video))


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
    c = req["candidate"]
    if c.get("page") and (c.get("long") or c.get("video") or core.within(final, core.CONFIG["mixes"])):
        core.video.record(final, c["page"], bool(c.get("video")))      # watch it later; saved now if asked
    if c.get("sub_owner"):                                              # a new mix from a show they follow
        core.notify_people.send(core.notify_people.owner_id(c["sub_owner"]), f"{c.get('name')}\nfrom {c.get('detail')}", "new_mix",
                                "New mix", core.notify_people.app_link())


# --------------------------------------------------------------------------- subscriptions
def save_subs():
    with LOCK:
        data = list(SUBS.values())
    core._save_json("subscriptions.json", data)


def subscribe(show, latest=False, owner=None, video=False):
    owner = owner or core.users.me_key()
    url = show.get("feed") or (show.get("channel_url") if show["kind"] == "single" else show["url"])
    with LOCK:
        for s in SUBS.values():
            if s["url"] == url and (s.get("owner") or "admin") == owner:
                if video and not s.get("video"):
                    s["video"] = True
                    save_subs()
                return s["id"]
    if show["kind"] == "single" and show.get("channel_url"):         # subscribed from one upload: follow the channel
        show = resolve_ytdlp(show["channel_url"])
        url = show["url"]
    sid = uuid.uuid4().hex[:12]
    ents = show["entries"]
    sub = {"id": sid, "title": show["title"], "author": show.get("author", ""), "cover": show.get("cover", ""),
           "url": url, "feed": show.get("feed", ""), "podcast": bool(show.get("podcast")),
           "seen": [e["id"] for e in ents[1:]] if latest else [e["id"] for e in ents],
           "created": time.time(), "last_check": time.time(), "last_new": None, "error": "", "fetched": 0, "owner": owner,
           "video": bool(video)}
    with LOCK:
        SUBS[sid] = sub
    save_subs()
    if latest and ents:
        run_sub(sub)
    return sid


PENDING = {}                   # url -> title, subscriptions being set up in the background


def subscribe_async(show, latest=False, video=False):
    """Subscribing can take a while (a whole channel has to be read): answer at once, work behind."""
    url = show.get("feed") or (show.get("channel_url") if show["kind"] == "single" else show["url"])
    owner = core.users.me_key()
    with LOCK:
        for s in SUBS.values():
            if s["url"] in (url, show.get("url")) and (s.get("owner") or "admin") == owner:
                if video and not s.get("video"):
                    s["video"] = True
                    threading.Thread(target=save_subs, daemon=True).start()
                return {"id": s["id"], "state": "done"}
        if (owner, url) in PENDING:
            return {"id": None, "state": "pending"}
        PENDING[(owner, url)] = show.get("author") or show["title"]
    url = (owner, url)

    def run():
        try:
            subscribe(show, latest, owner, video)
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
            sh = {**show, "title": sub["title"], "podcast": sub["podcast"] or show.get("podcast"), "video": bool(sub.get("video")),
                  "sub_owner": sub.get("owner") or "admin"}
            long = (e.get("duration") or 0) > core.CONFIG["mix_seconds"] or sh["podcast"]
            if CFG["auto_find"] and long:
                start_find(sh, e, keep=True, auto=True, owner=sub.get("owner") or "admin")   # downloads it, lists its songs
            else:
                get_entry(sh, e, sh["video"])
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
NUM = re.compile(r"^\s*#?\d{1,3}\s*(?:\.+|\)|:)\s*|^\s*#?\d{1,3}\s+[-–—]\s+(?=.+\s[-–—]\s)|^\s*#?0\d\s+(?=\S)")
# "4.", "4..", "4)", "4:", "#4 - Artist - Title", "04 Artist" (not "3 Doors Down", "50 Cent", "112 - Cupid")
JUNK = re.compile(r"https?://|www\.|@|\bfollow|\bsubscribe|instagram|facebook|twitter|tiktok|\bbookings?\b|"
                  r"\bcontact\b|e-?mail|\bbadges?\b|\bperks?\b|\bmembers?(?:hip)?\b|\bjoin (?:this|my|the|our)\b|"
                  r"\blevel \d\b|\b(?:audio|video|early|exclusive) access\b|\blinks? to\b|\bdownload links?\b|patreon|"
                  r"buy me a coffee|\bdonat|\bmerch\b|copyright|disclaimer|fair use|\btip jar\b", re.I)


def split_song(text):
    text = re.sub(r"\s+", " ", text).strip(" -–—*•·")
    bare = NUM.sub("", text, count=1).strip(" -–—*•·.")
    if DASH.search(bare):                          # a track number in front ("4..Artist - Title"): drop it
        text = bare
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
    lines = strip_html(text).splitlines()
    nums = [int(m.group(1)) for m in (re.match(r"^\s*#?(\d{1,3})\s+\S", x) for x in lines) if m]
    numbered = len(nums) >= 3 and {1, 2, 3} <= set(nums)          # a numbered list: "1 Artist - Title" ...
    for line in lines:
        line = line.strip()
        if numbered:
            line = re.sub(r"^#?\d{1,3}\s+(?=\S)", "", line)
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
    client = None
    try:                                             # no silent retries when Shazam says "too many": we pace ourselves
        from shazamio.client import HTTPClient
        from aiohttp_retry import ExponentialRetry
        client = HTTPClient(retry_options=ExponentialRetry(attempts=2, statuses={500, 502, 503, 504}))
        shz = loop.run_until_complete(_make_shazam(Shazam, http_client=client))
    except (ImportError, TypeError):
        client = None
        shz = loop.run_until_complete(_make_shazam(Shazam))

    def rec(wav, _off):
        try:
            res = loop.run_until_complete(asyncio.wait_for(shz.recognize(wav, max_skew=0.01), 25))
        except Exception as ex:  # noqa: BLE001
            if "ratelimit" in type(ex).__name__.lower() or "429" in str(ex):
                raise Throttled() from ex
            raise
        tr = res.get("track") or {}
        if tr.get("title") and tr.get("subtitle"):
            return tr["subtitle"], tr["title"]
        return None

    def close():
        loop.run_until_complete(shz.close())
        if client is not None:
            loop.run_until_complete(client.close())
        loop.close()
    rec.close = close
    return rec


async def _make_shazam(cls, **kw):
    return cls(**kw)


def segment(path, offset, headers=None, tmpdir=None):
    """12 seconds of the mix as a small WAV file (a real file: Shazam's reader needs the WAV sizes filled in).
    path is a file, or the mix's stream address (only those 12 seconds are fetched)."""
    remote = path.startswith("http")
    tmp = os.path.join(tmpdir or core.CONFIG["staging"], f"seg-{uuid.uuid4().hex[:8]}-{offset}.wav") if remote else f"{path}.{offset}.wav"
    pre = []
    if remote:
        pre = ["-reconnect", "1", "-reconnect_streamed", "1", "-rw_timeout", "20000000"]
        if headers:
            pre += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    try:
        try:
            p = subprocess.run(["ffmpeg", "-v", "error", "-y", *pre, "-ss", str(offset), "-t", "12", "-i", path, "-ac", "1",
                                "-ar", "16000", tmp], capture_output=True, timeout=20 if remote else 60)
        except subprocess.TimeoutExpired:
            return b""
        if p.returncode != 0:
            return b""
        with open(tmp, "rb") as fh:
            data = fh.read()
        return data if len(data) > 4000 else b""       # a header and no sound: nothing to listen to
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def moved_files(moved):
    """Tidy mixes moved some files: point the song finder's results at their new place."""
    if not moved:
        return
    with LOCK:
        for f in FINDS.values():
            if f.get("file") in moved:
                f["file"] = moved[f["file"]]
    save_finds()


def save_finds():
    with LOCK:
        data = list(FINDS.values())[-100:]
    core._save_json("finder.json", data)


def start_find(show, e, keep=False, auto=False, owner=None, get=None):
    owner = owner or core.users.me_key()
    with LOCK:                                     # the same episode only once (tapped twice, or a subscription)
        for f in FINDS.values():
            if f["state"] != "failed" and f["show"] == show["title"] and (f.get("owner") or "admin") == owner and \
                    ((f.get("entry") or {}).get("id") == e.get("id") or f["title"] == e["title"]):
                if keep and not f.get("keep"):
                    f["keep"] = True
                if get and not f.get("get"):              # asked again, this time to get the songs
                    f["get"] = True
                    if f.get("tracks"):
                        threading.Thread(target=want_songs, args=(f, list(f["tracks"])), daemon=True).start()
                return f["id"]
    fid = uuid.uuid4().hex[:12]
    f = {"id": fid, "title": e["title"], "show": show["title"], "video": bool(show.get("video")), "cover": e.get("cover") or show.get("cover", ""),
         "duration": e.get("duration") or 0, "state": "queued", "progress": 0, "message": "Waiting its turn",
         "tracks": [], "created": time.time(), "keep": keep, "auto": auto, "show_data": {k: show.get(k) for k in
         ("kind", "podcast", "title", "author", "cover", "feed", "url")}, "entry": e, "owner": owner,
         "get": (CFG["auto_get"] and not auto) if get is None else bool(get)}
    with LOCK:
        FINDS[fid] = f
    save_finds()
    if keep:                                       # keeping it: download and file it now, listen when it's its turn
        FETCH_POOL.submit(fetch_then_find, f)
    else:
        FIND_POOL.submit(run_find, f)
    return fid


def fetch_then_find(f):
    """Download the mix, and listen for its songs at the same time from the stream (no waiting for the download)."""
    if stream_source(f):
        f["fetching"] = True
        fset(f, state="queued", message="Downloading the mix; listening for the songs starts in a moment")
        FIND_POOL.submit(run_find, f)
    try:
        fetch_mix(f)
    except Exception as ex:  # noqa: BLE001
        if not f.get("cancel"):
            log("mix download failed:", traceback.format_exc())
            f["fetch_failed"] = str(ex)[:200]
            if f.get("state") not in ("listening", "done"):
                fset(f, state="failed", message=str(ex)[:200])
        return
    finally:
        f["fetching"] = False
    if not f.get("cancel") and not f.get("listening_started"):
        fset(f, state="queued", message="In your library: waiting to listen for the songs")
        FIND_POOL.submit(run_find, f)


def stream_source(f):
    """The mix's web page, when its audio can be read straight from the internet."""
    page = (f.get("entry") or {}).get("page") or ""
    if not page.startswith("http"):
        return ""
    return page if not CFG["test_shazam"] or os.environ.get("UMCULO_TEST_STREAMFIND") else ""


def _bg_fetch(f):
    try:
        fetch_mix(f)
    except Exception as ex:  # noqa: BLE001
        log("mix download failed:", ex)
        f["fetch_failed"] = str(ex)[:200]
    finally:
        f["fetching"] = False



def make_req(f):
    e, show = f["entry"], f["show_data"]
    who = core.users.person(f.get("owner") or "admin")
    return {"id": "f" + uuid.uuid4().hex[:11], "target": target_for(show, e), "candidate": candidate_for(show, e),
            "stop": threading.Event(), "user": None if not who or core.users.is_admin(who) else who["id"]}


def fetch_mix(f, req=None):
    """Download a mix you're keeping and put it in the library, so it plays from there."""
    e = f["entry"]
    own = req is None
    req = req or make_req(f)
    RUNNING_REQ[f["id"]] = req
    try:
        busy = lambda: f.get("state") in ("listening", "done")      # the listening has its own news to show
        if not busy():
            fset(f, state="downloading", message="Downloading the mix")
        path = dl_link(req)
        final = core.file_into_library(path, req)
        core.request_scan()
        f["file"] = final
        if e.get("page"):
            core.video.record(final, e["page"], bool(f.get("video")))
        f["local"] = final                           # from here on it's the library's copy: never deleted
        if not busy():
            fset(f, message="In your library")
        else:
            save_finds()
        return final
    finally:
        if own:
            RUNNING_REQ.pop(f["id"], None)
            import shutil
            shutil.rmtree(os.path.join(core.CONFIG["staging"], req["id"]), ignore_errors=True)


def page_tracklist(page):
    """The description and chapters of the page a mix came from (its tracklist is often there)."""
    if not page or not page.startswith("http"):
        return "", []
    try:
        with core._ydl({}) as y:
            info = y.extract_info(page, download=False) or {}
    except Exception as ex:  # noqa: BLE001
        log("couldn't read the mix's page:", ex)
        return "", []
    return info.get("description") or "", [{"t": int(c.get("start_time") or 0), "title": c.get("title") or ""}
                                           for c in info.get("chapters") or []]


def start_local_find(path, title, show, duration, cover="", page="", get=None):
    """The song finder on a mix that's already in your library: chapters for the player."""
    owner = core.users.me_key()
    with LOCK:
        for f in FINDS.values():
            if f.get("file") == path and f["state"] != "failed" and (f.get("owner") or "admin") == owner \
                    and not (f["state"] == "done" and not f.get("tracks")):      # found nothing last time: try again
                if get and not f.get("get"):
                    f["get"] = True
                    threading.Thread(target=want_songs, args=(f, list(f.get("tracks") or [])), daemon=True).start()
                return f["id"]
    fid = uuid.uuid4().hex[:12]
    e = {"id": "local-" + fid, "title": title, "duration": duration, "description": "", "chapters": [], "page": page or "",
         "page_pending": bool(page)}                 # its page's tracklist is read when the listening starts (no waiting here)
    f = {"id": fid, "title": title, "show": show, "cover": cover, "duration": duration, "state": "queued", "progress": 0,
         "message": "Waiting its turn", "tracks": [], "created": time.time(), "keep": False, "auto": False, "local": path,
         "file": path, "show_data": {"kind": "single", "podcast": False, "title": show, "author": show, "cover": cover,
                                     "feed": "", "url": ""}, "entry": e, "owner": owner,
         "get": CFG["auto_get"] if get is None else bool(get)}
    with LOCK:
        FINDS[fid] = f
    save_finds()
    FIND_POOL.submit(run_find, f)
    return fid


def find_for_file(path):
    """The song finder's result for this file (yours), newest first."""
    with LOCK:
        hits = [f for f in FINDS.values() if f.get("file") == path and core.users.mine(f)]
    return max(hits, key=lambda f: (f["state"] == "done", f["created"]), default=None)


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


GET_LOCK = threading.Lock()


def want_songs(f, songs):
    """Songs heard in (or listed for) a mix that you don't have: into the mix's playlist and off to your sources now."""
    who = core.users.person(f.get("owner") or "admin")
    before = getattr(core.users.LOCAL, "user", None)
    core.users.LOCAL.user = who
    try:
        new = []
        with GET_LOCK:
            asked = f.setdefault("asked", [])
            for t in songs:
                a, title = clean_artist(t.get("artist", "")), t.get("title", "")
                if not a or not title:
                    continue
                keys = {core.song_key(a, title), "t:" + title_key(title)}
                if keys & set(asked):
                    continue
                asked.extend(keys)
                new.append({"artist": a, "title": title, "duration": 0})
        new = [t for t in new if not owned(t["artist"], t["title"])]
        if not new:
            return
        with GET_LOCK:
            job = core.JOBS.get(f.get("get_job") or "")
            if job:
                core.add_tracks(job, new)
            else:
                name = core.safe(f["title"])
                jid = core.new_job(name, "import", os.path.join(core.CONFIG["playlists"], name + ".m3u8"), new)
                f["get_job"] = jid
                j = core.JOBS.get(jid)
                if j:                                      # you're listening to this mix now: these go first, and fast
                    j.update(priority=20, fast=True)
        log(f"song finder: getting {core.plural(len(new), 'song')} from {f['title']}")
        save_finds()
    except Exception as ex:  # noqa: BLE001
        log("song finder: couldn't start getting the songs:", ex)
    finally:
        core.users.LOCAL.user = before


def unnumber(job):
    """Songs queued before 3.3.1 from a numbered tracklist ("1 Daev Martian"): drop the numbers, try again now."""
    ts = job.get("tracks") or []
    nums = [t for t in ts if re.match(r"^\d{1,3}\s+\S", t.get("artist", ""))]
    if len(nums) < 3:
        return 0
    for t in nums:
        t["artist"] = re.sub(r"^\d{1,3}\s+", "", t["artist"])
        if t.get("state") in ("not_found", "failed"):
            t.update(state="pending", message="Trying again", silent_retry=False, retry_at=0)
            core.BATCH.submit(core.process_track, job, t)
    core.save_jobs()
    return len(nums)


def get_states(f):
    """song key -> where getting it has got to (searching, downloading, done, missing)."""
    job = core.JOBS.get(f.get("get_job") or "")
    if not job:
        return {}
    out = {}
    for t in list(job["tracks"]):
        st = t.get("state", "")
        out[core.song_key(t["artist"], t["title"])] = "done" if st in ("done", "ok", "kept", "have") or t.get("new_file") else st
        out["t:" + title_key(t["title"])] = out[core.song_key(t["artist"], t["title"])]
    return out


def first_pass(dur, step):
    """One bit every 3 minutes across the whole mix: about 20 for an hour, which Shazam answers at once."""
    return list(range(min(30, max(dur - 12, 0)), max(dur - 12, 1), max(step * 3, 120)))


def refine(res, step):
    """Listen between two bits only where something changes (another song, or nothing heard): a song that
    plays through both doesn't need the bit in between."""
    offs, out = sorted(res), []
    for a, b in zip(offs, offs[1:]):
        if b - a < step * 2:                       # 90 s apart is close enough: a song lasts 4 to 7 minutes
            continue
        ra, rb = res[a], res[b]
        if ra and rb and core.song_key(*ra) == core.song_key(*rb):
            continue
        out.append((a + b) // 2)
    return out


class Throttled(Exception):
    pass


class Pacer:
    """Shazam limits each server (a burst of about 20, then a few a minute). Every song finder waits its turn here,
    instead of the library's silent minute-long retries."""

    def __init__(self):
        self.lock, self.until, self.strikes = threading.Lock(), 0.0, 0

    def wait(self, f=None):
        while True:
            with self.lock:
                w = self.until - time.time()
            if w <= 0 or (f and f.get("cancel")):
                return
            time.sleep(min(w, 2))

    def limited(self):
        with self.lock:
            self.strikes += 1
            self.until = max(self.until, time.time() + min(20, 4 * self.strikes))

    def ok(self):
        with self.lock:
            self.strikes = 0


PACER = Pacer()
SHAZAM_SLOTS = threading.BoundedSemaphore(4)


def run_find(f):
    e, show = f["entry"], f["show_data"]
    if f.get("cancel"):
        return
    f["listening_started"] = True
    req = make_req(f)
    RUNNING_REQ[f["id"]] = req
    path = ""
    rec = None
    try:
        if e.pop("page_pending", False):
            e["description"], e["chapters"] = page_tracklist(e.get("page"))
        listed = parse_tracklist(e.get("description", ""), e.get("chapters"))
        fset(f, state="downloading", message="Getting the mix ready to listen to",
             tracks=[{**t, "from": "tracklist", "hits": 0} for t in listed])
        src, hdrs, page = "", None, stream_source(f)
        if page and f.get("keep") and not f.get("local") and not f.get("fetching"):   # back after a restart: download alongside
            f["fetching"] = True
            FETCH_POOL.submit(_bg_fetch, f)
        if f.get("local"):
            src = f["local"]
        elif page:
            try:                                     # straight from the internet: only the bits listened to are fetched
                src, hdrs, _ct = core.video.direct(page, core.mixfeed.AUDIO_FMT) if not os.environ.get("UMCULO_TEST_STREAMFIND") \
                    else (page, None, "")
            except Exception as ex:  # noqa: BLE001
                log("song finder: couldn't read the stream, downloading instead:", ex)
        if not src:
            if f.get("keep") and not f.get("fetching"):     # keeping it (a find from before a restart): into the library first
                fetch_mix(f, req)
            while f.get("fetching") and not f.get("local") and not f.get("cancel"):
                time.sleep(2)
            path = f["local"] if f.get("local") else dl_link(req)     # a mix in the library: just listen to it
            src = path
        dur = (core.audio_duration(src) if not src.startswith("http") else 0) or e.get("duration") or 0
        heard, rec_error = [], ""
        rec0 = recognizer()
        if rec0 and dur:
            if hasattr(rec0, "close"):
                rec0.close()
            step = max(CFG["finder_step"], 15)
            res, local = {}, threading.local()
            made, lock = [], threading.Lock()
            state = {"n": 0, "fails": 0, "empty": 0, "secs": 0.0, "stop": False, "limited": 0, "round": 0}
            tmpdir = os.path.join(core.CONFIG["staging"], req["id"])
            os.makedirs(tmpdir, exist_ok=True)
            first = first_pass(dur, step)
            cap = len(first) * 2 + 2                     # the first pass, and at most as many again where songs change

            def say(hit_now=False):
                n, waiting = state["n"], PACER.until > time.time()
                pc = int(n * 70 / max(len(first), 1)) if state["round"] == 0 else min(97, 70 + state["round"] * 10)
                fset(f, state="listening", progress=min(pc, 97),
                     message=f"Listening: {n} bits of the mix so far · {len(heard)} heard"
                     + (" · Shazam asked us to slow down for a moment" if waiting else ""),
                     **({"tracks": merge_tracks(listed, list(heard))} if hit_now else {}))

            def one(off):
                if f.get("cancel") or state["stop"]:
                    return
                if not getattr(local, "rec", None):
                    local.rec = recognizer()
                    with lock:
                        made.append(local.rec)
                t0 = time.time()
                use = f["local"] if f.get("local") and os.path.exists(f["local"]) else src
                wav = segment(use, off, hdrs if use.startswith("http") else None, tmpdir)
                if not wav and use.startswith("http"):     # the stream wouldn't give this bit: use the download instead
                    while f.get("fetching") and not f.get("local") and not f.get("cancel"):
                        time.sleep(2)
                    if f.get("local"):
                        wav = segment(f["local"], off)
                hit = None
                if wav:
                    for _attempt in range(60):           # Shazam allows a burst, then a few a minute: wait our turn
                        if f.get("cancel"):
                            return
                        PACER.wait(f)
                        try:
                            with SHAZAM_SLOTS:
                                hit = local.rec(wav, off)
                            PACER.ok()
                            with lock:
                                state["fails"] = 0
                            break
                        except Throttled:
                            PACER.limited()
                            with lock:
                                state["limited"] += 1
                            say()
                        except Exception as ex:  # noqa: BLE001
                            log("recognition failed at", off, ":", ex)
                            with lock:
                                state["fails"] += 1
                                if state["fails"] >= 8:
                                    state["stop"] = f"Audio recognition stopped working ({type(ex).__name__}); showing what was found"
                            break
                with lock:
                    res[off] = (hit[0], hit[1]) if hit else None
                    state["n"] += 1
                    state["secs"] += time.time() - t0
                    if hit:
                        heard.append({"t": off, "artist": hit[0], "title": hit[1]})
                        heard.sort(key=lambda x: x["t"])
                    elif wav:
                        state["empty"] += 1
                    say(bool(hit))
                if hit and f.get("get"):
                    want_songs(f, [{"t": off, "artist": hit[0], "title": hit[1]}])   # off to your sources straight away

            if listed and f.get("get"):
                want_songs(f, listed)
            say()
            try:
                batch = first                            # the whole mix first, then only where the songs change
                with cf.ThreadPoolExecutor(max_workers=max(1, min(CFG["listeners"], 4))) as pool:
                    while batch and not f.get("cancel") and not state["stop"] and len(res) < cap:
                        list(pool.map(one, batch[:cap - len(res)]))
                        state["round"] += 1
                        batch = [o for o in refine(res, step) if o not in res]
            finally:
                for r in made:
                    try:
                        r.close()
                    except Exception:  # noqa: BLE001
                        pass
            rec_error = state["stop"] or ""
            log(f"song finder: {f['title']}: {len(heard)} heard in {state['n']} bits ({state['empty']} not recognised, "
                f"{len(first)} in the first pass), {state['secs'] / max(state['n'], 1):.1f} s a bit, "
                f"Shazam asked us to wait {state['limited']} times")
            if state["n"] >= 10 and not heard and not rec_error:
                rec_error = "Shazam didn't recognise any of this mix (often unreleased edits or very long blends)"
        rec = None
        if f.get("cancel"):
            raise Stopped()
        tracks = merge_tracks(listed, heard)
        fset(f, message="Checking your library")
        for t in tracks:
            t["owned"] = owned(t["artist"], t["title"])
        if f.get("local"):
            path = ""                                # never delete the library's copy
        how = ("audio recognition" if heard else "") + (" and " if heard and listed else "") + ("the published tracklist" if listed else "")
        msg = (f"Found {core.plural(len(tracks), 'song')}" + (f" using {how}" if how else "") if tracks else
               ("No songs recognised" if rec else "No tracklist published, and audio recognition isn't installed"))
        fset(f, state="done", progress=100, tracks=tracks, message=rec_error or msg)
        if f.get("auto") and f.get("file"):                  # a subscription's new mix, in and listened to
            core.notify_people.send(core.notify_people.owner_id(f.get("owner")), f"{f['title']}\nfrom {f['show']}"
                                    + (f" · {core.plural(len(tracks), 'song')} found" if tracks else ""), "new_mix", "New mix",
                                    core.notify_people.app_link())
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
        if path and not f.get("local"):
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


def clean_artist(a):
    """'4..LebtoniQ' (found before 2.9.5) -> 'LebtoniQ'."""
    a = a or ""
    c = NUM.sub("", a, count=1).strip(" -.") if NUM.match(a) else a
    return c or a


def get_found(fid, indexes, name=""):
    f = FINDS.get(fid)
    if not f:
        raise ValueError("Unknown mix")
    want = [t for t in f["tracks"] if t["i"] in set(indexes)]
    if not want:
        raise ValueError("Choose at least one song")
    tracks = [{"artist": clean_artist(t["artist"]), "title": t["title"], "duration": 0} for t in want]
    name = core.safe(name or f["title"])
    return core.new_job(name, "import", os.path.join(core.CONFIG["playlists"], name + ".m3u8"), tracks)


# --------------------------------------------------------------------------- web routes
def view_sub(s):
    return {k: s.get(k) for k in ("id", "title", "author", "cover", "url", "podcast", "last_check", "last_new",
                                  "error", "fetched", "created", "video")}


def view_find(f, full=False):
    keys = ("id", "title", "show", "cover", "duration", "state", "progress", "message", "created", "file")
    out = {k: f.get(k) for k in keys}
    out["count"] = len(f.get("tracks") or [])
    if full:
        got = get_states(f) if f.get("get_job") else {}
        out["tracks"] = [{**t, "getting": got.get(core.song_key(clean_artist(t["artist"]), t["title"])) or got.get("t:" + title_key(t["title"])) or ""}
                         if got else t for t in f.get("tracks") or []]
        out["get"] = bool(f.get("get"))
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
        return {"id": get_entry(show, find_entry(show, b.get("entry", "")), bool(b.get("video")))}
    if path == "/api/link/find":
        show = get_show(b.get("show", ""))
        if b.get("video"):
            show = {**show, "video": True}
        return {"id": start_find(show, find_entry(show, b.get("entry", "")), bool(b.get("keep")))}
    if path == "/api/link/import":
        return {"job": import_show(get_show(b.get("show", "")), b.get("name", ""))}
    if path == "/api/link/getall":
        return {"ids": get_all(get_show(b.get("show", "")), bool(b.get("video")))}
    if path == "/api/link/subscribe":
        return subscribe_async(get_show(b.get("show", "")), bool(b.get("latest")), bool(b.get("video")))
    m = re.match(r"^/api/subscriptions/([0-9a-f]+)/(check|remove|video)$", path)
    if m:
        s = SUBS.get(m.group(1))
        if not core.users.mine(s):
            raise LookupError("unknown subscription")
        if m.group(2) == "video":                     # new mixes come with their video too (or stop)
            s["video"] = bool(b.get("on"))
            save_subs()
            return {"ok": True, "video": s["video"]}
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
    return {"sub_hours": CFG["sub_hours"], "finder_step": CFG["finder_step"], "auto_find": CFG["auto_find"],
            "auto_get": CFG["auto_get"], "finder_listeners": CFG["listeners"]}


def apply_setting(k, v):
    if k == "sub_hours":
        CFG["sub_hours"] = max(1.0, float(v))
        return True
    if k == "auto_find":
        CFG["auto_find"] = bool(v)
        return True
    if k == "auto_get":
        CFG["auto_get"] = bool(v)
        return True
    if k == "finder_listeners":
        CFG["listeners"] = max(1, min(16, int(v)))
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
        FINDS[f["id"]] = f
    threading.Thread(target=sub_loop, daemon=True).start()


def after_jobs():
    """Called once the download queue is loaded: pick up unfinished song finders, and repair old songs."""
    for f in list(FINDS.values()):
        if f["state"] == "queued" and f.get("message") == "Resumed after restart":
            FIND_POOL.submit(run_find, f)
    threading.Thread(target=_repair, daemon=True).start()


def _repair():
    """Fix songs queued from numbered tracklists before 3.3.1."""
    for f in list(FINDS.values()):
        ts = f.get("tracks") or []
        if sum(1 for t in ts if re.match(r"^\d{1,3}\s+\S", t.get("artist", ""))) >= 3:
            for t in ts:
                t["artist"] = re.sub(r"^\d{1,3}\s+", "", t.get("artist", ""))
            save_finds()
        job = core.JOBS.get(f.get("get_job") or "")
        if job:
            try:
                job["fast"] = True
                for t in job["tracks"]:              # put off only because Soulseek was quiet: go now (3.3.1)
                    if t.get("state") == "not_found" and t.get("silent_retry") and not re.match(r"^\d{1,3}\s+\S", t.get("artist", "")):
                        t.update(state="pending", message="Trying again", silent_retry=False, retry_at=0)
                        core.BATCH.submit(core.process_track, job, t)
                n = unnumber(job)
                if n:
                    log(f"song finder: took the list numbers off {core.plural(n, 'song')} in {f['title']}; trying them again")
            except Exception as ex:  # noqa: BLE001
                log("song finder repair:", ex)
