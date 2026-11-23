"""Right version or not: is this file the recording the catalogue means?

Deezer gives a free 30-second preview of every catalogue song: the real album recording. If those 30 seconds turn
up inside a file (the same comparison of the two soundtracks that lines music videos up with your songs), it's the
same recording. If they don't, it's another version: live, a remix, sped up, an instrumental, a cover, the video's
edit with its skit. (A clean edit has the same beat, so it still matches.)

Used two ways:
  - new downloads: a copy that's the wrong version is refused and the next source is tried (race in umculo.py)
  - Library care > Wrong versions: your songs are checked in the background, suspects are listed with
    "Get the right version" (which downloads it again, checks it, and replaces the wrong file everywhere)
"""
import os
import re
import tempfile
import threading
import time

core = None
LOCK = threading.Lock()
RESULTS = {}              # path -> {"verdict": "ok"/"wrong"/"unsure"/"none", "z", "mtime", "at", "artist", "title"}
SCAN = {"running": False, "done": 0, "total": 0, "wrong": 0, "started": 0, "current": ""}
# how alike the preview and the file are where it fits best (0-1): the same recording scores about 0.8, another
# version or song about 0.3 (dance music's steady beat makes anything a little alike). Between: unsure, left alone.
SAME, DIFFERENT = 0.55, 0.42


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def catalogue(artist, title, duration=0):
    """The catalogue's own recording of this song (Deezer), with its preview."""
    sv = core.songvideo
    a, t = sv.main_artist(artist), sv.core_title(title, artist)
    try:
        hits = core.deezer_search(f"{a} {t}", 10)
    except Exception:  # noqa: BLE001
        return None
    best, best_score = None, -1
    for h in hits:
        if not h.get("preview") or norm(t) not in norm(h["title"]) or norm(a) not in norm(h["artist"]):
            continue
        if re.search(r"\b(live|remix|instrumental|acoustic|sped up|slowed|karaoke)\b", h.get("version", "") + " " + h["title"], re.I) \
                and not re.search(r"\b(live|remix|instrumental|acoustic)\b", title, re.I):
            continue
        s = 10 - (abs(h["duration"] - duration) / 10 if duration and h.get("duration") else 0)
        if norm(h["title"]) == norm(t):
            s += 3
        if s > best_score:
            best, best_score = h, s
    return best


def compare(path, preview_url):
    """-> (verdict, z): "ok" (the same recording), "wrong" (another version), "unsure"."""
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False, dir=core.CONFIG.get("staging") or None)
    try:
        data, _h = core.http("GET", preview_url, timeout=20, raw=True)
        tmp.write(data or b"")
        tmp.close()
        if os.path.getsize(tmp.name) < 20000:
            return "none", 0.0
        sv = core.songvideo
        a = sv.onsets(tmp.name, 0, 30)
        dur = core.audio_duration(path) or 0
        b = sv.onsets(path, 0, min(dur or 900, 900) + 2)
        _k, z, r = sv.fit(a[: sv.FPS * 25], b)
        if len(b) < sv.FPS * 30:
            return "none", 0.0
        return ("ok" if r >= SAME and z >= 5 else "wrong" if r < DIFFERENT else "unsure"), round(r, 2)
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def check(path, artist, title, duration=0, preview=""):
    """Is this file the catalogue's recording? -> (verdict, z, catalogue entry or None)."""
    cat = {"preview": preview} if preview else catalogue(artist, title, duration)
    if not cat or not cat.get("preview"):
        return "none", 0.0, None
    try:
        v, z = compare(path, cat["preview"])
    except Exception as ex:  # noqa: BLE001
        core.log("version check failed:", title, ex)
        return "none", 0.0, cat
    return v, z, cat


def check_download(path, target):
    """For the downloader: False only when it's clearly another version (then the next copy is tried)."""
    if os.environ.get("UMCULO_TEST_VERSIONS") == "off":
        return True
    v, z, _c = check(path, target.get("artist", ""), target.get("title", ""), target.get("duration") or 0,
                     target.get("preview") or "")
    if v == "wrong":
        core.log(f"wrong version refused: {target.get('artist')} - {target.get('title')} (the catalogue's recording "
                 f"isn't in it, {z})")
        return False
    return True


# --------------------------------------------------------------------------- Library care: check every song
def save():
    with LOCK:
        data = dict(RESULTS)
    core._save_json("versions.json", data)


