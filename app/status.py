"""Status page: is everything Umculo depends on healthy?

Checks run in parallel, each with a short timeout, every few minutes in the background
(and on demand from the page). Results: ok (green), warn (amber), down (red).
"""
import collections
import concurrent.futures as cf
import datetime
import os
import shutil
import threading
import time
import urllib.parse

core = None
ERRORS = collections.deque(maxlen=40)          # recent problems from the log
LOGS = collections.deque(maxlen=3000)           # everything, for the log viewer
LOG_SEQ = [0]
STARTED = time.time()
LAST = {"at": 0, "checks": [], "overall": "unknown"}
LOCK = threading.Lock()
RUNNING = threading.Lock()


def record(line):
    """Called by core.log for every line; keeps the ones that look like trouble."""
    LOG_SEQ[0] += 1
    LOGS.append((LOG_SEQ[0], time.time(), line[:2000]))
    low = line.lower()
    if low.split(" ", 2)[-1].startswith("batch download failed"):
        return                                       # one source failing is normal; the track's final result is logged
    if any(w in low for w in ("failed", "error", "traceback", "unreachable", "couldn't", "refused")):
        ERRORS.appendleft({"at": time.time(), "text": line[:400]})


def gb(n):
    return f"{n / 1e9:.1f} GB" if n < 1e12 else f"{n / 1e12:.2f} TB"


def timed(fn):
    t = time.time()
    try:
        state, detail = fn()
    except Exception as e:  # noqa: BLE001
        state, detail = "down", str(e)[:200]
    return state, detail, int((time.time() - t) * 1000)


# --------------------------------------------------------------------------- checks
def c_navidrome():
    if core.builtin():
        db = core.musicdb
        if db.DB is None:
            return "down", "Library not open"
        n = db.q("SELECT COUNT(*) n FROM song", one=True)["n"]
        sc = db.SCAN
        if sc["error"]:
            return "warn", f"Umculo · {n:,} songs · last scan failed: {sc['error']}"
        if not sc["last"]:
            return "ok", f"Umculo · first scan running ({sc['count']:,} files so far)"
        ago = int((time.time() - sc["last"]) / 60)
        when = "just now" if ago < 1 else f"{ago} min ago" if ago < 120 else f"{ago // 60} h ago"
        return "ok", f"Umculo · {core.plural(n, 'song')} · scanned {when} in {sc['took']} s" + (" · scanning now" if sc["running"] else "")
    return c_navidrome_raw()


def c_navidrome_raw():
    if not core.CONFIG["nd_user"]:
        return "warn", "No Navidrome login set"
    r = core.navidrome_call("ping")
    if not r or r.get("status") != "ok":
        return "down", ((r or {}).get("error") or {}).get("message", "No answer")
    sc = (core.navidrome_call("getScanStatus") or {}).get("scanStatus") or {}
    songs = sc.get("count")
    return "ok", f"Navidrome {r.get('serverVersion', '')}".strip() + (f" · {songs:,} songs" if songs else "") + \
        (" · scanning now" if sc.get("scanning") else "")


SLSK_WATCH = {"since": 0, "tries": 0, "last": 0, "state": ""}
WATCH_EVERY = float(os.environ.get("SLSK_WATCH_EVERY", "60"))      # how often to look (s)
OFF_AFTER = float(os.environ.get("SLSK_OFF_AFTER", "120"))          # off this long: ask it to reconnect (s)


# --------------------------------------------------------------------------- restarting slskd
# slskd only lets its own admin restart it: Umculo signs in with slskd's web login (which the admin enters once
# in Settings; kept in config/slskd_login.json, readable only by Umculo, never sent back) and asks it to restart.
LOGIN_FILE = "slskd_login.json"
CANARY = os.environ.get("SLSK_CANARY", "Rihanna Umbrella")      # a song nearly everyone on Soulseek shares
RESTART_EVERY = float(os.environ.get("SLSK_RESTART_EVERY", "7200"))  # at most this often by itself (s)
CANARY_EVERY = float(os.environ.get("SLSK_CANARY_EVERY", "600"))     # check with the well-known song at most this often
SUSPECT_AFTER = int(os.environ.get("SLSK_SUSPECT_AFTER", "6"))       # searches in a row that found nothing


