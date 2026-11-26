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
import shutil
import tempfile
import threading
import time

core = None
LOCK = threading.Lock()
RESULTS = {}              # path -> {"verdict": "ok"/"wrong"/"unsure"/"none", "z", "mtime", "at", "artist", "title"}
OK_SINCE = 3              # "right" from this method on stays right (later methods only check more releases)
METHOD = 4                # how the check works: results from an older way are checked again
SCAN = {"running": False, "done": 0, "total": 0, "wrong": 0, "started": 0, "current": ""}
# how surely the preview is in the file (0-1, allowing for a copy playing up to 3% fast or slow): measured on real
# library files against catalogue previews, the same recording scores 0.74-0.97 (0.74: a pressing 2.8% fast),
# other versions (an instrumental, a different edit) and other songs 0.26-0.49. Between the two: unsure, left alone.
SAME, DIFFERENT = 0.68, 0.58
NOT_THE_REAL_ONE = re.compile(r"karaoke|tribute|originally performed|made popular|in the style of|re-?recorded|"
                              r"alternate|8-bit|cover|instrumental|live|remix|acoustic|sped up|slowed|"
                              r"version\)?\s*$", re.I)


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def catalogue(artist, title, duration=0):
    """The catalogue's own recording of this song (Deezer), with its preview."""
    c = candidates(artist, title, duration)
    return c[0] if c else None


def candidates(artist, title, duration=0, n=3):
    """The catalogue's releases of this song, best first (the same title can be several recordings: an album take,
    a compilation's re-recording...). Different preview files only."""
    sv = core.songvideo
    a, t = sv.main_artist(artist), sv.core_title(title, artist)
    try:
        hits = core.deezer_search(f"{a} {t}", 10)
    except Exception:  # noqa: BLE001
        return None
    scored = []
    for h in hits:
        if not h.get("preview") or norm(t) not in norm(h["title"]) or norm(a) not in norm(h["artist"]):
            continue
        extra = (h.get("version", "") + " " + h["title"] + " " + h.get("album", "")).lower()
        bad = NOT_THE_REAL_ONE.search(extra)
        if bad and bad.group(0).strip(" )").lower() not in title.lower() and not re.search(r"album version|radio edit|single", extra):
            continue
        s = 10 - (abs(h["duration"] - duration) / 10 if duration and h.get("duration") else 0)
        if norm(h["title"]) == norm(t):
            s += 3
        scored.append((s, h))
    scored.sort(key=lambda x: -x[0])
    out, seen = [], set()
    for _s, h in scored:
        f = h["preview"].split("?")[0]
        if f not in seen:
            seen.add(f)
            out.append(h)
    return out[:n]


def by_id(deezer_id):
    """The catalogue entry the download was for, with a fresh preview (preview links expire)."""
    try:
        d = core.http("GET", f"{core.CONFIG['deezer']}/track/{deezer_id}", timeout=15, retries=0) or {}
    except Exception:  # noqa: BLE001
        return None
    if not d.get("preview"):
        return None
    return {"preview": d["preview"], "duration": d.get("duration") or 0, "album": (d.get("album") or {}).get("title", "")}


def compare(path, preview_url):
    """-> (verdict, how sure 0-1): "ok" (the same recording), "wrong" (another version), "unsure", "none"."""
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
        if len(b) < sv.FPS * 30 or len(a) < sv.FPS * 20:
            return "none", 0.0
        r = sv.same_recording(a, b)
        return ("ok" if r >= SAME else "wrong" if r < DIFFERENT else "unsure"), round(r, 2)
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


def check(path, artist, title, duration=0, preview="", deezer_id=None):
    """Is this file the catalogue's recording? -> (verdict, how sure, catalogue entry or None)."""
    first = (by_id(deezer_id) if deezer_id else None) or ({"preview": preview} if preview else None)
    cats = ([first] if first else []) + [c for c in candidates(artist, title, duration)
                                         if not first or c["preview"].split("?")[0] != first["preview"].split("?")[0]]
    cats = [c for c in cats if c and c.get("preview")][:3]
    if not cats:
        return "none", 0.0, None
    best = ("none", 0.0, cats[0])
    for cat in cats:                     # right if it's any of the catalogue's releases of this song
        try:
            v, z = compare(path, cat["preview"])
        except Exception as ex:  # noqa: BLE001
            core.log("version check failed:", title, ex)
            continue
        if v == "ok":
            return v, z, cat
        if z > best[1] or best[0] == "none":
            best = (v, z, cat)
    return best


