"""Video for DJ mixes: watch the set while it plays.

The audio stays the song in your library; the video is a muted picture kept in step with it.
  - streamed: from the page the mix came from (or, failing that, the same set found on YouTube),
    as one progressive mp4 (720p at most), passed through with seeking
  - saved:    next to the audio, in a hidden folder:  <show>/.video/<mix>.mp4   (scans and tidy-ups skip it)

GET  /api/video/<song id>/info   -> {"mix", "available", "saved", "saving", "page"}
GET  /api/video/<song id>        -> the video (saved copy, or streamed through Umculo)
POST /api/video/<song id>/save   -> keep a copy of the video (in the background)
POST /api/video/<song id>/remove -> delete the saved copy
GET  /api/video/all              -> how saving every mix's video is going
POST /api/video/all              -> save the video of every mix of yours that hasn't got one (one at a time, in the background)

Links and subscriptions with "video": true save the video along with the audio.
"""
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request

core = None
LOCK = threading.Lock()
SRC = {}                 # audio path -> {"page": url, "at": time}   ("page": "" = searched, nothing found)
URLS = {}                # page -> (direct url, headers, expires)
SAVING = {}              # audio path -> {"state", "message", "progress"}
SEARCH_AGAIN = 7 * 86400
STREAM_FMT = "b[ext=mp4][height<=720]/b[ext=mp4]/b[height<=720]/b"          # picture and sound in one file (YouTube: 360p)
QUALITIES = ["480", "720", "1080", "1440", "best"]


def vfmt(q):
    """The picture only, at the chosen quality: the sound comes from the audio player, so the video needs none,
    and YouTube's picture-only streams go far beyond the 360p of its combined ones. H.264 first (every browser)."""
    h = 4320 if q == "best" else int(q if q in QUALITIES else "1080")
    return (f"bv*[height<={h}][vcodec^=avc1][ext=mp4]/bv*[height<={h}][ext=mp4]/bv*[height<={h}]"
            f"/b[height<={h}][ext=mp4]/b[ext=mp4]/b")


def save_fmt(q):
    h = 4320 if q == "best" else int(q if q in QUALITIES else "1080")
    return (f"bv*[height<={h}][vcodec^=avc1][ext=mp4]+ba[ext=m4a]/bv*[height<={h}][ext=mp4]+ba[ext=m4a]"
            f"/bv*[height<={h}]+ba/b[height<={h}][ext=mp4]/b")


def quality(qs=None):
    q = ((qs or {}).get("q") or [""])[0]
    return q if q in QUALITIES else core.CONFIG.get("video_quality") or "1080"


SAVE_FMT = save_fmt("720")


def test_file():
    return os.environ.get("UMCULO_TEST_VIDEO", "")


# --------------------------------------------------------------------------- where things are
def src_file():
    return os.path.join(core.CONFIG["config"], "mixsources.json")


def load():
    try:
        with open(src_file()) as fh:
            SRC.update(json.load(fh))
    except (OSError, ValueError):
        pass


def save_src():
    with LOCK:
        data = dict(SRC)
    tmp = src_file() + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, src_file())


def sidecar(path):
    d, n = os.path.split(path)
    return os.path.join(d, ".video", os.path.splitext(n)[0] + ".mp4")


def is_mix(row):
    root = core.CONFIG["mixes"].rstrip("/") + "/"
    return row["path"].startswith(root) or (row["duration"] or 0) >= 600


def song_row(sid):
    srv = core.server
    r = srv.db.q(f"SELECT * FROM song WHERE id=? AND {srv.lib_sql(core.users.current())}", (sid,), one=True)
    if not r:
        raise LookupError("Song not found")
    return r


def record(path, page, want_video=False):
    """A mix was downloaded from this page: remember it (for streaming its video) and save the video if asked."""
    if not path or not page or not re.match(r"^https?://", page):
        return
    with LOCK:
        SRC[path] = {"page": page, "at": time.time()}
    save_src()
    if want_video:
        start_save(path, page)


def moved(moves):
    """Tidy mixes moved some files: their videos and sources move with them."""
    changed = False
    for old, new in (moves or {}).items():
        sv = sidecar(old)
        if os.path.exists(sv):
            os.makedirs(os.path.dirname(sidecar(new)), exist_ok=True)
            shutil.move(sv, sidecar(new))
        with LOCK:
            if old in SRC:
                SRC[new] = SRC.pop(old)
                changed = True
    if changed:
        save_src()


