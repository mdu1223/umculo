"""v2.9: WhatsApp messages (pretend WAHA), gigs near you (pretend Bandsintown), alarms and hooks (pretend Home Assistant).
Usage: python3 test/v29_life.py PORT STATE_DIR   (the app runs with GIGS_API=http://127.0.0.1:PORT+3000)"""
import datetime, http.cookiejar, http.server, json, sys, threading, time, urllib.error, urllib.parse, urllib.request
PORT, S = int(sys.argv[1]), sys.argv[2]; BASE = f"http://localhost:{PORT}"; MOCK = f"http://127.0.0.1:{PORT + 3000}"; bad = []; n = 0
def check(c, what):
    global n; n += 1
    if not c: bad.append(what); print("FAIL", what)
def opener(): return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
A, X = opener(), opener()
def web(path, body=None, op=A):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        r = op.open(req, timeout=60); return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b"{}")
        except ValueError: return e.code, {}
M = {"wa": [], "ha": [], "state": "idle"}
EVENTS = {"kwesta": [
    {"id": "e1", "datetime": "2027-03-01T20:00:00", "url": "https://bit.example/e1", "offers": [{"url": "https://tix.example/e1"}],
     "venue": {"name": "Sun Arena", "city": "Johannesburg", "country": "South Africa", "latitude": "-26.04", "longitude": "28.06"}},
    {"id": "e2", "datetime": "2027-03-05T20:00:00", "url": "https://bit.example/e2", "offers": [],
     "venue": {"name": "Grand Arena", "city": "Cape Town", "country": "South Africa", "latitude": "-33.90", "longitude": "18.41"}},
    {"id": "e3", "datetime": "2027-04-01T20:00:00", "url": "https://bit.example/e3", "offers": [],
     "venue": {"name": "O2", "city": "London", "country": "United Kingdom", "latitude": "51.50", "longitude": "0.00"}}]}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj, code=200):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/artists/"):
            name = urllib.parse.unquote(u.path.split("/")[2]).lower()
            return self.send(EVENTS.get(name, {"errorMessage": "[NotFound] The artist was not found"}))
        st = {"entity_id": "media_player.kitchen", "state": M["state"], "attributes": {"friendly_name": "Kitchen", "volume_level": 0.2}}
        if u.path == "/api/": return self.send({"message": "API running."})
        if u.path == "/api/states": return self.send([st])
        if u.path == "/api/states/media_player.kitchen": return self.send(st)
        self.send({}, 404)
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/sendText": M["wa"].append((b, self.headers.get("X-Api-Key"))); return self.send({"id": "x"})
        M["ha"].append((self.path, b))
        if self.path.endswith("/play_media"): M["state"] = "playing"
        self.send([])
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT + 3000), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
web("/api/setup", {"username": "boss", "password": "bosspass", "name": "Boss"})
# WhatsApp
c, w = web("/api/notify/whatsapp", {"url": MOCK, "key": "k1", "session": "default"})
check(c == 200 and w["key_set"] and "key" not in w, f"WAHA connected; the key isn't sent back {w}")
c, r = web("/api/notify/test", {}); check(c == 400, "a test with no number says what's missing")
c, r = web("/api/notify", {"whatsapp": "082 123 4567", "events": {"gigs": True, "new_mix": True, "weekly": True, "ready": False}, "tz": "Africa/Johannesburg"})
check(r["prefs"]["whatsapp"] == "27821234567", f"a South African number gets its country code {r['prefs'].get('whatsapp')}")
c, r = web("/api/notify/test", {})
check(c == 200 and r["sent"] == ["whatsapp"] and M["wa"] and M["wa"][-1][0]["chatId"] == "27821234567@c.us" and M["wa"][-1][1] == "k1", f"test message on WhatsApp {M['wa'][-1:]}")
# gigs
c, g = web("/api/gigs"); check(g.get("needs_place"), "gigs ask where you are first")
songs = json.loads(urllib.request.urlopen(f"{BASE}/rest/search3.view?u=boss&p=bosspass&f=json&query=Ngud&songCount=3").read())["subsonic-response"]["searchResult3"]["song"]
ngud = next(x for x in songs if x["title"] == "Ngud")
web("/api/listen", {"kind": "lib", "id": ngud["id"], "played": 6, "duration": 6})
c, r = web("/api/notify", {"city": "", "country": "South Africa"})
c, g = web("/api/gigs"); ids = [x["id"] for x in g.get("gigs", [])]
check(ids == ["e1", "e2"], f"by country: Johannesburg and Cape Town, not London {ids}")
c, r = web("/api/notify", {"country": "", "lat": "-26.2", "lon": "28.05", "radius": "150"})
c, g = web("/api/gigs"); gg = g.get("gigs", [])
check([x["id"] for x in gg] == ["e1"] and gg[0]["km"] and gg[0]["km"] < 30 and gg[0]["tickets"] == "https://tix.example/e1", f"by distance: only the one nearby, with tickets {gg}")
before = len(M["wa"]); web("/api/gigs/refresh", {})
check(len(M["wa"]) == before + 1 and "Kwesta" in M["wa"][-1][0]["text"], "a message about the new gig")
web("/api/gigs/refresh", {}); check(len(M["wa"]) == before + 1, "only once per gig")
# alarms
c, r = web("/api/alarms"); check(c == 200 and r["alarms"] == [] and not r["ha"], "no alarms yet; Home Assistant not connected")
web("/api/ha", {"url": MOCK, "token": "t", "media_base": BASE})
c, r = web("/api/alarms", {"time": "25:00", "what": {"kind": "radio", "q": "hip hop"}, "speaker": "media_player.kitchen"}); check(c == 400, "a bad time is refused")
c, a = web("/api/alarms", {"time": "06:30", "days": [0, 1, 2, 3, 4], "what": {"kind": "radio", "q": "hip hop", "name": "Hip hop"},
                           "speaker": "media_player.kitchen", "speaker_name": "Kitchen", "volume": 0.4, "ramp": 5, "tz": "Africa/Johannesburg"})
