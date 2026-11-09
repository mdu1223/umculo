"""Umculo's own music library: a SQLite database of every song, album and artist, kept in step
with the folders by a quick incremental scanner (only changed files are read again).

Libraries:
  1   the main music folder (CONFIG["music"])
  n   one per person in a "separate" household (CONFIG["family"]/<username>)
IDs are stable (hashes of where things are), so apps keep their stars and queues across rescans.
"""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import traceback
import unicodedata

core = None
DB = None
LOCK = threading.RLock()
SCHEMA_VERSION = 3                     # bump when the scanner learns to read something new: everything is read again once
FULL_NEXT = [False]
HOST_MAP = [(h.split("=")[0], h.split("=")[1]) for h in os.environ.get("HOST_PATHS", "/mnt/Storage/Media=/data").split(",") if "=" in h]
SCAN = {"running": False, "count": 0, "last": 0, "took": 0, "added": 0, "changed": 0, "removed": 0, "error": ""}
AUDIO = ("mp3", "flac", "m4a", "aac", "ogg", "opus", "wav", "aiff", "aif", "wma", "alac", "ape", "wv", "dsf", "mp4")
SKIP_DIRS = {".", "_playlists", "@eaDir", ".metube", "#recycle", "lost+found"}
COVERS = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png", "album.jpg", "albumart.jpg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS library (id INTEGER PRIMARY KEY, name TEXT, path TEXT UNIQUE, owner TEXT);
CREATE TABLE IF NOT EXISTS song (
  id TEXT PRIMARY KEY, lib INTEGER, path TEXT UNIQUE, rel TEXT, dir TEXT, title TEXT, artist TEXT, album TEXT,
  albumartist TEXT, track INTEGER, disc INTEGER, year INTEGER, genre TEXT, duration REAL, bitrate INTEGER,
  samplerate INTEGER, bitdepth INTEGER, channels INTEGER, suffix TEXT, size INTEGER, mtime REAL, created REAL,
  album_id TEXT, artist_id TEXT, albumartist_id TEXT, art INTEGER, search TEXT, mbid TEXT, lyrics INTEGER);
CREATE INDEX IF NOT EXISTS song_album ON song(album_id);
CREATE INDEX IF NOT EXISTS song_artist ON song(artist_id);
CREATE INDEX IF NOT EXISTS song_aartist ON song(albumartist_id);
CREATE INDEX IF NOT EXISTS song_lib ON song(lib);
CREATE TABLE IF NOT EXISTS album (
  id TEXT PRIMARY KEY, lib INTEGER, name TEXT, artist TEXT, artist_id TEXT, year INTEGER, genre TEXT,
  songs INTEGER, duration REAL, size INTEGER, created REAL, cover TEXT, search TEXT, compilation INTEGER);
CREATE INDEX IF NOT EXISTS album_artist ON album(artist_id);
CREATE TABLE IF NOT EXISTS artist (id TEXT PRIMARY KEY, lib INTEGER, name TEXT, sort TEXT, albums INTEGER,
  songs INTEGER, search TEXT);
CREATE TABLE IF NOT EXISTS ann (user TEXT, item TEXT, kind TEXT, starred REAL, rating INTEGER, plays INTEGER,
  played REAL, PRIMARY KEY (user, item));
CREATE TABLE IF NOT EXISTS playlist (id TEXT PRIMARY KEY, name TEXT, owner TEXT, public INTEGER, comment TEXT,
  created REAL, changed REAL, path TEXT, mtime REAL);
CREATE TABLE IF NOT EXISTS plsong (pid TEXT, pos INTEGER, song TEXT, path TEXT);
CREATE INDEX IF NOT EXISTS plsong_pid ON plsong(pid);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS listen (user TEXT, song TEXT, title TEXT, artist TEXT, album TEXT, genre TEXT, secs INTEGER, at REAL,
  source TEXT, artist_id TEXT, album_id TEXT);
CREATE INDEX IF NOT EXISTS listen_user_at ON listen(user, at);
"""


def h(*parts):
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:16]


def fold(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def primary(a):
    """"Kabza De Small, Focalistic feat. X" -> "Kabza De Small"."""
    return re.split(r",| feat\.? | ft\.? | & | x ", a or "", flags=re.I)[0].strip() or (a or "")


def natural(s):
    """Sort key: "Track 2" before "Track 10", "The Weeknd" with the W's, accents ignored."""
    s = re.sub(r"^(the|a|an)\s+", "", fold(s or ""))
    return [(0, int(t), "") if t.isdigit() else (1, 0, t) for t in re.findall(r"\d+|\D+", s)]


