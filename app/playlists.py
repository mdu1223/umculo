"""Playlists, as one list: every .m3u in your Spotify Playlists folder and Umculo's playlists folder,
joined with what Umculo is doing to it (upgrade or import job) and whether it's kept in sync.

Each playlist is either
  * working   - an upgrade or import is running now
  * watching  - Umculo keeps looking for better copies and retries missing songs
  * finished  - everything is as good as it gets; Umculo stopped watching (moves here by itself)
  * resting   - you told Umculo to stop watching it
Actions: rename, stop/start watching, delete (keep the songs, or delete the songs only this
playlist uses), and remove single songs.
"""
import hashlib
import os
import re
import threading
import time

core = None
META = {}                      # path -> {"watch": bool, "why": "finished"|"you", "at": t}
LOCK = threading.Lock()
CACHE = {"at": 0, "data": None}


def pid(path):
    return hashlib.sha1(path.encode()).hexdigest()[:12]


def save():
    with LOCK:
        data = dict(META)
    core._save_json("playlists_meta.json", data)


def roots():
    people = os.path.join(core.CONFIG["config"], "people")
    extra = [os.path.join(people, d, "playlists") for d in (os.listdir(people) if os.path.isdir(people) else [])]
    return [r for r in [core.CONFIG["spotify_dir"], core.CONFIG["playlists"]] + extra if r and os.path.isdir(r)]


def owner_of(path, job=None):
    """-> user id of the person a playlist belongs to (None = yours, the admin's)."""
    if job and job.get("user"):
        return job["user"]
    people = os.path.join(core.CONFIG["config"], "people")
    if core.within(path, people):
        name = os.path.relpath(path, people).split(os.sep)[0]
        u = core.users.by_name(name)
        return u["id"] if u else None
    return None


def visible(c):
    """Members see their own playlists and the ones you share with them; you see everything."""
    me = core.users.current()
    if core.users.is_admin(me):
        return True
    return c["owner"] == me["id"] or c["path"] in (me.get("shared_playlists") or [])


def files():
    out = []
    for root in roots():
        for d, _s, fs in os.walk(root):
            for f in fs:
                if f.lower().endswith((".m3u", ".m3u8")) and not f.startswith("."):
                    out.append(os.path.join(d, f))
    return sorted(set(out))


def jobs_for(path):
    with core.JOB_LOCK:
        js = [j for j in core.JOBS.values() if j["m3u"] == path]
    return sorted(js, key=lambda j: j["created"], reverse=True)


def job_for(path):
    """The job that speaks for this playlist: a running one, else the newest."""
    js = jobs_for(path)
    return next((j for j in js if core.job_summary_running(j)), js[0] if js else None)


def watching(path):
    with LOCK:
        return (META.get(path) or {}).get("watch", True)


def set_watch(path, on, why="you"):
    with LOCK:
        META[path] = {"watch": bool(on), "why": "" if on else why, "at": time.time()}
    save()
    CACHE["at"] = 0


def finished_cleanly(job):
    """Nothing left to try: every song upgraded, or kept because nothing better exists."""
    c = core.job_summary(job)["counts"]
    return not core.job_summary_running(job) and not job.get("cancel") and \
        not any(c.get(k) for k in ("not_found", "failed", "cancelled", "pending", "searching", "downloading"))


def on_job_finished(job):
    """Called by the engine when a job settles: clean finishes stop being watched."""
    if job["kind"] == "album":
        return
    if finished_cleanly(job) and watching(job["m3u"]) and not follow_for(job["m3u"]):
        set_watch(job["m3u"], False, "finished")
        core.log(f"playlist finished, no longer watching: {job['name']}")


def follow_for(path):
    name = os.path.splitext(os.path.basename(path))[0].lower()
    with core.share.LOCK:
        for f in core.share.FOLLOWS.values():
            j = core.JOBS.get(f.get("job"))
            if f.get("m3u") == path or (j and j["m3u"] == path) or f["name"].lower() == name and \
                    os.path.dirname(path) == core.CONFIG["playlists"]:
                return f
    return None


