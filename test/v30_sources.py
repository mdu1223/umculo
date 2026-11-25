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
for pat in (r"(STAMP = .*?\n)", r"(JUNK = re.compile\(.*?\)\n)", r"(def parse_tracklist.*?\n\n\n)", r"(def first_pass.*?\n\n\n)", r"(def refine.*?\n\n\n)"):
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
num = g["parse_tracklist"]("""Tracklist:
1 Daev Martian & Deniece Marz - Groove Maxing
2 China Chameleon x Brandon Dhludhlu - Birds Sing (Buddynice Remix)
3 Ta-Ice & Myazisto - The Goodies
10 Sio - Somewhere""")
check([x["artist"] for x in num] == ["Daev Martian & Deniece Marz", "China Chameleon x Brandon Dhludhlu", "Ta-Ice & Myazisto", "Sio"], f"numbered tracklist {[x['artist'] for x in num]}")
one = g["parse_tracklist"]("3 Doors Down - Kryptonite\n50 Cent - In Da Club")
check([x["artist"] for x in one] == ["3 Doors Down", "50 Cent"], f"names that start with a number stay {[x['artist'] for x in one]}")
class _Core: song_key = staticmethod(lambda a, t: (a.lower(), t.lower()))
g["core"] = _Core
fp = g["first_pass"](3600, 60)
check(len(fp) == 20 and fp[0] == 30 and fp[1] - fp[0] == 180, f"the first pass covers an hour in 20 bits {fp[:3]} {len(fp)}")
exec(re.search(r"(def same.*?\n\n\n)", src, re.S).group(1), g)
import urllib.parse as _up
g["urllib"] = __import__("urllib"); exec(re.search(r"(def one_video.*?\n\n\n)", src, re.S).group(1), g)
check(g["one_video"]("https://www.youtube.com/watch?v=IdfVkx7qKYg&list=RDIdfVkx7qKYg&start_radio=1") == "https://www.youtube.com/watch?v=IdfVkx7qKYg",
      "a video link with YouTube's Mix list is that one video")
check(g["one_video"]("https://youtu.be/l9YZmIUW8Ds?si=x") == "https://www.youtube.com/watch?v=l9YZmIUW8Ds", "a short link too")
check(g["one_video"]("https://www.youtube.com/playlist?list=PL123") == "https://www.youtube.com/playlist?list=PL123", "a playlist link stays a playlist")
res = {30: ("A", "One"), 210: ("A", "One"), 390: ("B", "Two"), 570: None, 750: ("C", "Three")}
r1 = g["refine"](res, 60, 800, 1)
check(r1 == [270, 330, 450, 510, 630, 690], f"round 1: a bit a minute, not inside one song {r1}")
res.update({o: ("B", "Two") for o in (270, 330, 450)}); res.update({510: None, 630: ("C", "Three"), 690: ("C", "Three")})
r2 = g["refine"](res, 60, 800, 2)
check(r2 == [240, 480, 540, 600], f"round 2: halfway where the song changes or nothing was heard {r2}")
check(g["refine"](res, 60, 800, 3) == [], "two rounds, then done")
# music videos: official ones only, and lined up with your copy (or not at all)
import importlib.util
_sp = importlib.util.spec_from_file_location("sv", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "songvideo.py"))
sv = importlib.util.module_from_spec(_sp); _sp.loader.exec_module(sv)
off_ = lambda t, c: sv.is_official({"title": t, "channel": c}, "Rihanna", "Umbrella (feat. JAY-Z)")
check(off_("Rihanna - Umbrella (Orange Version) (Official Music Video) ft. JAY-Z", "RihannaVEVO"), "the artist's own video counts")
check(not off_("Rihanna - Umbrella (Lyrics) ft. Jay-Z", "7clouds"), "a lyric video doesn't")
check(not off_("Rihanna - Umbrella (Live at the BRITs)", "RihannaVEVO"), "a live version doesn't")
check(not off_("Umbrella - Rihanna cover", "Some Singer"), "a cover doesn't")
check(not off_("Rihanna - Diamonds (Official Video)", "RihannaVEVO"), "another song doesn't")
check(sv.is_official({"title": "Kendrick Lamar - LOYALTY. ft. Rihanna", "channel": "KendrickLamarVEVO"}, "Kendrick Lamar, Rihanna", "LOYALTY. FEAT. RIHANNA."), "a title with \"FEAT.\" and no brackets")
check(sv.core_title("J. Cole - Power Trip (Explicit) ft. Miguel", "J. Cole") == "Power Trip", "the artist's name and (Explicit) come off the title")
import random, struct, wave, tempfile
_d = tempfile.mkdtemp(); R = 8000
def _beats(sec, seed):
    rnd = random.Random(seed); out = [0.0] * int(sec * R); t = 0.0
    while t < sec - 0.2:
        amp = rnd.uniform(0.2, 0.9); ln = int(rnd.uniform(0.02, 0.12) * R); st = int(t * R)
        for i_ in range(ln):
            if st + i_ < len(out): out[st + i_] += amp * rnd.uniform(-1, 1) * (1 - i_ / ln)
        t += rnd.choice([0.25, 0.25, 0.5, 0.125, 0.375, 0.75])
    return out