def sort_name(n):
    n = (n or "").strip()
    return re.sub(r"^(the|a|an)\s+", "", n, flags=re.I).lower() or n.lower()


def q(sql, args=(), one=False):
    with LOCK:
        cur = DB.execute(sql, args)
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


def x(sql, args=()):
    with LOCK:
        DB.execute(sql, args)
        DB.commit()


# --------------------------------------------------------------------------- libraries
def ensure_libraries():
    """Main folder plus one per person (separate households)."""
    want = [(1, "Music", core.CONFIG["music"], "")]
    fam = core.CONFIG["family"]
    users = getattr(core, "users", None)
    if users and users.HOUSE.get("mode") == "separate":
        for u in list(users.USERS.values()):
            if u.get("role") != "admin":
                want.append((None, f"{u['name']}'s music", os.path.join(fam, u["username"]), u["id"]))
    with LOCK:
        for lid, name, path, owner in want:
            row = DB.execute("SELECT id FROM library WHERE path=?", (path,)).fetchone()
            if not row:
                if lid:
                    DB.execute("INSERT OR REPLACE INTO library (id, name, path, owner) VALUES (?,?,?,?)", (lid, name, path, owner))
                else:
                    DB.execute("INSERT INTO library (name, path, owner) VALUES (?,?,?)", (name, path, owner))
        DB.commit()
    return [dict(r) for r in q("SELECT * FROM library ORDER BY id")]


def libraries_for(u):
    """Library ids a person may see."""
    libs = [dict(r) for r in q("SELECT * FROM library ORDER BY id")]
    users = core.users
    if not u or users.is_admin(u):
        return [l["id"] for l in libs if not l["owner"]]
    ids = []
    sep = users.HOUSE.get("mode") == "separate"
    if not sep or u.get("share_all"):
        ids.append(1)
    ids += [l["id"] for l in libs if l["owner"] == u["id"]]
    return ids


# --------------------------------------------------------------------------- reading tags
ID3_KEYS = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "tracknumber": "TRCK",
            "discnumber": "TPOS", "date": "TDRC", "originaldate": "TDOR", "genre": "TCON", "bpm": "TBPM",
            "titlesort": "TSOT", "albumsort": "TSOA", "artistsort": "TSOP", "albumartistsort": "TSO2",
            "organization": "TPUB", "discsubtitle": "TSST"}


def id3_tags(t):
    out = {}
    for k, frame in ID3_KEYS.items():
        fr = t.getall(frame)
        if fr and fr[0].text:
            out[k] = [str(x) for x in fr[0].text]
    for fr in t.getall("TXXX"):
        d = (fr.desc or "").lower()
        if d.startswith(("replaygain_", "musicbrainz")) and fr.text:
            out[d.replace("musicbrainz ", "musicbrainz_").replace(" ", "")] = [str(fr.text[0])]
    if t.getall("UFID"):
        out["musicbrainz_trackid"] = [t.getall("UFID")[0].data.decode(errors="ignore")]
    return out


def first(tags, *keys):
    for k in keys:
        v = tags.get(k)
        if v:
            return str(v[0] if isinstance(v, list) else v).strip()
    return ""


def num(s):
    m = re.match(r"\s*(\d+)", str(s or ""))
    return int(m.group(1)) if m else 0


