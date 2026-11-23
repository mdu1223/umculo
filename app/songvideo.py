"""Music videos for songs: the official video, playing in step with the song in your library.

The sound stays your library's copy (your quality); the video is a muted picture. Music videos often start
earlier or later than the song (an intro, a skit), so Umculo lines them up by comparing the two soundtracks:
  1. find the official video on YouTube (the artist's channel or VEVO, or "Official Video" in the title;
     never lyric videos, visualisers, live versions, covers or uploads by other people)
  2. compare the song's sound with the video's at two places (near the start and 60% in): if both give the
     same offset, the video plays with that offset; if not (an edit, a skit in the middle) the song gets no video
     rather than one that's out of step

Each song is looked up once (in the background, when someone with music videos switched on plays it) and the
answer is remembered with the mixes' video sources (video.SRC).
"""
import array
import concurrent.futures as cf
import math
import os
import re
import subprocess
import time

core = None
LOOKING = {}             # path -> True while being looked up
POOL = cf.ThreadPoolExecutor(max_workers=2)
MATCHER = 3              # raise when finding videos gets better: songs that found none are looked up again
FPS = 20                 # loudness changes compared 20 times a second (50 ms: in step to the eye)
RATE = 4000
BAD = re.compile(r"\b(lyrics?|lyric video|audio|visuali[sz]er|live|cover|reaction|karaoke|instrumental|sped up|slowed|"
                 r"8d|nightcore|tiktok|teaser|trailer|behind the scenes|making of|dance practice|choreograph|fan ?made|"
                 r"remix|extended|acoustic|performance|session|edit audio|full album|mashup)\b", re.I)


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def main_artist(a):
    return re.split(r"\s*(?:,|&|\bfeat\.?|\bft\.?|\bx\b|\band\b|\bwith\b)\s*", a or "", flags=re.I)[0].strip()


def core_title(t, artist=""):
    """The song's name on its own: no "(feat. ...)", "ft. ...", "(Explicit)", "Artist - " in front."""
    t = t or ""
    a = main_artist(artist)
    if a and re.match(re.escape(a) + r"\s*[-–]\s*", t, re.I):                 # "J. Cole - Power Trip"
        t = re.sub(re.escape(a) + r"\s*[-–]\s*", "", t, count=1, flags=re.I)
    t = re.sub(r"\s*[\(\[][^\)\]]*(feat|ft\.|with|prod|remaster|version|edit|mix|explicit|clean)[^\)\]]*[\)\]]", "", t, flags=re.I)
    t = re.sub(r"\s+(feat\.?|ft\.?|featuring)\s.*$", "", t, flags=re.I)        # "LOYALTY. FEAT. RIHANNA."
    return t.strip(" .-")


def is_official(e, artist, title):
    """The artist's own video of this song, not a lyric video, live version or someone else's upload."""
    vt, ch = e.get("title") or "", e.get("channel") or e.get("uploader") or ""
    want_remix = bool(re.search(r"remix|live|acoustic", title, re.I))
    bad = BAD.search(vt)
    if bad and not (want_remix and bad.group(0).lower() in title.lower()):
        return False
    if norm(core_title(title, artist)) not in norm(vt):
        return False
    a, c = norm(main_artist(artist)), norm(ch)
    by_artist = bool(a) and (a.replace(" ", "") in c.replace(" ", "") or c.endswith("vevo") and a.replace(" ", "") in c.replace(" ", ""))
    said = bool(re.search(r"official (music )?video|\(official\)|\[official\]|\bmv\b|music video", vt, re.I))
    return by_artist or (said and a and a in norm(vt))


def candidates(row):
    q = f"{main_artist(row['artist'])} {core_title(row['title'], row['artist'])} official video"
    with core._ydl({"extract_flat": "in_playlist"}) as y:
        info = y.extract_info(f"ytsearch8:{q}", download=False) or {}
    out, looked = [], 0
    want = row["duration"] or 0
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        d = int(e.get("duration") or 0)
        if want and d and (d < want - 15 or d > want + 300):          # an intro or outro, not a different length song
            continue
        if not (e.get("channel") or e.get("uploader")) and looked < 3 \
                and norm(core_title(row["title"], row["artist"])) in norm(e.get("title")) and not BAD.search(e.get("title") or ""):
            looked += 1                              # search results sometimes leave the channel out: look it up
            e = {**e, **meta(f"https://www.youtube.com/watch?v={e['id']}")}
        if is_official(e, row["artist"] or "", row["title"] or ""):
            out.append({"page": f"https://www.youtube.com/watch?v={e['id']}", "duration": d, "title": e.get("title") or "",
                        "channel": e.get("channel") or e.get("uploader") or ""})
    return out[:2]


def meta(page):
    """A video's title and channel."""
    try:
        with core._ydl({}) as y:
            i = y.extract_info(page, download=False, process=False) or {}
        return {"title": i.get("title") or "", "channel": i.get("channel") or i.get("uploader") or ""}
    except Exception:  # noqa: BLE001
        return {}


