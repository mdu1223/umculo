"""Wake up to music, and music that starts by itself (Home Assistant automations).

An alarm plays a playlist, smart mix, mix show or radio on a Home Assistant speaker at a time and on
chosen days, starting quietly and getting louder. Each alarm keeps the time zone of the phone that set it.

A hook address lets Home Assistant start music in an automation ("when I get home", "at sunset"):
  GET or POST /api/hook/<hook key>/play?what=radio&q=Deep+House&speaker=media_player.lounge&volume=0.3
              /api/hook/<hook key>/stop?speaker=media_player.lounge
The hook key plays music as you and does nothing else; make a new one to cancel the old.

GET  /api/alarms                 -> {"alarms": [...], "hook": bool}
POST /api/alarms                 {id?, time "06:30", days [0..6] (Mon=0, empty = once), what {kind, id|q, name},
                                  speaker, volume, ramp (minutes), on, tz}
POST /api/alarms/<id>/delete | /api/alarms/<id>/test
POST /api/alarms/hook            -> {"key"} a new hook key (shown once)
"""
import datetime
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import uuid

core = None
LOCK = threading.Lock()
DATA = {"alarms": {}, "hooks": {}}       # hooks: sha256(key) -> user id


def path():
    return os.path.join(core.CONFIG["config"], "alarms.json")


def load():
    try:
        with open(path()) as fh:
            d = json.load(fh)
        DATA["alarms"].update(d.get("alarms") or {})
        DATA["hooks"].update(d.get("hooks") or {})
    except (OSError, ValueError):
        pass


def save():
    with LOCK:
        data = json.dumps(DATA)
    tmp = path() + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(data)
    os.replace(tmp, path())
    os.chmod(path(), 0o600)


def uid():
    return core.notify_people.uid()


def zone(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name or os.environ.get("TZ") or "Africa/Johannesburg")
    except Exception:  # noqa: BLE001
        return datetime.timezone.utc


# --------------------------------------------------------------------------- what to play
def as_item(x):
    if x.get("deezer_id"):
        return {k: x.get(k) for k in ("deezer_id", "title", "artist", "album", "cover", "cover_big", "duration")}
    c = x.get("cover")
    return {"nd_id": x["id"], "title": x.get("title"), "artist": x.get("artist"), "album": x.get("album"),
            "cover": f"/api/ndcover/{c}?s=600" if c and not str(c).startswith(("/", "http")) else c, "duration": x.get("duration")}


def items_for(what):
    """Run as the person: their library, their smart mixes."""
    kind = (what or {}).get("kind")
    if kind == "smart":
        songs = core.likes.smart(what.get("id") or "liked")["songs"]
    elif kind == "playlist":
        songs = core.libview.playlist(what.get("id") or "")["songs"]
    elif kind == "radio":
        songs = core.radio.build(what.get("q") or "", 1)["songs"]
    elif kind == "show":                                   # the newest mixes of a show
        srv, u = core.server, core.users.current()
        root = core.CONFIG["mixes"].rstrip("/") + "/"
        rows = srv.db.q(f"""SELECT * FROM song WHERE album_id=? AND substr(path, 1, ?) = ? AND {srv.lib_sql(u)}
                            ORDER BY created DESC LIMIT 5""", (what.get("id") or "", len(root), root))
        songs = [core.libview.song_view(srv.song(r, None)) for r in rows]
    else:
        raise ValueError("Choose what to play")
    items = [as_item(x) for x in songs if x.get("id") or x.get("deezer_id")]
    if not items:
        raise LookupError("Nothing to play there yet")
    return items


def ramp(entity, start, target, minutes):
    if minutes <= 0 or target <= start:
        return
    steps = max(1, int(minutes * 3))
    for k in range(1, steps + 1):
        time.sleep(20)
        if entity not in core.cast.SESS:
            return
        try:
            core.cast.service("media_player", "volume_set", {"entity_id": entity, "volume_level": round(start + (target - start) * k / steps, 3)})
        except Exception:  # noqa: BLE001
            return


def play(what, speaker, volume=0.3, ramp_min=0):
    items = items_for(what)
    v = max(0.0, min(1.0, float(volume if volume is not None else 0.3)))
    start = min(v, 0.06) if ramp_min else v
    st = core.cast.start(speaker, {"items": items, "start": 0, "volume": start})
    if ramp_min:
        threading.Thread(target=ramp, args=(speaker, start, v, float(ramp_min)), daemon=True).start()
    return st


# --------------------------------------------------------------------------- alarms
def clean(b, old=None):
    a = dict(old or {})
    t = str(b.get("time", a.get("time", "07:00")))
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", t):
        raise ValueError("Time like 06:30")
    a["time"] = t
    if "days" in b:
        a["days"] = sorted({int(d) for d in b.get("days") or [] if 0 <= int(d) <= 6})
    a.setdefault("days", [0, 1, 2, 3, 4])
    if "what" in b:
        w = b["what"] or {}
        if w.get("kind") not in ("smart", "playlist", "radio", "show"):
            raise ValueError("Choose what to play")
        a["what"] = {"kind": w["kind"], "id": str(w.get("id") or "")[:80], "q": str(w.get("q") or "")[:120],
                     "name": str(w.get("name") or w.get("q") or "")[:80]}
    if not a.get("what"):
        raise ValueError("Choose what to play")
    sp = b.get("speaker", a.get("speaker", ""))
    if not re.match(r"^media_player\.\w+$", sp or ""):
        raise ValueError("Choose a speaker")
    a["speaker"] = sp
    a["speaker_name"] = str(b.get("speaker_name", a.get("speaker_name", sp)))[:80]
    a["volume"] = max(0.05, min(1.0, float(b.get("volume", a.get("volume", 0.35)))))
    a["ramp"] = max(0, min(30, int(b.get("ramp", a.get("ramp", 5)))))
    a["on"] = bool(b.get("on", a.get("on", True)))
    a["tz"] = str(b.get("tz") or a.get("tz") or "")[:60]
    return a


