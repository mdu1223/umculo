"""2.2: together -> separate and back, per-person recipes, app stars that respect download permissions."""
import hashlib, http.cookiejar, json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
PORT, S = int(sys.argv[1]), sys.argv[2]
BASE = f"http://localhost:{PORT}"; FAM = S + "/data/Family"
ok = 0
def check(c, what):
    global ok
    if not c:
        print("FAIL:", what); sys.exit(1)
    ok += 1
def opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
A, J = opener(), opener()
def web(path, body=None, op=A, code=False):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    try:
        r = op.open(req, timeout=60); d = json.loads(r.read()); return (r.status, d) if code else d
    except urllib.error.HTTPError as e:
        d = json.loads(e.read() or b"{}"); return (e.code, d) if code else d
def sub(endpoint, user, pw, **p):
    s = "xyz9"; q = {"u": user, "t": hashlib.md5((pw + s).encode()).hexdigest(), "s": s, "v": "1.16.1", "c": "t", "f": "json"}; q.update(p)
    return json.loads(urllib.request.urlopen(f"{BASE}/rest/{endpoint}?" + urllib.parse.urlencode(q, doseq=True)).read())["subsonic-response"]
def local(xs):
    return [x for x in xs or [] if not str(x["id"]).startswith("umc-")]
def wait_scan():
    time.sleep(0.7)
    for _ in range(80):
        if not sub("getScanStatus", "boss", "bosspass")["scanStatus"]["scanning"]:
            return
        time.sleep(0.5)
def songs(user, pw):
    return sorted(x["title"] for x in local(sub("search3", user, pw, query="", songCount=200)["searchResult3"].get("song")))

web("/api/setup", {"username": "boss", "password": "bosspass", "name": "Boss"})
r = web("/api/users", {"mode": "together", "name": "Jess", "username": "jess", "password": "jesspw1", "preset": "ask"}); jid = r["id"]
web("/api/login", {"username": "jess", "password": "jesspw1"}, op=J)
mine = songs("boss", "bosspass")
check(len(mine) >= 5 and songs("jess", "jesspw1") == mine, f"together: she sees everything {len(mine)}")
# ---- a library each
c, r = web("/api/users/house", {"mode": "separate"}, op=J, code=True); check(c == 403, "only the admin switches")
check(web("/api/users/house", {"mode": "separate"})["house"] == "separate", "switched to separate")
check(os.path.isdir(FAM + "/jess/Music"), "her folder made")
wait_scan()
check(songs("jess", "jesspw1") == [], f"separate: she sees none of yours {songs('jess', 'jesspw1')}")
check(songs("boss", "bosspass") == mine, "you still see all of yours")
st = web("/api/lib/stats", op=J) if False else None
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=4", "-c:a", "libmp3lame", "-b:a", "320k", FAM + "/jess/Music/Tyla - Water.mp3"], check=True)
web("/api/connectors/action", {"id": "server", "action": "scan"}); wait_scan()
check(songs("jess", "jesspw1") == ["Tyla - Water"] or songs("jess", "jesspw1") == ["Water"], f"she sees her own {songs('jess', 'jesspw1')}")
web(f"/api/users/{jid}", {"share_all": True}); wait_scan()
check(len(songs("jess", "jesspw1")) == len(mine) + 1, "All of my music on: yours + hers")
web(f"/api/users/{jid}", {"share_all": False}); wait_scan()
check(len(songs("jess", "jesspw1")) == 1, "All of my music off again")
check(web("/api/users")["house"] == "separate", "house saved")
# ---- recipes are per person
r = web("/api/recipes", {"name": "Boss Mix", "emoji": "🔥", "ingredients": {"trending": 5}, "mode": "stream"}); rid = r["id"]
r2 = web("/api/recipes", {"name": "Jess Mix", "ingredients": {"trending": 5}, "mode": "stream"}, op=J); jrid = r2["id"]
check([x["name"] for x in web("/api/recipes")["recipes"]] == ["Boss Mix"], "you see your recipes only")
check([x["name"] for x in web("/api/recipes", op=J)["recipes"]] == ["Jess Mix"], "she sees hers only")
c, _ = web(f"/api/recipes/{rid}", op=J, code=True); check(c == 404, "can't open yours")
c, _ = web(f"/api/recipes/{rid}/delete", {}, op=J, code=True); check(c == 404, "can't delete yours")
check(rid in [x["id"] for x in web("/api/recipes")["recipes"]], "yours still there")
for _ in range(40):
    if all(x.get("count") for x in web("/api/recipes")["recipes"] + web("/api/recipes", op=J)["recipes"]):
        break
    time.sleep(0.5)
