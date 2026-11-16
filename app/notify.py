"""Messages to each person: WhatsApp (through WAHA) and/or their own ntfy topic.

What they can get:  new_mix  - a new mix from a show they subscribe to is in (with its tracklist count)
                    weekly   - Sunday evening: their week in music and the new mixes
                    gigs     - a gig near them by an artist they follow or play a lot
                    ready    - a song or album they asked for is in

GET  /api/notify            -> {"prefs": {...}, "whatsapp_ready", "ntfy_ready"}
POST /api/notify            {whatsapp, ntfy_topic, events: {...}, tz, city, country, lat, lon, radius}
POST /api/notify/test       -> sends a test to every channel you set
GET/POST /api/notify/whatsapp   (admin) WAHA address, API key and session; the key is never shown again
"""
import datetime
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request

core = None
LOCK = threading.Lock()
PREFS = {}                       # user id -> prefs
WA = {"url": "", "key": "", "session": "default"}
EVENTS = {"new_mix": True, "weekly": True, "gigs": True, "ready": False}


def path(name):
    return os.path.join(core.CONFIG["config"], name)


def load():
    WA.update(url=os.environ.get("WAHA_URL", ""), key=os.environ.get("WAHA_KEY", ""), session=os.environ.get("WAHA_SESSION", "default"))
    for name, target in (("notify.json", PREFS), ("whatsapp.json", WA)):
        try:
            with open(path(name)) as fh:
                target.update({k: v for k, v in json.load(fh).items() if v not in (None, "")})
        except (OSError, ValueError):
            pass


def save():
    with LOCK:
        data = json.dumps(PREFS)
    tmp = path("notify.json") + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(data)
    os.replace(tmp, path("notify.json"))


def uid():
    u = core.users.current()
    return (u or {}).get("id", "open")


def prefs(user_id=None):
    p = dict(PREFS.get(user_id or uid()) or {})
    p["events"] = {**EVENTS, **(p.get("events") or {})}
    return p


def tz(user_id):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(prefs(user_id).get("tz") or os.environ.get("TZ") or "Africa/Johannesburg")
    except Exception:  # noqa: BLE001
        return datetime.timezone.utc


def ntfy_base():
    u = urllib.parse.urlparse(core.CONFIG.get("ntfy") or "")
    return f"{u.scheme}://{u.netloc}" if u.scheme else ""


# --------------------------------------------------------------------------- sending
def whatsapp(number, text):
    if not (url_ok() and number):
        return False
    chat = re.sub(r"\D", "", number) + "@c.us"
    rq = urllib.request.Request(WA["url"].rstrip("/") + "/api/sendText", method="POST",
                                data=json.dumps({"chatId": chat, "text": text, "session": WA["session"] or "default"}).encode(),
                                headers={"Content-Type": "application/json", **({"X-Api-Key": WA["key"]} if WA["key"] else {})})
    with urllib.request.urlopen(rq, timeout=20) as r:
        r.read()
    return True


def ntfy(topic, text, title="Umculo", click=""):
    base = ntfy_base()
    if not (base and topic):
        return False
    h = {"Content-Type": "text/plain", "Title": title}
    if click:
        h["Click"] = click
    rq = urllib.request.Request(f"{base}/{urllib.parse.quote(topic)}", data=text.encode(), method="POST", headers=h)
    with urllib.request.urlopen(rq, timeout=15) as r:
        r.read()
    return True


def send(user_id, text, kind, title="Umculo", click=""):
    """-> list of channels it went to. Quietly does nothing for people who switched that kind off."""
    p = prefs(user_id)
    if kind != "test" and not p["events"].get(kind):
        return []
    out = []
    for ch, fn in (("whatsapp", lambda: whatsapp(p.get("whatsapp"), f"*{title}*\n{text}" + (f"\n{click}" if click else ""))),
                   ("ntfy", lambda: ntfy(p.get("ntfy_topic"), text, title, click))):
        try:
            if fn():
                out.append(ch)
        except Exception as ex:  # noqa: BLE001
            core.log(f"notify: {ch} to {user_id} failed:", ex)
    return out


def owner_id(owner_key):
    """Subscriptions keep an owner key ("admin" or a user id): -> a user id."""
    if owner_key in (None, "", "admin"):
        a = core.users.admin_user()
        return a["id"] if a else "open"
    return owner_key


def app_link():
    return (core.CONFIG.get("public_url") or "").rstrip("/")


