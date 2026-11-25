"""v3.9 your own playlists: add any song (from search, the player, a mix's song list) to a playlist or a new one;
songs you don't have are fetched and join when they arrive. Like any song by name (a song heard in a mix).
Usage: python3 test/v34_playlists.py PORT STATE_DIR"""
import json, os, sys, time, urllib.request
PORT, S = sys.argv[1], sys.argv[2]; B = f"http://localhost:{PORT}"; bad = []; n = 0
AUTH = "u=mdu&p=ndpass&f=json"
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)
def web(path, method="GET", body=None):
    rq = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=60) as r: return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return {**json.loads(e.read() or b"{}"), "_code": e.code}
        except ValueError: return {"_code": e.code}
M = os.path.join(S, "data", "Music"); PL = os.path.join(M, "_playlists")
def lines(name): return [l for l in open(os.path.join(PL, name + ".m3u8"), encoding="utf-8").read().splitlines() if l and not l.startswith("#")]
for _ in range(60):
    hits = (web(f"/rest/search3.view?{AUTH}&query=Ngud&songCount=3").get("subsonic-response", {}).get("searchResult3") or {}).get("song") or []
    if hits: break
    time.sleep(0.5)
ngud = hits[0]["id"] if hits else ""
check(bool(ngud), "a library song to add")
mine = web("/api/pl/mine")
check(any(p["name"] == "Family Faves" for p in mine.get("playlists", [])), f"your playlists are listed {mine}")
# a new playlist: one song from the library (by id), one by name that's in the library, one you don't have
r = web("/api/pl/add", "POST", {"name": "Late Night", "songs": [{"nd_id": ngud, "artist": "Kwesta", "title": "Ngud"},
        {"artist": "Mas Musiq", "title": "Zaka"}, {"artist": "Black Coffee", "title": "Drive", "duration": 300}]})
check(r.get("name") == "Late Night" and r.get("added") == 2 and r.get("getting") == 1, f"made with 2 in, 1 to get {r}")
got = lines("Late Night")
check(len(got) == 2 and any("Ngud" in l for l in got) and any("Zaka" in l for l in got), f"the playlist file has the 2 songs {got}")
check(any(p["name"] == "Late Night" for p in web("/api/pl/mine").get("playlists", [])), "the new playlist is listed")
dup = web("/api/pl/add", "POST", {"name": "late night", "songs": [{"artist": "X", "title": "Y"}]})
check(dup.get("_code") == 400 and "already" in (dup.get("error") or ""), f"a name you already use is refused {dup}")
# adding again: nothing twice
again = web("/api/pl/add", "POST", {"pl": r["id"], "songs": [{"nd_id": ngud, "artist": "Kwesta", "title": "Ngud"}, {"artist": "Black Coffee", "title": "Drive"}]})
check(again.get("already") == 2 and again.get("added") == 0 and again.get("getting") == 0, f"a song already in it isn't added twice {again}")
# an existing playlist made by hand keeps its songs when one is added
fam = next(p for p in web("/api/pl/mine")["playlists"] if p["name"] == "Family Faves")
before = lines("Family Faves")
add = web("/api/pl/add", "POST", {"pl": fam["id"], "songs": [{"artist": "Mas Musiq", "title": "Zaka"}, {"nd_id": ngud}]})
check(add.get("already") == 2, f"songs it has count as there {add}")
add2 = web("/api/pl/add", "POST", {"pl": fam["id"], "songs": [{"artist": "Black Coffee", "title": "Drive"}]})
time.sleep(1)
check(add2.get("getting") == 1 and lines("Family Faves")[:2] == before, f"the hand-made playlist keeps its songs {add2} {lines('Family Faves')}")
check(web("/api/pl/add", "POST", {"pl": fam["id"], "songs": []}).get("_code") == 400, "no songs: refused")
# like a song by name (heard in a mix): one you have is starred
lk = web("/api/like", "POST", {"artist": "Kwesta", "title": "Ngud", "value": 1})
check(lk.get("id") == "n" + ngud and not lk.get("getting"), f"liking a library song by name finds it {lk}")
check(("n" + ngud) in web("/api/likes").get("liked", []), "and it's liked")
lk2 = web("/api/like", "POST", {"artist": "Black Coffee", "title": "Drive", "value": 1})
check(lk2.get("ok") and lk2.get("getting") and lk2.get("id", "")[:1] in ("d", "k"), f"liking one you don't have gets it {lk2}")
check(lk2.get("id") in web("/api/likes").get("liked", []), "and it shows as liked (even an unreleased edit known only by name)")
web("/api/like", "POST", {"artist": "Kwesta", "title": "Ngud", "value": 0})
check(("n" + ngud) not in web("/api/likes").get("liked", []), "unliked")
print(("V34 OK %d" % n) if not bad else ("V34 FAILED %d/%d" % (len(bad), n)))
sys.exit(1 if bad else 0)