def slskd_login():
    d = core._load_json(LOGIN_FILE, {}) or {}
    u = d.get("username") or os.environ.get("SLSKD_USERNAME", "")
    p = d.get("password") or os.environ.get("SLSKD_PASSWORD", "")
    return (u, p) if u and p else None


def set_login(username, password):
    if not username or not password:
        core._save_json(LOGIN_FILE, {})
        return {"set": False}
    base = core.CONFIG["slskd"]
    try:                                             # check it works before keeping it
        r = core.http("POST", f"{base}/api/v0/session", {"username": username, "password": password}, timeout=15, retries=0) or {}
    except Exception as ex:  # noqa: BLE001
        raise ValueError(f"slskd didn't accept that login ({ex})") from ex
    if not (r.get("token") or r.get("Token")):
        raise ValueError("slskd didn't accept that login")
    core._save_json(LOGIN_FILE, {"username": username, "password": password})
    try:
        os.chmod(os.path.join(core.CONFIG["config"], LOGIN_FILE), 0o600)
    except OSError:
        pass
    return {"set": True, "username": username}


def login_needed():
    """Has slskd got its web login switched on? (Off: every request counts as its admin, no login needed.)"""
    try:
        r = core.http("GET", f"{core.CONFIG['slskd']}/api/v0/session/enabled", timeout=10, retries=0)
        return r is not False and str(r).lower() != "false"
    except Exception:  # noqa: BLE001
        return True


def can_restart():
    return bool(slskd_login()) or not login_needed()


def restart_slskd(why):
    """Ask slskd to restart itself: straight away when its web login is off, else signed in as its admin."""
    base = core.CONFIG["slskd"]
    if not login_needed():
        core.http("PUT", f"{base}/api/v0/application", {}, core.slskd_headers(), timeout=20, retries=0)
    else:
        login = slskd_login()
        if not login:
            raise LookupError("Add slskd's web login (Settings > Connectors > Soulseek) so Umculo can restart it")
        r = core.http("POST", f"{base}/api/v0/session", {"username": login[0], "password": login[1]}, timeout=15, retries=0) or {}
        token = r.get("token") or r.get("Token")
        if not token:
            raise LookupError("slskd didn't accept its saved login: enter it again in Settings > Connectors > Soulseek")
        core.http("PUT", f"{base}/api/v0/application", {}, {"Authorization": f"Bearer {token}"}, timeout=20, retries=0)
    SLSK_WATCH.update(restarted_at=time.time(), broken=False, canary_at=0, fails=0)
    core.SLSK.update(empty_streak=0, pause_until=0)
    core.log(f"Soulseek: restarted slskd ({why})")
    return "slskd is restarting: Soulseek is back in about a minute"


def canary_ok():
    """A search for a song nearly everyone shares. -> True (answers), False (an empty answer: connected but not
    working), None (an error: not evidence either way)."""
    try:
        return bool(core._slskd_search(CANARY, seconds=15))
    except Exception:  # noqa: BLE001
        return None


def downloading():
    """Is slskd moving a download right now? Then it works, whatever the searches say."""
    try:
        users = core.http("GET", f"{core.CONFIG['slskd']}/api/v0/transfers/downloads", headers=core.slskd_headers(),
                          timeout=10, retries=0) or []
    except Exception:  # noqa: BLE001
        return False
    for u in users:
        for d in u.get("directories") or []:
            for f in d.get("files") or []:
                if "InProgress" in (f.get("state") or ""):
                    return True
    return False


