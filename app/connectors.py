"""Settings > Connectors: every service Umculo talks to, in one place.

Each connector shows whether it's working (the same checks as the Status page), what it's set to,
lets you change addresses and keys without touching the compose file, test it, and switch sources
on and off. What you save here lives in config/connectors.json and wins over the compose file.
"""
import json
import os
import threading
import time

core = None
LOCK = threading.Lock()
SAVED = {}                                  # key -> value (overrides the compose file)
TESTS = {}                                  # connector id -> last test {"state","detail","ms","at"}
IMPORT = {"state": "idle", "message": "", "at": 0}


def stores():
    return {"core": core.CONFIG, "foryou": core.foryou.CFG, "library": core.library.CFG, "links": core.links.CFG,
            "share": core.share.CFG, "subsonic": core.subsonic.CFG}


# key, label, store, kind (text|url|secret|int|choice|file), extra
def F(key, label, store="core", kind="text", **kw):
    return {"key": key, "label": label, "store": store, "kind": kind, **kw}


CONNECTORS = [
    {"id": "server", "name": "Music server", "group": "Listening", "check": "navidrome", "icon": "server",
     "what": "What your music apps (Arpeggi and others) connect to. Umculo can be the server itself, or pass through to Navidrome.",
     "fields": [F("music_server", "Server", kind="choice", options=[["builtin", "Umculo (built in)"], ["navidrome", "Navidrome"]]),
                F("public_url", "Address for your apps", kind="url", help="The address you type into Arpeggi, e.g. https://home.example.ts.net"),
                F("scan_minutes", "Look for new files every (minutes)", kind="int", show_if=["music_server", "builtin"])],
     "actions": [["selftest", "Run a full check", ["music_server", "builtin"]], ["scan", "Rescan everything", ["music_server", "builtin"]], ["import", "Bring over stars, plays and playlists from Navidrome", ["music_server", "builtin"]]]},
    {"id": "navidrome", "name": "Navidrome", "group": "Listening", "check": "navidrome_raw", "icon": "server",
     "what": "Only needed while Navidrome is your music server, or to bring your stars and play counts over once.",
     "fields": [F("navidrome", "Address", kind="url"), F("nd_user", "Admin username"), F("nd_password", "Admin password", kind="secret"),
                F("navidrome_public", "Address for your apps", kind="url")]},
    {"id": "soulseek", "name": "Soulseek (slskd)", "group": "Sources", "check": "soulseek", "source": "soulseek", "icon": "share",
     "what": "Peer-to-peer: the best place for lossless and rare music.",
     "fields": [F("slskd", "slskd address", kind="url"), F("slskd_key", "API key", kind="secret"),
                F("slskd_downloads", "slskd downloads folder (as Umculo sees it)")]},
    {"id": "prowlarr", "name": "Usenet search (Prowlarr)", "group": "Sources", "check": "prowlarr", "source": "usenet", "icon": "search",
     "what": "Searches your Usenet indexers. Needs SABnzbd to download.",
     "fields": [F("prowlarr", "Prowlarr address", kind="url"), F("prowlarr_key", "API key", kind="secret")]},
    {"id": "sabnzbd", "name": "Usenet downloads (SABnzbd)", "group": "Sources", "check": "sabnzbd", "source": "usenet", "icon": "download",
     "what": "Downloads what Prowlarr finds.",
     "fields": [F("sab", "SABnzbd address", kind="url"), F("sab_key", "API key", kind="secret"), F("sab_category", "Category"),
                F("sab_complete", "Completed folder (as Umculo sees it)"), F("usenet_max_mb", "Skip releases bigger than (MB)", kind="int")]},
    {"id": "youtube", "name": "YouTube", "group": "Sources", "check": "youtube", "source": "youtube", "icon": "play",
     "what": "Last resort for songs nobody shares, and for mixes. Uses yt-dlp.",
     "fields": [F("cookies", "Cookies file (helps when YouTube asks if you're a bot)", kind="file",
                  help="Paste the contents of a cookies.txt exported from a browser signed in to YouTube")]},
    {"id": "soundcloud", "name": "SoundCloud", "group": "Sources", "check": "youtube", "source": "soundcloud", "icon": "cloud",
     "what": "DJ mixes and songs that only live on SoundCloud.", "fields": []},
    {"id": "deezer", "name": "Song catalogue (Deezer)", "group": "Finding music", "check": "deezer", "icon": "disc",
     "what": "Names, covers, albums and charts for everything you search. No account needed.",
     "fields": [F("deezer", "API address", kind="url")]},
    {"id": "itunes", "name": "Apple Music links", "group": "Finding music", "check": None, "icon": "link",
     "what": "Reads Apple Music songs, albums and playlists you share to Umculo. No account needed.",
     "fields": [F("itunes", "API address", store="share", kind="url")]},
    {"id": "spotify", "name": "Spotify links", "group": "Finding music", "check": None, "icon": "link",
     "what": "Reads public Spotify playlists you import or follow, from Spotify's public page. No account needed.",
     "fields": [F("spotify_dir", "Spotify Playlists folder")]},
    {"id": "shazam", "name": "Shazam recognition", "group": "Finding music", "check": "shazam", "icon": "mic",
     "what": "The mic button, and finding the songs inside DJ mixes.",
     "fields": [F("auto_find", "Find songs in new mixes automatically", store="links", kind="bool")]},
    {"id": "lrclib", "name": "Lyrics (LRCLIB)", "group": "Extras", "check": "lrclib", "icon": "text",
     "what": "Adds synced lyrics to new songs.",
     "fields": [F("lyrics_auto", "Add lyrics to new songs", store="library", kind="bool"), F("lrclib", "API address", store="library", kind="url")]},
    {"id": "listenbrainz", "name": "ListenBrainz", "group": "Extras", "check": "listenbrainz", "icon": "chart",
     "what": "Your listening history feeds For You; with a token, plays from your apps are sent there too.",
     "fields": [F("lb_user", "Username", store="foryou"), F("lb_token", "User token (to send your plays)", store="foryou", kind="secret")]},
    {"id": "ntfy", "name": "Phone notifications (ntfy)", "group": "Extras", "check": "ntfy", "icon": "bell",
     "what": "A push to your phone when downloads finish or need you.",
     "fields": [F("ntfy", "Topic address", kind="secret", help="e.g. https://ntfy.sh/your-private-topic")]},
]
FOLDERS = [
    ("f_music", "Your music", "music", "Everything Umculo plays, scans and serves to your apps. Point this at your existing collection: nothing is moved or renamed.", False),
    ("f_library", "New downloads", "library", "Where Umculo files songs it fetches (one folder per artist). Best inside your music folder.", True),
    ("f_playlists", "Playlists", "playlists", "Umculo's playlist files (.m3u8). Playlist files anywhere in your music folder are picked up too.", True),
    ("f_spotify", "Imported playlists", "spotify_dir", "Playlists you import from Spotify, Apple Music or a list of songs.", True),
    ("f_mixes", "Mixes and podcasts", "mixes", "DJ mixes and episodes from SoundCloud, YouTube, Mixcloud and podcasts.", True),
    ("f_family", "Family folders", "family", "Each person's own music when your household keeps separate collections.", True),
    ("f_staging", "Work space", "staging", "Temporary files while songs download and convert. Any fast disk with room.", True),
]
for fid, name, key, what, write in FOLDERS:
    CONNECTORS.append({"id": fid, "name": name, "group": "Folders", "check": "folder", "icon": "folder", "folder": key, "write": write,
                       "what": what, "fields": [F(key, "Folder (as Umculo sees it)", kind="folder")]})