def card(path):
    exists = os.path.isfile(path)
    entries = core.m3u_entries(path) if exists else []
    job = job_for(path)
    js = core.job_summary(job) if job else None
    kept = set()
    if job:                    # songs Umculo already looked for: "kept" (or, with best effort, "best available")
        states = ("kept", "done", "not_found") if core.CONFIG["best_effort"] else ("kept",)
        with core.JOB_LOCK:
            kept = {t.get("new_file") or t.get("file") for t in job["tracks"] if t["state"] in states}
    tiers = {}
    for e in entries:
        tier = core.file_quality(e)[0] if os.path.exists(e) else "missing"
        if tier in ("ok", "low", "unknown"):
            if core.good_enough(core.file_score_cached(e)):
                tier = "profile"          # as good as your quality profile asks for
            elif e in kept:
                tier = "kept"             # Umculo looked: nothing better exists yet
        tiers[tier] = tiers.get(tier, 0) + 1
    if job and job["kind"] != "upgrade":       # songs an import never found aren't in the file yet
        never = sum(js["counts"].get(k, 0) for k in ("not_found", "failed", "cancelled"))
        if never:
            tiers["missing"] = tiers.get("missing", 0) + never
    fol = follow_for(path)
    with LOCK:
        m = dict(META.get(path) or {})
    watch = m.get("watch", True)
    if js and js["running"]:
        status = "working"
    elif watch:
        status = "watching"
    else:
        status = "finished" if m.get("why") == "finished" else "resting"
    folder = "spotify" if core.within(path, core.CONFIG["spotify_dir"]) else "umculo"
    name = os.path.splitext(os.path.basename(path))[0]
    owner = owner_of(path, job)
    who = core.users.USERS.get(owner, {}).get("name", "") if owner else ""
    return {"id": pid(path), "path": path, "name": js["name"] if (js and not exists) else name, "folder": folder,
            "tracks": max(len(entries), js["total"] if js and job["kind"] != "upgrade" else 0), "tiers": tiers, "job": js,
            "follow": {k: fol.get(k) for k in ("id", "last_check", "added", "error", "count")} if fol else None,
            "watch": watch, "status": status, "since": m.get("at"), "owner": owner, "who": who,
            "shared_with": core.users.shared_with("playlist", path) if not owner else [],
            "mtime": os.path.getmtime(path) if exists else (job["created"] if job else 0)}


def all_cards(fresh=False):
    return [c for c in _all_cards(fresh) if visible(c)]


def _all_cards(fresh=False):
    if not fresh and CACHE["data"] is not None and time.time() - CACHE["at"] < 8:
        return CACHE["data"]
    paths = set(files())
    with core.JOB_LOCK:
        paths |= {j["m3u"] for j in core.JOBS.values() if j["kind"] != "album"}
    out = []
    for p in paths:
        try:
            out.append(card(p))
        except Exception as e:  # noqa: BLE001
            core.log("playlist card failed:", p, e)
    order = {"working": 0, "watching": 1, "finished": 2, "resting": 3}
    out.sort(key=lambda c: (order[c["status"]], c["name"].lower()))
    CACHE.update(at=time.time(), data=out)
    return out


def by_id(i):
    for c in all_cards():
        if c["id"] == i:
            return c
    for c in all_cards(True):
        if c["id"] == i:
            return c
    raise LookupError("Playlist not found; refresh the page")


# --------------------------------------------------------------------------- detail and song removal
def detail(i):
    c = by_id(i)
    job = job_for(c["path"])
    rows = []
    if job:
        with core.JOB_LOCK:
            tracks = [dict(t) for t in job["tracks"]]
        for t in tracks:
            f = t.get("new_file") or t.get("file") or ""
            if t.get("file") and not os.path.splitext(t["file"])[1]:
                continue                       # "*" lines spotDL wrote, from before Umculo skipped them
            state = t["state"]
            if state == "kept" and f and os.path.exists(f) and core.good_enough(core.file_score_cached(f)):
                state, t["message"] = "top", "Already in your quality: nothing to upgrade"      # nothing to upgrade: it's top quality
            rows.append({"key": f"t{t['i']}", "i": t["i"], "artist": t["artist"], "title": t["title"], "file": f,
                         "state": state, "quality": t.get("quality") or "", "new_quality": t.get("new_quality") or "",
                         "message": t.get("message") or "", "job": job["id"]})
        listed = {r["file"] for r in rows if r["file"]}
    else:
        listed = set()
    if os.path.isfile(c["path"]):
        for e in core.m3u_entries(c["path"]):
            if e in listed:
                continue
            d = core.describe_file(e) if os.path.exists(e) else {"artist": "", "title": os.path.basename(e),
                                                                  "quality": "missing", "tier": "missing"}
            rows.append({"key": "f" + hashlib.sha1(e.encode()).hexdigest()[:10], "artist": d["artist"], "title": d["title"],
                         "file": e, "state": "missing" if d["tier"] == "missing" else "listed", "quality": d["quality"],
                         "new_quality": "", "message": "", "job": None})
    return {**c, "rows": rows}


def used_elsewhere(path, skip):
    for p in files():
        if p != skip and os.path.isfile(p) and path in set(core.m3u_entries(p)):
            return True
    return False


