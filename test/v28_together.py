"""v2.8: stream keys (speakers/TVs), listen together, Home Assistant speakers (mock), the offline service worker.
Usage: python3 test/v28_together.py PORT STATE_DIR"""
import hashlib, http.cookiejar, http.server, json, sys, threading, time, urllib.error, urllib.parse, urllib.request
PORT, S = int(sys.argv[1]), sys.argv[2]; BASE = f"http://localhost:{PORT}"; bad = []; n = 0
def check(c, what):
    global n; n += 1
    if not c: bad.append(what); print("FAIL", what)
def opener(): return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
A, J, X = opener(), opener(), opener()
def web(path, body=None, op=A):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        r = op.open(req, timeout=60); raw = r.read()
        try: return r.status, json.loads(raw)
        except ValueError: return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, json.loads(raw or b"{}")
        except ValueError: return e.code, raw
def sub(endpoint, user, pw, **p):
    s = "xyz9"; q = {"u": user, "t": hashlib.md5((pw + s).encode()).hexdigest(), "s": s, "v": "1.16.1", "c": "t", "f": "json"}; q.update(p)
    return json.loads(urllib.request.urlopen(f"{BASE}/rest/{endpoint}?" + urllib.parse.urlencode(q, doseq=True)).read())["subsonic-response"]
def wait_scan():
    time.sleep(0.7)
    for _ in range(80):
        if not sub("getScanStatus", "boss", "bosspass")["scanStatus"]["scanning"]: return
        time.sleep(0.5)
