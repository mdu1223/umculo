"""Settings > Move in: everything someone needs to come to Umculo from what they used before.

1. Your music: point Umculo at the collection you already have (Folders), it reads it in place.
2. Your apps: Umculo is the music server; the same apps keep working with one address.
3. Your history: stars, ratings, play counts and playlists from any Subsonic server
   (Navidrome, Gonic, Airsonic, Ampache, LMS...), per person.
4. Your people: the accounts on the old server become Umculo accounts.
5. Retire the old apps: Umculo checks what's still running and what replaces it.
"""
import json
import secrets
import threading
import time
import urllib.parse

core = None
JOB = {"state": "idle", "message": "", "at": 0, "result": None}
PROBE = {"at": 0, "apps": []}

# id, name, ports, words that identify it, what Umculo does instead
RETIRED = [
    ("navidrome", "Navidrome", [4533], ["navidrome"], "Umculo is the music server now: same apps, same logins, your stars and playlists."),
    ("lidarr", "Lidarr", [8686], ["lidarr"], "Follow artists and albums under Search > an artist > Follow; Umculo fetches new releases."),
    ("aurral", "Aurral", [3001, 3000, 3002], ["aurral"], "For You, recipes and the weekly mix replace Aurral's flows."),
    ("metube", "MeTube", [8082, 8081], ["metube"], "Paste any YouTube, SoundCloud or Mixcloud link in Search or Mixes."),
    ("spotdl", "spotDL", [8800], ["spotdl"], "Playlists > Import a playlist takes Spotify and Apple Music links and keeps them in sync."),
    ("headphones", "Headphones", [8181], ["headphones"], "Follow artists in Umculo instead."),
    ("airsonic", "Airsonic", [4040], ["airsonic"], "Umculo is the music server now."),
    ("gonic", "Gonic", [4747], ["gonic"], "Umculo is the music server now."),
    ("beets", "beets web", [8337], ["beets"], "Library care fixes tags, covers, lyrics and duplicates."),
]


def probe_one(app, out):
    aid, name, ports, words, instead = app
    for port in ports:
        if aid == "navidrome" and port == 4533 and core.CONFIG["navidrome"]:
            urls = [core.CONFIG["navidrome"]]
        else:
            urls = [f"http://localhost:{port}"]
        for url in urls:
            try:
                data, _h = core.http("GET", url + "/", raw=True, timeout=2, retries=0)
                text = (data or b"")[:20000].decode(errors="ignore").lower()
            except Exception:  # noqa: BLE001
                continue
            if any(w in text for w in words) or (aid == "navidrome" and "/app/" in text):
                out.append({"id": aid, "name": name, "url": url, "instead": instead, "running": True})
                return


def probe(fresh=False):
    if not fresh and time.time() - PROBE["at"] < 300:
        return PROBE["apps"]
    out = []
    threads = [threading.Thread(target=probe_one, args=(a, out), daemon=True) for a in RETIRED]
    for t in threads:
        t.start()
    for t in threads:
        t.join(6)
    order = [a[0] for a in RETIRED]
    PROBE.update(at=time.time(), apps=sorted(out, key=lambda a: order.index(a["id"])))
    return PROBE["apps"]


def nd_people():
    """Accounts on the Navidrome Umculo knows about (admin login needed)."""
    if not core.CONFIG["nd_user"] or not core.CONFIG["nd_password"]:
        return []
    try:
        tok = core.users.nd_admin_token()
        res = core.http("GET", core.CONFIG["navidrome"] + "/api/user", headers={"x-nd-authorization": "Bearer " + tok}, timeout=10, retries=0)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in res or []:
        name = r.get("userName") or ""
        mine = core.users.by_name(name)
        out.append({"username": name, "name": r.get("name") or name, "admin": bool(r.get("isAdmin")),
                    "umculo": bool(mine) or name.lower() == (core.users.admin_user() or {}).get("username", "").lower()
                    or name == core.CONFIG["nd_user"]})
    return out