def slsk_watch():
    """slskd sometimes drops off Soulseek and stays there ("Disconnecting" for hours). Every minute: if it's been
    off for 2 minutes, tell it to reconnect (and, if it's stuck halfway, to finish disconnecting first)."""
    time.sleep(min(30, WATCH_EVERY))
    while True:
        try:
            if core.CONFIG.get("slskd") and "soulseek" in core.CONFIG.get("sources", []):
                base, h = core.CONFIG["slskd"], core.slskd_headers()
                st = core.http("GET", f"{base}/api/v0/server", headers=h, timeout=10, retries=0) or {}
                state = st.get("state") or ""
                if "LoggedIn" in state or "Logged In" in state:
                    if SLSK_WATCH["since"]:
                        core.log(f"Soulseek: connected again ({SLSK_WATCH['tries']} reconnect tries)")
                        SLSK_WATCH["check_after"] = time.time() + 90          # reconnected: check it really works
                    SLSK_WATCH.update(since=0, tries=0, state=state)
                    now = time.time()
                    suspect = core.SLSK["empty_streak"] >= SUSPECT_AFTER or (SLSK_WATCH.get("check_after") and now > SLSK_WATCH["check_after"])
                    if suspect and now - SLSK_WATCH.get("canary_at", 0) > CANARY_EVERY:
                        SLSK_WATCH.update(canary_at=now, check_after=0)
                        ok = canary_ok()
                        if ok is None:
                            pass                             # an error, not an empty answer: try again later
                        elif ok or downloading():
                            SLSK_WATCH.update(broken=False, fails=0)
                            core.SLSK["empty_streak"] = 0
                        else:
                            SLSK_WATCH["fails"] = SLSK_WATCH.get("fails", 0) + 1
                            SLSK_WATCH["broken"] = True
                            if SLSK_WATCH["fails"] < 2:          # once could be Soulseek being busy: check again soon
                                SLSK_WATCH["canary_at"] = now - CANARY_EVERY + min(180, CANARY_EVERY)
                            elif can_restart() and now - SLSK_WATCH.get("restarted_at", 0) > RESTART_EVERY:
                                try:
                                    restart_slskd("connected, but even a search for a well-known song found nothing")
                                except Exception as ex:  # noqa: BLE001
                                    core.log("Soulseek: couldn't restart slskd:", ex)
                            elif not can_restart():
                                core.log("Soulseek: connected but finding nothing; restart slskd "
                                         "(add its web login in Settings so Umculo can do it for you)")
                else:
                    now = time.time()
                    if not SLSK_WATCH["since"]:
                        SLSK_WATCH["since"] = now
                        core.log(f"Soulseek: slskd is {state or 'not connected'}")
                    off = now - SLSK_WATCH["since"]
                    wait = min(1800, OFF_AFTER * 2 ** min(SLSK_WATCH["tries"], 4))     # 2, 4, 8, 16, then every 30 min
                    if off > OFF_AFTER and now - SLSK_WATCH["last"] > wait:
                        SLSK_WATCH["tries"] += 1
                        SLSK_WATCH["last"] = now
                        try:
                            if "Disconnecting" in state or "Connecting" in state:
                                try:                         # slskd wants a reason with a disconnect (a JSON string)
                                    core.http("DELETE", f"{base}/api/v0/server", body="Umculo: reconnecting", headers=h,
                                              timeout=15, retries=0)
                                except Exception as ex:  # noqa: BLE001
                                    core.log("Soulseek: disconnect first:", ex)
                                time.sleep(5)
                            core.http("PUT", f"{base}/api/v0/server", body={}, headers=h, timeout=30, retries=0)
                            core.log(f"Soulseek: slskd was {state or 'off'} for {int(off / 60)} min; asked it to reconnect")
                        except Exception as ex:  # noqa: BLE001
                            core.log(f"Soulseek: couldn't ask slskd to reconnect ({ex}); restart slskd if this keeps up")
                    SLSK_WATCH["state"] = state
        except Exception as ex:  # noqa: BLE001
            if not SLSK_WATCH.get("unreachable"):
                core.log("Soulseek: can't reach slskd:", ex)
            SLSK_WATCH["unreachable"] = True
        else:
            SLSK_WATCH["unreachable"] = False
        time.sleep(WATCH_EVERY)