def read_file(path, root):
    import mutagen
    rel = os.path.relpath(path, root)
    suffix = path.rsplit(".", 1)[-1].lower()
    st = os.stat(path)
    tags, info, art, lyr = {}, None, 0, 0
    try:
        f = mutagen.File(path, easy=True)
        if f is not None:
            info = f.info
            tags = dict(f.tags or {})
    except Exception:  # noqa: BLE001
        pass
    try:
        raw = mutagen.File(path)
        if raw is not None and info is None:
            info = raw.info
        if raw is not None and raw.tags is not None and not tags and hasattr(raw.tags, "getall"):
            tags = id3_tags(raw.tags)              # AIFF / WAV / DSF: ID3 inside, which "easy" mode can't read
        if raw is not None and raw.tags is not None:
            keys = [str(k) for k in raw.tags.keys()] if hasattr(raw.tags, "keys") else []
            art = int(any(k.startswith("APIC") or k in ("covr", "METADATA_BLOCK_PICTURE") for k in keys) or
                      bool(getattr(raw, "pictures", None)))
            lyr = int(any(k.startswith("USLT") or k.lower() in ("lyrics", "unsyncedlyrics", "\xa9lyr") for k in keys))
    except Exception:  # noqa: BLE001
        pass
    stem = os.path.splitext(os.path.basename(path))[0]
    guess_artist, guess_title = (stem.split(" - ", 1) if " - " in stem else ("", stem))
    guess_title = re.sub(r"^\d+[\s.\-_]+", "", guess_title)
    title = first(tags, "title") or guess_title
    artist = first(tags, "artist") or guess_artist or "Unknown artist"
    album = first(tags, "album") or os.path.basename(os.path.dirname(path)) or "Unknown album"
    aartist = first(tags, "albumartist", "album artist") or primary(artist)
    if not first(tags, "album") and fold(album) in (fold(aartist), fold(primary(artist)), fold(artist)):
        album = title                              # a single in the artist's folder is its own release, not "the artist" album
    m = re.match(r"^(.+?)\s+[-–]\s+(.+)$", album)
    if m and fold(m.group(1)) in (fold(aartist), fold(artist), fold(primary(artist))):
        album = m.group(2).strip()                 # "Kwesta - DaKAR" is the album DaKAR
    year = num(first(tags, "date", "originaldate", "year"))
    return {"rel": rel, "dir": os.path.dirname(rel), "title": title, "artist": artist, "album": album,
            "albumartist": aartist, "track": num(first(tags, "tracknumber")), "disc": num(first(tags, "discnumber")) or 1,
            "year": year, "genre": first(tags, "genre"), "duration": round(float(getattr(info, "length", 0) or 0)),
            "bitrate": int((getattr(info, "bitrate", 0) or 0) / 1000) or (int(st.st_size * 8 / 1000 / max(getattr(info, "length", 0) or 1, 1))),
            "samplerate": int(getattr(info, "sample_rate", 0) or 0), "bitdepth": int(getattr(info, "bits_per_sample", 0) or 0),
            "channels": int(getattr(info, "channels", 0) or 0), "suffix": suffix, "size": st.st_size, "mtime": st.st_mtime,
            "art": art, "lyrics": lyr or int(os.path.exists(os.path.splitext(path)[0] + ".lrc")),
            "mbid": first(tags, "musicbrainz_trackid"), "extra": json.dumps(extras(tags), separators=(",", ":"))}


def extras(tags):
    """ReplayGain, BPM, sort names, MusicBrainz ids: what good apps use when they're there."""
    out = {}
    for k, key in (("trackGain", "replaygain_track_gain"), ("albumGain", "replaygain_album_gain"),
                   ("trackPeak", "replaygain_track_peak"), ("albumPeak", "replaygain_album_peak")):
        v = first(tags, key)
        if v:
            try:
                out.setdefault("rg", {})[k] = float(re.sub(r"[^\d.\-]", "", v))
            except ValueError:
                pass
    for k, key in (("bpm", "bpm"), ("sort", "titlesort"), ("albumsort", "albumsort"), ("artistsort", "artistsort"),
                   ("aasort", "albumartistsort"), ("mbalbum", "musicbrainz_albumid"), ("mbartist", "musicbrainz_artistid"),
                   ("mbaa", "musicbrainz_albumartistid"), ("comment", "comment"), ("disctitle", "discsubtitle"),
                   ("label", "organization")):
        v = first(tags, key)
        if v:
            out[k] = num(v) if k == "bpm" else v[:300]
    return out


# --------------------------------------------------------------------------- scanning
def host_path(target):
    """A folder linked with the server's own path (/mnt/Storage/Media/...) is /data/... inside Umculo."""
    for host, inside in HOST_MAP:
        if target == host or target.startswith(host.rstrip("/") + "/"):
            return inside + target[len(host.rstrip("/")):]
    return target


def walk(root):
    """Every song and playlist under root, following linked folders (like Navidrome does), never twice."""
    seen = set()
    stack = [(root, root)]
    while stack:
        shown, real = stack.pop()
        try:
            key = os.path.realpath(real)
            if key in seen:
                continue
            seen.add(key)
            entries = list(os.scandir(real))
        except OSError:
            continue
        for e in entries:
            name = e.name
            if name.startswith(".") or name in SKIP_DIRS:
                continue
            path = os.path.join(shown, name)
            try:
                if e.is_dir():                         # follows links that work
                    stack.append((path, e.path))
                    continue
                if e.is_symlink() and not os.path.exists(e.path):
                    target = host_path(os.readlink(e.path))
                    if os.path.isdir(target):
                        stack.append((path, target))
                    continue
            except OSError:
                continue
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if ext in AUDIO:
                yield os.path.join(real, name) if real != shown and not os.path.exists(path) else path, "audio"
            elif ext in ("m3u", "m3u8"):
                yield path, "playlist"