web("/api/setup", {"username": "boss", "password": "bosspass", "name": "Boss"})
_c, r = web("/api/users", {"mode": "together", "name": "Jess", "username": "jess", "password": "jesspw1", "preset": "ask"})
web("/api/login", {"username": "jess", "password": "jesspw1"}, op=J)
web("/api/users/house", {"mode": "separate"}); wait_scan()
song = next(x for x in sub("search3", "boss", "bosspass", query="Ngud", songCount=5)["searchResult3"]["song"] if x["title"] == "Ngud")
sid = song["id"]
# stream keys
c, k = web("/api/cast/key"); key = k.get("key", "")
check(c == 200 and key.count(".") == 2, "a stream key for this person")
c, _b = web(f"/api/ndstream/{sid}?k={key}", op=X); check(c == 200, f"a speaker plays your song with the key, no sign-in ({c})")
c, _b = web(f"/api/ndstream/{sid}?k={key[:-3]}abc", op=X); check(c == 401, f"a wrong key: no ({c})")
c, _b = web(f"/api/lib?k={key}", op=X); check(c == 401, "the key only plays music: nothing else")
c, _b = web(f"/api/ndstream/{sid}", op=J); check(c in (403, 404), f"in separate libraries she can't play your song ({c})")
# listen together
c, room = web("/api/party", {"queue": [{"nd_id": sid, "title": "Ngud", "artist": "Kwesta", "duration": 6}], "i": 0, "position": 2, "playing": True})
code = room.get("code", "")
check(c == 200 and len(code) == 5 and room["dj"], "the DJ opens a room")
c, g = web(f"/api/party/{code}/join", {}, op=J)
check(c == 200 and not g["dj"] and g["queue"][0]["nd_id"] == sid and g["dj_name"] == "Boss", "she joins and sees the queue")
check(g["position"] >= 2, "and where the DJ is")
c, _b = web(f"/api/ndstream/{sid}?k={g['key']}", op=J); check(c == 200, f"she can play the DJ's song with the room's key ({c})")
c, g = web(f"/api/party/{code}/add", {"item": {"deezer_id": 111, "title": "Machine Gun", "artist": "FreakSA"}, "next": True}, op=J)
check(c == 200 and g["queue"][1]["deezer_id"] == 111 and g["added"][-1]["by"] == "Jess", "she adds a song to play next")
c, _b = web(f"/api/party/{code}/state", {"i": 1, "position": 0, "playing": True}, op=J); check(c == 403, "only the DJ moves the music")
c, _b = web(f"/api/party/{code}/state", {"i": 1, "position": 10, "playing": True}); check(c == 200, "the DJ moves on")
time.sleep(1.1)
c, g = web(f"/api/party/{code}", op=J); check(g["i"] == 1 and 10.9 <= g["position"] < 13, f"she follows: song 2 at about 11 s ({g.get('position')})")
check(len([m for m in g["members"]]) == 2, "two people in the room")
c, _b = web(f"/api/party/{code}/state", {"i": 1, "position": 30, "playing": False}); time.sleep(0.6)
c, g = web(f"/api/party/{code}", op=J); check(not g["playing"] and g["position"] == 30, "paused together")
c, _b = web(f"/api/party/{code}/end", {}, op=J); check(c == 403, "only the DJ closes the room")
c, _b = web(f"/api/party/{code}/end", {}); c, _b = web(f"/api/party/{code}", op=J); check(c == 404, "closed")
c, _b = web("/api/party/ZZZZZ", op=J); check(c == 404, "unknown room")
# Home Assistant speakers (a pretend Home Assistant)
HA = {"state": "idle", "calls": []}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, obj):
        b = json.dumps(obj).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer secret-token": self.send_response(401); self.end_headers(); return
        st = {"entity_id": "media_player.kitchen", "state": HA["state"], "attributes": {"friendly_name": "Kitchen", "volume_level": 0.4, "media_position": 5}}
        if self.path == "/api/": return self.send({"message": "API running."})
        if self.path == "/api/states": return self.send([st, {"entity_id": "light.x", "state": "on", "attributes": {}}])
        if self.path == "/api/states/media_player.kitchen": return self.send(st)
        self.send_response(404); self.end_headers()
    def do_POST(self):
        b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        HA["calls"].append((self.path, b))
        if self.path.endswith("/play_media"): HA["state"] = "playing"
        if self.path.endswith("/media_pause"): HA["state"] = "paused"
        self.send([])
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT + 3000), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
c, h = web("/api/ha", {"url": f"http://127.0.0.1:{PORT + 3000}", "token": "secret-token", "media_base": BASE})
check(c == 200 and h["ok"] and h["token_set"] and "token" not in h, f"Home Assistant connects; the token is never sent back {h}")
c, _b = web("/api/ha", op=J); check(c == 403, "only the admin sets it up")
c, sp = web("/api/speakers"); check(c == 200 and [x["id"] for x in sp["speakers"]] == ["media_player.kitchen"], f"speakers listed {sp}")
c, st = web("/api/speakers/media_player.kitchen/play", {"items": [{"nd_id": sid, "title": "Ngud", "artist": "Kwesta"}, {"deezer_id": 111, "title": "Machine Gun", "artist": "FreakSA"}], "start": 0})
pm = [b for p, b in HA["calls"] if p.endswith("/play_media")]
check(c == 200 and pm and pm[0]["entity_id"] == "media_player.kitchen", "Umculo sends the song to the speaker")
url = pm[0]["media_content_id"] if pm else ""
check(url.startswith(BASE + f"/api/ndstream/{sid}") and "k=" in url and "speaker=1" in url, f"with an address the speaker can open {url}")
c, _b = web(url.replace(BASE, ""), op=X); check(c == 200, "and the speaker can fetch it without signing in")
time.sleep(3); HA["state"] = "idle"
for _ in range(10):
    time.sleep(1)
    pm = [b for p, b in HA["calls"] if p.endswith("/play_media")]
    if len(pm) >= 2: break
check(len(pm) >= 2 and "/api/stream/111" in pm[-1]["media_content_id"], "when the song ends, the next one plays")
c, st = web("/api/speakers/media_player.kitchen/pause", {}); check(c == 200 and not st["playing"], "pause on the speaker")
c, st = web("/api/speakers/media_player.kitchen/stop", {}); check(c == 200 and st.get("stopped"), "stop")
c, _b = web("/api/speakers/media_player.kitchen/next", {}); check(c == 404, "nothing playing there now")
web("/api/ha", {"forget": True}); c, sp = web("/api/speakers"); check(not sp["ready"], "disconnect Home Assistant")
# offline
c, js = web("/sw.js", op=X); check(c == 200 and b"umc-offline" in js, "the offline worker is served to anyone (it holds no data)")
print(f"V28 OK {n}" if not bad else f"V28 FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
