"""Full check of the built-in music server against your real library, and side by side with Navidrome.

Settings > Connectors > Music server > Run a full check. The report is plain text you can copy and send.
"""
import os
import random
import re
import subprocess
import time

core = None
LAST = {"at": 0, "report": "", "running": False}


def run():
    if LAST["running"]:
        raise ValueError("A check is already running")
    LAST["running"] = True
    try:
        return _run()
    finally:
        LAST["running"] = False


def _run():
    db, srv, users = core.musicdb, core.server, core.users
    lines, counts = [], {"ok": 0, "warn": 0, "bad": 0}
    t_all = time.time()

    def say(state, text):
        counts[state] += 1
        lines.append(({"ok": "OK  ", "warn": "WARN", "bad": "FAIL"}[state], text))

    def timed(fn):
        t = time.time()
        try:
            return fn(), int((time.time() - t) * 1000), None
        except Exception as e:  # noqa: BLE001
            return None, int((time.time() - t) * 1000), e

    admin = users.admin_user() or users.OPEN
    if db.DB is None:
        say("bad", "Library database isn't open")
        return finish(lines, counts, t_all)
    n = db.q("SELECT (SELECT COUNT(*) FROM song) s, (SELECT COUNT(*) FROM album) a, (SELECT COUNT(*) FROM artist WHERE albums > 0) r,"
             " (SELECT COUNT(*) FROM playlist) p, (SELECT COALESCE(SUM(size),0) FROM song) b", one=True)
    sc = db.SCAN
    say("ok" if sc["last"] and not sc["error"] else "warn",
        f"Library: {n['s']:,} songs, {n['a']:,} albums, {n['r']:,} artists, {n['p']} playlists, {n['b'] / 1e9:.1f} GB"
        + (f"; last scan {int(time.time() - sc['last'])} s ago took {sc['took']} s" if sc["last"] else "; not scanned yet")
        + (f"; scan error: {sc['error']}" if sc["error"] else ""))
    if not n["s"]:
        say("bad", "No songs: is the music folder mounted where Umculo expects it?")
        return finish(lines, counts, t_all)
    for lib in db.q("SELECT * FROM library ORDER BY id"):
        c = db.q("SELECT COUNT(*) n FROM song WHERE lib=?", (lib["id"],), one=True)["n"]
        who = users.USERS.get(lib["owner"] or "", {}).get("username") or "everyone/admin"
        say("ok" if os.path.isdir(lib["path"]) else "bad", f"Folder {lib['id']} ({who}): {lib['path']} · {c:,} songs"
            + ("" if os.path.isdir(lib["path"]) else " · FOLDER MISSING"))

    # files
    rows = db.q("SELECT id, lib, path, title, artist, album, suffix, bitrate, duration FROM song")
    missing = [r for r in rows if not os.path.exists(r["path"])]
    say("ok" if not missing else "warn", f"Files on disk: {len(rows) - len(missing):,} of {len(rows):,}"
        + (" · missing e.g. " + "; ".join(r["path"] for r in missing[:3]) if missing else ""))
    zero = [r for r in rows if not r["duration"]]
    say("ok" if not zero else "warn", f"Songs with a length: {len(rows) - len(zero):,} of {len(rows):,}"
        + (" · broken or unreadable files (download them again): " + "; ".join(
            f"{os.path.basename(r['path'])} ({os.path.getsize(r['path']) // 1000} KB)" for r in zero[:5] if os.path.exists(r["path"])) if zero else ""))
    untagged = db.q("SELECT COUNT(*) n FROM song WHERE artist='Unknown artist'", one=True)["n"]
    say("ok" if not untagged else "warn", f"Songs without an artist tag: {untagged}")
    dups = db.q("""SELECT lib, lower(title) t, lower(artist) a, COUNT(*) n FROM song GROUP BY lib, lower(title), lower(artist), round(duration)
                   HAVING n > 1""")
    say("ok" if not dups else "warn", f"Exact duplicates (same title, artist and length): {len(dups)}"
        + (" · e.g. " + "; ".join(f"{d['a']} - {d['t']}" for d in dups[:3]) if dups else ""))
    empty = db.q("SELECT COUNT(*) n FROM album WHERE songs = 0", one=True)["n"]
    one = db.q("SELECT COUNT(*) n FROM album WHERE songs = 1", one=True)["n"]
    say("ok" if not empty else "bad", f"Albums: {n['a']:,} ({one:,} singles); empty albums: {empty}")
    split = db.q("""SELECT album, COUNT(DISTINCT album_id) k FROM song WHERE album != '' GROUP BY lib, dir, lower(album) HAVING k > 1""")
    say("ok" if not split else "warn", f"Albums split in two inside one folder: {len(split)}"
        + (" · e.g. " + "; ".join(s["album"] for s in split[:3]) if split else ""))

    # what apps ask for, timed
    for ep, p in (("getArtists", {}), ("getAlbumList2", {"type": "newest", "size": "50"}), ("getAlbumList2", {"type": "random", "size": "50"}),
                  ("search3", {"query": "", "songCount": "500"}), ("getStarred2", {}), ("getPlaylists", {}), ("getGenres", {})):
        res, ms, err = timed(lambda: srv.call(admin, ep, {k: [v] for k, v in p.items()}))
        say("bad" if err else "ok" if ms < 1500 else "warn", f"{ep} {p.get('type', '')}: " + (f"failed: {err}" if err else f"{ms} ms"))
    sample = random.sample(rows, min(25, len(rows)))
    bad = []
    for r in sample:
        res, ms, err = timed(lambda: srv.call(admin, "getSong", {"id": [r["id"]]}))
        if err:
            bad.append(f"{r['title']}: {err}")
    say("ok" if not bad else "bad", f"getSong on {len(sample)} random songs" + (": " + "; ".join(bad[:3]) if bad else ": all fine"))
    words = [w for w in core.musicdb.fold(random.choice(rows)["artist"]).split() if len(w) > 2][:2]
    if words:
        res, ms, err = timed(lambda: srv.call(admin, "search3", {"query": [" ".join(words)], "songCount": ["20"]}))
        hits = len((res or {}).get("searchResult3", {}).get("song") or [])
        say("ok" if hits else "bad", f"Search \"{' '.join(words)}\": {hits} songs in {ms} ms")

    # pictures
    albums = db.q("SELECT id, name, artist FROM album ORDER BY RANDOM() LIMIT 30")
    no_art = []
    t = time.time()
    for a in albums:
        try:
            if not srv.cover_bytes(a["id"]):
                no_art.append(f"{a['artist']} - {a['name']}")
        except Exception as e:  # noqa: BLE001
            no_art.append(f"{a['name']} ({e})")
    say("ok" if len(no_art) <= len(albums) // 10 else "warn",
        f"Cover art: {len(albums) - len(no_art)} of {len(albums)} random albums have one ({int((time.time() - t) * 1000)} ms)"
        + (" · none for e.g. " + "; ".join(no_art[:3]) if no_art else ""))

    # playing
    for kind, cond in (("lossless", "suffix IN ('flac','wav','aiff','alac','ape','wv')"), ("mp3", "suffix='mp3'"), ("m4a", "suffix IN ('m4a','mp4','aac')")):
        r = db.q(f"SELECT * FROM song WHERE {cond} ORDER BY RANDOM() LIMIT 1", one=True)
        if not r:
            continue
        t = time.time()
        p = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-t", "5", "-i", r["path"], "-map", "0:a:0", "-c:a", "libmp3lame",
                            "-b:a", "192k", "-f", "mp3", "-"], capture_output=True, timeout=60)
        ok = p.returncode == 0 and len(p.stdout) > 20000
        say("ok" if ok else "bad", f"Convert a {kind} song for streaming ({os.path.basename(r['path'])}): "
            + (f"{len(p.stdout) // 1000} KB in {int((time.time() - t) * 1000)} ms" if ok else p.stderr.decode(errors='replace')[:200]))
    lyr = db.q("SELECT COUNT(*) n FROM song WHERE lyrics=1", one=True)["n"]
    say("ok", f"Songs with lyrics: {lyr:,} of {n['s']:,}")

    # playlists
    waiting = db.q("""SELECT playlist.name, COUNT(*) n FROM plsong JOIN playlist ON playlist.id = plsong.pid WHERE plsong.song = ''
                      GROUP BY playlist.id ORDER BY n DESC""")
    total_wait = sum(w["n"] for w in waiting)
    say("ok" if not total_wait else "warn", f"Playlist entries whose file isn't there: {total_wait}"
        + (" · " + "; ".join(f"{w['name']} ({w['n']})" for w in waiting[:4]) if waiting else ""))

    # people
    for u in users.USERS.values():
        ls = db.libraries_for(u)
        c = db.q(f"SELECT COUNT(*) n FROM song WHERE lib IN ({','.join(str(i) for i in ls) or '-1'})", one=True)["n"]
        secret = bool(users.subsonic_secret(u))
        say("ok" if secret else "warn", f"{u.get('name') or u['username']} ({u['username']}, {u.get('role')}): sees {c:,} songs"
            + ("" if secret else " · can't sign in to music apps yet: they need to sign in to Umculo once"))

    try:
        compare_navidrome(say, rows)
    except Exception as e:  # noqa: BLE001
        say("warn", f"Navidrome comparison stopped: {e}")
    return finish(lines, counts, t_all)