def remove_song(i, key, delete_file=False):
    c = by_id(i)
    d = detail(i)
    row = next((r for r in d["rows"] if r["key"] == key), None)
    if not row:
        raise LookupError("That song is no longer in the playlist")
    if row["job"]:
        job = core.JOBS.get(row["job"])
        if job:
            with core.JOB_LOCK:
                job["tracks"] = [t for t in job["tracks"] if t["i"] != row["i"]]
            core.save_jobs()
    f = row["file"]
    if os.path.isfile(c["path"]) and f:
        with core.M3U_LOCK:
            core.write_m3u(c["path"], [e for e in core.m3u_entries(c["path"]) if e != f])
    deleted = False
    if delete_file and f and os.path.exists(f) and not used_elsewhere(f, c["path"]):
        deleted = core.delete_song_file(f)
    CACHE["at"] = 0
    core.nd_scan()
    return {"ok": True, "deleted": deleted}


# --------------------------------------------------------------------------- rename / delete / watch
def nd_playlist(name):
    try:
        for p in ((core.nd_call("getPlaylists") or {}).get("playlists") or {}).get("playlist") or []:
            if p.get("name") == name:
                return p
    except Exception:  # noqa: BLE001
        pass
    return None


def nd_drop(name):
    """Remove Navidrome's copy of a playlist. -> "deleted" | "none" | "refused: why"."""
    if core.builtin():
        return "none"                    # the built-in server follows the files: nothing left behind
    p = nd_playlist(name)
    if not p:
        return "none"
    r = core.nd_call("deletePlaylist", id=p["id"]) or {}
    if r.get("status") == "ok":
        return "deleted"
    why = (r.get("error") or {}).get("message") or "no answer"
    core.log(f"Navidrome wouldn't delete its old playlist '{name}': {why}")
    return "refused: " + why


def after_rename(old_path, old_name, new_name):
    """Navidrome rescans slowly, and some tools rewrite playlist files: check twice and tidy up."""
    for wait in ((40, 180) if not core.builtin() else ()):
        time.sleep(wait)
        if os.path.exists(old_path):
            core.log(f"'{os.path.basename(old_path)}' came back after the rename to '{new_name}': another program "
                     f"writes this playlist file, so rename it there instead")
            return
        if nd_playlist(new_name) and nd_drop(old_name) == "deleted":
            core.log(f"removed Navidrome's leftover '{old_name}' playlist")
            core.nd_scan()


def rename(i, new):
    c = by_id(i)
    new = core.safe(new)
    if not new or new == c["name"]:
        raise ValueError("Type a new name")
    ext = os.path.splitext(c["path"])[1] or ".m3u8"
    dest = os.path.join(os.path.dirname(c["path"]), new + ext)
    if os.path.exists(dest):
        raise ValueError("There's already a playlist with that name")
    old_nd = nd_playlist(c["name"])
    if os.path.isfile(c["path"]):
        os.rename(c["path"], dest)
    with core.JOB_LOCK:
        for j in core.JOBS.values():
            if j["m3u"] == c["path"]:
                j["m3u"], j["name"] = dest, new
    core.save_jobs()
    with LOCK:
        if c["path"] in META:
            META[dest] = META.pop(c["path"])
    save()
    with core.share.LOCK:
        for f in core.share.FOLLOWS.values():
            j = core.JOBS.get(f.get("job"))
            if f.get("m3u") == c["path"] or (j and j["m3u"] == dest):
                f["name"], f["m3u"] = new, dest
    core.share.save_follows()
    if c["path"] == mixtapes_path():          # Umculo keeps writing it, under the new name
        MIX["name"] = new
        MIX["sig"] = None
        mix_save()
    nd = nd_drop(c["name"]) if old_nd else "none"   # Navidrome would keep the old name as a second copy
    core.nd_scan()
    threading.Thread(target=after_rename, args=(c["path"], c["name"], new), daemon=True).start()
    CACHE["at"] = 0
    core.log(f"renamed playlist '{c['name']}' to '{new}' (Navidrome copy: {nd})")
    return {"ok": True, "id": pid(dest), "navidrome": nd}


def delete(i, songs=False):
    c = by_id(i)
    removed = 0
    entries = core.m3u_entries(c["path"]) if os.path.isfile(c["path"]) else []
    for j in jobs_for(c["path"]):
        j["cancel"] = True
        with core.JOB_LOCK:
            core.JOBS.pop(j["id"], None)
            if not entries:
                entries = [t.get("new_file") or t.get("file") for t in j["tracks"] if t.get("new_file") or t.get("file")]
    core.save_jobs()
    fol = follow_for(c["path"])
    if fol:
        with core.share.LOCK:
            core.share.FOLLOWS.pop(fol["id"], None)
        core.share.save_follows()
    if c["path"] == mixtapes_path():          # you don't want it: stop making it
        MIX["on"] = False
        mix_save()
    if os.path.isfile(c["path"]):
        os.remove(c["path"])
    if songs:
        for e in dict.fromkeys(entries):
            if e and os.path.exists(e) and not used_elsewhere(e, c["path"]) and core.delete_song_file(e):
                removed += 1
    with LOCK:
        META.pop(c["path"], None)
    save()
    nd_drop(c["name"])
    core.nd_scan()
    CACHE["at"] = 0
    core.log(f"deleted playlist {c['name']}" + (f" and {removed} songs only it used" if songs else ""))
    return {"ok": True, "deleted_songs": removed}


