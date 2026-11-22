"""v2.9.5: track numbers in mix tracklists, the inbox folder (bought songs and zips get filed), songs from your mixes.
Usage: python3 test/v30_sources.py PORT STATE_DIR   (run with INBOX_EVERY=2)"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error, zipfile
PORT, S = sys.argv[1], sys.argv[2]; B = f"http://localhost:{PORT}"; bad = []; n = 0
AUTH = "u=mdu&p=ndpass&f=json"
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)
def web(path, method="GET", body=None):
    rq = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=60) as r: return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return {**json.loads(e.read() or b"{}"), "_code": e.code}
        except ValueError: return {"_code": e.code}
# track numbers (the parser itself)
src = open(os.path.join(os.path.dirname(__file__), "..", "app", "links.py")).read(); g = {"re": re}
for pat in (r"(DASH = .*?\n)", r"(NUM = re.compile\(.*?\)\n)", r"(def split_song.*?\n\n\n)"): exec(re.search(pat, src, re.S).group(1), g)
cases = {"4..LebtoniQ, Sef Pico & NtoMusica - Banomona": ("LebtoniQ, Sef Pico & NtoMusica", "Banomona"), "12..De'Luks - Wangkgotsofatsa": ("De'Luks", "Wangkgotsofatsa"),
         "1. Black Coffee - Drive": ("Black Coffee", "Drive"), "07 Shimza - Thusa": ("Shimza", "Thusa"), "3 Doors Down - Kryptonite": ("3 Doors Down", "Kryptonite"),
         "112 - Cupid": ("112", "Cupid"), "50 Cent - In Da Club": ("50 Cent", "In Da Club"), "#5 - Kabza De Small - Sponono": ("Kabza De Small", "Sponono"),
         "10) Culoe De Song - Y.O.U": ("Culoe De Song", "Y.O.U"), "2Point1 - Tribe": ("2Point1", "Tribe")}
for text, want in cases.items():
    check(g["split_song"](text) == want, f"tracklist line {text!r} -> {g['split_song'](text)}")
# a published tracklist: songs only, not the channel's membership perks or links
for pat in (r"(STAMP = .*?\n)", r"(JUNK = re.compile\(.*?\)\n)", r"(def parse_tracklist.*?\n\n\n)", r"(def listen_order.*?\n\n\n)"):
    exec(re.search(pat, src, re.S).group(1), g)
g["strip_html"] = lambda t: t; g["secs"] = lambda t: sum(int(x) * 60 ** i for i, x in enumerate(reversed(t.split(":"))))
desc = """Deep and Soulful House Mix 19
DSG Level 1 - Get loyalty badges next to your name in comments and live chat
DSG Level 2 - Audio access (Links to download the mixes)
DSG Level 3 - Video access (Links to download the videos)
Join this channel to get access to perks
00:00 Black Coffee - Drive
05:10 Culoe De Song - Y.O.U
11:02 Atmos Blaq - Love Song"""
got = g["parse_tracklist"](desc)
check([x["title"] for x in got] == ["Drive", "Y.O.U", "Love Song"], f"tracklist without membership perks: {[x['title'] for x in got]}")
order = g["listen_order"](4415, 60)
check(len(order) == len(set(order)) == len(range(30, 4403, 60)) and order[1] - order[0] == 180, f"listening covers the whole mix first {order[:4]}")
# the inbox
M = os.path.join(S, "data", "Music"); inbox = os.path.join(M, "_inbox"); os.makedirs(inbox, exist_ok=True)
def mp3(path, artist, title):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=5", "-c:a", "libmp3lame", "-metadata", f"artist={artist}", "-metadata", f"title={title}", path], check=True)
    old = time.time() - 120; os.utime(path, (old, old))
mp3(os.path.join(inbox, "bought.mp3"), "LebtoniQ", "Banomona")
tmp = os.path.join(S, "ep"); os.makedirs(tmp, exist_ok=True)
mp3(os.path.join(tmp, "01 Shimza - Thusa.mp3"), "Shimza", "Thusa"); mp3(os.path.join(tmp, "02.mp3"), "Shimza", "Gaba")
with zipfile.ZipFile(os.path.join(inbox, "Shimza EP.zip"), "w") as z:
    for f in os.listdir(tmp): z.write(os.path.join(tmp, f), f)
    z.writestr("../evil.mp3", b"x")
old = time.time() - 120; os.utime(os.path.join(inbox, "Shimza EP.zip"), (old, old))
check(web("/api/inbox").get("waiting") == 2, f"the inbox sees what's waiting {web('/api/inbox')}")
got = []
for _ in range(60):
    time.sleep(1)
    ib = web("/api/inbox"); got = [x["title"] for x in ib.get("filed", [])]
    if {"Banomona", "Thusa", "Gaba"} <= set(got): break
check({"Banomona", "Thusa", "Gaba"} <= set(got), f"bought songs and a zip's songs are filed {ib}")
lib = os.path.join(M, "Umculo")
check(any("Banomona" in f for _d, _x, fs in os.walk(os.path.join(lib, "LebtoniQ")) for f in fs), "filed under the artist")
check(not os.path.exists(os.path.join(S, "data", "evil.mp3")) and not os.path.exists(os.path.join(M, "evil.mp3")), "a zip can't write outside the inbox")
check(os.path.exists(os.path.join(inbox, ".unpacked", "Shimza EP.zip")), "the zip is set aside, not deleted")
found = None
for _ in range(60):
    hits = (web(f"/rest/search3.view?{AUTH}&query=Banomona&songCount=5").get("subsonic-response", {}).get("searchResult3") or {}).get("song") or []
    found = next((x for x in hits if x["title"] == "Banomona" and not str(x["id"]).startswith("umc-")), None)
    if found: break
    time.sleep(1)
check(found, "and it's in the library")
# songs from your mixes
w = web("/api/chapters/wanted"); check("songs" in w, "the wanted list answers")
r = web("/api/chapters/wanted/get", "POST", {}); check(r.get("_code") == 400 or r.get("job"), "get them all: a job, or a clear 'nothing to get'")
# Hearthis.at: a new free source (only uploads the uploader offers as free downloads)
st = web("/api/settings")
check("hearthis" in (st.get("sources") or []), f"Hearthis.at is switched on for an existing install {st.get('sources')}")
c = web("/api/check", "POST", {"track": {"artist": "LebtoniQ", "title": "Banomona", "duration": 30}})
cid = c.get("id"); cands = []
for _ in range(40):
    v = web(f"/api/check/{cid}")
    cands = [x for x in v.get("candidates", []) if x["source"] == "hearthis"]
    d = v.get("done"); 
    if (d.get("hearthis") if isinstance(d, dict) else d) and cands: break
    time.sleep(0.5)
names = [x["name"] for x in cands]
check(names == ["LebtoniQ - Banomona (Original Mix)"], f"only the free download is offered (not fan-only, not stream-only) {names}")
check(cands and cands[0]["quality"] == "MP3 original" and cands[0]["detail"] == "LebtoniQ", f"with its quality and uploader {cands[:1]}")
if cands:
    r = web("/api/get", "POST", {"check_id": cid, "candidate_id": cands[0]["id"]})
    rid = r.get("id"); state = {}
    for _ in range(60):
        reqs = web("/api/requests").get("requests", [])
        state = next((x for x in reqs if x["id"] == rid), {})
        if state.get("state") in ("ready", "failed", "done"): break
        time.sleep(0.5)
    check(state.get("state") in ("ready", "done"), f"downloaded from Hearthis.at {state.get('state')} {state.get('message')}")
    check(any("Banomona" in f and not f.endswith(".txt") for d, _x, fs in os.walk(os.path.join(M, "Umculo")) for f in fs if "LebtoniQ" in d), "and filed in the library")
print(f"V30 OK {n}" if not bad else f"V30 FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