def c_soulseek():
    st = core.http("GET", f"{core.CONFIG['slskd']}/api/v0/server", headers=core.slskd_headers(), timeout=10) or {}
    state = st.get("state") or ""
    if SLSK_WATCH.get("broken") and ("LoggedIn" in state or "Logged In" in state):
        can = " (Umculo will restart it)" if can_restart() else ": add slskd's login so Umculo can restart it"
        return "down", f"Connected as {st.get('username', '')}, but searches find nothing{can}"
    s = core.SLSK
    extra = f" · {s['searches']} searches, {s['empty']} unanswered"
    if s["pause_until"] > time.time():
        return "warn", f"Resting playlist searches for {int((s['pause_until'] - time.time()) / 60) + 1} min (Soulseek stopped answering){extra}"
    if "LoggedIn" in state or "Logged In" in state:
        user = st.get("username")
        if not user:
            try:
                app = core.http("GET", f"{core.CONFIG['slskd']}/api/v0/application", headers=core.slskd_headers(),
                                timeout=10, retries=0) or {}
                user = (app.get("user") or {}).get("username") or (app.get("server") or {}).get("username")
            except Exception:  # noqa: BLE001
                user = None
        return "ok", (f"Connected as {user}" if user else "Connected") + extra
    w = SLSK_WATCH
    tries = f" · Umculo asked it to reconnect {core.plural(w['tries'], 'time')}" if w["tries"] else ""
    since = f" for {int((time.time() - w['since']) / 60)} min" if w["since"] else ""
    return "down", f"slskd is {state or 'not connected to Soulseek'}{since}{tries}"


def till_ts(v):
    """Prowlarr's '2026-11-25T03:10:00Z' -> unix time (0 if missing)."""
    if not v:
        return 0
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def c_prowlarr():
    if not core.CONFIG["prowlarr_key"]:
        return "warn", "Not set up (PROWLARR_KEY)"
    h = {"X-Api-Key": core.CONFIG["prowlarr_key"]}
    health = core.http("GET", f"{core.CONFIG['prowlarr']}/api/v1/health", headers=h, timeout=10) or []
    idx = core.http("GET", f"{core.CONFIG['prowlarr']}/api/v1/indexer", headers=h, timeout=10) or []
    status = core.http("GET", f"{core.CONFIG['prowlarr']}/api/v1/indexerstatus", headers=h, timeout=10) or []
    on = [i for i in idx if i.get("enable")]
    names = {i.get("id"): i.get("name") or "an indexer" for i in idx}
    bad = []
    for st in status:                               # Prowlarr pauses an indexer that keeps failing, then tries it again
        till = till_ts(st.get("disabledTill"))
        if till and till > time.time():
            bad.append(f"{names.get(st.get('indexerId'), 'an indexer')} paused by Prowlarr until "
                       + time.strftime("%H:%M", time.localtime(till)) + (" tomorrow" if till - time.time() > 12 * 3600 else ""))
    msg = f"{len(on)} indexers on" + (" · " + "; ".join(bad) if bad else "")
    issues = [x.get("message", "") for x in health if x.get("type") in ("error", "warning")]
    if not on:
        return "down", "No indexers enabled"
    if bad or issues:
        return "warn", msg + (" · " + "; ".join(issues[:2]) if issues else "")
    return "ok", msg


def c_sab():
    if not core.CONFIG["sab_key"]:
        return "warn", "Not set up (SABNZBD_KEY)"
    q = (core.sab("queue", limit=1) or {}).get("queue") or {}
    if not q:
        return "down", "No answer"
    msg = " · ".join(x for x in (q.get("status"), f"{q.get('noofslots', 0)} in queue",
                                 f"{q['speed']}B/s" if q.get("speed") not in (None, "", "0") else "",
                                 f"{q['diskspace1']} GB free" if q.get("diskspace1") else "") if x)
    if q.get("paused"):
        return "warn", "Paused · " + msg
    return "ok", msg


def c_deezer():
    r = core.http("GET", f"{core.CONFIG['deezer']}/search?limit=1&q=amapiano", timeout=10) or {}
    return ("ok", "Catalogue search working") if r.get("data") is not None else ("down", "No answer")