def delete_preview(i):
    """How many songs would really be deleted (those no other playlist uses)."""
    c = by_id(i)
    entries = core.m3u_entries(c["path"]) if os.path.isfile(c["path"]) else []
    only = [e for e in dict.fromkeys(entries) if os.path.exists(e) and not used_elsewhere(e, c["path"])]
    return {"songs": len(entries), "only_here": len(only),
            "size": sum(os.path.getsize(e) for e in only if os.path.exists(e))}


def upgrade_all():
    """'Upgrade all' only touches Spotify playlists you're still watching."""
    return [core.start_upgrade(c["path"]) for c in all_cards(True)
            if c["folder"] == "spotify" and c["watch"] and c["status"] != "working"]


# --------------------------------------------------------------------------- Mixtapes: every mix, one playlist
MIX = {"name": "Mixtapes", "on": True, "sig": None}


def mixtapes_path():
    return os.path.join(core.CONFIG["playlists"], core.safe(MIX["name"]) + ".m3u8")


def write_mixtapes(force=False):
    """Keep a playlist of every mix and episode Umculo has (newest first)."""
    if not MIX["on"]:
        return
    root = core.CONFIG["mixes"]
    found = []
    for d, _s, fs in os.walk(root):
        for f in fs:
            if f.lower().endswith(tuple("." + e for e in core.AUDIO)) and not f.startswith("."):
                p = os.path.join(d, f)
                found.append((os.path.getmtime(p), p))
    files = [p for _t, p in sorted(found, reverse=True)]
    sig = (mixtapes_path(), tuple(files))
    if not force and sig == MIX["sig"] and os.path.exists(mixtapes_path()):
        return
    MIX["sig"] = sig
    if not files:
        return
    with core.M3U_LOCK:
        core.write_m3u(mixtapes_path(), files)
    CACHE["at"] = 0


def mixtapes_loop():
    time.sleep(30)
    while True:
        try:
            write_mixtapes()
        except Exception as e:  # noqa: BLE001
            core.log("mixtapes playlist failed:", e)
        time.sleep(300)


def mix_save():
    core._save_json("mixtapes.json", {"name": MIX["name"], "on": MIX["on"]})


# --------------------------------------------------------------------------- web
def handle_get(path, qs):
    if path == "/api/pl":
        return {"playlists": all_cards(bool(qs.get("fresh")))}
    m = re.match(r"^/api/pl/([0-9a-f]{12})$", path)
    if m:
        return detail(m.group(1))
    m = re.match(r"^/api/pl/([0-9a-f]{12})/delete-preview$", path)
    if m:
        return delete_preview(m.group(1))
    return None


def handle_post(path, b):
    if path == "/api/pl/upgrade-all":
        return {"ids": upgrade_all()}
    m = re.match(r"^/api/pl/([0-9a-f]{12})/(rename|delete|watch|unwatch|remove|upgrade)$", path)
    if not m:
        return None
    i, act = m.groups()
    me = core.users.current()
    if not core.users.is_admin(me) and by_id(i)["owner"] != me["id"]:
        raise PermissionError("This playlist is shared with you; only the admin can change it")
    if act == "rename":
        return rename(i, b.get("name", ""))
    if act == "delete":
        return delete(i, bool(b.get("songs")))
    if act == "remove":
        return remove_song(i, b.get("key", ""), bool(b.get("delete")))
    c = by_id(i)
    if act == "upgrade":
        set_watch(c["path"], True)
        if c["folder"] == "spotify":
            return {"id": core.start_upgrade(c["path"])}
        job = job_for(c["path"])
        if not job:
            raise ValueError("Nothing to upgrade here yet")
        return {"count": core.requeue(job, ("not_found", "failed", "cancelled", "kept"))}
    set_watch(c["path"], act == "watch")
    if act == "unwatch":
        fol = follow_for(c["path"])
        if fol:                                   # not watching = not syncing either
            with core.share.LOCK:
                core.share.FOLLOWS.pop(fol["id"], None)
            core.share.save_follows()
        for j in jobs_for(c["path"]):
            if core.job_summary_running(j):
                j["cancel"] = True
        core.save_jobs()
    return {"ok": True}


def init(umculo):
    global core
    core = umculo
    META.update(core._load_json("playlists_meta.json", {}))
    MIX.update(core._load_json("mixtapes.json", {}))
    threading.Thread(target=mixtapes_loop, daemon=True).start()
