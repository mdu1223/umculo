"""Library care: tidy-ups, lyrics and backups.

  * One artist, two folders ("FreakSA" and "Freak SA") -> merge them
  * The same song more than once -> keep the best copy, point every playlist at it
  * Leftover files in the Spotify Playlists folder that no playlist uses any more
  * Songs without cover art -> fetch it from the catalogue
  * Disk use per folder
  * Lyrics from LRCLIB (free, synced where available), written into the files so Navidrome
    and Amperfy show them; new downloads get them automatically
  * A nightly backup of Umculo's own state (requests, playlists, subscriptions, settings)
Nothing is deleted outside the Umculo and Spotify Playlists folders.
"""
import datetime
import os
import shutil
import tarfile
import threading
import time
import traceback
import urllib.parse

core = None
E = os.environ.get
CFG = {
    "lrclib": E("LRCLIB_API", "https://lrclib.net").rstrip("/"),
    "lyrics_auto": E("LYRICS_AUTO", "1") == "1",
    "backup_dir": E("BACKUP_DIR", "/backup"),
    "backup_keep": int(E("BACKUP_KEEP", "14")),
    "backup_hour": int(E("BACKUP_HOUR", "3")),
}
REPORT = {"state": "idle", "message": "", "at": 0, "artists": [], "dupes": [], "leftovers": [], "no_cover": [],
          "disk": {}, "folders": [], "progress": 0}
LOCK = threading.Lock()
BUSY = threading.Lock()


def log(*a):
    core.log(*a)


def setr(**kw):
    with LOCK:
        REPORT.update(kw)


def deletable(path):
    return core.within(path, core.CONFIG["library"]) or core.within(path, core.CONFIG["spotify_dir"])


# --------------------------------------------------------------------------- scan
def scan():
    if not BUSY.acquire(blocking=False):
        return
    try:
        setr(state="scanning", message="Reading your library", progress=0)
        music, lib, sp = core.CONFIG["music"], core.CONFIG["library"], core.CONFIG["spotify_dir"]
        files = []
        for root in (lib, sp):
            for d, _s, fs in os.walk(root):
                for f in fs:
                    if f.rsplit(".", 1)[-1].lower() in core.AUDIO and not f.startswith("."):
                        files.append(os.path.join(d, f))
        # 1. artist folders that differ only in spelling
        groups = {}
        if os.path.isdir(lib):
            for name in os.listdir(lib):
                p = os.path.join(lib, name)
                if os.path.isdir(p):
                    groups.setdefault(core.norm(name).replace(" ", ""), []).append(name)
        artists = [{"names": sorted(v, key=lambda n: (-len(os.listdir(os.path.join(lib, n))), n.count(" "), n)),
                    "files": sum(len(os.listdir(os.path.join(lib, n))) for n in v)}
                   for v in groups.values() if len(v) > 1]
        # 2. the same song more than once; 4. songs without cover art
        by_key, no_cover = {}, []
        for n, f in enumerate(files, 1):
            if n % 200 == 0:
                setr(progress=int(n * 100 / len(files)), message=f"Reading songs: {n} of {len(files)}")
            d = core.describe_file(f)
            if not d["title"]:
                continue
            k = core.song_key(d["artist"], d["title"])
            by_key.setdefault(k, []).append({"file": f, "quality": d["quality"], "score": core.file_score(f),
                                             "artist": d["artist"], "title": d["title"]})
            if core.within(f, lib) and not has_cover(f):
                no_cover.append({"file": f, "artist": d["artist"], "title": d["title"]})
        dupes = []
        for k, copies in by_key.items():
            if len(copies) > 1:
                copies.sort(key=lambda c: -c["score"])
                dupes.append({"key": k, "artist": copies[0]["artist"], "title": copies[0]["title"],
                              "keep": copies[0], "others": copies[1:]})
        # 3. leftovers in the Spotify folder
        used = set()
        for root in (core.CONFIG["playlists"], sp):
            for d, _s, fs in os.walk(root):
                for f in fs:
                    if f.lower().endswith((".m3u", ".m3u8")) and not f.startswith("."):
                        try:
                            used.update(core.m3u_entries(os.path.join(d, f)))
                        except Exception:  # noqa: BLE001
                            pass
        leftovers = [{"file": f, "size": os.path.getsize(f)} for f in files
                     if core.within(f, sp) and os.path.normpath(f) not in used]
        # 5. disk use
        folders = []
        if os.path.isdir(music):
            for name in sorted(os.listdir(music)):
                p = os.path.join(music, name)
                if os.path.isdir(p):
                    folders.append({"name": name, "size": dir_size(p)})
        du = shutil.disk_usage(music) if os.path.isdir(music) else None
        setr(state="done", at=time.time(), progress=100, artists=artists, dupes=dupes, leftovers=leftovers,
             no_cover=no_cover, folders=sorted(folders, key=lambda x: -x["size"]),
             disk={"total": du.total, "used": du.used, "free": du.free} if du else {},
             message=f"Checked {core.plural(len(files), 'song')}")
    except Exception as e:  # noqa: BLE001
        log("library scan failed:", traceback.format_exc())
        setr(state="failed", message=str(e)[:200])
    finally:
        BUSY.release()