BY_ID = {c["id"]: c for c in CONNECTORS}


def folder_check(c):
    p = core.CONFIG.get(c["folder"]) or ""
    if not p:
        return "warn", "Not set"
    if not os.path.isdir(p):
        return "down", f"{p} doesn't exist (as Umculo sees it): check the volumes in your compose file"
    if c["write"] and not os.access(p, os.W_OK):
        return "down", f"{p} is read-only for Umculo"
    if c["folder"] == "music":
        db = core.musicdb
        n = db.q("SELECT COUNT(*) n FROM song", one=True)["n"] if db.DB is not None and core.builtin() else count_audio(p, 50000)
        return "ok", f"{p} · {n:,} songs"
    if c["folder"] in ("library", "playlists", "spotify_dir", "mixes") and not core.within(p, core.CONFIG["music"]):
        return "warn", f"{p} is outside your music folder, so your apps won't see what's in it"
    return "ok", p


def count_audio(p, cap):
    n = 0
    for d, dirs, fs in os.walk(p):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        n += sum(1 for f in fs if f.rsplit(".", 1)[-1].lower() in core.musicdb.AUDIO)
        if n >= cap:
            break
    return n


def browse(path):
    """Folders Umculo can see, to pick one in the app."""
    path = os.path.normpath(path or "/data")
    if not os.path.isdir(path):
        path = "/data" if os.path.isdir("/data") else "/"
    out = []
    try:
        for e in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if e.name.startswith(".") or not e.is_dir():
                continue
            out.append({"name": e.name, "path": e.path})
    except OSError as e:
        return {"path": path, "parent": os.path.dirname(path), "dirs": [], "error": str(e)}
    files = 0
    try:
        files = sum(1 for e in os.scandir(path) if e.is_file() and e.name.rsplit(".", 1)[-1].lower() in core.musicdb.AUDIO)
    except OSError:
        pass
    return {"path": path, "parent": os.path.dirname(path) if path != "/" else "", "dirs": out[:500], "songs_here": files}