# --------------------------------------------------------------------------- finding the video
def page_for(row, search=True):
    """The page this mix's video is on: where it was downloaded from, or the same set found on YouTube."""
    if test_file():
        return "test"
    with LOCK:
        known = SRC.get(row["path"])
    if known and (known["page"] or time.time() - known.get("at", 0) < SEARCH_AGAIN):
        return known["page"]
    if not search:
        return ""
    page = ""
    want = row["duration"] or 0
    q = re.sub(r"\s+", " ", f"{row['title']} {row['artist'] or ''}").strip()
    try:
        with core._ydl({"extract_flat": "in_playlist"}) as y:
            info = y.extract_info(f"ytsearch5:{q}", download=False) or {}
        for e in info.get("entries") or []:
            d = e.get("duration") or 0
            if want and d and abs(d - want) <= max(30, want * 0.03):     # the same set: the same length
                page = e.get("url") or e.get("webpage_url") or ""
                if page and not page.startswith("http"):
                    page = "https://www.youtube.com/watch?v=" + page
                break
    except Exception as ex:  # noqa: BLE001
        core.log("video search failed:", row["title"], ex)
        return ""                                  # try again next time
    with LOCK:
        SRC[row["path"]] = {"page": page, "at": time.time()}
    save_src()
    return page


INFLIGHT = {}            # (page, fmt) -> lock: the player, the song finder and the download share one lookup


def direct(page, fmt=STREAM_FMT):
    """-> (url, headers, content type) of one progressive file for this page, cached for a while (the links expire)."""
    now = time.time()
    key = (page, fmt)
    with LOCK:
        c = URLS.get(key)
    if c and c[2] > now:
        return c[0], c[1], c[3]
    with LOCK:
        gate = INFLIGHT.setdefault(key, threading.Lock())
    with gate:
        with LOCK:
            c = URLS.get(key)
        if c and c[2] > time.time():               # someone else just looked it up
            return c[0], c[1], c[3]
        with core._ydl({"format": fmt}) as y:
            info = y.extract_info(page, download=False) or {}
        return _remember(key, info, now)


def _remember(key, info, now):
    url, ext = info.get("url"), info.get("ext") or ""
    if not url and info.get("requested_formats"):
        url, ext = info["requested_formats"][0].get("url"), info["requested_formats"][0].get("ext") or ext
    if not url:
        raise LookupError("Nothing to play for this mix")
    hdrs = {k: v for k, v in (info.get("http_headers") or {}).items() if k.lower() in ("user-agent", "referer", "cookie")}
    ctype = {"m4a": "audio/mp4", "mp4": "video/mp4", "mp3": "audio/mpeg", "webm": "audio/webm", "opus": "audio/ogg"}.get(ext, "")
    with LOCK:
        URLS[key] = (url, hdrs, now + 25 * 60, ctype)
    return url, hdrs, ctype


def info(sid):
    row = song_row(sid)
    mix = is_mix(row)
    saved = os.path.exists(sidecar(row["path"]))
    st = SAVING.get(row["path"]) or {}
    page = page_for(row) if mix and not saved else ""
    return {"mix": mix, "available": bool(saved or page), "saved": saved, "saving": st.get("state") == "saving",
            "message": st.get("message", ""), "progress": st.get("progress", 0),
            "page": page if page and page != "test" else ""}


# --------------------------------------------------------------------------- watching
def serve(h, sid, q=None):
    row = song_row(sid)
    sv = sidecar(row["path"])
    if os.path.exists(sv):
        return core.subsonic.serve_file(h, sv, "video/mp4")
    if test_file():
        return core.subsonic.serve_file(h, test_file(), "video/mp4")
    page = page_for(row, search=is_mix(row))
    if not page:
        raise LookupError("No video for this mix")
    return proxy(h, page, vfmt(q or quality()), "video/mp4")


def proxy(h, page, fmt, ctype=None):
    """Pass one progressive file from the page's site through Umculo, with seeking (Range)."""
    if test_file():
        return core.subsonic.serve_file(h, test_file(), ctype or "audio/mp4")
    for attempt in (0, 1):
        url, hdrs, ct = direct(page, fmt)
        rq = urllib.request.Request(url, headers={**hdrs, **({"Range": h.headers["Range"]} if h.headers.get("Range") else {})})
        try:
            up = urllib.request.urlopen(rq, timeout=30)
            break
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410) and attempt == 0:     # the link expired: get a fresh one
                with LOCK:
                    URLS.pop((page, fmt), None)
                continue
            raise
    with up:
        h.send_response(up.status)
        for k in ("Content-Length", "Content-Range", "Accept-Ranges"):
            if up.headers.get(k):
                h.send_header(k, up.headers[k])
        h.send_header("Content-Type", ctype or ct or up.headers.get("Content-Type") or "audio/mp4")
        h.send_header("Cache-Control", "no-store")
        h.end_headers()
        while True:
            chunk = up.read(65536)
            if not chunk:
                break
            h.wfile.write(chunk)
    return None


# --------------------------------------------------------------------------- saving
def start_save(path, page):
    with LOCK:
        if (SAVING.get(path) or {}).get("state") == "saving":
            return
        SAVING[path] = {"state": "saving", "message": "Saving the video", "progress": 0}
    threading.Thread(target=run_save, args=(path, page), daemon=True).start()


