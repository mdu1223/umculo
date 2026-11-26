"""v3.7 right versions: a song whose catalogue recording (Deezer's 30-second preview) isn't in the file is listed
under Library care > Wrong versions; "It's fine" puts it away; "Get the right version" downloads it again.
Usage: python3 test/v32_versions.py PORT STATE_DIR"""
import json, os, random, struct, subprocess, sys, time, urllib.request, wave
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
R = 8000
def beats(sec, seed):
    rnd = random.Random(seed); out = [0.0] * int(sec * R); t = 0.0
    while t < sec - 0.2:
        amp = rnd.uniform(0.2, 0.9); ln = int(rnd.uniform(0.02, 0.12) * R); st = int(t * R)
        for i in range(ln):
            if st + i < len(out): out[st + i] += amp * rnd.uniform(-1, 1) * (1 - i / ln)
        t += rnd.choice([0.25, 0.25, 0.5, 0.125, 0.375, 0.75])
    return out
def mp3(path, x, title=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    w = path + ".wav"; f = wave.open(w, "wb"); f.setnchannels(1); f.setsampwidth(2); f.setframerate(R)
    f.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, v)) * 20000)) for v in x)); f.close()
    meta = ["-metadata", f"title={title}", "-metadata", "artist=Versionbeat", "-metadata", "album=Versions"] if title else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", w, *meta, "-c:a", "libmp3lame", "-b:a", "128k", path], check=True)
    os.remove(w)
right_rec, other_rec, wrong_rec = beats(150, 11), beats(150, 22), beats(150, 33)
M = os.path.join(S, "data", "Music", "Versionbeat")
mp3(os.path.join(M, "Right One.mp3"), right_rec, "Right One")          # the album recording
mp3(os.path.join(M, "Wrong One.mp3"), wrong_rec, "Wrong One")          # not the catalogue's recording
mp3(os.path.join(S, "vpreview", "right.mp3"), right_rec[40 * R:70 * R])
mp3(os.path.join(S, "vpreview", "wrong.mp3"), other_rec[40 * R:70 * R])
web(f"/rest/startScan.view?{AUTH}")
for _ in range(60):
    hits = (web(f"/rest/search3.view?{AUTH}&query=Versionbeat&songCount=9").get("subsonic-response", {}).get("searchResult3") or {}).get("song") or []
    hits = [h for h in hits if h.get("artist") == "Versionbeat"]       # not the catalogue's suggestions
    if len(hits) >= 2: break
    time.sleep(0.5)
check(len(hits) >= 2, "the two songs are in the library")
v = web("/api/versions/scan", "POST")
check(v.get("running") is True or v.get("done") is not None, f"the check starts {v}")
for _ in range(240):
    v = web("/api/versions")
    if not v.get("running"): break
    time.sleep(0.5)
sus = [x["title"] for x in v.get("suspects", [])]
check("Wrong One" in sus, f"a song that isn't the catalogue's recording is listed {sus} {v.get('done')}/{v.get('total')}")
check("Right One" not in sus, f"the right recording isn't {sus}")
wid = next((x["id"] for x in v.get("suspects", []) if x["title"] == "Wrong One"), None)
r = web("/api/versions/fix", "POST", {"ids": [wid]})
check(r.get("queued") == 1 and r.get("job"), f"get the right version: queued {r}")
jl = web("/api/playlists").get("jobs", [])
check(any(j.get("name") == "Right versions" for j in jl), "as a download job")
for _ in range(120):          # the mock sources don't have it: it says why (Soulseek silent: it tries again by itself)
    x = next((x for x in web("/api/versions").get("suspects", []) if x["id"] == wid), {})
    if x.get("fix_note") and not any(w in x["fix_note"] for w in ("Looking", "Getting", "Downloading")): break
    time.sleep(0.5)
check(x and x.get("fix_note") and (not x.get("fixing") or "trying again" in x["fix_note"]), f"not got: on the list, saying why {x}")
j = next(j for j in web("/api/playlists").get("jobs", []) if j.get("name") == "Right versions")
jd = web(f"/api/playlists/jobs/{j['id']}")
t0 = (jd.get("tracks") or [{}])[0]
check(t0.get("quality") and t0.get("any_quality") is True, f"the job knows the copy's quality, and takes any quality {t0.get('quality')} {t0.get('any_quality')}")
check(web("/api/versions/fix", "POST", {"ids": [wid]}).get("queued") == (0 if x.get("fixing") else 1), "tried again when it isn't already")
for _ in range(120):
    x = next((x for x in web("/api/versions").get("suspects", []) if x["id"] == wid), {})
    if not x.get("fixing"): break
    time.sleep(0.5)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import versions as V
for art, path, want in (("artist", "/m/Umculo/Caiphus Semenya/Caiphus Semenya - Angelina.mp3", "Caiphus Semenya"),
                        ("", "/m/Umculo/Lauryn Hill/Ex-Factor.flac", "Lauryn Hill"),
                        ("Unknown Artist", "/m/x/artist/artist - Song.mp3", "Unknown Artist"),
                        ("Chief Keef", "/m/Umculo/Chief Keef/Love Sosa.wav", "Chief Keef")):
    check(V.real_artist(art, path) == want, f"artist for {art!r} {path}: {V.real_artist(art, path)!r}")
r = web("/api/versions/dismiss", "POST", {"ids": [wid]})
check("Wrong One" not in [x["title"] for x in r.get("suspects", [])], "It's fine: put away")
r = web("/api/versions/scan", "POST")
for _ in range(60):
    v = web("/api/versions")
    if not v.get("running"): break
    time.sleep(0.5)
check(v.get("total") == 0, f"checked songs aren't checked again {v.get('total')}")
print(f"V32 OK {n}" if not bad else f"V32 FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