def mine(a):
    return a and a.get("user") == uid()


def due(a, now):
    if not a.get("on"):
        return False
    t = datetime.datetime.fromtimestamp(now, zone(a.get("tz")))
    if t.strftime("%H:%M") != a["time"]:
        return False
    if a["days"] and t.weekday() not in a["days"]:
        return False
    return a.get("fired") != t.strftime("%Y-%m-%d %H:%M")


def fire(a, now=None):
    t = datetime.datetime.fromtimestamp(now or time.time(), zone(a.get("tz")))
    a["fired"] = t.strftime("%Y-%m-%d %H:%M")
    if not a["days"]:
        a["on"] = False                                  # a one-off alarm switches itself off
    save()
    u = core.users.USERS.get(a["user"]) or core.users.admin_user() or core.users.OPEN

    def go():
        try:
            play(a["what"], a["speaker"], a["volume"], a["ramp"])
            core.log(f"alarm {a['time']}: playing {a['what'].get('name')} on {a.get('speaker_name')}")
        except Exception as ex:  # noqa: BLE001
            core.log("alarm failed:", ex)
            core.notify_people.send(a["user"], f"Your {a['time']} alarm couldn't play: {ex}", "test", "Alarm")
    core.subsonic.as_person(u, go)


def tick(now=None):
    now = now or time.time()
    for a in list(DATA["alarms"].values()):
        if due(a, now):
            fire(a, now)


def loop():
    while True:
        time.sleep(20 - time.time() % 20)
        try:
            tick()
        except Exception as ex:  # noqa: BLE001
            core.log("alarms:", ex)


def view(a):
    return {k: a.get(k) for k in ("id", "time", "days", "what", "speaker", "speaker_name", "volume", "ramp", "on", "tz")}


# --------------------------------------------------------------------------- hooks (Home Assistant automations)
def new_hook():
    key = secrets.token_urlsafe(24)
    me = uid()
    with LOCK:
        DATA["hooks"] = {h: u for h, u in DATA["hooks"].items() if u != me}
        DATA["hooks"][hashlib.sha256(key.encode()).hexdigest()] = me
    save()
    return {"key": key}


def hook(handler, method):
    """-> True when this was a hook address (answered here, no sign-in)."""
    u = urllib.parse.urlparse(handler.path)
    m = re.match(r"^/api/hook/([\w-]{20,64})/(play|stop|pause|resume|next)$", u.path)
    if not m:
        return False
    who = DATA["hooks"].get(hashlib.sha256(m.group(1).encode()).hexdigest())
    if not who:
        handler.send_json({"error": "Unknown hook"}, 401)
        return True
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    if method == "POST":
        try:
            n = int(handler.headers.get("Content-Length") or 0)
            q.update(json.loads(handler.rfile.read(n) or b"{}") if n else {})
        except ValueError:
            pass
    person = core.users.USERS.get(who) or (core.users.OPEN if who == "open" else None)
    if not person or person.get("disabled"):
        handler.send_json({"error": "Unknown hook"}, 401)
        return True
    core.users.LOCAL.user = person
    try:
        sp = q.get("speaker", "")
        if not re.match(r"^media_player\.\w+$", sp):
            raise ValueError("speaker=media_player.<name>")
        if m.group(2) == "play":
            what = {"kind": q.get("what") or "radio", "id": q.get("id", ""), "q": q.get("q", ""), "name": q.get("q") or q.get("id", "")}
            st = play(what, sp, float(q.get("volume") or 0.3), float(q.get("ramp") or 0))
            handler.send_json({"ok": True, "playing": (st.get("item") or {}).get("title")})
        else:
            handler.send_json(core.cast.control(sp, m.group(2), {}))
    except (ValueError, LookupError) as ex:
        handler.send_json({"error": str(ex)}, 400)
    except Exception as ex:  # noqa: BLE001
        core.log("hook failed:", ex)
        handler.send_json({"error": str(ex)}, 500)
    finally:
        core.users.LOCAL.user = None
    return True


# --------------------------------------------------------------------------- web
def handle_get(p, _qs):
    if p == "/api/alarms":
        me = uid()
        return {"alarms": [view(a) for a in DATA["alarms"].values() if a.get("user") == me],
                "hook": me in DATA["hooks"].values(), "ha": bool(core.cast.CFG["url"] and core.cast.CFG["token"])}
    return None


def handle_post(p, b):
    if p == "/api/alarms":
        aid = b.get("id")
        old = DATA["alarms"].get(aid) if aid else None
        if aid and not mine(old):
            raise LookupError("Alarm not found")
        a = clean(b, old)
        a.setdefault("id", uuid.uuid4().hex[:10])
        a["user"] = uid()
        with LOCK:
            DATA["alarms"][a["id"]] = a
        save()
        return view(a)
    if p == "/api/alarms/hook":
        return new_hook()
    m = re.match(r"^/api/alarms/([0-9a-f]{10})/(delete|test)$", p)
    if not m:
        return None
    a = DATA["alarms"].get(m.group(1))
    if not mine(a):
        raise LookupError("Alarm not found")
    if m.group(2) == "delete":
        with LOCK:
            DATA["alarms"].pop(a["id"], None)
        save()
        return {"ok": True}
    st = play(a["what"], a["speaker"], a["volume"], 0)
    return {"ok": True, "playing": (st.get("item") or {}).get("title")}


def init(umculo):
    global core
    core = umculo
    load()
    threading.Thread(target=loop, daemon=True).start()
