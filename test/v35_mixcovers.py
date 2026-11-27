"""v3.9.7 a mix's own cover: a channel's mixes are filed in one album, but each has its own picture
("Pick up where you left off" showed one mix's cover on all of them). Short songs keep their album's cover.
Usage: python3 test/v35_mixcovers.py PORT STATE_DIR"""
import hashlib, json, os, subprocess, sys, time, urllib.request
PORT, S = sys.argv[1], sys.argv[2]; B = f"http://localhost:{PORT}"; bad = []; n = 0
AUTH = "u=mdu&p=ndpass&f=json"
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)
def get(path):
    with urllib.request.urlopen(B + path, timeout=60) as r: return r.read()
def rest(path): return json.loads(get(path)).get("subsonic-response", {})

D = os.path.join(S, "data", "Music", "Umculo", "Stay True Sounds")
os.makedirs(D, exist_ok=True)
pics = {}
for c, secs in (("red", 1260), ("blue", 1260), ("lime", 200)):
    jpg = os.path.join(S, f"{c}.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={c}:s=64x64", "-frames:v", "1", jpg], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-i", jpg, "-t", str(secs),
                    "-map", "0:a", "-map", "1", "-c:a", "libmp3lame", "-b:a", "8k", "-c:v", "mjpeg", "-disposition:v", "attached_pic",
                    "-metadata", "artist=Stay True Sounds", "-metadata", "album=Stay True Sounds", "-metadata", f"title=Mix {c}",
                    os.path.join(D, f"Stay True Sounds - Mix {c}.mp3")], check=True)
rest(f"/rest/startScan.view?{AUTH}")
songs = []
for _ in range(120):
    songs = [s for s in (rest(f"/rest/search3.view?{AUTH}&query=Mix&songCount=20").get("searchResult3") or {}).get("song") or []
             if s.get("title", "").startswith("Mix ")]
    if len(songs) == 3: break
    time.sleep(0.5)
check(len(songs) == 3, f"three songs in one album {[(s.get('title'), s.get('albumId')) for s in songs]}")
by = {s["title"]: s for s in songs}
if len(by) == 3:
    check(len({s["albumId"] for s in songs}) == 1, "filed in one album (the channel)")
    check(by["Mix red"]["coverArt"] == by["Mix red"]["id"] and by["Mix blue"]["coverArt"] == by["Mix blue"]["id"], "a mix's cover is its own")
    check(by["Mix lime"]["coverArt"] == by["Mix lime"]["albumId"], "a short song keeps its album's cover")
    h = {t: hashlib.md5(get(f"/rest/getCoverArt.view?{AUTH}&id={by[t]['coverArt']}&size=64")).hexdigest() for t in by}
    check(h["Mix red"] != h["Mix blue"], f"two mixes of one channel, two covers {h}")
print(f"MIXCOVERS OK {n}" if not bad else f"MIXCOVERS FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