def dir_size(path):
    total = 0
    for d, _s, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return total


def has_cover(path):
    try:
        import mutagen
        m = mutagen.File(path)
        if m is None:
            return True
        if hasattr(m, "pictures") and m.pictures:
            return True
        tags = m.tags or {}
        if hasattr(tags, "getall") and tags.getall("APIC"):
            return True
        return any(k in tags for k in ("covr", "metadata_block_picture"))
    except Exception:  # noqa: BLE001
        return True


# --------------------------------------------------------------------------- fixes
def move_everywhere(old, new):
    core.replace_everywhere(old, new)
    with core.INDEX_LOCK:
        for v in core.INDEX.values():
            if os.path.normpath(v["path"]) == os.path.normpath(old):
                v["path"] = new
        data = dict(core.INDEX)
    core._save_json("index.json", data)


def merge_artists():
    lib, n = core.CONFIG["library"], 0
    for g in REPORT["artists"]:
        keep = os.path.join(lib, g["names"][0])
        for other in g["names"][1:]:
            src_dir = os.path.join(lib, other)
            for f in os.listdir(src_dir):
                src, dst = os.path.join(src_dir, f), os.path.join(keep, f)
                if os.path.exists(dst):
                    base, ext = os.path.splitext(dst)
                    dst = f"{base} (2){ext}"
                shutil.move(src, dst)
                move_everywhere(src, dst)
                n += 1
            try:
                os.rmdir(src_dir)
            except OSError:
                pass
    return f"Moved {core.plural(n, 'file')} into one folder per artist"


def fix_dupes():
    n = 0
    for d in REPORT["dupes"]:
        for o in d["others"]:
            if not os.path.exists(o["file"]) or not deletable(o["file"]):
                continue
            move_everywhere(o["file"], d["keep"]["file"])
            os.remove(o["file"])
            n += 1
    return f"Removed {n} extra copies; playlists now use the best one"


def delete_leftovers():
    n = size = 0
    for f in REPORT["leftovers"]:
        if os.path.exists(f["file"]) and deletable(f["file"]):
            os.remove(f["file"])
            n += 1
            size += f["size"]
    return f"Deleted {n} leftover files ({size // 1_000_000} MB)"


def add_covers():
    n = 0
    for f in REPORT["no_cover"]:
        try:
            hits = core.deezer_search(f"{f['artist']} {f['title']}", limit=5)
            tgt = core.make_target(f["artist"], f["title"])
            hit = next((h for h in hits if core.match(f"{h['artist']} {h['title']}", tgt)[0]), None)
            if hit and (hit.get("cover_big") or hit.get("cover")):
                core.tag_file(f["file"], {**tgt, "artist": f["artist"], "title": f["title"],
                                          "album": hit.get("album", ""), "cover": hit.get("cover_big") or hit["cover"]})
                n += 1
        except Exception as e:  # noqa: BLE001
            log("cover failed:", f["file"], e)
    return f"Added cover art to {n} of {core.plural(len(REPORT['no_cover']), 'song')}"