bp = [p["name"] for p in sub("getPlaylists", "boss", "bosspass")["playlists"].get("playlist", [])]
jp = [p["name"] for p in sub("getPlaylists", "jess", "jesspw1")["playlists"].get("playlist", [])]
print("   app playlists:", bp, jp)
check("Jess Mix" not in bp and not any(n.startswith("Boss Mix") for n in jp), f"apps show each person their own mixes {bp} {jp}")
check(sub("getPlaylist", "jess", "jesspw1", id="umc-pl-r" + rid)["status"] == "failed", "can't open your mix by id")
# ---- stars from apps follow download permission
before = len(web("/api/approvals")["approvals"])
sub("star", "jess", "jesspw1", id="umc-dz-3135556"); time.sleep(1)
ap = web("/api/approvals")["approvals"]
check(len(ap) == before + 1 and ap[0]["state"] == "waiting" and ap[0]["name"] == "Jess", f"'ask': a star becomes a request {ap[:1]}")
web(f"/api/users/{jid}", {"perms": {"download": "no"}})
sub("star", "jess", "jesspw1", id="umc-dz-3135557"); time.sleep(1)
check(len(web("/api/approvals")["approvals"]) == before + 1, "'off': a star is just a star")
# ---- and back to one library
check(web("/api/users/house", {"mode": "together"})["house"] == "together", "back to together"); wait_scan()
check(len(songs("jess", "jesspw1")) == len(mine) + 1, "together again: sees everything (and keeps hers)")
# ---- 2.2.1: Mixes, follows, album downloads and For You are each person's own
sh = web("/api/link", {"url": "https://www.podomatic.com/podcasts/deepshizzol"})
web("/api/link/subscribe", {"show": sh["id"], "latest": False})
for _ in range(30):
    if web("/api/mixes")["subscriptions"]:
        break
    time.sleep(0.5)
bs = web("/api/mixes")["subscriptions"]
check(len(bs) == 1, f"your subscription {bs}")
check(web("/api/mixes", op=J)["subscriptions"] == [], "she doesn't see your subscriptions")
c, _ = web(f"/api/subscriptions/{bs[0]['id']}/remove", {}, op=J, code=True); check(c == 404, "she can't remove yours")
web("/api/link/subscribe", {"show": web("/api/link", {"url": "https://www.podomatic.com/podcasts/deepshizzol"}, op=J)["id"], "latest": False}, op=J)
for _ in range(30):
    if web("/api/mixes", op=J)["subscriptions"]:
        break
    time.sleep(0.5)
check(len(web("/api/mixes", op=J)["subscriptions"]) == 1 and len(web("/api/mixes")["subscriptions"]) == 1, "same show: one subscription each")
c, f = web("/api/artists/follow", {"name": "Kwesta"}, op=J, code=True)
check(c == 200, f"she follows an artist {c} {f}")
check([x["name"] for x in web("/api/artists", op=J)["following"]] == [f["name"]], "her follows")
check(web("/api/artists")["following"] == [], "not yours")
web("/api/artists/follow", {"name": "Kwesta"})
web(f"/api/artists/{f['id']}/unfollow", {}, op=J)
check(web("/api/artists", op=J)["following"] == [] and len(web("/api/artists")["following"]) == 1, "her unfollow leaves yours")
c, _ = web(f"/api/artists/{f['id']}/unfollow", {}, op=J, code=True); check(c == 404, "can't unfollow for you")
web("/api/album/get", {"id": "302127"}); time.sleep(1)
check(len(web("/api/artists")["albums"]) == 1, "your album download in your queue")
check(web("/api/artists", op=J)["albums"] == [], "not in hers")
fy = web("/api/foryou", op=J)
check(fy["lb_user"] == "" and fy["hint"] and fy["weekly"] is None and fy["shelves"] == [], f"her For You starts clean {str(fy)[:200]}")
c, _ = web("/api/foryou/weekly", {}, op=J, code=True); check(c == 403, "the weekly mix is yours")
check(web("/api/foryou")["hint"] == "", "yours unchanged")
c, pl = web("/api/playlists", op=J, code=True); check(c == 200 and pl["local"] == [] and all(True for _ in pl["jobs"]), "no Spotify folder playlists for her")
print("V22 OK", ok)