def run_save(path, page):
    out = sidecar(path)
    tmp_dir = os.path.join(os.path.dirname(out), ".part")
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        if test_file():
            shutil.copy(test_file(), out + ".part")
            os.replace(out + ".part", out)
        else:
            st = SAVING[path]

            def hook(d):
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if d.get("status") == "downloading" and tot:
                    st["progress"] = int((d.get("downloaded_bytes") or 0) * 100 / tot)
            stem = os.path.join(tmp_dir, "v")
            with core._ydl({"skip_download": False, "format": save_fmt(quality()), "merge_output_format": "mp4",
                            "outtmpl": stem + ".%(ext)s", "progress_hooks": [hook], "overwrites": True}) as y:
                y.extract_info(page, download=True)
            got = next((os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if f.startswith("v.") and f.endswith(".mp4")), "")
            if not got:
                raise RuntimeError("the video didn't download as mp4")
            os.replace(got, out)
        SAVING[path] = {"state": "saved", "message": "Video saved", "progress": 100}
        core.log("video saved:", out)
    except Exception as ex:  # noqa: BLE001
        core.log("video save failed:", path, ex)
        SAVING[path] = {"state": "failed", "message": f"Couldn't save the video: {str(ex)[:160]}", "progress": 0}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


ALL = {"queue": [], "done": 0, "failed": 0, "none": 0, "current": "", "running": False, "errors": []}


def mix_rows(u):
    srv = core.server
    root = core.CONFIG["mixes"].rstrip("/") + "/"
    return srv.db.q(f"""SELECT * FROM song WHERE substr(path, 1, ?) = ? AND {srv.lib_sql(u)}
                        ORDER BY created DESC""", (len(root), root))


def all_view():
    return {"queued": len(ALL["queue"]), "done": ALL["done"], "failed": ALL["failed"], "no_video": ALL["none"],
            "current": ALL["current"], "running": ALL["running"], "errors": ALL["errors"][-5:]}


def save_all():
    if not core.users.can("download"):
        raise PermissionError("Saving videos needs download permission")
    rows = [r for r in mix_rows(core.users.current()) if not os.path.exists(sidecar(r["path"]))]
    with LOCK:
        have = {x["path"] for x in ALL["queue"]} | {ALL["current"]}
        new = [{"path": r["path"], "title": r["title"], "duration": r["duration"], "artist": r["artist"]} for r in rows if r["path"] not in have]
        ALL["queue"] += new
        if not ALL["running"]:
            ALL.update(done=0, failed=0, none=0, errors=[], running=True)
            threading.Thread(target=run_all, daemon=True).start()
    return {**all_view(), "added": len(new)}


def run_all():
    """One video at a time, so the server and the internet line stay usable."""
    try:
        while True:
            with LOCK:
                if not ALL["queue"]:
                    break
                job = ALL["queue"].pop(0)
                ALL["current"] = job["title"]
            if os.path.exists(sidecar(job["path"])) or not os.path.exists(job["path"]):
                continue
            page = page_for(job)
            if not page:
                ALL["none"] += 1
                continue
            SAVING[job["path"]] = {"state": "saving", "message": "Saving the video", "progress": 0}
            run_save(job["path"], page)
            if (SAVING.get(job["path"]) or {}).get("state") == "saved":
                ALL["done"] += 1
            else:
                ALL["failed"] += 1
                ALL["errors"].append(f"{job['title'][:60]}: {(SAVING.get(job['path']) or {}).get('message', '')[:120]}")
            time.sleep(5)
    finally:
        ALL["running"], ALL["current"] = False, ""
        if ALL["done"] or ALL["failed"]:
            core.log(f"mix videos: {ALL['done']} saved, {ALL['failed']} failed, {ALL['none']} without a video found")


def save(sid):
    if not core.users.can("download"):
        raise PermissionError("Saving videos needs download permission")
    row = song_row(sid)
    if os.path.exists(sidecar(row["path"])):
        return {"ok": True, "saved": True}
    page = page_for(row)
    if not page:
        raise LookupError("No video found for this mix")
    start_save(row["path"], page)
    return {"ok": True, "saving": True}


def remove(sid):
    if not core.users.can("download"):
        raise PermissionError("Only someone who can download can remove a saved video")
    row = song_row(sid)
    sv = sidecar(row["path"])
    if os.path.exists(sv):
        os.remove(sv)
    SAVING.pop(row["path"], None)
    return {"ok": True}


# --------------------------------------------------------------------------- web
def handle_get(h, path, qs=None):
    """Called from do_GET with the handler, since the video is streamed, not JSON. -> True when handled."""
    if path == "/api/video/all":
        return h.send_json(all_view()) or True
    m = re.match(r"^/api/video/([\w-]{1,40})(/info)?$", path)
    if not m:
        return None
    if m.group(2):
        return h.send_json(info(m.group(1))) or True
    serve(h, m.group(1), quality(qs))
    return True


def handle_post(path):
    if path == "/api/video/all":
        return save_all()
    m = re.match(r"^/api/video/([\w-]{1,40})/(save|remove)$", path)
    if not m:
        return None
    return save(m.group(1)) if m.group(2) == "save" else remove(m.group(1))


def init(umculo):
    global core
    core = umculo
    load()
