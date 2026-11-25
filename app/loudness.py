"""Even volume: how loud each song is, so the player can bring them all to the same level.

A library that mixes lossless rips, YouTube downloads and 1960s recordings jumps in volume from song to song.
Umculo measures each song once (EBU R128 integrated loudness and true peak, with ffmpeg, at low priority in the
background; songs coming up in someone's queue first) and keeps the result per file (redone if the file changes).
Nothing in your files is changed.

  - the web player turns each song to TARGET (the level Spotify and YouTube use), never so far that it clips
  - music apps get it as ReplayGain (the Subsonic replayGain field) when the file has no ReplayGain tags of its own
"""
import os
import re
import subprocess
import threading
import time

core = None
TARGET = -14.0            # LUFS: what the web player aims for
RG_REF = -18.0            # LUFS: ReplayGain 2's reference, for apps
LOCK = threading.Lock()
DATA = {}                 # path -> {"lufs", "peak" (dBTP), "mtime"}
WANT = []                 # paths to measure first (someone's about to play them)
STATE = {"running": False, "done": 0, "left": 0, "current": ""}
WAKE = threading.Event()


def measure(path):
    """-> (integrated loudness LUFS, true peak dBTP) or None."""
    try:
        p = subprocess.run(["nice", "-n", "15", "ffmpeg", "-nostdin", "-nostats", "-hide_banner", "-i", path, "-vn",
                            "-af", "ebur128=peak=true:framelog=verbose", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as ex:
        core.log("even volume: couldn't measure", os.path.basename(path), ex)
        return None
    out = p.stderr or ""
    if p.returncode != 0:                            # a failed run still prints a summary, all zeros: not a measurement
        core.log("even volume: couldn't measure", os.path.basename(path), (out.strip().splitlines() or ["?"])[-1][:200])
        return None
    i = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", out)
    pk = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?|-inf)\s*dBFS", out)
    if not i:
        core.log("even volume: couldn't measure", os.path.basename(path), (out.strip().splitlines() or ["?"])[-1][:200])
        return None
    lufs = float(i[-1])
    peak = float(pk[-1]) if pk and pk[-1] != "-inf" else 0.0
    if lufs < -60:                                   # silence, or a file ffmpeg couldn't read properly
        return None
    return lufs, peak


def known(path):
    """The measurement for this file as it is now (or a note that it couldn't be measured), else None."""
    d = DATA.get(path)
    if not d:
        return None
    try:
        if abs(os.path.getmtime(path) - d.get("mtime", 0)) > 1:
            return None
    except OSError:
        return None
    return d


def usable(d):
    return d and d.get("lufs") is not None


def gain_db(d, target=TARGET):
    """How much to turn this song up or down to reach target, without going over its true peak (-1 dBTP)."""
    g = target - d["lufs"]
    return round(min(g, -1.0 - d["peak"]), 2)


def replaygain(path):
    """For music apps: ReplayGain values worked out from the measurement (None if not measured yet)."""
    d = known(path)
    if not usable(d):
        return None
    return {"trackGain": round(RG_REF - d["lufs"], 2), "trackPeak": round(10 ** (d["peak"] / 20), 4)}


def save():
    with LOCK:
        data = dict(DATA)
    core._save_json("loudness.json", data)


def one(path):
    STATE["current"] = os.path.basename(path)
    m = measure(path)
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return
    with LOCK:                                       # a song that can't be measured isn't tried again until it changes
        DATA[path] = {"lufs": round(m[0], 2), "peak": round(m[1], 2), "mtime": mt} if m else {"lufs": None, "mtime": mt}
    STATE["done"] += 1


def loop():
    """Measure every song in the library, a little at a time, queued songs first."""
    WAKE.wait(60)                                    # let the server settle (or start now: someone's about to play)
    WAKE.clear()
    while True:
        try:
            if not core.CONFIG.get("loudness", True):
                WAKE.wait(600)
                WAKE.clear()
                continue
            rows = core.server.db.q("SELECT path FROM song WHERE duration < 1200") or []
            todo = [r["path"] for r in rows if not known(r["path"])]
            STATE.update(running=bool(todo), left=len(todo))
            n = 0
            while todo or WANT:
                with LOCK:
                    path = WANT.pop(0) if WANT else todo.pop(0)
                if not known(path) and os.path.exists(path):
                    one(path)
                    n += 1
                    if n % 25 == 0:
                        save()
                STATE["left"] = len(todo)
                time.sleep(0.2)
            if n:
                save()
                core.log(f"even volume: measured {core.plural(n, 'song')}")
            STATE.update(running=False, current="", left=0)
        except Exception as ex:  # noqa: BLE001
            core.log("even volume:", ex)
        WAKE.wait(1800)                              # new songs are picked up within half an hour (or when asked)
        WAKE.clear()


def for_ids(ids):
    """The web player's question: how loud are these songs? Unmeasured ones go to the front of the line."""
    srv, u = core.server, core.users.current()
    out = {}
    for sid in ids[:50]:
        r = srv.db.q(f"SELECT path FROM song WHERE id=? AND {srv.lib_sql(u)}", (str(sid),), one=True)
        if not r:
            continue
        d = known(r["path"])
        if usable(d):
            out[sid] = {"gain": gain_db(d), "lufs": d["lufs"]}
        elif not d:
            with LOCK:
                if r["path"] not in WANT:
                    WANT.append(r["path"])
            WAKE.set()
    return out


def handle_get(path, qs):
    if path == "/api/loudness":
        ids = [x for x in (qs.get("ids", [""])[0] or "").split(",") if x]
        return {"target": TARGET, "songs": for_ids(ids), **{k: STATE[k] for k in ("running", "left", "current")}}
    return None


def init(umculo):
    global core
    core = umculo
    DATA.update({k: v for k, v in (core._load_json("loudness.json", {}) or {}).items()
                 if not (v.get("lufs") == 0.0 and v.get("peak") == 0.0)})    # a failed run once read as 0 LUFS
    threading.Thread(target=loop, daemon=True).start()
