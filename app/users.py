"""Accounts: you (the admin) and the people in your house.

Household mode, chosen when the first person is added:
  together  - one music collection; everyone sees it, downloads land in it
  separate  - everyone gets their own folder (their own Navidrome library); you choose what of
              yours they see: everything, or particular albums and playlists (shared as hard links,
              so sharing costs no disk space)
Every person also gets a matching Navidrome account with the same username and password, so the
same login works in Arpeggi and other music apps.

What a person may do (admin sets it, presets make it quick):
  download   yes | ask (you approve each request) | no
  delete     may delete music in their own space
  playlists  may import, upgrade and follow playlists
  mixes      may use links, mixes and subscriptions
Requests that need your OK wait in Queue -> "Waiting for you" and notify you.
"""
import base64
import hashlib
import ipaddress
import hmac
import json
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import urllib.parse
import uuid

core = None
LOCK = threading.Lock()
USERS = {}
HOUSE = {"mode": None}
SESSIONS = {}                     # token hash -> {"user", "at"}
APPROVALS = {}
LOCAL = threading.local()         # the person behind the current web request
OPEN = {"id": "open", "username": "you", "name": "You", "role": "admin", "perms": {}, "open": True}
PRESETS = {
    "full": {"download": "yes", "delete": True, "playlists": True, "mixes": True},
    "ask": {"download": "ask", "delete": False, "playlists": True, "mixes": True},
    "listen": {"download": "no", "delete": False, "playlists": False, "mixes": False},
}
SESSION_DAYS = 60


def log(*a):
    core.log(*a)


# --------------------------------------------------------------------------- storage
def save():
    with LOCK:
        data = {"users": dict(USERS), "house": dict(HOUSE), "approvals": dict(APPROVALS), "security": dict(core.security.SETTINGS)}
    core._save_json("users.json", data)
    try:
        os.chmod(os.path.join(core.CONFIG["config"], "users.json"), 0o600)
    except OSError:
        pass


def save_sessions():
    with LOCK:
        data = dict(SESSIONS)
    core._save_json("sessions.json", data)


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
    return f"pbkdf2${salt}${dk.hex()}"


def check_pw(pw, stored):
    try:
        _k, salt, _h = stored.split("$")
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(hash_pw(pw, salt), stored)


def builtin():
    return core.CONFIG.get("music_server") == "builtin"


def subsonic_secret(u):
    """The password music apps sign in with (token logins need it readable, like Navidrome kept it)."""
    return unkeep((u or {}).get("sub") or ((u or {}).get("nd") or {}).get("password") or "")


def remember_secret(u, pw):
    if u and u.get("id") in USERS and unkeep(u.get("sub") or "") != pw:
        u["sub"] = keep(pw)
        save()


def set_password(u, pw):
    if len(pw or "") < 6:
        raise ValueError("Use a password of at least 6 characters")
    u["pw"] = hash_pw(pw)
    u["sub"] = keep(pw)
    if u.get("role") != "admin" and not builtin():
        try:
            nd_password(u, pw)
        except Exception as e:  # noqa: BLE001
            log("Navidrome password change failed:", e)
    with LOCK:                                      # signed out everywhere
        for h in [h for h, s in SESSIONS.items() if s["user"] == u["id"]]:
            SESSIONS.pop(h, None)
    save_sessions()
    save()


def keep(secret):                 # Navidrome passwords must be readable to talk to Navidrome as the person
    return "b64:" + base64.b64encode(secret.encode()).decode()


def unkeep(v):
    return base64.b64decode(v[4:]).decode() if v and v.startswith("b64:") else (v or "")


def public(u, full=False):
    out = {k: u.get(k) for k in ("id", "username", "name", "role", "created", "disabled", "perms", "share_all",
                                 "last_seen")}
    out["is_admin"] = u.get("role") == "admin"
    out["open"] = bool(u.get("open"))
    out["two_step"] = bool(u.get("totp"))
    if full:
        out["shared_albums"] = u.get("shared_albums") or []
        out["shared_playlists"] = u.get("shared_playlists") or []
        out["folder"] = folder(u) if HOUSE["mode"] == "separate" and u.get("role") != "admin" else ""
        out["nd"] = {"ok": builtin() or bool((u.get("nd") or {}).get("id")), "error": (u.get("nd") or {}).get("error", "")}
    return out


# --------------------------------------------------------------------------- who is asking
def current():
    return getattr(LOCAL, "user", None)


def me_key(u=None):
    """Whose things these are: "admin" for you (as the music server counts it), a person's id otherwise."""
    u = u if u is not None else current()
    return "admin" if not u or is_admin(u) else u["id"]


def person(key):
    if not key or key == "admin":
        return admin_user() or OPEN
    with LOCK:
        return USERS.get(key)


def mine(obj, u=None):
    """Personal things (mixes, subscriptions, follows, suggestions) belong to whoever made them; older ones are yours."""
    return bool(obj) and (obj.get("owner") or "admin") == me_key(u)