def overview():
    db, users = core.musicdb, core.users
    n = db.q("SELECT (SELECT COUNT(*) FROM song) s, (SELECT COUNT(*) FROM album) a, (SELECT COUNT(*) FROM playlist) p", one=True) \
        if db.DB is not None else {"s": 0, "a": 0, "p": 0}
    imported = db.q("SELECT v FROM kv WHERE k='imported'", one=True) if db.DB is not None else None
    adm = users.admin_user() or {}
    return {
        "server": core.CONFIG.get("music_server"), "music": core.CONFIG["music"], "library": core.CONFIG["library"],
        "songs": n["s"], "albums": n["a"], "playlists": n["p"], "scan": {k: v for k, v in db.SCAN.items() if k != "requested"},
        "apps": {"address": core.CONFIG.get("public_url") or "", "username": adm.get("username") or core.CONFIG["nd_user"]},
        "navidrome": {"url": core.CONFIG["navidrome"], "user": core.CONFIG["nd_user"], "set": bool(core.CONFIG["nd_password"])},
        "imported": json.loads(imported["v"]) if imported else None,
        "retired": probe(), "people": nd_people(), "job": JOB,
        "umculo_people": [{"id": u["id"], "name": u.get("name"), "username": u["username"], "role": u.get("role")} for u in users.USERS.values()],
        "house": users.HOUSE.get("mode"),
    }


def run_import(base, accounts):
    def say(m):
        JOB.update(message=m, at=time.time())
    try:
        if not core.builtin():
            raise ValueError("Switch to Umculo's own music server first (step 2)")
        while core.musicdb.SCAN["running"] or not core.musicdb.SCAN["last"]:
            say("Waiting for Umculo to finish reading your music")
            time.sleep(3)
        res = core.server.import_navidrome(say, base=base, accounts=accounts)
        bad = res.get("failed") or []
        JOB.update(state="failed" if bad and not res["people"] else "done", at=time.time(), result=res,
                   message=res["summary"] + (f". Couldn't sign in as {', '.join(bad)}: check the password" if bad else ""))
        core.log("move in:", JOB["message"])
    except Exception as e:  # noqa: BLE001
        JOB.update(state="failed", message=str(e)[:300], at=time.time())


def handle_post(p, b):
    users = core.users
    out = core.importers.handle_post(p, b)
    if out is not None:
        return out
    if p == "/api/migrate/import":
        if JOB["state"] == "running":
            raise ValueError("An import is already running")
        base = (b.get("url") or "").strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError("The old server's address should start with http:// or https://")
        name, pw = (b.get("username") or "").strip(), b.get("password") or ""
        if not name or not pw:
            raise ValueError("Their username and password on the old server, please")
        target = b.get("person") or "admin"
        u = None if target == "admin" else users.USERS.get(target)
        if target != "admin" and not u:
            raise ValueError("Pick who it's for")
        try:                                                   # check the login before starting
            ping = core.http("GET", f"{base}/rest/ping?" + urllib.parse.urlencode({"u": name, "p": pw, "v": "1.16.1", "c": "umculo", "f": "json"}),
                             timeout=10, retries=0)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Can't reach {base}: {e}")
        r = (ping or {}).get("subsonic-response") or {}
        if r.get("status") != "ok":
            raise ValueError("That server said: " + ((r.get("error") or {}).get("message") or "no"))
        JOB.update(state="running", message=f"Reading {r.get('type') or 'the server'} as {name}", at=time.time(), result=None)
        threading.Thread(target=run_import, args=(base, [(u, name, pw)]), daemon=True).start()
        return JOB
    if p == "/api/migrate/accounts":
        if not users.admin_user():
            raise ValueError("Make your own admin account first (tap the circle at the top), then add people")
        if not users.HOUSE.get("mode") and not b.get("mode"):
            raise ValueError("Choose how your household keeps its music first")
        made = []
        for name in b.get("usernames") or []:
            if users.by_name(name):
                continue
            pw = secrets.token_urlsafe(6)
            r = users.add_person({"name": name, "username": name, "password": pw, "preset": b.get("preset") or "ask",
                                  "mode": b.get("mode")})
            made.append({"id": r["id"], "username": r["username"], "password": pw})
        return {"made": made}
    if p == "/api/migrate/probe":
        return {"retired": probe(True)}
    if p == "/api/migrate/switch":
        core.connectors.update("server", {"music_server": "builtin"})
        return {"ok": True}
    return None


def init(umculo):
    global core
    core = umculo