def scan(full=False):
    if SCAN["running"]:
        return False
    SCAN.update(running=True, count=0, added=0, changed=0, removed=0, error="")
    t0 = time.time()
    try:
        libs = ensure_libraries()
        seen, pl_files = set(), []
        known = {r["path"]: (r["mtime"], r["size"]) for r in q("SELECT path, mtime, size FROM song")}
        batch = []
        for lib in libs:
            root = lib["path"]
            if not os.path.isdir(root):
                continue
            for path, kind in walk(root):
                if kind == "playlist":
                    pl_files.append((path, lib))
                    continue
                seen.add(path)
                SCAN["count"] += 1
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                old = known.get(path)
                if old and not full and abs(old[0] - st.st_mtime) < 1 and old[1] == st.st_size:
                    continue
                try:
                    t = read_file(path, root)
                except Exception as e:  # noqa: BLE001
                    core.log("scan: couldn't read", path, e)
                    continue
                batch.append((path, lib, t, bool(old)))
                if len(batch) >= 200:
                    write_songs(batch)
                    batch = []
        write_songs(batch)
        gone = [p for p in known if p not in seen]
        for i in range(0, len(gone), 500):
            chunk = gone[i:i + 500]
            x(f"DELETE FROM song WHERE path IN ({','.join('?' * len(chunk))})", chunk)
        SCAN["removed"] = len(gone)
        rebuild()
        sync_playlists(pl_files)
        SCAN.update(last=time.time(), took=round(time.time() - t0, 1))
        if SCAN["added"] or SCAN["changed"] or SCAN["removed"]:
            core.log(f"library scan: {SCAN['count']} songs, {SCAN['added']} new, {SCAN['changed']} changed, "
                     f"{SCAN['removed']} gone ({SCAN['took']} s)")
    except Exception as e:  # noqa: BLE001
        SCAN["error"] = str(e)[:200]
        core.log("library scan failed:", traceback.format_exc())
    finally:
        SCAN["running"] = False
    return True