def check_download(path, target):
    """For the downloader: False only when it's clearly another version (then the next copy is tried)."""
    if os.environ.get("UMCULO_TEST_VERSIONS") == "off":
        return True
    v, z, cat = check(path, target.get("artist", ""), target.get("title", ""), target.get("duration") or 0,
                      "", target.get("deezer_id"))
    want = (cat or {}).get("duration") or target.get("duration") or 0
    have = core.audio_duration(path) or 0
    if v == "wrong" and want and have and abs(want - have) <= 2:
        # the album's length to the second: refusing a right song costs more than letting a rare wrong one by
        # (Library care can still list it)
        core.log(f"version check unsure (kept, same length): {target.get('artist')} - {target.get('title')} ({z})")
        return True
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
            if k and k.get("method", 1) < METHOD and not current_ok(k):
                k = None
            if k and k.get("mtime") == mt and (k.get("verdict") in ("ok", "wrong", "dismissed")
                                               or time.time() - k.get("at", 0) < 7 * 86400):   # unsure: again in a week
                continue
            todo.append((r, mt))
        SCAN.update(total=len(todo), done=0)
        for r, mt in todo:
            if not SCAN["running"]:
                break
            SCAN["current"] = f"{r['artist']} - {r['title']}"
            v, z, cat = check(r["path"], r["artist"] or "", r["title"] or "", r["duration"] or 0)
            with LOCK:
                RESULTS[r["path"]] = {"verdict": v, "z": z, "mtime": mt, "at": time.time(), "id": r["id"], "method": METHOD,
                                      "artist": r["artist"], "title": r["title"], "duration": r["duration"],
                                      "catalogue": (cat or {}).get("album", ""), "cat_duration": (cat or {}).get("duration", 0)}
            SCAN["done"] += 1
            if SCAN["done"] % 20 == 0:
                save()
        save()
        SCAN["wrong"] = len(view()["suspects"])
        core.log(f"wrong versions: checked {SCAN['done']} songs; {SCAN['wrong']} in the library look like another version")
    finally:
        SCAN.update(running=False, current="")


def current_ok(v):
    return v.get("verdict") in ("ok", "dismissed") and v.get("method", 1) >= OK_SINCE


PLACEHOLDER = {"", "artist", "unknown", "unknown artist", "[unknown]", "various artists", "various", "va", "none"}


def real_artist(artist, path):
    """The artist, or (when the tag is a placeholder like "artist") the one in the file or folder name."""
    a = (artist or "").strip()
    if a.lower() not in PLACEHOLDER:
        return a
    stem = os.path.splitext(os.path.basename(path))[0]
    if " - " in stem and stem.split(" - ", 1)[0].strip().lower() not in PLACEHOLDER:
        return stem.split(" - ", 1)[0].strip()
    folder = os.path.basename(os.path.dirname(path))
    return folder if folder.lower() not in PLACEHOLDER else a


def owner_of(path):
    """The family member whose folder this file is in (None: the main library)."""
    fam = core.CONFIG["family"]
    if not path or not core.within(path, fam):
        return None
    return core.users.by_name(os.path.relpath(path, fam).split(os.sep)[0])


def fix_track(path):
    """The newest "Right versions" job song for this file (a copy), or None."""
    best = None
    with core.JOB_LOCK:
        for j in core.JOBS.values():
            if j.get("name") != "Right versions":
                continue
            for t in j["tracks"]:
                if t.get("replace") and os.path.normpath(t.get("file") or "") == os.path.normpath(path) and \
                        (best is None or j["created"] >= best[0]):
                    best = (j["created"], dict(t))
    return best[1] if best else None


def settle():
    """Songs being fixed: done -> off the list; not got (held back, not on your sources) -> back, saying why."""
    with LOCK:
        busy = [(p, v) for p, v in RESULTS.items() if v.get("verdict") == "wrong" and v.get("fixing")]
    changed = False
    for p, v in busy:
        t = fix_track(p)
        st = (t or {}).get("state")
        if st in ("pending", "searching", "downloading"):
            continue
        with LOCK:
            v["fixing"] = False
            if st == "done" and t.get("new_file") and os.path.exists(t["new_file"]):
                v["verdict"], v["fixed_to"] = "fixed", t["new_file"]
            else:
                v["fix_note"] = (t or {}).get("message") or "Couldn't get it this time"
        changed = True
    if changed:
        save()


