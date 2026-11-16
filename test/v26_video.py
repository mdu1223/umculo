"""v2.6 video for DJ mixes: a mix has a video to watch (streamed with seeking), it can be saved next to the mix,
the saved copy follows the mix when mixes are tidied, songs that aren't mixes have none.
Usage: python3 test/v26_video.py PORT STATE_DIR"""
import json, os, subprocess, sys, time, urllib.request
PORT, S = sys.argv[1], sys.argv[2]; B = f"http://localhost:{PORT}"; bad = []; n = 0
AUTH = "u=mdu&p=ndpass&f=json"
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)
def raw(path, method="GET", body=None, headers=None):
    rq = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(rq, timeout=60) as r: return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e: return e.code, dict(e.headers), e.read()
def web(path, method="GET", body=None):
    _c, _h, b = raw(path, method, body)
    try: return json.loads(b or b"{}")
    except ValueError: return {}
M = os.path.join(S, "data", "Music"); mixes = os.path.join(M, "meta_podcasts")
mix = os.path.join(mixes, "DJ Test - Deep Session 01.mp3")
os.makedirs(mixes, exist_ok=True)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=6", "-c:a", "libmp3lame", mix], check=True)
import mutagen
f = mutagen.File(mix, easy=True); f.add_tags() if f.tags is None else None
f["title"], f["artist"] = "Deep Session 01", "DJ Test"; f.save()
web(f"/rest/startScan.view?{AUTH}")
song = None
for _ in range(60):
    hits = (web(f"/rest/search3.view?{AUTH}&query=Deep%20Session&songCount=5").get("subsonic-response", {}).get("searchResult3") or {}).get("song") or []
    song = next((x for x in hits if x.get("title") == "Deep Session 01"), None)
    if song: break
    time.sleep(0.5)
check(song, "the mix is in the library")
sid = song["id"] if song else "x"
i = web(f"/api/video/{sid}/info")
check(i.get("mix") and i.get("available") and not i.get("saved"), "a mix has a video to watch")
c, h, b = raw(f"/api/video/{sid}", headers={"Range": "bytes=0-99"})
check(c == 206 and h.get("Content-Type") == "video/mp4" and len(b) == 100, f"the video streams with seeking ({c} {b[:200]!r})")
sv = web(f"/api/video/{sid}/save", "POST")
check(sv.get("ok"), f"save the video {sv}")
side = os.path.join(mixes, ".video", "DJ Test - Deep Session 01.mp4")
for _ in range(40):
    if web(f"/api/video/{sid}/info").get("saved"): break
    time.sleep(0.25)
check(os.path.exists(side), "the video is saved next to the mix, in a hidden folder")
check(web(f"/api/video/{sid}/info").get("saved"), "and it shows as saved")
ngud = web(f"/rest/search3.view?{AUTH}&query=Ngud&songCount=1")["subsonic-response"]["searchResult3"]["song"][0]
ni = web(f"/api/video/{ngud['id']}/info")
check(ni.get("mix") is False and not ni.get("available"), "a normal song has no video")
check("error" in web("/api/video/nope/info"), "unknown song is an error")
r = web("/api/mixes/tidy", "POST")
check(r.get("moved", 0) >= 1, "tidy mixes moves the loose mix into its show's folder")
check(os.path.exists(os.path.join(mixes, "DJ Test", ".video", "DJ Test - Deep Session 01.mp4")) and not os.path.exists(side),
      "the saved video moves with it")
moved_id = None
for _ in range(60):
    web(f"/rest/startScan.view?{AUTH}")
    hits = (web(f"/rest/search3.view?{AUTH}&query=Deep%20Session&songCount=5").get("subsonic-response", {}).get("searchResult3") or {}).get("song") or []
    moved_id = next((x["id"] for x in hits if x["id"] != sid and x.get("title") == "Deep Session 01"), None)
    if moved_id: break
    time.sleep(1)
mi = web(f"/api/video/{moved_id}/info") if moved_id else {}
check(mi.get("saved"), f"after the rescan the moved mix still has its video {mi} {os.listdir(mixes)}")
check(web(f"/api/lib/mixes").get("mixes") and all(".video" not in json.dumps(x) for x in web("/api/lib/mixes")["mixes"]), "video files never show up as mixes")
print(f"V26 OK {n}" if not bad else f"V26 FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
