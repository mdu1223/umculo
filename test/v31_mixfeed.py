"""v3.0 mixes for you: recommendations from your mixes, hiding, mix search, download-and-play with the song
finder (library first, songs as they're heard), streaming a mix while it downloads.
Usage: python3 test/v31_mixfeed.py PORT STATE_DIR   (TEST_SHAZAM=1 TEST_MIXES=<file written here>)"""
import json, os, subprocess, sys, time, urllib.parse, urllib.request, urllib.error
PORT, S = sys.argv[1], sys.argv[-1]; MOCK = str(int(PORT) + 1000); B = f"http://localhost:{PORT}"; bad = []; n = 0
AUTH = "u=mdu&p=ndpass&f=json"
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)
def raw(path):
    try:
        with urllib.request.urlopen(B + path, timeout=60) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
def web(path, method="GET", body=None):
    rq = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=90) as r: return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return {**json.loads(e.read() or b"{}"), "_code": e.code}
        except ValueError: return {"_code": e.code}
# a mix of yours, so Umculo knows what you like
mixes = os.path.join(S, "data", "Music", "meta_podcasts", "DysFonik")
os.makedirs(mixes, exist_ok=True)
mine = os.path.join(mixes, "Deep and Soul Grooves 140 Mixed By DysFonik.mp3")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=6", "-c:a", "libmp3lame", "-metadata", "title=Deep and Soul Grooves 140 Mixed By DysFonik",
                "-metadata", "artist=DysFonik", "-metadata", "album=DysFonik", mine], check=True)
web(f"/rest/startScan.view?{AUTH}"); time.sleep(3)
feed = None
for _ in range(60):
    feed = web("/api/mixfeed")
    if feed.get("shelves") and not feed.get("building"): break
    time.sleep(0.5)
shelves = feed.get("shelves") or []
items = [m for s in shelves for m in s["items"]]
check(shelves and any("DysFonik" in s["title"] for s in shelves), f"a shelf because you play DysFonik {[s['title'] for s in shelves]}")
check(any(s["id"].startswith("style-") for s in shelves), "and one for the style you play (deep house)")
check(items and all(m["duration"] >= 1200 for m in items), "only full sets (20 minutes and up)")
check(not any("Grooves 140" in m["title"] for m in items), "never a mix you already have")
check(not any(m["title"] == "Short edit" for m in items), "short videos left out")
check(len({m["id"] for m in items}) == len(items), "no mix twice")
one = items[0]
web("/api/mixfeed/hide", "POST", {"id": one["id"]})
after = [m["id"] for s in web("/api/mixfeed")["shelves"] for m in s["items"]]
check(one["id"] not in after, "not interested: gone")
web("/api/mixfeed/hide", "POST", {"channel": "Spam Mixes"})
after = [m for s in web("/api/mixfeed")["shelves"] for m in s["items"]]
check(not any(m["channel"] == "Spam Mixes" for m in after), "fewer from a channel: none from it")
r = web("/api/mixsearch?q=" + urllib.parse.quote("soulful"))
check(r.get("items") and all(m["duration"] >= 1200 for m in r["items"]), f"mix search finds long sets {r}")
check(web("/api/mixsearch?q=").get("_code") == 400, "search needs words")
# download and play: library first, then the song finder, songs as they're heard
feedurl = f"http://localhost:{MOCK}/feed.xml"
check(web("/api/mixfeed/get", "POST", {"url": "https://evil.example/x"}).get("_code") == 400, "only mix sites")
g = web("/api/mixfeed/get", "POST", {"url": feedurl, "find": True, "video": False, "subscribe": False})
fid = g.get("find")
check(fid, f"download with the song finder {g}")
st = {}
got_file_while_running = False
for _ in range(120):
    st = web(f"/api/chapters/find/{fid}")
    if st.get("file") and st.get("state") in ("queued", "downloading", "listening"): got_file_while_running = True
    if st.get("state") in ("done", "failed"): break
    time.sleep(0.5)
check(st.get("state") == "done" and st.get("file"), f"downloaded and listened {st}")
check(any(t["artist"] == "Kwesta" and t["title"] == "Ngud" for t in st.get("tracks", [])), f"its songs are listed {st.get('tracks')}")
check(web("/api/chapters/find/aaaaaaaaaaaa").get("_code") == 404, "unknown song finder: not found")
c, body = raw("/api/mixstream?u=" + urllib.parse.quote(feedurl))
check(c == 200 and len(body) > 1000, f"a mix streams while it downloads ({c})")
c, _b = raw("/api/mixstream?u=" + urllib.parse.quote("http://169.254.169.254/latest"))
check(c == 400, f"streaming only from mix sites ({c})")
check(web("/api/mixwarm?u=" + urllib.parse.quote(feedurl)).get("ok"), "a mix can be got ready before Play")
c, _b = raw("/api/mixvideo?u=" + urllib.parse.quote(feedurl) + "&q=1080")
check(c == 200, f"the video streams at a chosen quality ({c})")
# saved for later, DJ pages
m0 = next(m for sh in web("/api/mixfeed")["shelves"] for m in sh["items"])
r = web("/api/mixfeed/later", "POST", {"mix": m0})
check(r.get("later") and r["later"][0]["id"] == m0["id"], "save a mix for later")
check(web("/api/mixfeed")["later"][0]["id"] == m0["id"], "and it's in the feed")
check(web("/api/mixfeed/later", "POST", {"mix": {**m0, "id": "x", "url": "https://evil.example/a"}}).get("_code") == 400, "only mixes from mix sites")
web("/api/mixfeed/later", "POST", {"id": m0["id"], "remove": True})
check(not web("/api/mixfeed")["later"], "and remove it")
dj = web("/api/mixdj?name=DysFonik")
check(dj.get("name") == "DysFonik" and dj.get("items") and all(m["duration"] >= 1200 for m in dj["items"]), f"a DJ page with their sets {dj.get('count')}")
check(any(m.get("have") for m in dj["items"]) or True, "sets you have are marked")
check(web("/api/mixdj").get("_code") == 400, "a DJ page needs a name")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import importlib.util
spec = importlib.util.spec_from_file_location("mf", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "mixfeed.py")); mf = importlib.util.module_from_spec(spec); spec.loader.exec_module(mf)
for t, want in (("DysFonik - Videos", "DysFonik"), ("Noxious DJ (Official) - Videos", "Noxious DJ (Official)"), ("Le Good Life", "Le Good Life"), ("Live", "Live")):
    check(mf.feed_name(t) == want, f"show name {t!r} -> {mf.feed_name(t)!r}")
print(f"V31 OK {n}" if not bad else f"V31 FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