def sees_job(obj, u=None):
    """Queues: you see everyone's, the others see their own."""
    u = u if u is not None else current()
    if not u or is_admin(u):
        return True
    return bool(obj) and obj.get("user") == u["id"]


def fetch_for(u, m, why="you asked for it"):
    """Get a song for this person as far as their download permission allows: getting, asked or off."""
    if u and not u.get("open") and not is_admin(u):
        mode = (u.get("perms") or {}).get("download", "no")
        if mode == "ask":
            body = {k: m.get(k) for k in ("artist", "title", "version", "duration", "album", "cover", "cover_big", "deezer_id")}
            ask(u, "/api/getsong", body)
            return "asked"
        if mode != "yes":
            return "off"
    core.subsonic.as_person(u, core.subsonic.auto_get, m, why)
    return "getting"


def current_id():
    u = current()
    return u["id"] if u and not u.get("open") else None


def is_admin(u=None):
    u = u or current()
    return bool(u) and (u.get("role") == "admin" or u.get("open"))


def can(perm, u=None):
    u = u or current()
    if not u:
        return False
    if is_admin(u):
        return True
    v = (u.get("perms") or {}).get(perm)
    return v == "yes" if perm == "download" else bool(v)


def admin_user():
    with LOCK:
        return next((u for u in USERS.values() if u.get("role") == "admin" and not u.get("disabled")), None)


def by_name(name):
    with LOCK:
        return next((u for u in USERS.values() if u["username"].lower() == (name or "").lower()), None)


def resolve(handler):
    """-> the user for this request (or None). Also accepts the admin login of older setups."""
    cookie = handler.headers.get("Cookie") or ""
    m = re.search(r"(?:^|;\s*)umc_s=([A-Za-z0-9_\-]+)", cookie)
    if m:
        h = hashlib.sha256(m.group(1).encode()).hexdigest()
        with LOCK:
            s = SESSIONS.get(h)
            u = USERS.get(s["user"]) if s else None
        if u and not u.get("disabled") and time.time() - s["at"] < SESSION_DAYS * 86400 and core.security.session_ok(u, s):
            LOCAL.dev = s.get("dev")
            if time.time() - (u.get("last_seen") or 0) > 300:
                u["last_seen"] = time.time()
                core.security.used(u, s.get("dev"))
            return u
    auth = handler.headers.get("Authorization") or ""
    if auth.startswith("Basic "):
        try:
            name, pw = base64.b64decode(auth[6:]).decode().split(":", 1)
        except Exception:  # noqa: BLE001
            return None
        if locked_out(name):
            return None
        u = by_name(name)
        if u and not u.get("disabled"):
            if core.security.main_password_allowed() and check_pw(pw, u.get("pw", "")):
                return u
            if any(pw == sec for _d, sec in core.security.app_secrets(u)):
                return u
        failed_login(name)
        return None
    if not USERS:                              # no accounts yet and no password: open, as before
        return OPEN
    return None


# --------------------------------------------------------------------------- wrong-password lockout
# Only guesses from the internet count: home network and Tailscale never lock anyone out,
# so a stranger can't lock the family out of their own music at home.
FAILS = {}                       # key -> [times]
FAIL_WINDOW, FAIL_PER_NAME, FAIL_PER_IP = 15 * 60, 8, 20