def run_fix(name):
    if not BUSY.acquire(blocking=False):
        raise ValueError("Umculo is already working on the library")
    try:
        setr(state="fixing", message="Working…")
        msg = {"artists": merge_artists, "dupes": fix_dupes, "leftovers": delete_leftovers,
               "covers": add_covers, "lyrics": lyrics_all}[name]()
        core.request_scan()
        log("library:", msg)
    except Exception as e:  # noqa: BLE001
        log("library fix failed:", traceback.format_exc())
        msg = f"Failed: {e}"
    finally:
        BUSY.release()
    scan()
    setr(message=f"{msg} · {REPORT['message']}")


# --------------------------------------------------------------------------- lyrics
def fetch_lyrics(artist, title, album="", duration=0):
    q = {"artist_name": artist, "track_name": title}
    if album:
        q["album_name"] = album
    if duration:
        q["duration"] = int(duration)
    try:
        r = core.http("GET", f"{CFG['lrclib']}/api/get?" + urllib.parse.urlencode(q), timeout=20, retries=1) or {}
    except Exception:  # noqa: BLE001
        r = {}
    if not (r.get("syncedLyrics") or r.get("plainLyrics")):
        try:
            res = core.http("GET", f"{CFG['lrclib']}/api/search?" + urllib.parse.urlencode(
                {"artist_name": artist, "track_name": title}), timeout=20, retries=1) or []
            r = next((x for x in res if x.get("syncedLyrics") or x.get("plainLyrics")), {})
        except Exception:  # noqa: BLE001
            r = {}
    if r.get("instrumental"):
        return None
    return r.get("syncedLyrics") or r.get("plainLyrics") or None


def write_lyrics(path, text):
    import mutagen
    ext = path.rsplit(".", 1)[-1].lower()
    if ext == "mp3":
        from mutagen.id3 import ID3, USLT
        tags = ID3(path)
        if tags.getall("USLT"):
            return False
        tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
        tags.save()
    elif ext in ("m4a", "aac", "alac"):
        from mutagen.mp4 import MP4
        f = MP4(path)
        if f.get("\xa9lyr"):
            return False
        f["\xa9lyr"] = [text]
        f.save()
    else:
        f = mutagen.File(path)
        if f is None:
            return False
        if ext in ("wav", "aiff", "aif", "dsf") or hasattr(f.tags, "getall"):   # these keep ID3 tags inside
            from mutagen.id3 import USLT
            if f.tags is None:
                f.add_tags()
            if f.tags.getall("USLT"):
                return False
            f.tags.add(USLT(encoding=3, lang="eng", desc="", text=text))
            f.save()
            return True
        if f.get("lyrics") or f.get("LYRICS"):
            return False
        f["lyrics"] = [text]
        f.save()
    return True


def has_lyrics(path):
    try:
        import mutagen
        m = mutagen.File(path)
        tags = (m.tags if m else None) or {}
        if hasattr(tags, "getall"):                       # ID3
            return bool(tags.getall("USLT"))
        for k in ("\xa9lyr", "lyrics", "LYRICS", "unsyncedlyrics"):
            try:
                if k in tags:
                    return True
            except (ValueError, KeyError):
                continue
        return False
    except Exception:  # noqa: BLE001
        return True


def lyrics_for(path):
    try:
        if has_lyrics(path):
            return False
        d = core.describe_file(path)
        tags = core.read_tags(path)
        text = fetch_lyrics(d["artist"], d["title"], tags.get("album", ""), d["duration"])
        return bool(text) and write_lyrics(path, text)
    except Exception as e:  # noqa: BLE001
        log("lyrics failed:", path, e)
        return False


