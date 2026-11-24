"""v3.8 even volume: the server measures each song's loudness; the web player gets how much to turn each one down,
and music apps get ReplayGain when the file has none of its own.
Usage: python3 test/v33_loudness.py PORT STATE_DIR"""
import json, os, subprocess, sys, time, urllib.request
PORT, S = sys.argv[1], sys.argv[2]; B = f"http://localhost:{PORT}"; bad = []; n = 0
AUTH = "u=mdu&p=ndpass&f=json"
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)
def web(path):
    try:
        with urllib.request.urlopen(B + path, timeout=60) as r: return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e: return {"_code": e.code}
M = os.path.join(S, "data", "Music", "Loudbeat")
os.makedirs(M, exist_ok=True)
for name, vol in (("Loud One", "0dB"), ("Quiet One", "-24dB")):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=40:c=white:a=0.9", "-af", f"volume={vol}",
                    "-metadata", f"title={name}", "-metadata", "artist=Loudbeat", "-metadata", "album=Levels",
                    "-c:a", "libmp3lame", "-b:a", "128k", os.path.join(M, name + ".mp3")], check=True)
web(f"/rest/startScan.view?{AUTH}")
for _ in range(60):
    hits = (web(f"/rest/search3.view?{AUTH}&query=Loudbeat&songCount=9").get("subsonic-response", {}).get("searchResult3") or {}).get("song") or []
    if len(hits) >= 2: break
    time.sleep(0.5)
check(len(hits) >= 2, "the two songs are in the library")
ids = {h["title"]: h["id"] for h in hits}
r = web("/api/loudness?ids=" + ",".join(ids.values()))
check(r.get("target") == -14.0, f"the player is told the level {r}")
t0 = time.time()
for _ in range(120):
    r = web("/api/loudness?ids=" + ",".join(ids.values()))
    if len(r.get("songs", {})) == 2: break
    time.sleep(0.5)
print("measured in %.1f s" % (time.time() - t0)); s = r.get("songs", {})
check(len(s) == 2, f"both songs measured once asked for {r}")
loud, quiet = s.get(ids.get("Loud One"), {}), s.get(ids.get("Quiet One"), {})
check(loud.get("lufs", 0) - quiet.get("lufs", 0) > 15, f"the loud one measures louder {loud} {quiet}")
check(loud.get("gain", 0) < -1, f"the loud one is turned down {loud}")
check(quiet.get("gain", 0) > loud.get("gain", 0) + 15, f"the quiet one much less (or up) {quiet}")
g = web(f"/rest/getSong.view?{AUTH}&id={ids.get('Loud One')}").get("subsonic-response", {}).get("song", {})
rg = g.get("replayGain") or {}
check(isinstance(rg.get("trackGain"), (int, float)) and rg["trackGain"] < 0, f"music apps get ReplayGain {rg}")
check(0 < rg.get("trackPeak", 0) < 4, f"with the peak {rg}")
check(web("/api/loudness?ids=nope").get("songs") == {}, "an unknown song is just left out")
if bad:
    import sqlite3
    db = sqlite3.connect(os.path.join(S, "config", "library.db"))
    print("library:", db.execute("SELECT id, lib, path FROM song WHERE artist='Loudbeat'").fetchall(), "ids:", ids)
    print("now:", web("/api/loudness?ids=" + ",".join(ids.values())))
    try: print("".join(l for l in open(os.path.join(S, "app.log")) if "even volume" in l)[-1500:])
    except OSError: pass
print(("V33 OK %d" % n) if not bad else ("V33 FAILED %d/%d" % (len(bad), n)))
sys.exit(1 if bad else 0)
