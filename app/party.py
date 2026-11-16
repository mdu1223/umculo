"""Listen together: everyone in the room hears the same song at the same moment, on their own phone.

The person who starts the room is the DJ: their player leads (song, position, play/pause) and everyone
follows. Anyone in the room can add songs, to play next or at the end. Songs from the DJ's library
play for guests too, with the DJ's stream key (for playing only, while the room is open).

POST /api/party                         {queue, i, position, playing}   -> {"code"}   start a room
GET  /api/party/<code>                  -> the room: queue, i, position (now), playing, members, ver
POST /api/party/<code>/join | leave
POST /api/party/<code>/state            (the DJ) {queue?, i, position, playing}
POST /api/party/<code>/add              {item, next}
POST /api/party/<code>/end              (the DJ)
"""
import random
import re
import threading
import time

core = None
LOCK = threading.Lock()
ROOMS = {}
IDLE = 6 * 3600


def me():
    u = core.users.current()
    return (u or {}).get("id", "open"), (u or {}).get("name") or (u or {}).get("username") or "Someone"


def clean_item(x):
    keep = ("nd_id", "deezer_id", "title", "artist", "album", "album_id", "cover", "cover_big", "duration", "year")
    it = {k: x.get(k) for k in keep if x.get(k) not in (None, "")}
    if not (it.get("nd_id") or it.get("deezer_id")):
        raise ValueError("That song can't be shared")
    return it


def room(code):
    r = ROOMS.get((code or "").upper())
    if not r or time.time() - r["touched"] > IDLE:
        raise LookupError("That room has closed")
    return r


def view(r):
    uid, _n = me()
    if uid == r["host"]:
        r["pending"] = []                             # the DJ's player takes the room's queue as it is now
    now = time.time()
    pos = r["position"] + (now - r["at"]) * r.get("rate", 1) if r["playing"] else r["position"]
    members = [{"name": m["name"], "dj": m["id"] == r["host"], "you": m["id"] == uid} for m in r["members"].values()
               if now - m["seen"] < 30]
    return {"code": r["code"], "queue": r["queue"], "i": r["i"], "position": pos, "playing": r["playing"], "ver": r["ver"],
            "dj": r["host"] == uid, "dj_name": r["host_name"], "members": members, "key": r["key"], "server_time": now,
            "added": r["added"][-5:]}


def touch(r):
    uid, name = me()
    r["members"][uid] = {"id": uid, "name": name, "seen": time.time()}
    r["touched"] = time.time()


def create(b):
    uid, name = me()
    queue = [clean_item(x) for x in (b.get("queue") or [])][:500]
    with LOCK:
        for code in [c for c, r in ROOMS.items() if time.time() - r["touched"] > IDLE or r["host"] == uid]:
            ROOMS.pop(code, None)                     # one room per DJ; old rooms close
        code = "".join(random.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
        r = {"code": code, "host": uid, "host_name": name, "queue": queue, "i": max(0, int(b.get("i") or 0)),
             "position": float(b.get("position") or 0), "playing": bool(b.get("playing", True)), "at": time.time(),
             "rate": float(b.get("rate") or 1), "ver": 1, "members": {}, "touched": time.time(), "added": [],
             "key": core.cast.make_key(uid, IDLE)}
        touch(r)
        ROOMS[code] = r
    return view(r)


def set_state(r, b):
    uid, _n = me()
    if uid != r["host"]:
        raise PermissionError("Only the DJ moves the music; you can add songs")
    if "queue" in b:
        q = [clean_item(x) for x in b["queue"]][:500]
        ident = lambda x: (x.get("nd_id"), x.get("deezer_id"))
        have = {ident(x) for x in q}
        at = max(0, min(len(q), int(b.get("i", r["i"])) + 1))
        for it in reversed([x for x in r.get("pending", []) if ident(x) not in have]):
            q.insert(at, it)                          # songs a guest added that the DJ's player hasn't seen yet
        r["queue"] = q
    r["i"] = max(0, min(len(r["queue"]) - 1, int(b.get("i", r["i"]))))
    r["position"] = float(b.get("position") or 0)
    r["playing"] = bool(b.get("playing"))
    r["rate"] = float(b.get("rate") or 1)
    r["at"] = time.time()
    r["ver"] += 1


def add(r, b):
    it = clean_item(b.get("item") or {})
    _uid, name = me()
    at = r["i"] + 1 if b.get("next") else len(r["queue"])
    r["queue"].insert(at, it)
    r.setdefault("pending", []).append(it)
    r["added"].append({"by": name, "title": it.get("title", ""), "at": time.time()})
    r["ver"] += 1
    return at


def handle_get(path, _qs):
    m = re.match(r"^/api/party/([A-Za-z0-9]{5})$", path)
    if not m:
        return None
    with LOCK:
        r = room(m.group(1))
        touch(r)
        return view(r)


def handle_post(path, b):
    if path == "/api/party":
        return create(b)
    m = re.match(r"^/api/party/([A-Za-z0-9]{5})/(join|leave|state|add|end)$", path)
    if not m:
        return None
    with LOCK:
        r = room(m.group(1))
        act = m.group(2)
        uid, _n = me()
        if act == "join":
            touch(r)
        elif act == "leave":
            r["members"].pop(uid, None)
            return {"ok": True}
        elif act == "state":
            touch(r)
            set_state(r, b)
        elif act == "add":
            touch(r)
            add(r, b)
        elif act == "end":
            if uid != r["host"]:
                raise PermissionError("Only the DJ can close the room")
            ROOMS.pop(r["code"], None)
            return {"ok": True, "ended": True}
        return view(r)


def init(umculo):
    global core
    core = umculo