def write_songs(batch):
    if not batch:
        return
    now = time.time()
    with LOCK:
        for path, lib, t, existed in batch:
            sid = h("song", lib["id"], t["rel"])
            alb = h("album", lib["id"], fold(t["albumartist"]), fold(t["album"]))
            art = h("artist", lib["id"], fold(primary(t["artist"])))
            aart = h("artist", lib["id"], fold(t["albumartist"]))
            created = (DB.execute("SELECT created FROM song WHERE id=?", (sid,)).fetchone() or [None])[0] or \
                (os.path.getmtime(path) if not existed else now)
            DB.execute("""INSERT OR REPLACE INTO song (id, lib, path, rel, dir, title, artist, album, albumartist, track,
                disc, year, genre, duration, bitrate, samplerate, bitdepth, channels, suffix, size, mtime, created,
                album_id, artist_id, albumartist_id, art, search, mbid, lyrics, extra) VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                       (sid, lib["id"], path, t["rel"], t["dir"], t["title"], t["artist"], t["album"], t["albumartist"],
                        t["track"], t["disc"], t["year"], t["genre"], t["duration"], t["bitrate"], t["samplerate"],
                        t["bitdepth"], t["channels"], t["suffix"], t["size"], t["mtime"], created, alb, art, aart,
                        t["art"], fold(f"{t['title']} {t['artist']} {t['album']} {t['albumartist']}"), t["mbid"], t["lyrics"], t["extra"]))
            SCAN["changed" if existed else "added"] += 1
        DB.commit()


def rebuild():
    """Albums and artists from the songs (fast, all in SQL)."""
    with LOCK:
        merge_split_albums()
        DB.execute("DELETE FROM album")
        DB.execute("""INSERT INTO album (id, lib, name, artist, artist_id, year, genre, songs, duration, size, created,
                      cover, search, compilation)
            SELECT album_id, lib, MIN(album), MIN(albumartist), MIN(albumartist_id), MAX(year), MAX(genre), COUNT(*),
                   SUM(duration), SUM(size), MIN(created), MIN(path), '', COUNT(DISTINCT artist) > 2
            FROM song GROUP BY album_id""")
        rows = DB.execute("SELECT id, name, artist FROM album").fetchall()
        DB.executemany("UPDATE album SET search=? WHERE id=?", [(fold(r["name"] + " " + r["artist"]), r["id"]) for r in rows])
        DB.execute("DELETE FROM artist")
        DB.execute("""INSERT INTO artist (id, lib, name, sort, albums, songs, search)
            SELECT albumartist_id, lib, MIN(albumartist), '', COUNT(DISTINCT album_id), COUNT(*), ''
            FROM song GROUP BY albumartist_id""")
        seen = {r["id"] for r in DB.execute("SELECT id FROM artist").fetchall()}
        extra = {}
        for r in DB.execute("SELECT artist_id, artist, lib, COUNT(*) n FROM song GROUP BY artist_id").fetchall():
            if r["artist_id"] not in seen:            # featured / track-only artists get a page too
                name = primary(r["artist"])
                extra[r["artist_id"]] = (r["artist_id"], r["lib"], name, "", 0, r["n"], "")
        DB.executemany("INSERT OR IGNORE INTO artist (id, lib, name, sort, albums, songs, search) VALUES (?,?,?,?,?,?,?)",
                       list(extra.values()))
        rows = DB.execute("SELECT id, name FROM artist").fetchall()
        DB.executemany("UPDATE artist SET sort=?, search=? WHERE id=?", [(sort_name(r["name"]), fold(r["name"]), r["id"]) for r in rows])
        DB.commit()


def merge_split_albums():
    """One album in one folder whose songs name different album artists (features, compilations) stays one album:
    under the artist on most of it, or Various Artists."""
    groups = DB.execute("""SELECT lib, dir, album, COUNT(DISTINCT albumartist_id) n FROM song WHERE album != ''
                           GROUP BY lib, dir, lower(album) HAVING n > 1""").fetchall()
    for g in groups:
        rows = DB.execute("SELECT id, albumartist FROM song WHERE lib=? AND dir=? AND lower(album)=lower(?)",
                          (g["lib"], g["dir"], g["album"])).fetchall()
        count = {}
        for r in rows:
            count[r["albumartist"]] = count.get(r["albumartist"], 0) + 1
        top, n = max(count.items(), key=lambda kv: kv[1])
        name = top if n * 2 > len(rows) else "Various Artists"
        alb = h("album", g["lib"], fold(name), fold(g["album"]))
        aart = h("artist", g["lib"], fold(name))
        DB.executemany("UPDATE song SET albumartist=?, albumartist_id=?, album_id=? WHERE id=?",
                       [(name, aart, alb, r["id"]) for r in rows])
    # Across folders: Umculo files each song under its own artist, so "Balloon (feat. Kwesta)" by Jimmy Nevis
    # on Kwesta's DaKAR sits in Jimmy Nevis' folder. A song joins the album of an artist it features.
    groups = DB.execute("""SELECT lib, lower(album) a, COUNT(DISTINCT albumartist_id) n FROM song WHERE album != ''
                           GROUP BY lib, lower(album) HAVING n > 1""").fetchall()
    for g in groups:
        rows = DB.execute("SELECT id, albumartist, albumartist_id, artist, title FROM song WHERE lib=? AND lower(album)=?",
                          (g["lib"], g["a"])).fetchall()
        count = {}
        for r in rows:
            count[r["albumartist"]] = count.get(r["albumartist"], 0) + 1
        moves = []
        for r in rows:
            words = " " + fold(r["artist"] + " " + r["title"]) + " "
            for other, n in sorted(count.items(), key=lambda kv: -kv[1]):
                if other != r["albumartist"] and n >= count[r["albumartist"]] and other.lower() not in ("various artists",) \
                        and len(fold(other)) > 2 and f" {fold(other)} " in words:
                    alb = DB.execute("SELECT album_id, album FROM song WHERE lib=? AND albumartist=? AND lower(album)=? LIMIT 1",
                                     (g["lib"], other, g["a"])).fetchone()
                    moves.append((other, h("artist", g["lib"], fold(other)), alb["album_id"], r["id"]))
                    break
        if moves:
            DB.executemany("UPDATE song SET albumartist=?, albumartist_id=?, album_id=? WHERE id=?", moves)


def sync_playlists(files):
    """Playlist files (yours, Spotify's, imports, each person's) become playlists of their owner."""
    paths = {p for p, _lib in files}
    try:
        paths |= set(core.playlists.files())
    except Exception:  # noqa: BLE001
        pass
    want = {}
    for path in paths:
        try:
            owner = core.playlists.owner_of(path)
        except Exception:  # noqa: BLE001
            owner = None
        want[path] = owner or "admin"
    have = {r["path"]: dict(r) for r in q("SELECT * FROM playlist WHERE path != ''")}
    for path in [p for p in have if p not in want]:
        x("DELETE FROM plsong WHERE pid=?", (have[path]["id"],))
        x("DELETE FROM playlist WHERE id=?", (have[path]["id"],))
    for path, owner in want.items():
        try:
            mt = os.path.getmtime(path)
        except OSError:
            continue
        old = have.get(path)
        if old and abs((old["mtime"] or 0) - mt) < 1 and old["owner"] == owner:
            continue
        pid = old["id"] if old else "p" + h("pl", path)
        entries = core.m3u_entries(path)
        name = os.path.splitext(os.path.basename(path))[0]
        with LOCK:
            DB.execute("""INSERT OR REPLACE INTO playlist (id, name, owner, public, comment, created, changed, path, mtime)
                          VALUES (?,?,?,?,?,?,?,?,?)""", (pid, name, owner, 0, "", (old or {}).get("created") or mt, mt, path, mt))
            DB.execute("DELETE FROM plsong WHERE pid=?", (pid,))
            for i, e in enumerate(entries):
                r = DB.execute("SELECT id FROM song WHERE path=?", (e,)).fetchone()
                DB.execute("INSERT INTO plsong (pid, pos, song, path) VALUES (?,?,?,?)", (pid, i, r["id"] if r else "", e))
            DB.commit()


def relink_playlists():
    """After a scan added songs, fill playlist entries that were waiting for their file."""
    with LOCK:
        for r in DB.execute("SELECT rowid, path FROM plsong WHERE song=''").fetchall():
            s = DB.execute("SELECT id FROM song WHERE path=?", (r["path"],)).fetchone()
            if s:
                DB.execute("UPDATE plsong SET song=? WHERE rowid=?", (s["id"], r["rowid"]))
        DB.commit()


def scan_loop():
    time.sleep(5)
    while True:
        try:
            if core.CONFIG.get("music_server") == "builtin":
                scan(FULL_NEXT[0])
                FULL_NEXT[0] = False
                relink_playlists()
                core.server.prune_cache()
        except Exception:  # noqa: BLE001
            core.log("scan loop:", traceback.format_exc())
        for _ in range(int(core.CONFIG.get("scan_minutes", 15)) * 6):
            time.sleep(10)
            if SCAN.get("requested"):
                SCAN["requested"] = False
                break


def move_main(path):
    """The main music folder moved: same library, new place, read again (ids follow the files' new paths)."""
    with LOCK:
        DB.execute("UPDATE library SET path=? WHERE id=1", (path,))
        DB.commit()
    FULL_NEXT[0] = True
    request_scan()


def request_scan():
    """Look at the folders again soon (now, if no scan is running)."""
    if core.CONFIG.get("music_server") != "builtin":
        return
    if SCAN["running"]:
        SCAN["requested"] = True
        return
    threading.Thread(target=lambda: (scan(), relink_playlists()), daemon=True).start()


def init(umculo):
    global core, DB
    core = umculo
    path = os.path.join(core.CONFIG["config"], "library.db")
    DB = sqlite3.connect(path, check_same_thread=False, timeout=30)
    DB.row_factory = sqlite3.Row
    DB.execute("PRAGMA journal_mode=WAL")
    DB.execute("PRAGMA synchronous=NORMAL")
    DB.executescript(SCHEMA)
    cols = {r["name"] for r in DB.execute("PRAGMA table_info(song)").fetchall()}
    if "extra" not in cols:
        DB.execute("ALTER TABLE song ADD COLUMN extra TEXT DEFAULT ''")
    v = DB.execute("SELECT v FROM kv WHERE k='schema'").fetchone()
    if not v or int(v["v"]) < SCHEMA_VERSION:
        FULL_NEXT[0] = True                       # read every file again once (new things to read in them)
        DB.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
    DB.commit()
    threading.Thread(target=scan_loop, daemon=True).start()