check(c == 200 and a["time"] == "06:30" and a["on"], "an alarm is set")
c, t = web(f"/api/alarms/{a['id']}/test", {})
pm = [b for p, b in M["ha"] if p.endswith("/play_media")]
check(c == 200 and pm and "/api/ndstream/" in pm[-1]["media_content_id"], f"testing it plays on the speaker {t}")
now = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=1)
c, once = web("/api/alarms", {"time": now.strftime("%H:%M"), "days": [], "what": {"kind": "radio", "q": "amapiano", "name": "Amapiano"},
                              "speaker": "media_player.kitchen", "volume": 0.5, "ramp": 0, "tz": "UTC"})
k = len([1 for p, _b in M["ha"] if p.endswith("/play_media")])
for _ in range(110):
    time.sleep(1)
    if len([1 for p, _b in M["ha"] if p.endswith("/play_media")]) > k: break
check(len([1 for p, _b in M["ha"] if p.endswith("/play_media")]) > k, "the alarm goes off at its time")
c, r = web("/api/alarms"); o = next(x for x in r["alarms"] if x["id"] == once["id"])
check(not o["on"], "a one-off alarm switches itself off")
vols = [b.get("volume_level") for p, b in M["ha"] if p.endswith("/volume_set")]
check(0.4 in vols or 0.06 in vols or 0.5 in vols, f"the volume is set {vols}")
c, _r = web(f"/api/alarms/{a['id']}/delete", {}); c, r = web("/api/alarms"); check(a["id"] not in [x["id"] for x in r["alarms"]], "delete")
# hooks
c, h = web("/api/alarms/hook", {}); key = h.get("key", "")
check(len(key) >= 24, "a hook key")
k = len([1 for p, _b in M["ha"] if p.endswith("/play_media")])
c, r = web(f"/api/hook/{key}/play?what=radio&q=amapiano&speaker=media_player.kitchen&volume=0.2", op=X)
check(c == 200 and r.get("ok") and len([1 for p, _b in M["ha"] if p.endswith("/play_media")]) > k, f"Home Assistant starts music with the hook, no sign-in {r}")
c, r = web(f"/api/hook/{key}/stop?speaker=media_player.kitchen", op=X); check(c == 200, "and stops it")
c, r = web(f"/api/hook/{'x' * 32}/play?speaker=media_player.kitchen", op=X); check(c == 401, "a wrong hook key: no")
c, r = web(f"/api/hook/{key}/play?what=radio&q=x&speaker=evil", op=X); check(c == 400, "a hook only plays on a speaker")
c, r = web(f"/api/lib?k={key}", op=X); check(c == 401, "the hook key opens nothing else")
c, h2 = web("/api/alarms/hook", {}); c, r = web(f"/api/hook/{key}/stop?speaker=media_player.kitchen", op=X); check(c == 401, "a new hook key cancels the old one")
print(f"V29 OK {n}" if not bad else f"V29 FAILED {len(bad)}/{n}", flush=True)
import os
time.sleep(int(os.environ.get("V29_HOLD") or 0))      # keeps the pretend services up for the UI test
sys.exit(1 if bad else 0)