COOKIES = "youtube-cookies.txt"


def path():
    return os.path.join(core.CONFIG["config"], "connectors.json")


def load():
    try:
        with open(path()) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = {}
    SAVED.update(data.get("values") or {})
    fields = {f["key"]: f for c in CONNECTORS for f in c["fields"]}
    for k, v in SAVED.items():
        f = fields.get(k)
        if f and f["kind"] != "file":
            put(f, v)


def save():
    with LOCK:
        data = {"values": dict(SAVED)}
    tmp = path() + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path())


def get(f):
    if f["kind"] == "file":
        return os.path.isfile(os.path.join(core.CONFIG["config"], COOKIES))
    return stores()[f["store"]].get(f["key"])


def put(f, v):
    st = stores()[f["store"]]
    if f["kind"] == "folder":
        v = os.path.normpath(str(v or "").strip()) if str(v or "").strip() else ""
        if not v.startswith("/"):
            raise ValueError("Use a full path, like /data/Music")
        if not os.path.isdir(v):
            raise ValueError(f"{v} doesn't exist inside Umculo. Add it to Umculo's volumes in your compose file first")
    if f["kind"] == "int":
        v = max(0, int(v))
    elif f["kind"] == "bool":
        v = v in (True, 1, "1", "true", "on", "yes")
    elif f["kind"] == "choice":
        if v not in [o[0] for o in f["options"]]:
            raise ValueError(f"{f['label']}: pick one of the choices")
    else:
        v = str(v or "").strip()
        if f["kind"] == "url":
            v = v.rstrip("/")
            if v and not v.startswith(("http://", "https://")):
                raise ValueError(f"{f['label']} should start with http:// or https://")
    st[f["key"]] = v


def source_on(c):
    s = c.get("source")
    if not s:
        return None
    return s in core.CONFIG["sources"] or s in core.CONFIG["batch_sources"]


