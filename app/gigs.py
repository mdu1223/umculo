"""Gigs near you: upcoming shows by artists you follow or play a lot, from Bandsintown.

Where you are comes from your notification settings: a city and country, or your location (with a distance).
Checked once a day; a new gig near you is a message (WhatsApp / ntfy) once.

GET  /api/gigs            -> {"gigs": [{"id", "artist", "date", "venue", "city", "country", "km", "url", "tickets"}],
                              "where": {...}, "checked"}
POST /api/gigs/refresh    -> look again now
"""
import json
import math
import os
import threading
import time
import urllib.parse
import urllib.error
import urllib.request

core = None
LOCK = threading.Lock()
CACHE = {}                 # artist (lower) -> (fetched at, events)
STATE = {"checked": {}}    # user id -> last check
API = os.environ.get("GIGS_API", "https://rest.bandsintown.com")
APP_ID = os.environ.get("GIGS_APP_ID", "umculo")
REFUSED = {"at": 0, "why": ""}   # Bandsintown said no to our app ID: don't ask for every artist, say why instead


def km(a, b, c, d):
    r = 6371
    p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(x))


def events(artist):
    k = artist.lower()
    hit = CACHE.get(k)
    if hit and time.time() - hit[0] < 20 * 3600:
        return hit[1]
    if REFUSED["at"] and time.time() - REFUSED["at"] < 6 * 3600:
        return []
    url = f"{API}/artists/{urllib.parse.quote(artist, safe='')}/events?app_id={APP_ID}&date=upcoming"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=15) as r:
            data = json.loads(r.read() or b"[]")
    except urllib.error.HTTPError as ex:
        if ex.code in (401, 403):
            if not REFUSED["at"]:
                core.log(f"gigs: off for now, Bandsintown wants its own app ID (HTTP {ex.code}); set GIGS_APP_ID to turn gigs on")
            REFUSED.update(at=time.time(), why="Bandsintown isn't answering Umculo's requests (it now wants its own app ID). "
                           "Set GIGS_APP_ID to a key from Bandsintown to turn gigs back on.")
            return []
        core.log("gigs: couldn't check", artist, ex)
        data = []
    except Exception as ex:  # noqa: BLE001
        core.log("gigs: couldn't check", artist, ex)
        data = []
    if not isinstance(data, list):                 # Bandsintown answers {"errorMessage": ...} for unknown artists
        data = []
    CACHE[k] = (time.time(), data)
    return data


def artists_for(user_id):
    """Who to look up: artists you follow, then the ones you play most."""
    u = core.users.USERS.get(user_id) or core.users.OPEN
    names = []
    mine = core.users.me_key(u)
    for f in list(core.artists.FOLLOWS.values()):
        if mine in core.artists.fans(f) and f.get("name"):
            names.append(f["name"])
    srv = core.server
    for r in srv.db.q("""SELECT artist, COUNT(*) AS n FROM listen WHERE user=? AND at > ? AND artist != ''
                         GROUP BY artist ORDER BY n DESC LIMIT 25""", (srv.uid(u), time.time() - 180 * 86400)):
        a = core.musicdb.primary(r["artist"])
        if a and a.lower() not in {x.lower() for x in names}:
            names.append(a)
    return names[:40]


def near(ev, p):
    v = ev.get("venue") or {}
    if p.get("lat") is not None and p.get("lon") is not None and v.get("latitude"):
        try:
            d = km(float(p["lat"]), float(p["lon"]), float(v["latitude"]), float(v["longitude"]))
        except (TypeError, ValueError):
            return None
        return d if d <= float(p.get("radius") or 150) else None
    if p.get("country") and (v.get("country") or "").lower() != p["country"].lower():
        return None
    if p.get("city") and (v.get("city") or "").lower() != p["city"].lower():
        return None
    return 0 if (p.get("country") or p.get("city")) else None


def gigs_for(user_id):
    p = core.notify_people.prefs(user_id)
    out = []
    for a in artists_for(user_id):
        for ev in events(a):
            d = near(ev, p)
            if d is None:
                continue
            v = ev.get("venue") or {}
            offers = [o.get("url") for o in ev.get("offers") or [] if o.get("url")]
            out.append({"id": str(ev.get("id")), "artist": a, "date": ev.get("datetime", ""), "venue": v.get("name", ""),
                        "city": v.get("city", ""), "country": v.get("country", ""), "km": round(d) if d else None,
                        "url": ev.get("url", ""), "tickets": offers[0] if offers else "", "lineup": ev.get("lineup") or [a]})
    seen, uniq = set(), []
    for g in sorted(out, key=lambda g: g["date"]):
        if g["id"] not in seen:
            seen.add(g["id"])
            uniq.append(g)
    return uniq


def tell(user_id, gigs):
    """Message once per gig."""
    p = core.notify_people.prefs(user_id)
    sent = set(p.get("gigs_sent") or [])
    new = [g for g in gigs if g["id"] not in sent]
    if not new:
        return 0
    for g in new[:5]:
        when = g["date"][:16].replace("T", " ")
        core.notify_people.send(user_id, f"{g['artist']} · {when}\n{g['venue']}, {g['city']}" + (f" ({g['km']} km)" if g.get("km") else ""),
                                "gigs", "Gig near you", g.get("tickets") or g.get("url"))
    with core.notify_people.LOCK:
        pr = core.notify_people.PREFS.setdefault(user_id, {})
        pr["gigs_sent"] = list(sent | {g["id"] for g in new})[-500:]
    core.notify_people.save()
    return len(new)


def check(user_id, message=True):
    g = gigs_for(user_id)
    STATE["checked"][user_id] = time.time()
    if message:
        tell(user_id, g)
    return g


def loop():
    time.sleep(120)
    while True:
        for user_id in list(core.notify_people.PREFS):
            p = core.notify_people.prefs(user_id)
            if (p.get("country") or p.get("lat") is not None) and time.time() - STATE["checked"].get(user_id, 0) > 86400:
                try:
                    core.subsonic.as_person(core.users.USERS.get(user_id) or core.users.admin_user(), check, user_id)
                except Exception as ex:  # noqa: BLE001
                    core.log("gigs check failed:", user_id, ex)
        time.sleep(3600)


def handle_get(path, _qs):
    if path != "/api/gigs":
        return None
    i = core.notify_people.uid()
    p = core.notify_people.prefs(i)
    where = {k: p.get(k) for k in ("city", "country", "lat", "lon", "radius")}
    if not (p.get("country") or p.get("city") or p.get("lat") is not None):
        return {"gigs": [], "where": where, "needs_place": True}
    gigs = check(i, message=False)
    return {"gigs": gigs, "where": where, "checked": STATE["checked"].get(i),
            "error": REFUSED["why"] if REFUSED["at"] and not gigs else ""}


def handle_post(path, _b):
    if path != "/api/gigs/refresh":
        return None
    i = core.notify_people.uid()
    for a in artists_for(i):
        CACHE.pop(a.lower(), None)
    return {"gigs": check(i, message=True)}


def init(umculo):
    global core
    core = umculo
    threading.Thread(target=loop, daemon=True).start()