def lyrics_all():
    lib = core.CONFIG["library"]
    files = [os.path.join(d, f) for d, _s, fs in os.walk(lib) for f in fs
             if f.rsplit(".", 1)[-1].lower() in core.AUDIO and not f.startswith(".")]
    n = 0
    for i, f in enumerate(files, 1):
        setr(message=f"Lyrics: {i} of {len(files)} songs, {n} added", progress=int(i * 100 / max(len(files), 1)))
        n += lyrics_for(f)
        time.sleep(0.2)                                   # be kind to LRCLIB
    return f"Added lyrics to {core.plural(n, 'song')}"


def after_filing(path):
    """Called for every new song Umculo files into the library."""
    if CFG["lyrics_auto"] and not core.within(path, core.CONFIG["mixes"]):
        threading.Thread(target=lyrics_for, args=(path,), daemon=True).start()


# --------------------------------------------------------------------------- backups
def backup():
    dest = CFG["backup_dir"] if os.path.isdir(CFG["backup_dir"]) else os.path.join(core.CONFIG["config"], "backups")
    os.makedirs(dest, exist_ok=True)
    name = os.path.join(dest, f"umculo-{datetime.date.today().isoformat()}.tar.gz")
    with tarfile.open(name, "w:gz") as tar:
        for f in os.listdir(core.CONFIG["config"]):
            if f.endswith(".json"):
                tar.add(os.path.join(core.CONFIG["config"], f), arcname=f)
    old = sorted(f for f in os.listdir(dest) if f.startswith("umculo-") and f.endswith(".tar.gz"))
    for f in old[:-CFG["backup_keep"]]:
        os.remove(os.path.join(dest, f))
    log("backup written:", name)
    return name


def loop():
    last = None
    while True:
        time.sleep(600)
        now = datetime.datetime.now()
        if now.hour == CFG["backup_hour"] and last != now.date():
            try:
                backup()
                last = now.date()
            except Exception as e:  # noqa: BLE001
                log("backup failed:", e)
            if now.weekday() == 6:
                scan()                                    # weekly library check, Sunday night


# --------------------------------------------------------------------------- web
def view():
    with LOCK:
        r = dict(REPORT)
    return {"state": r["state"], "message": r["message"], "at": r["at"], "progress": r["progress"],
            "artists": r["artists"][:50], "artists_n": len(r["artists"]),
            "dupes": [{"artist": d["artist"], "title": d["title"], "keep": d["keep"]["quality"],
                       "others": [o["quality"] for o in d["others"]]} for d in r["dupes"][:50]],
            "dupes_n": len(r["dupes"]), "dupes_files": sum(len(d["others"]) for d in r["dupes"]),
            "leftovers_n": len(r["leftovers"]), "leftovers_mb": sum(f["size"] for f in r["leftovers"]) // 1_000_000,
            "no_cover_n": len(r["no_cover"]), "disk": r["disk"], "folders": r["folders"][:12],
            "lyrics_auto": CFG["lyrics_auto"], "busy": BUSY.locked()}


def handle_get(path, qs):
    if path == "/api/library":
        return view()
    return None


def handle_post(path, b):
    if path == "/api/library/scan":
        threading.Thread(target=scan, daemon=True).start()
        return {"ok": True}
    if path == "/api/library/fix":
        name = b.get("what", "")
        if name not in ("artists", "dupes", "leftovers", "covers", "lyrics"):
            raise ValueError("Unknown fix")
        if name != "lyrics" and REPORT["state"] != "done":
            raise ValueError("Run a library check first")
        threading.Thread(target=run_fix, args=(name,), daemon=True).start()
        return {"ok": True}
    if path == "/api/library/backup":
        return {"file": backup()}
    return None


def settings_view():
    return {"lyrics_auto": CFG["lyrics_auto"]}


def apply_setting(k, v):
    if k == "lyrics_auto":
        CFG["lyrics_auto"] = bool(v)
        return True
    return False


def init(umculo):
    global core
    core = umculo
    threading.Thread(target=loop, daemon=True).start()