def c_youtube():
    import yt_dlp
    v = yt_dlp.version.__version__
    with core._ydl({"extract_flat": "in_playlist"}) as y:
        res = y.extract_info("ytsearch1:kabza de small", download=False) or {}
    n = len(res.get("entries") or [])
    helper = ""
    try:
        core.http("GET", "http://127.0.0.1:4416/ping", timeout=3, retries=0)
        helper = " · bot-check helper running"
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(os.path.join(core.CONFIG["config"], "youtube-cookies.txt")):
        helper += " · using cookies"
    bot = [e for e in ERRORS if "not a bot" in e["text"] and time.time() - e["at"] < 3 * 3600]
    if bot:
        return "warn", (f"yt-dlp {v} · YouTube asked {len(bot)}× in the last 3 h to prove we're not a bot; "
                        "YouTube downloads may fail (other sources still work)" + helper)
    return ("ok" if n else "warn"), f"yt-dlp {v} · " + ("search working" if n else "search returned nothing") + helper


def c_shazam():
    try:
        import shazamio  # noqa: F401
    except ImportError:
        return "warn", "Not installed: the song finder and the mic button can only use tracklists"
    finds = [f for f in core.links.FINDS.values() if f.get("state") in ("done", "failed")]
    last = max(finds, key=lambda f: f["created"]) if finds else None
    if last and "stopped working" in (last.get("message") or ""):
        return "warn", "Last song finder run lost Shazam part-way: " + last["message"]
    return "ok", "Installed" + (f" · last song finder: {last['message']}" if last else "")


def c_lrclib():
    core.http("GET", f"{core.library.CFG['lrclib']}/api/search?q=hello", timeout=10, retries=0)
    return "ok", "Lyrics service answering" + ("" if core.library.CFG["lyrics_auto"] else " (auto lyrics off)")


def c_listenbrainz():
    user = core.foryou.CFG["lb_user"]
    if not user:
        return "warn", "Not set (optional): add your username in Settings"
    r = core.http("GET", f"{core.foryou.CFG['lb_api']}/1/user/{urllib.parse.quote(user)}/listen-count", timeout=10) or {}
    n = (r.get("payload") or {}).get("count")
    return ("ok", f"{user}: {n:,} listens") if n is not None else ("warn", "User not found")


def c_ntfy():
    return ("ok", "Phone notifications on") if core.CONFIG["ntfy"] else ("warn", "Off: add your ntfy address in Settings")


def c_disk():
    out, worst = [], "ok"
    for label, path in (("Music", core.CONFIG["music"]), ("App data", core.CONFIG["config"])):
        if not os.path.isdir(path):
            continue
        du = shutil.disk_usage(path)
        pct = du.free * 100 / du.total
        out.append(f"{label}: {gb(du.free)} free ({pct:.0f}%)")
        if du.free < 5e9 or pct < 3:
            worst = "down"
        elif du.free < 50e9 or pct < 10:
            worst = "warn" if worst == "ok" else worst
    cache = os.path.join(core.CONFIG["staging"], "stream")
    if os.path.isdir(cache):
        size = sum(os.path.getsize(os.path.join(cache, f)) for f in os.listdir(cache)
                   if os.path.isfile(os.path.join(cache, f)))
        out.append(f"stream cache {gb(size)}")
    return worst, " · ".join(out)


def c_umculo():
    with core.JOB_LOCK:
        jobs = list(core.JOBS.values())
    running = [j for j in jobs if core.job_summary(j)["running"]]
    with core.REQ_LOCK:
        active = [r for r in core.REQS.values() if r["state"] in ("queued", "searching", "downloading", "filing")]
    finds = [f for f in core.links.FINDS.values() if f["state"] in ("queued", "downloading", "listening")]
    up = int(time.time() - STARTED)
    return "ok", (f"v{core.VERSION} · up {up // 86400}d {up % 86400 // 3600}h · {core.plural(len(running), 'playlist')} working, "
                  f"{core.plural(core.BATCH.waiting(), 'song')} waiting · {core.plural(len(active), 'download')} · "
                  f"{core.plural(len(finds), 'mix')} being listened to")


CHECKS = [("umculo", "Umculo", c_umculo), ("navidrome", "Music server", c_navidrome), ("soulseek", "Soulseek", c_soulseek),
          ("prowlarr", "Prowlarr", c_prowlarr), ("sabnzbd", "SABnzbd", c_sab), ("deezer", "Song catalogue (Deezer)", c_deezer),
          ("youtube", "YouTube / SoundCloud", c_youtube), ("shazam", "Shazam recognition", c_shazam),
          ("lrclib", "Lyrics (LRCLIB)", c_lrclib), ("listenbrainz", "ListenBrainz", c_listenbrainz),
          ("ntfy", "Notifications", c_ntfy), ("disk", "Disk space", c_disk)]