def _wav(name, x):
    pth = os.path.join(_d, name); w = wave.open(pth, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(R)
    w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v)) * 20000)) for v in x)); w.close(); return pth
_song = _beats(150, 1)
_s = _wav("song.wav", _song)
_v = _wav("video.wav", _beats(7.35, 99) + _song)
_e = _wav("edit.wav", _song[:int(80 * R)] + _beats(10, 77) + _song[int(80 * R):])
o1, ok1 = sv.align(_s, _v, 150.0, 157.35)
check(ok1 and abs(o1 - 7.35) < 0.06, f"a video with a 7.35 s intro is lined up {o1} {ok1}")
o2, ok2 = sv.align(_s, _e, 150.0, 160.0)
check(not ok2, f"a different edit (a skit in the middle) is refused {o2} {ok2}")
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
# Soulseek: slskd stuck "Disconnecting" -> Umculo tells it to finish disconnecting and reconnect
MK = f"http://localhost:{int(PORT) + 1000}"
urllib.request.urlopen(MK + "/test/slsk-stuck").read()
ok = False
for _ in range(40):
    st = json.loads(urllib.request.urlopen(MK + "/test/slsk-state").read())
    if st["state"].endswith("LoggedIn") and st["puts"]:
        ok = True; break
    time.sleep(0.5)
check(ok, f"a stuck Soulseek connection is reconnected by itself {st}")
# Soulseek connected but finding nothing: Umculo restarts slskd (with slskd's own login), by itself or from the button
lg = web("/api/slskd/login")
check(lg.get("set") is False, f"no slskd login yet {lg}")
r = web("/api/slskd/restart", "POST")
check("login" in (r.get("error") or "").lower(), f"restart without a login says to add it {r}")
r = web("/api/slskd/login", "POST", {"username": "slsk-admin", "password": "wrong"})
check(r.get("error"), f"a wrong slskd login isn't kept {r}")
r = web("/api/slskd/login", "POST", {"username": "slsk-admin", "password": "s3cret"})
check(r.get("set") and "password" not in json.dumps(web("/api/slskd/login")), f"the right login is kept, and never sent back {r}")
urllib.request.urlopen(MK + "/test/slsk-broken").read()
web("/api/check", "POST", {"text": "Nobody - Nothing Here"})     # a search that finds nothing: worth checking
ok = False
for _ in range(60):
    st = json.loads(urllib.request.urlopen(MK + "/test/slsk-state").read())
    if st["restarts"] >= 1: ok = True; break
    time.sleep(0.5)
check(ok, f"connected but finding nothing: Umculo restarts slskd by itself {st}")
before = st["restarts"]
r = web("/api/slskd/restart", "POST")
st = json.loads(urllib.request.urlopen(MK + "/test/slsk-state").read())
check(r.get("ok") and st["restarts"] == before + 1, f"the Restart Soulseek button restarts it {r} {st}")
# slskd with its web login switched off: restarted without one
web("/api/slskd/login", "POST", {"username": "", "password": ""})
urllib.request.urlopen(MK + "/test/slsk-noauth").read()
lg = web("/api/slskd/login")
check(lg.get("needed") is False and not lg.get("set"), f"slskd without a web login needs none {lg}")
before = json.loads(urllib.request.urlopen(MK + "/test/slsk-state").read())["restarts"]
r = web("/api/slskd/restart", "POST")
st = json.loads(urllib.request.urlopen(MK + "/test/slsk-state").read())
check(r.get("ok") and st["restarts"] == before + 1, f"and the button restarts it {r} {st}")
print(f"V30 OK {n}" if not bad else f"V30 FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