def compare_navidrome(say, rows):
    if not core.CONFIG["nd_user"] or not core.CONFIG["nd_password"]:
        say("ok", "Navidrome: not set up, nothing to compare")
        return
    r = core.navidrome_call("ping")
    if not r or r.get("status") != "ok":
        say("ok", "Navidrome: not answering (fine if you've retired it), nothing to compare")
        return
    srv, db = core.server, core.musicdb
    nd_songs, off = [], 0
    while True:
        res = core.navidrome_call("search3", query="", songCount=500, songOffset=off, artistCount=0, albumCount=0) or {}
        batch = (res.get("searchResult3") or {}).get("song") or []
        nd_songs += batch
        if len(batch) < 500 or off > 300000:
            break
        off += 500
    def keys(title, dur):                          # artists are written differently by each app; title + length is enough
        t, d = core.musicdb.fold(re.sub(r"\s*[(\[].*$", "", title or "")) or core.musicdb.fold(title), int(round(float(dur or 0)))
        return [(t, d + x) for x in (-2, -1, 0, 1, 2)]
    mine_rows = db.q("SELECT title, artist, duration, rel, lib FROM song")
    mine = {k for r in mine_rows for k in keys(r["title"], r["duration"])}
    theirs = {k for s in nd_songs for k in keys(s.get("title"), s.get("duration"))}
    only_nd = [s for s in nd_songs if not any(k in mine for k in keys(s.get("title"), s.get("duration")))]
    only_me = [r for r in mine_rows if not any(k in theirs for k in keys(r["title"], r["duration"]))]
    try:
        folders = [f.get("name") for f in ((core.navidrome_call("getMusicFolders") or {}).get("musicFolders") or {}).get("musicFolder") or []]
    except Exception:  # noqa: BLE001
        folders = []
    say("ok" if len(only_nd) <= max(3, len(nd_songs) // 100) else "warn",
        f"Navidrome has {len(nd_songs):,} songs in {len(folders)} librar{'y' if len(folders) == 1 else 'ies'} ({', '.join(folders)}), Umculo {len(rows):,}"
        + (f" · {len(only_nd)} only in Navidrome, e.g. " + "; ".join(f"{s.get('artist')} - {s.get('title')} ({s.get('suffix')})" for s in only_nd[:4]) if only_nd else "")
        + (f" · {len(only_me)} only in Umculo, e.g. " + "; ".join(r["rel"] for r in only_me[:3]) if only_me else ""))
    nd_albums = 0
    off = 0
    while True:
        res = core.navidrome_call("getAlbumList2", type="alphabeticalByName", size=500, offset=off) or {}
        b = (res.get("albumList2") or {}).get("album") or []
        nd_albums += len(b)
        if len(b) < 500:
            break
        off += 500
    ua = db.q("SELECT COUNT(*) n FROM album WHERE lib=1", one=True)["n"]
    say("ok" if abs(nd_albums - ua) <= max(3, nd_albums // 50) else "warn", f"Albums: Navidrome {nd_albums:,}, Umculo {ua:,}")
    nd_art = sum(len(i.get("artist") or []) for i in ((core.navidrome_call("getArtists") or {}).get("artists") or {}).get("index") or [])
    ur = db.q("SELECT COUNT(*) n FROM artist WHERE lib=1 AND albums > 0", one=True)["n"]
    say("ok" if abs(nd_art - ur) <= max(3, nd_art // 20) else "warn", f"Artists: Navidrome {nd_art:,}, Umculo {ur:,}")
    nd_pl = {p["name"].lower(): p.get("songCount", 0) for p in ((core.navidrome_call("getPlaylists") or {}).get("playlists") or {}).get("playlist") or []}
    admin = core.users.admin_user() or core.users.OPEN
    my_pl = {p["name"].lower(): p["songCount"] for p in srv.call(admin, "getPlaylists", {})["playlists"]["playlist"]}
    diff = [f"{k} (Navidrome {v}, Umculo {my_pl.get(k, 'missing')})" for k, v in nd_pl.items() if my_pl.get(k) != v]
    say("ok" if not diff else "warn", f"Playlists: Navidrome {len(nd_pl)}, Umculo {len(my_pl)}" + (" · different: " + "; ".join(diff[:5]) if diff else ", same songs in each"))
    nd_star = (core.navidrome_call("getStarred2") or {}).get("starred2") or {}
    me = srv.call(admin, "getStarred2", {})["starred2"]
    say("ok", f"Stars: Navidrome {len(nd_star.get('song') or [])} songs/{len(nd_star.get('album') or [])} albums, "
              f"Umculo {len(me.get('song') or [])} songs/{len(me.get('album') or [])} albums")


def finish(lines, counts, t0):
    head = (f"Umculo {core.VERSION} full check · {time.strftime('%Y-%m-%d %H:%M')} · {int(time.time() - t0)} s · "
            f"{counts['ok']} fine, {counts['warn']} to look at, {counts['bad']} failed")
    report = head + "\n" + "\n".join(f"{s} {t}" for s, t in lines)
    LAST.update(at=time.time(), report=report)
    core.log("full check:", head)
    return {"report": report, "counts": counts, "lines": [{"state": s.strip().lower(), "text": t} for s, t in lines]}


def init(umculo):
    global core
    core = umculo