# --------------------------------------------------------------------------- lining the picture up with the song
def onsets(src, start, dur, headers=None):
    """How the loudness changes, FPS times a second, from start for dur seconds of a file or web address."""
    pre = []
    if src.startswith("http"):
        pre = ["-reconnect", "1", "-reconnect_streamed", "1", "-rw_timeout", "20000000"]
        if headers:
            pre += ["-headers", "".join(f"{k}: {v}\r\n" for k, v in headers.items())]
    p = subprocess.run(["ffmpeg", "-v", "error", *pre, "-ss", f"{max(0, start):.2f}", "-t", f"{dur:.2f}", "-i", src,
                        "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"], capture_output=True, timeout=120)
    a = array.array("h")
    a.frombytes(p.stdout[: len(p.stdout) // 2 * 2])
    n = RATE // FPS
    loud = []
    for i in range(0, len(a) - n + 1, n):
        fr = a[i:i + n]
        loud.append(math.log10(1e-6 + sum(x * x for x in fr) / n))
    return [max(0.0, loud[i] - loud[i - 1]) for i in range(1, len(loud))]


def _z(x):
    if not x:
        return x
    m = sum(x) / len(x)
    s = math.sqrt(sum((v - m) ** 2 for v in x) / len(x)) or 1.0
    return [(v - m) / s for v in x]


def best_lag(a, b):
    """Where a fits best inside b: (lag in frames, how clearly it fits: the peak in standard deviations)."""
    a, b = _z(a), _z(b)
    n = len(a)
    if n < FPS * 10 or len(b) < n:
        return 0, 0.0
    scores = []
    for k in range(0, len(b) - n + 1):
        bb = b[k:k + n]
        scores.append(sum(x * y for x, y in zip(a, bb)) / n)
    k = max(range(len(scores)), key=scores.__getitem__)
    m = sum(scores) / len(scores)
    s = math.sqrt(sum((v - m) ** 2 for v in scores) / len(scores)) or 1.0
    return k, (scores[k] - m) / s


def align(song, video, song_dur, video_dur, headers=None):
    """-> (offset, sure): video time = song time + offset. Checked at two places; sure only if they agree."""
    seg = 30.0
    t1 = min(30.0, song_dur * 0.15)
    lo = max(0.0, t1 - 20)
    hi = min(max(video_dur, song_dur) - seg, t1 + max(0, video_dur - song_dur) + 60)
    a = onsets(song, t1, seg)
    b = onsets(video, lo, hi - lo + seg, headers)
    k, z = best_lag(a, b)
    off = lo + k / FPS - t1
    if z < 6:
        return off, False
    t2 = song_dur * 0.6
    if t2 + seg > song_dur:
        return off, True
    lo2 = max(0.0, t2 + off - 8)
    a2 = onsets(song, t2, seg)
    b2 = onsets(video, lo2, seg + 16, headers)
    k2, z2 = best_lag(a2, b2)
    off2 = lo2 + k2 / FPS - t2
    return off, z2 >= 5 and abs(off2 - off) <= 0.25


# --------------------------------------------------------------------------- looking a song up
def known(path):
    with core.video.LOCK:
        return dict(core.video.SRC.get(path) or {})


def remember(path, **kw):
    with core.video.LOCK:
        core.video.SRC[path] = {**(core.video.SRC.get(path) or {}), **kw, "at": time.time(), "song": True, "matcher": MATCHER}
    core.video.save_src()


def find(row):
    """Look the song's video up and line it up (slow: a few seconds). -> the remembered answer."""
    path = row["path"]
    if core.video.test_file():
        remember(path, page="test", offset=float(os.environ.get("UMCULO_TEST_SONGVIDEO_OFFSET", "2.0")), ok=True, why="")
        return known(path)
    why, best = "No official video on YouTube", None
    try:
        for c in candidates(row):
            url, hdrs, _ct = core.video.direct(c["page"], core.mixfeed.AUDIO_FMT)
            off, sure = align(path, url, float(row["duration"] or 0), float(c["duration"] or 0), hdrs)
            if sure:
                best = {"page": c["page"], "offset": round(off, 2), "ok": True, "why": "", "vtitle": c["title"], "vchannel": c["channel"]}
                break
            why = "The official video is a different edit from your copy, so it can't play in step"
    except Exception as ex:  # noqa: BLE001
        core.log("music video: couldn't look up", row["title"], ex)
        return {}                                   # try again next time
    remember(path, **(best or {"page": "", "offset": 0, "ok": False, "why": why}))
    if best:
        core.log(f"music video: {row['artist']} - {row['title']}: {best['page']} ({best['offset']:+.2f} s)")
    return known(path)


FILLING = set()


def fill(path, page):
    if path in FILLING:
        return
    FILLING.add(path)

    def go():
        m = meta(page)
        if m:
            remember(path, vtitle=m.get("title", ""), vchannel=m.get("channel", "") or "YouTube")
    POOL.submit(go)


def look(row):
    """Start looking in the background (once)."""
    path = row["path"]
    with core.video.LOCK:
        if LOOKING.get(path):
            return
        LOOKING[path] = True
    r = dict(row)

    def go():
        try:
            find(r)
        finally:
            LOOKING.pop(path, None)
    POOL.submit(go)


def state(row):
    """-> what the player needs: looking / available / offset / why."""
    k = known(row["path"])
    fresh = k.get("song") and (k.get("ok") or (time.time() - k.get("at", 0) < core.video.SEARCH_AGAIN
                                               and k.get("matcher", 1) >= MATCHER))
    if not fresh:
        look(row)
        return {"looking": True, "available": False}
    if LOOKING.get(row["path"]):
        return {"looking": True, "available": False}
    if k.get("ok") and not k.get("vchannel") and str(k.get("page", "")).startswith("http"):
        fill(row["path"], k["page"])                 # found before the channel was kept: look it up once
    return {"available": bool(k.get("ok") and k.get("page")), "offset": k.get("offset", 0), "why": k.get("why", ""),
            "page": k.get("page") if k.get("page") not in ("", "test") else "", "vtitle": k.get("vtitle", ""), "vchannel": k.get("vchannel", "")}


def init(umculo):
    global core
    core = umculo