def view(c):
    fields = []
    for f in c["fields"]:
        v = get(f)
        d = {k: f[k] for k in ("key", "label", "kind") if k in f}
        for k in ("options", "help", "show_if"):
            if k in f:
                d[k] = f[k]
        if f["kind"] in ("secret", "file"):
            d["set"] = bool(v)
            d["value"] = ""
            if f["kind"] == "secret" and v and f["key"] == "ntfy":
                d["hint"] = v.split("//")[-1].split("/")[0] + "/…"          # which server, not the topic
        else:
            d["value"] = v
        d["saved"] = f["key"] in SAVED
        fields.append(d)
    t = TESTS.get(c["id"]) or status_of(c)
    return {"id": c["id"], "name": c["name"], "group": c["group"], "what": c["what"], "icon": c.get("icon", ""),
            "on": source_on(c), "fields": fields, "actions": c.get("actions", []), "state": t.get("state", "unknown"),
            "detail": t.get("detail", ""), "checked": t.get("at", 0), "testable": bool(c.get("check")),
            "used": in_use(c)}


def in_use(c):
    if c["id"] == "navidrome":
        return not core.builtin() or IMPORT["state"] == "running"
    return True


def status_of(c):
    if c.get("check") == "folder":
        st, detail = folder_check(c)
        return {"state": st, "detail": detail, "at": time.time()}
    if not c.get("check"):
        return {"state": "ok", "detail": "No setup needed"}
    for chk in (core.status.LAST.get("checks") or []):
        if chk["id"] == c["check"]:
            return {"state": chk["state"], "detail": chk["detail"], "at": core.status.LAST.get("at", 0)}
    return {}


def test(cid):
    c = BY_ID.get(cid)
    if not c:
        raise LookupError("No such connector")
    if c.get("check") == "folder":
        st, detail = folder_check(c)
        r = {"state": st, "detail": detail, "ms": 0}
    elif not c.get("check"):
        r = {"state": "ok", "detail": "No setup needed", "ms": 0}
    else:
        fn = core.status.CHECK_FNS.get(c["check"])
        state, detail, ms = core.status.timed(fn)
        r = {"state": state, "detail": detail, "ms": ms}
    r["at"] = time.time()
    TESTS[cid] = r
    return r


def set_source(name, on):
    if not on and any([x for x in core.CONFIG[k] if x != name] == [] for k in ("sources", "batch_sources")):
        raise ValueError("Keep at least one source on")
    for key in ("sources", "batch_sources"):
        cur = list(core.CONFIG[key])
        if on and name not in cur:
            cur.append(name)
        if not on and name in cur and len(cur) > 1:
            cur.remove(name)
        core.apply_settings({key: cur})