# --------------------------------------------------------------------------- the weekly note
def weekly_text(user_id):
    srv = core.server
    u = core.users.USERS.get(user_id) or core.users.OPEN
    week = time.time() - 7 * 86400
    root = core.CONFIG["mixes"].rstrip("/") + "/"
    mixes = srv.db.q(f"""SELECT title, album FROM song WHERE created > ? AND substr(path, 1, ?) = ? AND {srv.lib_sql(u)}
                         ORDER BY created DESC LIMIT 6""", (week, len(root), root))
    who = srv.uid(u)
    mins = srv.db.q("SELECT SUM(secs) AS s, COUNT(*) AS n FROM listen WHERE user=? AND at > ?", (who, week), one=True)
    top = srv.db.q("""SELECT artist, COUNT(*) AS n FROM listen WHERE user=? AND at > ? AND artist != ''
                      GROUP BY artist ORDER BY n DESC LIMIT 3""", (who, week))
    lines = []
    if mins and mins["s"]:
        lines.append(f"You listened for {round(mins['s'] / 60)} minutes this week ({mins['n']} songs).")
    if top:
        lines.append("Most played: " + ", ".join(t["artist"] for t in top) + ".")
    if mixes:
        lines.append("New mixes:\n" + "\n".join(f"• {m['title']} ({m['album']})" for m in mixes))
    lines.append("Your Weekly mix is fresh in For You.")
    return "\n".join(lines)


def weekly_due(user_id, now=None):
    p = prefs(user_id)
    if not p["events"].get("weekly") or not (p.get("whatsapp") or p.get("ntfy_topic")):
        return False
    t = datetime.datetime.fromtimestamp(now or time.time(), tz(user_id))
    week = t.strftime("%G-%V")
    return t.weekday() == 6 and t.hour >= 18 and p.get("weekly_sent") != week


def tick(now=None):
    """Every few minutes: the weekly notes that are due."""
    for user_id in list(PREFS):
        try:
            if weekly_due(user_id, now):
                send(user_id, weekly_text(user_id), "weekly", "Your week on Umculo", app_link())
                with LOCK:
                    PREFS[user_id]["weekly_sent"] = datetime.datetime.fromtimestamp(now or time.time(), tz(user_id)).strftime("%G-%V")
                save()
        except Exception as ex:  # noqa: BLE001
            core.log("weekly note failed:", user_id, ex)


def loop():
    while True:
        time.sleep(300)
        tick()


# --------------------------------------------------------------------------- web
def view():
    p = prefs()
    p.pop("weekly_sent", None)
    p.pop("gigs_sent", None)
    return {"prefs": p, "whatsapp_ready": url_ok(), "ntfy_ready": bool(ntfy_base())}


def set_prefs(b):
    i = uid()
    with LOCK:
        p = PREFS.setdefault(i, {})
        if "whatsapp" in b:
            n = re.sub(r"\D", "", b.get("whatsapp") or "")
            if n.startswith("0") and len(n) == 10:            # 082 123 4567 -> 2782 123 4567
                n = "27" + n[1:]
            p["whatsapp"] = n
        if "ntfy_topic" in b:
            p["ntfy_topic"] = re.sub(r"[^\w-]", "", b.get("ntfy_topic") or "")[:64]
        if isinstance(b.get("events"), dict):
            p["events"] = {k: bool(v) for k, v in b["events"].items() if k in EVENTS}
        for k in ("tz", "city", "country"):
            if k in b:
                p[k] = str(b.get(k) or "")[:80]
        for k in ("lat", "lon", "radius"):
            if k in b:
                try:
                    p[k] = float(b[k]) if b[k] not in (None, "") else None
                except (TypeError, ValueError):
                    pass
    save()
    return view()


def handle_get(path_, _qs):
    if path_ == "/api/notify":
        return view()
    if path_ == "/api/notify/whatsapp":
        if not core.users.is_admin():
            raise PermissionError("Only the admin can set up WhatsApp")
        return {"url": WA["url"], "session": WA["session"], "key_set": bool(WA["key"])}
    return None


def url_ok():
    return bool(re.match(r"^https?://", WA["url"] or ""))


def handle_post(path_, b):
    if path_ == "/api/notify":
        return set_prefs(b)
    if path_ == "/api/notify/test":
        got = send(uid(), "Messages from Umculo will look like this.", "test", "Umculo test", app_link())
        if not got:
            raise ValueError("Nothing was sent: add your WhatsApp number or an ntfy topic first (and check the connection)")
        return {"ok": True, "sent": got}
    if path_ == "/api/notify/whatsapp":
        if not core.users.is_admin():
            raise PermissionError("Only the admin can set up WhatsApp")
        data = {}
        try:
            with open(path("whatsapp.json")) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            pass
        for k in ("url", "session"):
            if k in b:
                data[k] = (b.get(k) or "").strip().rstrip("/")
        if data.get("url") and not re.match(r"^https?://", data["url"]):
            raise ValueError("The WAHA address starts with http:// (for example http://192.168.0.2:3000); the API key goes in its own box")
        if b.get("key"):
            data["key"] = b["key"].strip()
        if b.get("forget"):
            data = {}
        with open(path("whatsapp.json"), "w") as fh:
            json.dump(data, fh)
        os.chmod(path("whatsapp.json"), 0o600)
        WA.update(url="", key="", session="default")
        load()
        return {"url": WA["url"], "session": WA["session"], "key_set": bool(WA["key"])}
    return None


def init(umculo):
    global core
    core = umculo
    load()
    threading.Thread(target=loop, daemon=True).start()