CHECK_FNS = {k: fn for k, _n, fn in CHECKS}
CHECK_FNS["navidrome_raw"] = c_navidrome_raw


def run():
    if not RUNNING.acquire(blocking=False):
        return
    try:
        with cf.ThreadPoolExecutor(len(CHECKS)) as ex:
            futs = [(k, name, ex.submit(timed, fn)) for k, name, fn in CHECKS]
            checks = []
            for k, name, f in futs:
                try:
                    state, detail, ms = f.result(timeout=40)
                except Exception as e:  # noqa: BLE001
                    state, detail, ms = "down", f"Check timed out ({e})", 40000
                checks.append({"id": k, "name": name, "state": state, "detail": detail, "ms": ms})
        important = {"umculo", "navidrome", "soulseek", "disk"}
        overall = "down" if any(c["state"] == "down" and c["id"] in important for c in checks) else \
            "warn" if any(c["state"] != "ok" and c["id"] not in ("listenbrainz", "ntfy") for c in checks) else "ok"
        with LOCK:
            LAST.update(at=time.time(), checks=checks, overall=overall)
    finally:
        RUNNING.release()


def loop():
    time.sleep(20)
    while True:
        try:
            run()
        except Exception as e:  # noqa: BLE001
            core.log("status checks failed:", e)
        time.sleep(300)


def handle_get(path, qs):
    if path == "/api/status":
        if qs.get("fresh") or time.time() - LAST["at"] > 300:
            run()
        with LOCK:
            return {**LAST, "errors": list(ERRORS)[:20], "version": core.VERSION}
    if path == "/api/logs":
        after = int((qs.get("after") or ["0"])[0] or 0)
        q = (qs.get("q") or [""])[0].lower()
        only = (qs.get("only") or [""])[0]
        words = {"problems": ("failed", "error", "traceback", "couldn't", "refused", "not a bot"),
                 "soulseek": ("soulseek", "slskd"), "youtube": ("youtube", "yt-dlp", "soundcloud"),
                 "playlists": ("playlist", "upgrade", "import", "kept", "batch"),
                 "mixes": ("mix", "subscription", "find", "episode", "shazam"),
                 "streaming": ("stream", "subsonic")}.get(only)
        rows = [r for r in list(LOGS) if r[0] > after and (not q or q in r[2].lower())
                and (not words or any(w in r[2].lower() for w in words))]
        return {"lines": [{"n": n, "at": t, "text": x} for n, t, x in rows[-500:]], "last": LOG_SEQ[0]}
    if path == "/api/streams":
        if not core.users.is_admin():
            raise PermissionError("Only the admin can see this")
        return core.server.stream_report() if core.builtin() else {"streams": [], "summary": {}, "tips": []}
    if path == "/api/slskd/login":
        if not core.users.is_admin():
            raise PermissionError("Only the admin can see this")
        lg = slskd_login()
        return {"set": bool(lg), "username": lg[0] if lg else "", "needed": login_needed(),
                "restarted_at": SLSK_WATCH.get("restarted_at", 0), "broken": bool(SLSK_WATCH.get("broken"))}
    if path == "/api/status/summary":
        with LOCK:
            return {"overall": LAST["overall"], "at": LAST["at"]}
    return None


def handle_post(path, b):
    if path not in ("/api/slskd/restart", "/api/slskd/login"):
        return None
    if not core.users.is_admin():
        raise PermissionError("Only the admin can do this")
    if path == "/api/slskd/restart":
        return {"ok": True, "message": restart_slskd("restarted from the app")}
    return set_login((b or {}).get("username", "").strip(), (b or {}).get("password", ""))


def init(umculo):
    global core
    core = umculo
    threading.Thread(target=loop, daemon=True).start()
    threading.Thread(target=slsk_watch, daemon=True).start()