def view():
    settle()
    with LOCK:
        wrong = [{"path": p, **v} for p, v in RESULTS.items() if v.get("verdict") == "wrong" and v.get("method", 1) >= METHOD
                 and os.path.exists(p)]
        checked = sum(1 for v in RESULTS.values() if (v.get("verdict") in ("ok", "wrong", "dismissed") and v.get("method", 1) >= METHOD)
                      or current_ok(v))
    for x in wrong:
        x["artist"] = real_artist(x.get("artist"), x["path"])
        if x.get("fixing"):
            t = fix_track(x["path"])
            x["fix_note"] = (t or {}).get("message") or ""
    wrong.sort(key=lambda x: (x.get("artist") or "").lower())
    return {**SCAN, "checked": checked, "suspects": [{k: x.get(k) for k in ("id", "artist", "title", "duration", "z", "catalogue",
                                                                         "cat_duration", "fixing", "fix_note")} for x in wrong]}


def fix(ids):
    """Get the right version of these songs: download again (checked against the catalogue) and replace them.
    Any quality will do (the wrong recording is worse than a lossy right one); a better copy is looked for later.
    A song in a family member's folder is fixed as them, so the right one lands back in their folder."""
    if not core.users.can("download"):
        raise PermissionError("Getting songs needs download permission")
    settle()
    picked = []
    with LOCK:
        for p, v in RESULTS.items():
            if v.get("id") in ids and v.get("verdict") == "wrong" and not v.get("fixing") and os.path.exists(p):
                v["fixing"] = True
                v.pop("fix_note", None)
                picked.append((p, dict(v)))
    groups = {}
    for p, v in picked:
        try:
            tier, label = core.file_quality(p)
        except Exception:  # noqa: BLE001
            tier, label = "", ""
        u = owner_of(p)
        groups.setdefault(u["id"] if u else "", (u, []))[1].append(
            {"artist": real_artist(v.get("artist"), p), "title": v["title"], "duration": v.get("cat_duration") or v.get("duration") or 0,
             "file": p, "replace": True, "any_quality": True, "quality": label, "tier": tier})
    jobs = []
    prev = core.users.current()
    for u, tracks in groups.values():
        core.users.LOCAL.user = u or prev
        try:
            jobs.append(core.new_job("Right versions", "upgrade", "", tracks))
        finally:
            core.users.LOCAL.user = prev
    save()
    return {"ok": True, "queued": len(picked), "job": jobs[0] if jobs else None, "jobs": jobs}


def repair():
    """Fixes from 3.9.2 and before: a family member's song went into the main library (put it back in their folder),
    and songs held back by the quality setting (get them, in the best quality there is)."""
    core.users.LOCAL.user = core.users.admin_user()
    moved = 0
    with core.JOB_LOCK:
        tracks = [t for j in core.JOBS.values() if j.get("name") == "Right versions" for t in j["tracks"] if t.get("replace")]
    for t in tracks:
        old, new = t.get("file") or "", t.get("new_file") or ""
        u = owner_of(old)
        if t.get("state") != "done" or not u or not new or not os.path.exists(new) or not core.within(new, core.CONFIG["library"]):
            continue
        dest_dir = os.path.join(core.users.music_root(u["id"]), os.path.basename(os.path.dirname(new)))
        dest = os.path.join(dest_dir, os.path.basename(new))
        if not core.within(dest_dir, core.users.folder(u)) or os.path.exists(dest):
            continue
        try:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(new, dest)
            core.replace_everywhere(new, dest)
            t["new_file"] = dest
            moved += 1
        except OSError as ex:
            core.log("wrong versions: couldn't move", new, ex)
    if moved:
        core.save_jobs()
        core.request_scan()
        core.log(f"wrong versions: put {core.plural(moved, 'song')} back in the family folder they came from")
    settle()
    with LOCK:
        held = [v["id"] for v in RESULTS.values() if v.get("verdict") == "wrong" and "best effort is off" in (v.get("fix_note") or "")]
    if held:
        fix(held)
        core.log(f"wrong versions: getting {core.plural(len(held), 'song')} that waited for a better quality")


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
    if os.environ.get("UMCULO_TEST_VERSIONS") != "off" or os.environ.get("UMCULO_TEST_REPAIR"):
        threading.Thread(target=_repair_later, daemon=True).start()
    if any(v.get("method", 1) < METHOD and not current_ok(v) for v in RESULTS.values()) and os.environ.get("UMCULO_TEST_VERSIONS") != "off":
        threading.Thread(target=recheck_later, daemon=True).start()


def recheck_later():
    """The check got better since these songs were checked: check them again (in the background, once things settle)."""
    time.sleep(600)
    if SCAN["running"]:
        return
    SCAN.update(running=True, started=time.time())
    core.log("wrong versions: checking songs again with the improved comparison")
    run_scan(core.users.admin_user())


def _repair_later():
    time.sleep(float(os.environ.get("UMCULO_REPAIR_AFTER", "90")))     # once the jobs are loaded and things settle
    try:
        repair()
    except Exception as ex:  # noqa: BLE001
        core.log("wrong versions: repair failed:", ex)
