"""v2.7 player: chapters for a mix from the song finder on the library copy; radio from a style, an era, an artist.
Usage: python3 test/v27_player.py PORT STATE_DIR   (needs TEST_SHAZAM=1 in the environment script)"""
import json, os, subprocess, sys, time, urllib.request
PORT, S = sys.argv[1], sys.argv[2]; B = f"http://localhost:{PORT}"; bad = []; n = 0
AUTH = "u=mdu&p=ndpass&f=json"
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)
def web(path, method="GET", body=None):
    rq = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=90) as r: return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return {**json.loads(e.read() or b"{}"), "_code": e.code}
        except ValueError: return {"error": "bad", "_code": e.code}
def songs(q):
    return (web(f"/rest/search3.view?{AUTH}&query={urllib.parse.quote(q)}&songCount=20").get("subsonic-response", {}).get("searchResult3") or {}).get("song") or []
import urllib.parse
# a mix in the library
mixes = os.path.join(S, "data", "Music", "meta_podcasts", "DJ Test")
os.makedirs(mixes, exist_ok=True)
mix = os.path.join(mixes, "Deep Session 02.mp3")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=6", "-c:a", "libmp3lame", mix], check=True)
import mutagen
f = mutagen.File(mix, easy=True); f.add_tags() if f.tags is None else None
f["title"], f["artist"] = "Deep Session 02", "DJ Test"; f.save()
web(f"/rest/startScan.view?{AUTH}")
song = None
for _ in range(60):
    song = next((x for x in songs("Deep Session") if x.get("title") == "Deep Session 02"), None)
    if song: break
    time.sleep(0.5)
check(song, "the mix is in the library")
sid = song["id"] if song else "x"
c = web(f"/api/chapters/{sid}")
check(c.get("state") == "none" and c.get("can_find"), f"no chapters yet, and the finder can run {c}")
check(web(f"/api/chapters/{sid}/find", "POST").get("ok"), "start the finder on the library copy")
for _ in range(80):
    c = web(f"/api/chapters/{sid}")
    if c.get("state") in ("done", "failed"): break
    time.sleep(0.5)
check(c.get("state") == "done", f"the finder finished {c}")
t = (c.get("tracks") or [{}])[0]
check(t.get("title") == "Ngud" and t.get("artist") == "Kwesta" and t.get("t") == 0, f"the song and its time are there {t}")
check(t.get("owned"), "and it knows you have that song")
check(os.path.exists(mix), "the library copy is never deleted")
check(web(f"/api/chapters/{sid}/find", "POST").get("find") == c.get("find"), "finding again reuses the result")
check(web("/api/chapters/nope").get("_code") == 404, "unknown song: not found")
# radio
r = web("/api/radio?q=amapiano&new=0")
lib = [x for x in r.get("songs") or [] if x.get("lib")]
check(lib and all("Kabza" in x["artist"] or "Mas Musiq" in x["artist"] for x in lib), f"amapiano radio is amapiano {lib}")
check(lib == r["songs"][:len(lib)], "your own songs come first when you ask for no new ones")
check("Amapiano" in (r.get("style") or ""), "it says the style")
r = web("/api/radio?q=hip%20hop%20from%20the%202010s&new=0")
lib = [x for x in r.get("songs") or [] if x.get("lib")]
check(lib and all(x.get("year") == 2016 for x in lib) and "2010" in r.get("style", ""), f"an era is kept {r.get('style')} {lib}")
r = web("/api/radio?q=Kwesta&new=0")
check(r.get("name") == "Kwesta radio" and r["songs"] and "Kwesta" in r["songs"][0]["artist"], f"artist radio starts with the artist {r.get('name')} {r.get('songs', [{}])[:1]}")
r = web("/api/radio?q=amapiano&new=1")
check("songs" in r, "radio with new songs mixed in works")
r = web("/api/radio?q=jazz%20from%20the%2060s&new=0")
check(r.get("_code") in (None, 404) and (r.get("songs") or r.get("error")), "nothing in that style: a clear answer, not a crash")
check(web("/api/radio?q=").get("_code") == 400, "radio needs words")
st = web("/api/radio/stations").get("stations") or []
check(any(x["q"] == "Amapiano" for x in st) and any(x["q"] == "chill" for x in st), f"station suggestions from your library {st}")
print(f"V27 OK {n}" if not bad else f"V27 FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
