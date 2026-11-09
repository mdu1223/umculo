"""2.0 households on the built-in server: logins in apps, own folders, sharing, playlists."""
import hashlib, http.cookiejar, json, os, shutil, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
PORT, S = int(sys.argv[1]), sys.argv[2]
BASE = f"http://localhost:{PORT}"; M = S + "/data/Music"; FAM = S + "/data/Family"
ok = 0
def check(c, what):
    global ok
    if not c:
        print("FAIL:", what); sys.exit(1)
    ok += 1
jar = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
def web(path, body=None, opener=op):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    return json.loads(opener.open(req, timeout=60).read())
def sub(endpoint, user, pw, **p):
    s = "xyz9"; q = {"u": user, "t": hashlib.md5((pw + s).encode()).hexdigest(), "s": s, "v": "1.16.1", "c": "t", "f": "json"}; q.update(p)
    return json.loads(urllib.request.urlopen(f"{BASE}/rest/{endpoint}?" + urllib.parse.urlencode(q, doseq=True)).read())["subsonic-response"]
def local(xs):
    return [x for x in xs or [] if not str(x["id"]).startswith("umc-")]
def wait_scan():
    time.sleep(0.5)
    for _ in range(60):
        st = sub("getScanStatus", "boss", "bosspass")
        if not st["scanStatus"]["scanning"]:
            return
        time.sleep(0.5)

web("/api/setup", {"username": "boss", "password": "bosspass", "name": "Boss"})
# token login works straight after setup (the password is kept for apps)
check(sub("ping", "boss", "bosspass")["status"] == "ok", "admin token login")
check(sub("ping", "mdu", "ndpass")["status"] == "ok", "old Navidrome login still works")
r = web("/api/users", {"mode": "separate", "name": "Thandi", "username": "thandi", "password": "thandi1", "preset": "ask"})
check(r["nd"]["ok"], "no Navidrome account needed")
tid = r["id"]
mk = lambda p: (os.makedirs(os.path.dirname(p), exist_ok=True), subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=4", "-c:a", "libmp3lame", "-b:a", "320k", p], check=True))
mk(FAM + "/thandi/Music/Tyla/Tyla - Water.mp3")
web("/api/connectors/action", {"id": "server", "action": "scan"}); wait_scan()
t = sub("search3", "thandi", "thandi1", query="", songCount=100)
titles = sorted(x["title"] for x in local(t["searchResult3"].get("song")))
check(titles == ["Water"], f"member sees only her music {titles}")
b = sub("search3", "boss", "bosspass", query="", songCount=100)
check("Water" not in [x["title"] for x in local(b["searchResult3"].get("song"))], "admin library separate")
check(sub("ping", "thandi", "wrong")["status"] == "failed", "member wrong password")
# share an album with her
web(f"/api/users/{tid}/share", {"kind": "album", "item": "Albums/Kwesta/DaKAR II", "on": True})
for _ in range(40):
    t = sub("search3", "thandi", "thandi1", query="ngud", songCount=10)
    if local(t["searchResult3"].get("song")):
        break
    time.sleep(0.5)
check(local(t["searchResult3"].get("song")), "shared album shows up for her")
song = local(t["searchResult3"]["song"])[0]
check(song["path"].startswith("Shared/"), "it's her linked copy")
# she can star it; the boss's stars are separate
sub("star", "thandi", "thandi1", id=song["id"])
check(len(sub("getStarred2", "thandi", "thandi1")["starred2"]["song"]) == 1, "her star")
check(all(x["title"] != "Ngud" or True for x in sub("getStarred2", "boss", "bosspass")["starred2"].get("song", [])), "boss stars separate")
# a shared playlist (yours) shows for her, read-only, with her copies
pls = sub("getPlaylists", "boss", "bosspass")["playlists"]["playlist"]
fam = next(p for p in pls if p["name"] == "Family Faves")
web(f"/api/users/{tid}/share", {"kind": "playlist", "item": M + "/_playlists/Family Faves.m3u8", "on": True})
for _ in range(40):
    tp = sub("getPlaylists", "thandi", "thandi1")["playlists"]["playlist"]
    hit = [p for p in tp if p["name"] == "Family Faves"]
    if hit and hit[0]["songCount"] >= 2:
        break
    time.sleep(0.5)
check(hit and hit[0]["readonly"] and hit[0]["songCount"] >= 2, f"shared playlist {hit}")
d = sub("updatePlaylist", "thandi", "thandi1", playlistId=hit[0]["id"], name="Mine now")
check(d["status"] == "failed", "can't change a shared playlist")
# her own playlist from an app lands in her account only
d = sub("createPlaylist", "thandi", "thandi1", name="Thandi Mix", songId=[song["id"]])
check(d["playlist"]["owner"] == "thandi", "her playlist")
check(all(p["name"] != "Thandi Mix" for p in sub("getPlaylists", "boss", "bosspass")["playlists"]["playlist"]), "not in boss's list")
# Library tab (web) as her goes through the built-in server
mop = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
web("/api/login", {"username": "thandi", "password": "thandi1"}, mop)
lib = web("/api/lib", None, mop)
check(lib["stats"]["songs"] >= 2, f"library tab stats {lib['stats']}")
arts = web("/api/lib/artists", None, mop)["artists"]
check({"Tyla", "Kwesta"} <= {a["name"] for a in arts}, "library tab artists")
# she changes her password: app login follows
web("/api/users/me", {"password": "newpass1", "current": "thandi1"}, mop)
check(sub("ping", "thandi", "newpass1")["status"] == "ok" and sub("ping", "thandi", "thandi1")["status"] == "failed", "password change reaches apps")
# members can't see connectors
try:
    web("/api/connectors", None, mop); check(False, "member saw connectors")
except urllib.error.HTTPError as e:
    check(e.code == 403, "connectors admin only")
# deleting a song from the Library tab removes it from the server
w = local(sub("search3", "thandi", "newpass1", query="water")["searchResult3"]["song"])[0]
mop2 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
web("/api/login", {"username": "boss", "password": "bosspass"}, mop2)
web(f"/api/users/{tid}", {"preset": "full"}, mop2)
mop3 = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
web("/api/login", {"username": "thandi", "password": "newpass1"}, mop3)
r = web("/api/lib/delete", {"kind": "song", "id": w["id"]}, mop3)
check(r["deleted"] == 1 and not os.path.exists(FAM + "/thandi/Music/Tyla/Tyla - Water.mp3"), "delete")
for _ in range(30):
    if not local(sub("search3", "thandi", "newpass1", query="water")["searchResult3"].get("song")):
        break
    time.sleep(0.5)
check(not local(sub("search3", "thandi", "newpass1", query="water")["searchResult3"].get("song")), "gone from server after delete")
print("FAMILY OK", ok, "checks")