def songs():
    srv = core.server
    rows = srv.db.q("SELECT id, path, artist, title, duration, suffix, bitrate FROM song WHERE duration < 600 ORDER BY path") or []
    mixes = core.CONFIG["mixes"]
    rows = [r for r in rows if not core.within(r["path"], mixes)]
    lossy_first = sorted(rows, key=lambda r: (str(r["suffix"]).lower() not in ("opus", "m4a", "webm", "ogg"), r["path"]))
    return lossy_first


def run_scan(user):
    core.users.LOCAL.user = user
    try:
        todo = []
        for r in songs():
            try:
                mt = os.path.getmtime(r["path"])
            except OSError:
                continue
            k = RESULTS.get(r["path"])
            if k and k.get("mtime") == mt and (k.get("verdict") in ("ok", "wrong", "dismissed")
                                               or time.time() - k.get("at", 0) < 7 * 86400):   # unsure: again in a week
                continue
            todo.append((r, mt))
        SCAN.update(total=len(todo), done=0, wrong=sum(1 for v in RESULTS.values() if v.get("verdict") == "wrong"))
        for r, mt in todo:
            if not SCAN["running"]:
                break
            SCAN["current"] = f"{r['artist']} - {r['title']}"
            v, z, cat = check(r["path"], r["artist"] or "", r["title"] or "", r["duration"] or 0)
            with LOCK:
                RESULTS[r["path"]] = {"verdict": v, "z": z, "mtime": mt, "at": time.time(), "id": r["id"],
                                      "artist": r["artist"], "title": r["title"], "duration": r["duration"],
                                      "catalogue": (cat or {}).get("album", ""), "cat_duration": (cat or {}).get("duration", 0)}
            SCAN["done"] += 1
            if v == "wrong":
                SCAN["wrong"] += 1
            if SCAN["done"] % 20 == 0:
                save()
        save()
        core.log(f"wrong versions: checked {SCAN['done']} songs, {SCAN['wrong']} look like another version")
    finally:
        SCAN.update(running=False, current="")


def view():
    with LOCK:
        wrong = [{"path": p, **v} for p, v in RESULTS.items() if v.get("verdict") == "wrong" and os.path.exists(p)]
        checked = sum(1 for v in RESULTS.values() if v.get("verdict") in ("ok", "wrong", "dismissed"))
    wrong.sort(key=lambda x: (x.get("artist") or "").lower())
    return {**SCAN, "checked": checked, "suspects": [{k: x.get(k) for k in ("id", "artist", "title", "duration", "z", "catalogue",
                                                                         "cat_duration", "fixing")} for x in wrong]}


def fix(ids):
    """Get the right version of these songs: download again (checked against the catalogue) and replace them."""
    if not core.users.can("download"):
        raise PermissionError("Getting songs needs download permission")
    tracks = []
    with LOCK:
        for p, v in RESULTS.items():
            if v.get("id") in ids and v.get("verdict") == "wrong" and os.path.exists(p):
                v["fixing"] = True
                tracks.append({"artist": v["artist"], "title": v["title"], "duration": v.get("cat_duration") or v.get("duration") or 0,
                               "file": p, "replace": True})
    if not tracks:
        return {"ok": True, "queued": 0}
    jid = core.new_job("Right versions", "upgrade", "", tracks)
    save()
    return {"ok": True, "queued": len(tracks), "job": jid}


def dismiss(ids):
    with LOCK:
        for v in RESULTS.values():
            if v.get("id") in ids:
                v["verdict"] = "dismissed"
    save()
    return view()


def handle_get(path, qs):
    if path == "/api/versions":
        if not core.users.is_admin():
            raise PermissionError("Only the admin can see this")
        return view()
    return None


def handle_post(path, b):
    if not path.startswith("/api/versions"):
        return None
    if not core.users.is_admin():
        raise PermissionError("Only the admin can do this")
    b = b or {}
    if path == "/api/versions/scan":
        if not SCAN["running"]:
            SCAN.update(running=True, started=time.time())
            threading.Thread(target=run_scan, args=(core.users.current(),), daemon=True).start()
        return view()
    if path == "/api/versions/stop":
        SCAN["running"] = False
        return view()
    if path == "/api/versions/fix":
        return fix([str(x) for x in b.get("ids") or []])
    if path == "/api/versions/dismiss":
        return dismiss([str(x) for x in b.get("ids") or []])
    return None


def init(umculo):
    global core
    core = umculo
    RESULTS.update(core._load_json("versions.json", {}) or {})
