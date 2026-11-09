"""Status page: is everything Umculo depends on healthy?

Checks run in parallel, each with a short timeout, every few minutes in the background
(and on demand from the page). Results: ok (green), warn (amber), down (red).
"""
import collections
import concurrent.futures as cf
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


def c_soulseek():
    st = core.http("GET", f"{core.CONFIG['slskd']}/api/v0/server", headers=core.slskd_headers(), timeout=10) or {}
    state = st.get("state") or ""
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
    return "down", f"slskd is {state or 'not connected to Soulseek'}"


def c_prowlarr():
    if not core.CONFIG["prowlarr_key"]:
        return "warn", "Not set up (PROWLARR_KEY)"
    h = {"X-Api-Key": core.CONFIG["prowlarr_key"]}
    health = core.http("GET", f"{core.CONFIG['prowlarr']}/api/v1/health", headers=h, timeout=10) or []
    idx = core.http("GET", f"{core.CONFIG['prowlarr']}/api/v1/indexer", headers=h, timeout=10) or []
    status = core.http("GET", f"{core.CONFIG['prowlarr']}/api/v1/indexerstatus", headers=h, timeout=10) or []
    on = [i for i in idx if i.get("enable")]
    bad = [s for s in status if s.get("disabledTill")]
    msg = f"{len(on)} indexers on" + (f", {len(bad)} temporarily disabled" if bad else "")
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
    if path == "/api/status/summary":
        with LOCK:
            return {"overall": LAST["overall"], "at": LAST["at"]}
    return None


def init(umculo):
    global core
    core = umculo
    threading.Thread(target=loop, daemon=True).start()