def update(cid, values, on=None):
    c = BY_ID.get(cid)
    if not c:
        raise LookupError("No such connector")
    fields = {f["key"]: f for f in c["fields"]}
    was_builtin = core.builtin()
    old_music = core.CONFIG["music"]
    for k, v in (values or {}).items():
        f = fields.get(k)
        if not f:
            continue
        if f["kind"] == "file":
            p = os.path.join(core.CONFIG["config"], COOKIES)
            if v == "__clear__":
                if os.path.exists(p):
                    os.remove(p)
            elif str(v).strip():
                if "\t" not in v and "# Netscape" not in v:
                    raise ValueError("That doesn't look like a cookies.txt file (Netscape format)")
                with open(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                    fh.write(v.strip() + "\n")
            continue
        if f["kind"] == "secret" and v in ("", None):
            continue                                    # left blank = keep what's there
        if f["kind"] == "secret" and v == "__clear__":
            v = ""
        put(f, v)
        with LOCK:
            SAVED[k] = stores()[f["store"]][k]
    if on is not None and c.get("source"):
        set_source(c["source"], bool(on))
    save()
    TESTS.pop(cid, None)
    if core.builtin() and not was_builtin:
        core.log("music server: Umculo now serves your apps itself")
        core.musicdb.request_scan()
        if core.CONFIG["nd_user"] and not core.musicdb.q("SELECT v FROM kv WHERE k='imported'", one=True):
            start_import()
    elif was_builtin and not core.builtin():
        core.log("music server: passing your apps through to Navidrome again")
    if core.CONFIG["music"] != old_music:              # a different collection: read it from scratch
        core.log(f"music folder: {old_music} -> {core.CONFIG['music']}")
        core.musicdb.move_main(core.CONFIG["music"])
    for k in ("library", "playlists", "spotify_dir", "mixes", "staging", "family"):
        if c.get("folder") == k:
            os.makedirs(core.CONFIG[k], exist_ok=True)
    core.subsonic.AUTH.clear()
    threading.Thread(target=core.status.run, daemon=True).start()
    return view(c)


def start_import():
    if IMPORT["state"] == "running":
        return IMPORT
    IMPORT.update(state="running", message="Waiting for the first library scan", at=time.time())
    threading.Thread(target=run_import, daemon=True).start()
    return IMPORT


def run_import():
    try:
        while core.musicdb.SCAN["running"] or not core.musicdb.SCAN["last"]:
            time.sleep(3)
        IMPORT["message"] = "Reading Navidrome"
        res = core.server.import_navidrome(lambda m: IMPORT.update(message=m))
        core.musicdb.x("INSERT OR REPLACE INTO kv (k, v) VALUES ('imported', ?)", (json.dumps(res),))
        IMPORT.update(state="done", message=res["summary"], at=time.time())
        core.log("from Navidrome:", res["summary"])
    except Exception as e:  # noqa: BLE001
        IMPORT.update(state="failed", message=str(e)[:200], at=time.time())
        core.log("Navidrome import failed:", e)


def action(cid, what):
    if what == "scan":
        if not core.builtin():
            raise ValueError("Switch the music server to Umculo first")
        threading.Thread(target=lambda: (core.musicdb.scan(True), core.musicdb.relink_playlists()), daemon=True).start()
        return {"ok": True, "message": "Rescanning every file"}
    if what == "selftest":
        if not core.builtin():
            raise ValueError("Switch the music server to Umculo first")
        return core.selftest.run()
    if what == "import":
        if not core.CONFIG["nd_user"]:
            raise ValueError("Fill in the Navidrome connector first")
        return start_import()
    raise ValueError("Unknown action")


def server_info():
    db = core.musicdb
    if not core.builtin() or db.DB is None:
        return None
    n = db.q("SELECT (SELECT COUNT(*) FROM song) s, (SELECT COUNT(*) FROM album) a, (SELECT COUNT(*) FROM artist WHERE albums > 0) r", one=True)
    return {"songs": n["s"], "albums": n["a"], "artists": n["r"], "scan": {k: v for k, v in db.SCAN.items() if k != "requested"},
            "import": IMPORT}


# --------------------------------------------------------------------------- web
def handle_get(p, qs):
    if p == "/api/folders":
        return browse((qs.get("path") or [""])[0])
    if p == "/api/migrate":
        return core.migrate.overview()
    if p == "/api/connectors":
        if not core.status.LAST.get("at") and not core.status.RUNNING.locked():
            threading.Thread(target=core.status.run, daemon=True).start()
        groups = []
        for c in CONNECTORS:
            if c["group"] not in groups:
                groups.append(c["group"])
        return {"connectors": [view(c) for c in CONNECTORS], "groups": groups, "server": server_info(),
                "import": IMPORT, "checking": core.status.RUNNING.locked() or not core.status.LAST.get("at")}
    return None


def handle_post(p, b):
    if p == "/api/connectors/save":
        return update(b.get("id"), b.get("values") or {}, b.get("on"))
    if p.startswith("/api/migrate/"):
        return core.migrate.handle_post(p, b)
    if p == "/api/connectors/test":
        return test(b.get("id"))
    if p == "/api/connectors/action":
        return action(b.get("id"), b.get("action"))
    return None


def init(umculo):
    global core
    core = umculo
    core.foryou.CFG.setdefault("lb_token", os.environ.get("LISTENBRAINZ_TOKEN", ""))
    load()
