"""Tidy mixes: one folder per show, the show's name as the album, duplicates set aside (never deleted).

  meta_podcasts/DysFonik - Videos/x.opus   ->  meta_podcasts/DysFonik/x.opus     album "DysFonik"
  meta_podcasts/Aquatone ... 048.opus      ->  meta_podcasts/Dark Horse SA/...   (loose: the channel, from the artist tag)
  a second copy of the same mix            ->  meta_podcasts/.duplicates/<show>/  (hidden from the library; delete when you like)

Runs after each library scan, so new downloads stay tidy too.
GET  /api/mixes/tidy   -> the plan, nothing changed      (admin)
POST /api/mixes/tidy   -> do it, then rescan             (admin)
"""
import os
import re
import shutil
import threading

core = None
LOCK = threading.Lock()
SUFFIX = re.compile(r"\s*[-–|:]\s*(videos|shorts|uploads|releases|live|topic|streams?|podcast)\s*$", re.I)
AUDIO = {"mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "aiff", "wma", "alac"}
LOOSE = "More mixes"


def show_name(name):
    """'DysFonik - Videos' -> 'DysFonik'; 'Dark Horse SA, Oscar Mbo' -> 'Dark Horse SA' (the channel comes first)."""
    n = SUFFIX.sub("", (name or "").split(",")[0]).strip(" .-_")
    return re.sub(r'[\\/:*?"<>|]+', " ", n).strip() or LOOSE


def tags_of(path):
    import mutagen
    try:
        f = mutagen.File(path, easy=True)
    except Exception:  # noqa: BLE001
        return {}, 0, 0
    if not f:
        return {}, 0, 0
    g = lambda k: ((f.tags or {}).get(k) or [""])[0] if f.tags else ""
    info = getattr(f, "info", None)
    return ({"artist": g("artist"), "title": g("title"), "album": g("album"), "albumartist": g("albumartist")},
            getattr(info, "length", 0) or 0, getattr(info, "bitrate", 0) or 0)


def set_album(path, show):
    """album and album artist = the show, whatever the download wrote there."""
    import mutagen
    ext = path.rsplit(".", 1)[-1].lower()
    if ext in ("m4a", "aac", "alac", "mp4"):
        from mutagen.mp4 import MP4
        f = MP4(path)
        f["\xa9alb"], f["aART"] = [show], [show]
        f.save()
        return
    f = mutagen.File(path, easy=True)
    if f is None:
        return
    if f.tags is None:
        f.add_tags()
    f["album"], f["albumartist"] = [show], [show]
    f.save()


def key_title(t, fallback):
    t = t or os.path.splitext(fallback)[0]
    t = re.sub(r"^\d{4}-\d{2}-\d{2}\s*-\s*", "", t)            # podcast files start with the date
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


def same_mix(a, b):
    """The same recording twice: one title is the other (or its start, before the channel's tagline),
    and they run the same length. 'Mix | September' and 'Mix | November' are different mixes."""
    ka, kb = a["key"], b["key"]
    if not (ka == kb or ka.startswith(kb + " ") or kb.startswith(ka + " ")):
        return False
    la, lb = a["length"], b["length"]
    return not la or not lb or abs(la - lb) <= max(20, 0.02 * max(la, lb))


def plan():
    root = core.CONFIG["mixes"].rstrip("/")
    if not os.path.isdir(root):
        return {"moves": [], "duplicates": [], "retag": []}
    folders = {}                                                 # show (lower) -> the folder name to use
    files = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        rel = os.path.relpath(d, root)
        for n in names:
            if n.startswith(".") or n.rsplit(".", 1)[-1].lower() not in AUDIO:
                continue
            p = os.path.join(d, n)
            tg, length, br = tags_of(p)
            top = rel.split(os.sep)[0] if rel != "." else ""
            show = show_name(top) if top else show_name(tg.get("artist") or "")
            files.append({"path": p, "name": n, "top": top, "show": show, "tags": tg, "length": length, "br": br,
                          "size": os.path.getsize(p)})
    for f in files:                                              # an existing folder's spelling wins
        if f["top"] and show_name(f["top"]) == f["top"]:
            folders.setdefault(f["show"].lower(), f["top"])
    for f in files:
        folders.setdefault(f["show"].lower(), f["show"])
        f["show"] = folders[f["show"].lower()]
    groups = []                                                  # lists of files that are the same mix
    for f in files:
        f["key"] = key_title(f["tags"].get("title"), f["name"])
        g = next((g for g in groups if g[0]["show"] == f["show"] and all(same_mix(f, x) for x in g)), None)
        if g:
            g.append(f)
        else:
            groups.append([f])
    moves, dups, retag = [], [], []
    for fs in groups:
        longest = round(max(x["length"] for x in fs) / 60)      # the full mix (within a minute), then the best bitrate
        fs.sort(key=lambda f: (round(f["length"] / 60) >= longest - 1, f["br"], f["size"]), reverse=True)
        keep, extra = fs[0], fs[1:]
        dest = os.path.join(root, keep["show"], keep["name"])
        if keep["path"] != dest:
            moves.append({"from": keep["path"], "to": dest, "show": keep["show"]})
        if keep["tags"].get("album") != keep["show"] or keep["tags"].get("albumartist") != keep["show"]:
            retag.append({"file": dest, "show": keep["show"]})
        for f in extra:
            dups.append({"from": f["path"], "to": os.path.join(root, ".duplicates", f["show"], f["name"]), "show": f["show"],
                         "same_as": dest})
    return {"moves": moves, "duplicates": dups, "retag": retag}


def free(path):
    if not os.path.exists(path):
        return path
    b, e = os.path.splitext(path)
    k = 2
    while os.path.exists(f"{b} ({k}){e}"):
        k += 1
    return f"{b} ({k}){e}"


def apply(p=None):
    """Move, set the album, set duplicates aside. Never deletes a file."""
    with LOCK:
        p = p or plan()
        moved = {}
        for m in p["duplicates"] + p["moves"]:
            if not os.path.exists(m["from"]):
                continue
            os.makedirs(os.path.dirname(m["to"]), exist_ok=True)
            to = free(m["to"]) if os.path.abspath(m["from"]) != os.path.abspath(m["to"]) else m["to"]
            shutil.move(m["from"], to)
            moved[m["from"]] = to
        done = 0
        for r in p["retag"]:
            path = r["file"] if os.path.exists(r["file"]) else moved.get(r["file"], r["file"])
            try:
                set_album(path, r["show"])
                done += 1
            except Exception as ex:  # noqa: BLE001
                core.log("tidy mixes: couldn't tag", path, ex)
        root = core.CONFIG["mixes"].rstrip("/")
        for d, _dirs, _names in sorted(os.walk(root), key=lambda x: -len(x[0])):     # empty old folders go
            if d != root and not os.path.basename(d).startswith(".") and not os.listdir(d):
                os.rmdir(d)
        for fn in (core.links.moved_files, core.video.moved):     # found tracklists and saved videos follow the files
            try:
                fn(moved)
            except Exception as ex:  # noqa: BLE001
                core.log("tidy mixes: couldn't update", fn.__module__, ex)
        if moved or done:
            core.log(f"tidy mixes: {len(p['moves'])} moved, {len(p['duplicates'])} duplicates set aside, {done} tagged")
            core.request_scan()
        return {"moved": len(p["moves"]), "duplicates": len(p["duplicates"]), "tagged": done}


def marker():
    return os.path.join(core.CONFIG["config"], "mixtidy.done")


def after_scan():
    """Called after each scan: new downloads land in their show's folder with the show as the album.
    Only once you've tidied by hand (and seen what it does) the first time."""
    if not core.CONFIG.get("tidy_mixes", True) or not os.path.exists(marker()):
        return
    p = plan()
    if p["moves"] or p["retag"] or p["duplicates"]:
        apply(p)


def handle(method, path):
    if path != "/api/mixes/tidy":
        return None
    if not core.users.is_admin():
        raise PermissionError("Only the admin can tidy the mixes")
    if method == "GET":
        p = plan()
        root = core.CONFIG["mixes"].rstrip("/") + "/"
        rel = lambda x: x.replace(root, "")
        return {"moves": [{"from": rel(m["from"]), "to": rel(m["to"])} for m in p["moves"]],
                "duplicates": [{"from": rel(m["from"]), "same_as": rel(m["same_as"])} for m in p["duplicates"]],
                "retag": [{"file": rel(r["file"]), "album": r["show"]} for r in p["retag"]]}
    out = apply()
    with open(marker(), "w") as fh:
        fh.write("tidied by hand once; after each scan from now on\n")
    return out


def init(umculo):
    global core
    core = umculo