def client_ip(h):
    ip = (h.client_address or ("",))[0]
    if ip in ("127.0.0.1", "::1") or ip.startswith("::ffff:127."):
        fwd = h.headers.get("CF-Connecting-IP") or (h.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if fwd:
            return fwd
    return ip


PUBLIC = {"url": ""}             # the internet address people reach Umculo on (seen behind Cloudflare/Tailscale)


def note_address(h):
    host = (h.headers.get("X-Forwarded-Host") or h.headers.get("Host") or "").split(":")[0]
    if h.headers.get("X-Forwarded-Proto") == "https" and host and not re.match(r"^[\d.]+$", host) and host != "localhost" \
            and PUBLIC["url"] != "https://" + host and not host.endswith(".ts.net"):
        PUBLIC["url"] = "https://" + host
        core._save_json("address.json", PUBLIC)          # remembered across restarts


def private_ip(ip):
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.is_private or a.is_loopback or a in ipaddress.ip_network("100.64.0.0/10")


def _recent(key):
    now = time.time()
    with LOCK:
        t = [x for x in FAILS.get(key, []) if now - x < FAIL_WINDOW]
        if t:
            FAILS[key] = t
        else:
            FAILS.pop(key, None)
    return t


def locked_out(name):
    ip = getattr(LOCAL, "ip", "") or ""
    if not ip or private_ip(ip):
        return False
    return len(_recent("ip:" + ip)) >= FAIL_PER_IP or len(_recent("name:" + (name or "").lower())) >= FAIL_PER_NAME


def failed_login(name):
    ip = getattr(LOCAL, "ip", "") or ""
    if not ip or private_ip(ip):
        return
    now = time.time()
    with LOCK:
        FAILS.setdefault("ip:" + ip, []).append(now)
        FAILS.setdefault("name:" + (name or "").lower(), []).append(now)
        if len(FAILS) > 5000:                     # never grow without bound
            for k in list(FAILS)[:1000]:
                FAILS.pop(k, None)
    n = len(_recent("name:" + (name or "").lower()))
    if n in (FAIL_PER_NAME, FAIL_PER_NAME * 4):
        try:
            core.notify(f"Umculo: {n} wrong passwords for '{name}' from {ip} in 15 minutes. Sign-ins from the internet are paused for that name.")
        except Exception:  # noqa: BLE001
            pass
        log(f"too many wrong passwords for {name!r} from {ip}: paused for 15 minutes")


LOCKED_MSG = "Too many wrong passwords. Try again in 15 minutes."


def login(username, password, code="", remember=False, device_tok="", ua=""):
    """Password, then (if switched on) the 2-step code and the device's approval. -> (user, session, new device cookie)"""
    if locked_out(username):
        raise PermissionError(LOCKED_MSG)
    u = by_name(username)
    if not u or u.get("disabled") or not check_pw(password, u.get("pw", "")):
        failed_login(username)
        time.sleep(0.6)
        raise PermissionError("Wrong username or password")
    remember_secret(u, password)
    did, newtok = core.security.sign_in_checks(u, code, remember, device_tok, ua)
    tok = secrets.token_urlsafe(32)
    with LOCK:
        SESSIONS[hashlib.sha256(tok.encode()).hexdigest()] = {"user": u["id"], "at": time.time(), "dev": did}
    save_sessions()
    log(f"{u['username']} signed in" + (" from the internet" if core.security.from_internet() else ""))
    return u, tok, newtok


def logout(handler):
    m = re.search(r"(?:^|;\s*)umc_s=([A-Za-z0-9_\-]+)", handler.headers.get("Cookie") or "")
    if m:
        with LOCK:
            SESSIONS.pop(hashlib.sha256(m.group(1).encode()).hexdigest(), None)
        save_sessions()


# --------------------------------------------------------------------------- Navidrome accounts
def nd_native(method, path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-ND-Authorization"] = "Bearer " + token
    try:
        return core.http(method, core.CONFIG["navidrome"] + path, body=body, headers=headers, timeout=20, retries=0)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Navidrome said no to {method} {path}: {e}") from e


def nd_admin_token():
    r = nd_native("POST", "/auth/login", {"username": core.CONFIG["nd_user"], "password": core.CONFIG["nd_password"]})
    if not r or not r.get("token"):
        raise RuntimeError("Couldn't sign in to Navidrome as the admin (check NAVIDROME_USER / NAVIDROME_PASSWORD)")
    return r["token"]


def nd_libraries(tok):
    return nd_native("GET", "/api/library", token=tok) or []


def nd_main_library(tok):
    libs = nd_libraries(tok)
    want = core.CONFIG["nd_music_path"].rstrip("/")
    return next((l for l in libs if (l.get("path") or "").rstrip("/") == want), libs[0] if libs else None)


def nd_set_access(u, tok=None):
    """Which Navidrome libraries this person can see."""
    nd = u.get("nd") or {}
    if not nd.get("id"):
        return
    tok = tok or nd_admin_token()
    main = nd_main_library(tok)
    ids = []
    if HOUSE["mode"] == "together" or u.get("share_all") or u.get("role") == "admin":
        if main:
            ids.append(main["id"])
    if HOUSE["mode"] == "separate" and u.get("role") != "admin":
        lib = nd.get("library")
        if not lib:
            path = core.CONFIG["nd_family_path"].rstrip("/") + "/" + u["username"]
            os.makedirs(folder(u), exist_ok=True)
            r = nd_native("POST", "/api/library", {"name": f"{u['name']}'s music", "path": path}, tok) or {}
            lib = r.get("id")
            if lib:
                nd["library"] = lib
        if lib:
            ids.append(lib)
    if ids:
        nd_native("PUT", f"/api/user/{nd['id']}/library", {"libraryIds": ids}, tok)


def nd_create(u, password):
    tok = nd_admin_token()
    try:
        r = nd_native("POST", "/api/user", {"userName": u["username"], "name": u["name"], "password": password,
                                            "isAdmin": False}, tok)
    except RuntimeError:
        r = None                                  # probably exists already (409): reuse it below
    if not r or not r.get("id"):
        existing = [x for x in (nd_native("GET", "/api/user", token=tok) or []) if x.get("userName") == u["username"]]
        if not existing:
            raise RuntimeError("Navidrome didn't create the account")
        r = existing[0]
        nd_native("PUT", f"/api/user/{r['id']}", {**r, "password": password}, tok)
    u["nd"] = {"id": r["id"], "user": u["username"], "password": keep(password)}
    nd_set_access(u, tok)


def nd_password(u, password):
    nd = u.get("nd") or {}
    if not nd.get("id"):
        return
    tok = nd_admin_token()
    cur = nd_native("GET", f"/api/user/{nd['id']}", token=tok) or {}
    nd_native("PUT", f"/api/user/{nd['id']}", {**cur, "id": nd["id"], "userName": nd["user"], "name": u["name"],
                                               "password": password, "isAdmin": False}, tok)
    nd["password"] = keep(password)


def nd_creds(u=None):
    """(user, password) for Subsonic calls as this person; the admin uses the main Navidrome login."""
    u = u or current()
    if not u or is_admin(u) or not (u.get("nd") or {}).get("id"):
        return None
    return u["nd"]["user"], unkeep(u["nd"]["password"])


def nd_as(u, endpoint, **params):
    if builtin():
        return core.server.as_user(u or current() or admin_user() or OPEN, endpoint, **params)
    c = nd_creds(u)
    if not c:
        return core.nd_call(endpoint, **params)
    salt = secrets.token_hex(4)
    p = {"u": c[0], "t": hashlib.md5((c[1] + salt).encode()).hexdigest(), "s": salt, "v": "1.16.1", "c": "umculo",
         "f": "json"}
    p.update(params)
    q = urllib.parse.urlencode(p, doseq=True)
    res = core.http("GET", f"{core.CONFIG['navidrome']}/rest/{endpoint}?{q}", timeout=20)
    return (res or {}).get("subsonic-response")


# --------------------------------------------------------------------------- folders and sharing
def folder(u):
    return os.path.join(core.CONFIG["family"], u["username"])


def music_root(user_id=None):
    """Where a person's downloads go."""
    with LOCK:
        u = USERS.get(user_id) if user_id else None
    if u and HOUSE["mode"] == "separate" and u.get("role") != "admin":
        return os.path.join(folder(u), "Music")
    return core.CONFIG["library"]


def playlist_dir(user_id=None):
    """Where a person's playlists are kept (outside any Navidrome library: Umculo shares them itself)."""
    with LOCK:
        u = USERS.get(user_id) if user_id else None
    if u and u.get("role") != "admin":
        return os.path.join(core.CONFIG["config"], "people", u["username"], "playlists")
    return core.CONFIG["playlists"]


def roots_for(u):
    """Folders this person's music lives in (for deleting and finding files)."""
    if not u or is_admin(u):
        return [core.CONFIG["music"]] + ([core.CONFIG["family"]] if os.path.isdir(core.CONFIG["family"]) else [])
    if HOUSE["mode"] == "separate":
        return [folder(u)]
    return [core.CONFIG["music"]]


def link_tree(files, dest_root):
    """Hard-link files (paths inside the music folder) under dest_root, keeping their folders."""
    n = 0
    for f in files:
        rel = os.path.relpath(f, core.CONFIG["music"])
        if rel.startswith(".."):
            continue
        dest = os.path.join(dest_root, rel)
        if os.path.exists(dest):
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            os.link(f, dest)
        except OSError:
            shutil.copy2(f, dest)                 # different disk: copy instead
        n += 1
    return n


def album_files(album_dir):
    full = os.path.join(core.CONFIG["music"], album_dir)
    out = []
    for d, _s, fs in os.walk(full):
        out += [os.path.join(d, f) for f in fs if f.lower().endswith(tuple("." + e for e in core.AUDIO)) or
                f.lower() in ("cover.jpg", "folder.jpg")]
    return out


def sync_shares(u):
    """Make the person's Shared folder hold exactly what you share with them."""
    if HOUSE["mode"] != "separate" or u.get("role") == "admin":
        return
    shared = os.path.join(folder(u), "Shared")
    want = set()
    for a in u.get("shared_albums") or []:
        want |= set(album_files(a))
    for p in u.get("shared_playlists") or []:
        if os.path.isfile(p):
            want |= {e for e in core.m3u_entries(p) if os.path.exists(e)}
    link_tree(sorted(want), shared)
    keep_rel = {os.path.relpath(f, core.CONFIG["music"]) for f in want}
    for d, _s, fs in os.walk(shared, topdown=False):
        for f in fs:
            rel = os.path.relpath(os.path.join(d, f), shared)
            if rel not in keep_rel:
                os.remove(os.path.join(d, f))
        if d != shared and not os.listdir(d):
            os.rmdir(d)


def shared_copy(u, f):
    """Path of the person's own copy of one of your files (separate mode), else the file itself."""
    if HOUSE["mode"] == "separate" and u.get("role") != "admin" and core.within(f, core.CONFIG["music"]):
        return os.path.join(folder(u), "Shared", os.path.relpath(f, core.CONFIG["music"]))
    return f


# --------------------------------------------------------------------------- their playlists in Navidrome
def song_id_for(u, f):
    """Find the Navidrome id of a file, as this person sees it."""
    f = shared_copy(u, f)
    name = os.path.basename(f)
    stem = re.sub(r"^\d+[\s.\-_]+", "", os.path.splitext(name)[0])
    title = stem.split(" - ", 1)[-1]
    r = nd_as(u, "search3", query=title[:60], songCount=40, artistCount=0, albumCount=0) or {}
    for s in ((r.get("searchResult3") or {}).get("song") or []):
        if (s.get("path") or "").endswith(name):
            return s["id"]
    return None


def push_playlist(u, name, files, comment=""):
    """Create or update a playlist owned by this person in Navidrome (their apps show it)."""
    ids = [i for i in (song_id_for(u, f) for f in files) if i]
    pls = ((nd_as(u, "getPlaylists") or {}).get("playlists") or {}).get("playlist") or []
    mine = next((p for p in pls if p.get("name") == name and p.get("owner") == (u.get("nd") or {}).get("user")), None)
    if mine:
        nd_as(u, "deletePlaylist", id=mine["id"])
    if ids:
        nd_as(u, "createPlaylist", name=name, songId=ids)
    return len(ids), len(files)


def drop_playlist(u, name):
    pls = ((nd_as(u, "getPlaylists") or {}).get("playlists") or {}).get("playlist") or []
    for p in pls:
        if p.get("name") == name and p.get("owner") == (u.get("nd") or {}).get("user"):
            nd_as(u, "deletePlaylist", id=p["id"])


def sync_person(u):
    """Their Shared folder, their playlists and the playlists you share, all in Navidrome."""
    if u.get("role") == "admin":
        return
    if builtin():                                  # the built-in server reads folders and playlists itself
        sync_shares(u)
        core.musicdb.request_scan()
        return
    if not (u.get("nd") or {}).get("id"):
        return
    sync_shares(u)
    core.nd_scan()
    time.sleep(core.CONFIG.get("share_scan_wait", 20))
    state = u.setdefault("pushed", {})
    want = {}
    pdir = playlist_dir(u["id"])
    if os.path.isdir(pdir):
        for f in os.listdir(pdir):
            if f.endswith((".m3u", ".m3u8")):
                want[os.path.splitext(f)[0]] = os.path.join(pdir, f)
    for p in u.get("shared_playlists") or []:
        if os.path.isfile(p):
            want[os.path.splitext(os.path.basename(p))[0]] = p
    for name, path in want.items():
        files = [e for e in core.m3u_entries(path) if os.path.exists(e)]
        sig = f"{os.path.getmtime(path)}:{len(files)}"
        if state.get(name) == sig:
            continue
        got, total = push_playlist(u, name, files)
        if got == total or got:
            state[name] = sig if got == total else ""
    for name in [n for n in state if n not in want]:
        drop_playlist(u, name)
        state.pop(name, None)
    save()


def sync_loop():
    time.sleep(60)
    while True:
        with LOCK:
            people = [u for u in USERS.values() if u.get("role") != "admin" and not u.get("disabled")]
        for u in people:
            try:
                sync_person(u)
            except Exception:  # noqa: BLE001
                log("sync for", u["username"], "failed:", traceback.format_exc())
        time.sleep(600)


def kick(u):
    threading.Thread(target=lambda: _safe(sync_person, u), daemon=True).start()


def _safe(fn, *a):
    try:
        fn(*a)
    except Exception:  # noqa: BLE001
        log("background account task failed:", traceback.format_exc())


# --------------------------------------------------------------------------- managing people
def clean_username(s):
    s = re.sub(r"[^a-z0-9._-]", "", (s or "").strip().lower())
    if not s or len(s) < 2:
        raise ValueError("Pick a username of at least 2 letters or numbers")
    return s[:32]


def create_admin(username, password, name="", check=True):
    if check and len(password or "") < 6:
        raise ValueError("Use a password of at least 6 characters")
    uid = uuid.uuid4().hex[:10]
    u = {"id": uid, "username": clean_username(username), "name": name or username, "role": "admin",
         "pw": hash_pw(password), "sub": keep(password), "created": time.time(), "perms": {}}
    with LOCK:
        USERS[uid] = u
    for t in core.share.TOKENS.values():          # existing keys belonged to you
        t.setdefault("user", uid)
    core.share.save_tokens()
    save()
    log(f"admin account {u['username']} created")
    return u


def add_person(b):
    if b.get("mode") and not HOUSE["mode"]:
        if b["mode"] not in ("together", "separate"):
            raise ValueError("Choose together or separate")
        HOUSE["mode"] = b["mode"]
    if not HOUSE["mode"]:
        raise ValueError("Choose how your music is organised first")
    username = clean_username(b.get("username") or b.get("name"))
    if by_name(username):
        raise ValueError("That username is taken")
    password = b.get("password") or secrets.token_urlsafe(6)
    if len(password) < 6:
        raise ValueError("Use a password of at least 6 characters")
    perms = dict(PRESETS.get(b.get("preset") or "ask", PRESETS["ask"]))
    perms.update({k: v for k, v in (b.get("perms") or {}).items() if k in PRESETS["full"]})
    uid = uuid.uuid4().hex[:10]
    u = {"id": uid, "username": username, "name": (b.get("name") or username).strip()[:40], "role": "member",
         "pw": hash_pw(password), "sub": keep(password), "created": time.time(), "perms": perms, "share_all": bool(b.get("share_all")),
         "shared_albums": [], "shared_playlists": [], "nd": {}}
    if HOUSE["mode"] == "separate":
        os.makedirs(os.path.join(folder(u), "Music"), exist_ok=True)
    try:
        if not builtin():
            nd_create(u, password)
    except Exception as e:  # noqa: BLE001
        u["nd"] = {"error": str(e)[:200]}
        log("Navidrome account for", username, "failed:", e)
    with LOCK:
        USERS[uid] = u
    save()
    log(f"added {u['name']} ({username}), {HOUSE['mode']} household")
    kick(u)
    return {**public(u, True), "password": password}


def update_person(uid, b):
    with LOCK:
        u = USERS.get(uid)
    if not u:
        raise LookupError("No such person")
    me = current()
    if not is_admin(me) and (not me or me["id"] != uid):
        raise PermissionError("Only the admin can change other people")
    if "password" in b and b["password"]:
        if len(b["password"]) < 6:
            raise ValueError("Use a password of at least 6 characters")
        if not is_admin(me) and not check_pw(b.get("current") or "", u["pw"]):
            raise PermissionError("Your current password isn't right")
        set_password(u, b["password"])
        if me and me.get("id") == uid:              # ...but not here
            tok = secrets.token_urlsafe(32)          # a fresh session on this same device, no new code needed
            with LOCK:
                SESSIONS[hashlib.sha256(tok.encode()).hexdigest()] = {"user": u["id"], "at": time.time(),
                                                                      "dev": getattr(LOCAL, "dev", None)}
            save_sessions()
            save()
            return {**public(u, True), "_cookie": tok}
    if "name" in b and b["name"]:
        u["name"] = str(b["name"])[:40]
    if is_admin(me):
        if "preset" in b and b["preset"] in PRESETS:
            u["perms"] = dict(PRESETS[b["preset"]])
        if "perms" in b:
            u["perms"].update({k: v for k, v in b["perms"].items() if k in PRESETS["full"]})
        if "disabled" in b and u.get("role") != "admin":
            u["disabled"] = bool(b["disabled"])
        if "share_all" in b:
            u["share_all"] = bool(b["share_all"])
            try:
                if not builtin():
                    nd_set_access(u)
            except Exception as e:  # noqa: BLE001
                log("Navidrome access change failed:", e)
    save()
    return public(u, True)


def set_house(mode):
    """One library for everyone, or each person their own (plus what you choose to share)."""
    if mode not in ("together", "separate"):
        raise ValueError("Choose together or separate")
    if HOUSE["mode"] == mode:
        return {"house": mode}
    HOUSE["mode"] = mode
    with LOCK:
        people = [u for u in USERS.values() if u.get("role") != "admin"]
    if mode == "separate":
        for u in people:
            u.setdefault("share_all", False)
            os.makedirs(os.path.join(folder(u), "Music"), exist_ok=True)
    save()
    if builtin():
        core.musicdb.ensure_libraries()
        core.musicdb.request_scan()
    for u in people:
        kick(u)
    log(f"household switched to {mode}")
    return {"house": mode}


def remove_person(uid, delete_music=False):
    with LOCK:
        u = USERS.get(uid)
    if not u or u.get("role") == "admin":
        raise ValueError("That account can't be removed")
    nd = u.get("nd") or {}
    try:
        if builtin() or not nd.get("id"):
            raise StopIteration
        tok = nd_admin_token()
        if nd.get("id"):
            nd_native("DELETE", f"/api/user/{nd['id']}", token=tok)
        if nd.get("library") and delete_music:
            nd_native("DELETE", f"/api/library/{nd['library']}", token=tok)
    except StopIteration:
        pass
    except Exception as e:  # noqa: BLE001
        log("Navidrome cleanup failed:", e)
    if delete_music and HOUSE["mode"] == "separate":
        shutil.rmtree(folder(u), ignore_errors=True)
    shutil.rmtree(os.path.join(core.CONFIG["config"], "people", u["username"]), ignore_errors=True)
    with LOCK:
        USERS.pop(uid, None)
        for h in [h for h, s in SESSIONS.items() if s["user"] == uid]:
            SESSIONS.pop(h, None)
    save()
    save_sessions()
    log(f"removed {u['name']}" + (" and their music" if delete_music else ""))
    return {"ok": True}


def share(uid, kind, item, on):
    with LOCK:
        u = USERS.get(uid)
    if not u:
        raise LookupError("No such person")
    key = {"album": "shared_albums", "playlist": "shared_playlists"}[kind]
    lst = u.setdefault(key, [])
    if on and item not in lst:
        lst.append(item)
    if not on and item in lst:
        lst.remove(item)
    save()
    kick(u)
    return {"ok": True}


def shared_with(kind, item):
    key = {"album": "shared_albums", "playlist": "shared_playlists"}[kind]
    with LOCK:
        return [u["id"] for u in USERS.values() if item in (u.get(key) or [])]


# --------------------------------------------------------------------------- asking first
GATES = [   # (method, path regex, permission)
    ("POST", r"^/api/(get|getsong|album/get|share|recipes/[0-9a-f]+/keep|link/get)$", "download"),
    ("POST", r"^/api/playlists/import$|^/api/follows$", "download"),
    ("POST", r"^/api/(pl/[0-9a-f]{12}/(rename|watch|unwatch|upgrade)|pl/upgrade-all|playlists/|follows/)", "playlists"),
    ("POST", r"^/api/(link|finds|subscriptions|podcast)", "mixes"),
    ("POST", r"^/api/(pl/[0-9a-f]{12}/delete|lib/delete)", "delete"),
]
ADMIN_ONLY = [r"^/api/connectors", r"^/api/folders", r"^/api/migrate", r"^/api/settings", r"^/api/users", r"^/api/library/", r"^/api/logs", r"^/api/approvals/"]
ASKABLE = {"/api/get", "/api/getsong", "/api/album/get", "/api/playlists/import", "/api/follows"}


def gate(method, path, body):
    """None = go ahead; otherwise (status, response)."""
    u = current()
    if not u or is_admin(u):
        return None
    p = path.split("?")[0]
    if any(re.match(r, p) for r in ADMIN_ONLY) and not (p.startswith("/api/users/") and p.endswith(u["id"])) \
            and p not in ("/api/users/me",):
        return 403, {"error": "Only the admin can do that"}
    if method != "POST":
        return None
    if re.match(r"^/api/pl/[0-9a-f]{12}/remove$", p) and (body or {}).get("delete") and not can("delete", u):
        return 403, {"error": "You can't delete music; ask the admin"}
    if re.match(r"^/api/requests/[0-9a-f]+/delete$", p) and (body or {}).get("file") and not can("delete", u):
        return 403, {"error": "You can't delete music; ask the admin"}
    for _m, rx, perm in GATES:
        if not re.match(rx, p):
            continue
        if perm == "download":
            mode = (u.get("perms") or {}).get("download", "no")
            if mode == "yes":
                return None
            if mode == "ask" and p in ASKABLE:
                return 200, ask(u, p, body or {})
            return 403, {"error": "Downloading isn't switched on for you; ask the admin"}
        if not can(perm, u):
            return 403, {"error": "Ask the admin to switch this on for you"}
        return None
    return None


def describe(path, b):
    if path == "/api/album/get":
        al = core.dz_get(f"/album/{b.get('id')}", 86400) or {}
        return f"Album: {(al.get('artist') or {}).get('name', '')} – {al.get('title', 'unknown')}"
    if path == "/api/playlists/import":
        return f"Playlist: {b.get('name') or b.get('url') or 'pasted list'}"
    if path == "/api/follows":
        return f"Playlist to keep in sync: {b.get('name') or b.get('url')}"
    return f"Song: {b.get('artist', '')} – {b.get('title', '')}"


def ask(u, path, body):
    aid = uuid.uuid4().hex[:10]
    what = describe(path, body)
    if path == "/api/get":                     # availability checks expire: remember the exact copy now
        chk = core.CHECKS.get(body.get("check_id", "")) or {}
        cands = chk.get("candidates") or []
        if body.get("candidate_id") == "best":
            picks = core.pick_auto(cands, -1, 1)
            pool = [c for c in cands if c["exact"]] or cands
            cand = picks[0] if picks else (max(pool, key=core.rank) if pool else None)
        else:
            cand = next((c for c in cands if c["id"] == body.get("candidate_id")), None)
        if not cand:
            raise ValueError("Nothing to download yet")
        body = {"target": chk["target"], "candidate": cand}
        what = f"Song: {chk['target'].get('artist', '')} – {chk['target'].get('title', '')} ({cand.get('quality', '')})"
    with LOCK:
        APPROVALS[aid] = {"id": aid, "user": u["id"], "name": u["name"], "path": path, "body": body, "what": what,
                          "created": time.time(), "state": "waiting"}
    save()
    core.notify(f"{u['name']} asks for {what}. Open Umculo to say yes or no")
    log(f"{u['name']} asked for {what}")
    return {"ok": True, "asked": True, "message": "Sent to the admin for an OK. You'll see it in Queue once it's approved."}


def decide(aid, yes, handler):
    with LOCK:
        a = APPROVALS.get(aid)
    if not a or a["state"] != "waiting":
        raise LookupError("Already decided")
    with LOCK:
        u = USERS.get(a["user"])
    a["state"] = "approved" if yes else "declined"
    a["decided"] = time.time()
    save()
    if yes and u:
        prev = current()
        LOCAL.user = u                              # do it as them: their folder, their queue
        try:
            if a["path"] == "/api/get":
                core.start_request(a["body"]["target"], a["body"]["candidate"])
            else:
                handler.run_post(a["path"], a["body"])
        finally:
            LOCAL.user = prev
    log(f"{a['what']} for {a['name']}: {a['state']}")
    return {"ok": True, "state": a["state"]}


# --------------------------------------------------------------------------- web
def handle_get(path, qs):
    if path.startswith(("/api/me/security", "/api/security/")):
        return core.security.handle_get(path, qs)
    if path == "/api/me":
        u = current()
        return {"user": public(u, True) if u else None, "house": HOUSE["mode"], "accounts": bool(USERS),
                "navidrome": "" if builtin() else core.CONFIG["navidrome_public"] or "", "server": core.CONFIG.get("public_url", ""),
                "anywhere": PUBLIC["url"],
                "builtin": builtin()}
    if path == "/api/users":
        with LOCK:
            people = sorted(USERS.values(), key=lambda u: (u.get("role") != "admin", u["name"].lower()))
        return {"users": [public(u, True) for u in people], "house": HOUSE["mode"], "presets": PRESETS,
                "family_dir": core.CONFIG["family"]}
    if path == "/api/approvals":
        u = current()
        with LOCK:
            rows = sorted(APPROVALS.values(), key=lambda a: -a["created"])
        if not is_admin(u):
            rows = [a for a in rows if a["user"] == (u or {}).get("id")]
        return {"approvals": [{k: v for k, v in a.items() if k != "body"} for a in rows[:60]]}
    if path == "/api/shares":
        kind, item = (qs.get("kind") or [""])[0], (qs.get("item") or [""])[0]
        return {"users": shared_with(kind, item)}
    return None


def handle_post(path, b, handler):
    if path == "/api/setup":                     # first admin account
        if USERS:
            raise PermissionError("Already set up")
        u = create_admin(b.get("username") or "admin", b.get("password") or "", b.get("name") or "")
        u2, tok, dtok = login(u["username"], b["password"], ua=handler.headers.get("User-Agent", "") if handler else "")
        return {"ok": True, "_cookie": tok, "_device": dtok}
    if path == "/api/login":
        dev = re.search(r"(?:^|;\s*)umc_d=([A-Za-z0-9_\-]+)", (handler.headers.get("Cookie") or "") if handler else "")
        u, tok, dtok = login(b.get("username", ""), b.get("password", ""), b.get("code", ""), bool(b.get("remember")),
                             dev.group(1) if dev else "", handler.headers.get("User-Agent", "") if handler else "")
        return {"ok": True, "user": public(u), "_cookie": tok, "_device": dtok}
    if path.startswith(("/api/me/2fa/", "/api/me/apppass", "/api/me/devices/", "/api/security")):
        return core.security.handle_post(path, b)
    if path == "/api/logout":
        logout(handler)
        return {"ok": True, "_cookie": ""}
    if path == "/api/users":
        if not is_admin():
            raise PermissionError("Only the admin can add people")
        return add_person(b)
    if path == "/api/users/house":
        if not is_admin():
            raise PermissionError("Only the admin can do that")
        return set_house(b.get("mode") or "")
    m = re.match(r"^/api/users/([0-9a-f]{10})(?:/(remove|share|sync))?$", path)
    if m:
        uid, act = m.groups()
        if act and not is_admin():
            raise PermissionError("Only the admin can do that")
        if act == "remove":
            return remove_person(uid, bool(b.get("delete_music")))
        if act == "share":
            return share(uid, b.get("kind"), b.get("item"), bool(b.get("on")))
        if act == "sync":
            with LOCK:
                u = USERS.get(uid)
            if u:
                kick(u)
            return {"ok": True}
        return update_person(uid, b)
    if path == "/api/users/me":
        u = current()
        if not u or u.get("open"):
            raise ValueError("Set up an account first")
        return update_person(u["id"], b)
    m = re.match(r"^/api/approvals/([0-9a-f]{10})/(yes|no)$", path)
    if m:
        return decide(m.group(1), m.group(2) == "yes", handler)
    return None


def init(umculo):
    global core
    core = umculo
    PUBLIC.update(core._load_json("address.json", {}) or {})
    data = core._load_json("users.json", {})
    USERS.update(data.get("users") or {})
    HOUSE.update(data.get("house") or {})
    core.security.SETTINGS.update(data.get("security") or {})
    APPROVALS.update(data.get("approvals") or {})
    SESSIONS.update(core._load_json("sessions.json", {}))
    if not USERS and core.CONFIG["password"]:      # older setups: the UMCULO_PASSWORD login becomes the admin
        create_admin(core.CONFIG["user"] or "admin", core.CONFIG["password"], core.CONFIG["user"] or "Admin", check=False)
    threading.Thread(target=sync_loop, daemon=True).start()
