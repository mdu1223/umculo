"""Play on other things: AirPlay and Chromecast from the browser, and any speaker Home Assistant knows.

Speakers and TVs fetch the music themselves and can't sign in, so the player hands them addresses that
carry a stream key: good for 12 hours, for one person, and only for playing (songs, covers, videos).

GET  /api/cast/key                     -> {"key", "exp"}  the stream key for this person
GET  /api/speakers                     -> {"ready", "speakers": [{"id", "name", "state", "volume", "mine"}]}
GET  /api/speakers/<entity>            -> what Umculo is playing there
POST /api/speakers/<entity>/play       {items: [...], start, position}   Umculo plays the queue there, song after song
POST /api/speakers/<entity>/(pause|resume|next|prev|stop|volume|seek)   {v}
GET/POST /api/ha                       (admin) Home Assistant address and token; the token is never shown again
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request

core = None
LOCK = threading.Lock()
KEY_PATHS = re.compile(r"^/api/(ndstream|stream|ndcover|video)/")
TTL = 12 * 3600
SESS = {}                      # entity -> {"user", "items", "i", "started", "paused", "at"}
CFG = {"url": "", "token": "", "media_base": ""}


# --------------------------------------------------------------------------- stream keys
def secret():
    p = os.path.join(core.CONFIG["config"], "stream.key")
    if not os.path.exists(p):
        with open(p, "w") as fh:
            fh.write(secrets.token_hex(32))
        os.chmod(p, 0o600)
    with open(p) as fh:
        return fh.read().strip().encode()


def make_key(user_id, ttl=TTL):
    exp = int(time.time() + ttl)
    sig = hmac.new(secret(), f"{user_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{exp}.{sig}"


def user_for_key(key, path):
    """The person a stream key belongs to, when it's valid for this address; else None."""
    if not key or not KEY_PATHS.match(path or ""):
        return None
    try:
        uid, exp, sig = key.rsplit(".", 2)
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    good = hmac.new(secret(), f"{uid}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(good, sig):
        return None
    if uid == "open":                                  # only while there are no accounts at all
        return None if core.users.USERS else core.users.OPEN
    u = core.users.USERS.get(uid)
    return u if u and not u.get("disabled") else None


def key_view():
    u = core.users.current()
    k = make_key(u["id"] if u else "open")
    return {"key": k, "exp": int(k.split(".")[1])}


# --------------------------------------------------------------------------- Home Assistant
def cfg_path():
    return os.path.join(core.CONFIG["config"], "homeassistant.json")


def load():
    CFG.update(url=os.environ.get("HA_URL", ""), token=os.environ.get("HA_TOKEN", ""), media_base=os.environ.get("HA_MEDIA_BASE", ""))
    try:
        with open(cfg_path()) as fh:
            CFG.update({k: v for k, v in json.load(fh).items() if v})
    except (OSError, ValueError):
        pass


def save_cfg(b):
    data = {}
    try:
        with open(cfg_path()) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        pass
    for k in ("url", "media_base"):
        if k in b:
            data[k] = (b.get(k) or "").strip().rstrip("/")
    if b.get("token"):
        data["token"] = b["token"].strip()
    if b.get("forget"):
        data = {}
    with open(cfg_path(), "w") as fh:
        json.dump(data, fh)
    os.chmod(cfg_path(), 0o600)
    CFG.update(url="", token="", media_base="")
    load()
    return ha_view()


def ha_view():
    ok, msg = False, "Not set up"
    if CFG["url"] and CFG["token"]:
        try:
            ha("GET", "/api/")
            ok, msg = True, "Connected"
        except Exception as ex:  # noqa: BLE001
            msg = f"Can't reach Home Assistant: {str(ex)[:120]}"
    return {"url": CFG["url"], "token_set": bool(CFG["token"]), "media_base": CFG["media_base"], "ok": ok, "message": msg}


def ha(method, path, body=None, timeout=10):
    if not (CFG["url"] and CFG["token"]):
        raise LookupError("Home Assistant isn't connected (Settings → Speakers)")
    rq = urllib.request.Request(CFG["url"].rstrip("/") + path, method=method,
                                data=json.dumps(body).encode() if body is not None else None,
                                headers={"Authorization": "Bearer " + CFG["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def service(domain, svc, data):
    return ha("POST", f"/api/services/{domain}/{svc}", data)


def speakers():
    if not (CFG["url"] and CFG["token"]):
        return {"ready": False, "speakers": []}
    out, off = [], []
    for s in ha("GET", "/api/states"):
        e = s.get("entity_id", "")
        if not e.startswith("media_player."):
            continue
        if s.get("state") == "unavailable":
            off.append((s.get("attributes") or {}).get("friendly_name") or e)
            continue
        a = s.get("attributes") or {}
        out.append({"id": e, "name": a.get("friendly_name") or e.split(".", 1)[1], "state": s.get("state"),
                    "volume": a.get("volume_level"), "mine": e in SESS})
    return {"ready": True, "speakers": sorted(out, key=lambda x: x["name"].lower()), "unavailable": sorted(off)}


def base_url():
    return (CFG["media_base"] or core.CONFIG.get("public_url") or "").rstrip("/")


def media_url(it, key):
    base = base_url()
    if not base:
        raise ValueError("Set the address speakers use to reach Umculo (Settings → Speakers)")
    if it.get("nd_id"):
        return f"{base}/api/ndstream/{it['nd_id']}?speaker=1&k={key}"
    if it.get("deezer_id"):
        return f"{base}/api/stream/{it['deezer_id']}?k={key}"
    raise ValueError("That song can't be played on a speaker")


def cover_url(it, key):
    c = it.get("cover_big") or it.get("cover") or ""
    if c.startswith("/"):
        return f"{base_url()}{c}{'&' if '?' in c else '?'}k={key}"
    return c


def play_item(entity):
    s = SESS[entity]
    it = s["items"][s["i"]]
    key = make_key(s["user"])
    service("media_player", "play_media", {
        "entity_id": entity, "media_content_id": media_url(it, key), "media_content_type": "music",
        "extra": {"title": it.get("title", ""), "thumb": cover_url(it, key),
                  "metadata": {"metadataType": 3, "title": it.get("title", ""), "artist": it.get("artist", ""),
                               "albumName": it.get("album", ""), "images": [{"url": cover_url(it, key)}]}}})
    s.update(started=time.time(), paused=False, seen_playing=False)
    if s.get("seek"):
        time.sleep(1.5)
        try:
            service("media_player", "media_seek", {"entity_id": entity, "seek_position": s.pop("seek")})
        except Exception:  # noqa: BLE001
            pass


def start(entity, b):
    items = [x for x in (b.get("items") or []) if x.get("nd_id") or x.get("deezer_id")][:500]
    if not items:
        raise ValueError("Nothing to play")
    u = core.users.current()
    with LOCK:
        SESS[entity] = {"user": u["id"] if u else "open", "items": items, "i": max(0, min(len(items) - 1, int(b.get("start") or 0))),
                        "seek": float(b.get("position") or 0) or None}
    if b.get("volume") is not None:
        service("media_player", "volume_set", {"entity_id": entity, "volume_level": float(b["volume"])})
    play_item(entity)
    ensure_loop()
    return session_view(entity)


def control(entity, action, b):
    s = SESS.get(entity)
    if action == "volume":
        service("media_player", "volume_set", {"entity_id": entity, "volume_level": max(0.0, min(1.0, float(b.get("v") or 0)))})
        return {"ok": True}
    if not s:
        raise LookupError("Umculo isn't playing on that speaker")
    if action == "pause":
        service("media_player", "media_pause", {"entity_id": entity})
        s["paused"] = True
    elif action == "resume":
        service("media_player", "media_play", {"entity_id": entity})
        s["paused"] = False
    elif action == "seek":
        service("media_player", "media_seek", {"entity_id": entity, "seek_position": float(b.get("v") or 0)})
    elif action in ("next", "prev"):
        s["i"] = max(0, min(len(s["items"]) - 1, s["i"] + (1 if action == "next" else -1)))
        play_item(entity)
    elif action == "stop":
        try:
            service("media_player", "media_stop", {"entity_id": entity})
        finally:
            SESS.pop(entity, None)
    return session_view(entity) if entity in SESS else {"ok": True, "stopped": True}


def session_view(entity):
    s = SESS.get(entity)
    if not s:
        return {"playing": False}
    pos = 0
    try:
        st = ha("GET", f"/api/states/{entity}")
        a = st.get("attributes") or {}
        pos = a.get("media_position") or 0
        if st.get("state") == "playing" and a.get("media_position_updated_at"):
            import datetime
            t = datetime.datetime.fromisoformat(a["media_position_updated_at"]).timestamp()
            pos += max(0, time.time() - t)
        vol = a.get("volume_level")
    except Exception:  # noqa: BLE001
        vol = None
    return {"playing": not s.get("paused"), "i": s["i"], "item": s["items"][s["i"]], "count": len(s["items"]),
            "position": pos, "volume": vol, "next": s["items"][s["i"] + 1] if s["i"] + 1 < len(s["items"]) else None}


_LOOP = {"on": False}


def ensure_loop():
    with LOCK:
        if _LOOP["on"]:
            return
        _LOOP["on"] = True
    threading.Thread(target=loop, daemon=True).start()


def loop():
    """Song after song: when the speaker finishes one (it goes idle), send the next."""
    while True:
        time.sleep(2)
        with LOCK:
            ents = list(SESS)
        if not ents:
            with LOCK:
                if not SESS:
                    _LOOP["on"] = False
                    return
            continue
        for e in ents:
            s = SESS.get(e)
            if not s or s.get("paused"):
                continue
            try:
                st = ha("GET", f"/api/states/{e}").get("state")
            except Exception:  # noqa: BLE001
                continue
            if st == "playing":
                s["seen_playing"] = True
            elif st in ("idle", "off", "standby", "paused") and (s.get("seen_playing") or time.time() - s["started"] > 25):
                if st == "paused" and not s.get("seen_playing"):
                    continue
                if st == "paused" or st == "off":              # paused or switched off at the speaker: follow it
                    s["paused"] = True
                    continue
                if s["i"] + 1 < len(s["items"]):
                    s["i"] += 1
                    try:
                        play_item(e)
                    except Exception as ex:  # noqa: BLE001
                        core.log("speaker: couldn't play the next song on", e, ex)
                else:
                    SESS.pop(e, None)


# --------------------------------------------------------------------------- web
def handle_get(path, _qs):
    if path == "/api/cast/key":
        return key_view()
    if path == "/api/speakers":
        return speakers()
    if path == "/api/ha":
        if not core.users.is_admin():
            raise PermissionError("Only the admin can set up Home Assistant")
        return ha_view()
    m = re.match(r"^/api/speakers/(media_player\.[\w]+)$", path)
    if m:
        return session_view(m.group(1))
    return None


def handle_post(path, b):
    if path == "/api/ha":
        if not core.users.is_admin():
            raise PermissionError("Only the admin can set up Home Assistant")
        return save_cfg(b)
    m = re.match(r"^/api/speakers/(media_player\.[\w]+)/(play|pause|resume|next|prev|stop|volume|seek)$", path)
    if not m:
        return None
    if m.group(2) == "play":
        return start(m.group(1), b)
    return control(m.group(1), m.group(2), b)


def init(umculo):
    global core
    core = umculo
    load()
