"""Song finder (3.9.12): a restart carries on where listening stopped (Shazam isn't asked again for bits it
answered), and listening stops when a whole pass finds no new song.
Runs the real song finder on a silent 40-minute mix with a stand-in Shazam that counts what it's asked.
Usage: python3 test/unit_find_resume.py"""
import json, os, subprocess, sys, tempfile, threading
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
T = tempfile.mkdtemp()
for k, v in {"CONFIG_DIR": T + "/config", "MUSIC_DIR": T + "/music", "STAGING_DIR": T + "/staging", "FINDER_STEP_SECONDS": "60",
             "UMCULO_TEST_SHAZAM": T + "/shazam.json"}.items():
    os.environ[k] = v
for d in ("config", "music", "staging"):
    os.makedirs(os.path.join(T, d), exist_ok=True)
SONGS = [(0, 400, "A", "One"), (400, 800, "B", "Two"), (800, 1200, "C", "Three"), (1200, 1600, "D", "Four"),
         (1600, 2000, "E", "Five"), (2000, 2400, "F", "Six")]
json.dump([list(x) for x in SONGS], open(T + "/shazam.json", "w"))
import umculo as core, links
links.core = core
core.CONFIG["staging"] = T + "/staging"
links.CFG["test_shazam"] = T + "/shazam.json"
links.CFG["finder_step"] = 60
links.log = lambda *a: print("   log:", *a) if "carrying on" in " ".join(map(str, a)) or "no new song" in " ".join(map(str, a)) else None
links.save_finds = lambda: None
links.owned = lambda *a: ""
bad = []; n = 0
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)

mix = T + "/mix.mp3"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "2400", "-b:a", "8k", mix], check=True)
asked = []
real = links.recognizer
def counting():
    r = real()
    def rec(wav, off):
        asked.append(off)
        return r(wav, off)
    return rec
links.recognizer = counting

def new_find():
    return {"id": "x1", "title": "Test mix", "show": "", "state": "queued", "entry": {"title": "Test mix", "id": "e1", "description": "", "chapters": [], "duration": 2400},
            "show_data": {"kind": "single", "title": "", "author": "", "cover": "", "feed": "", "url": "", "podcast": False},
            "local": mix, "file": mix, "duration": 2400, "keep": False, "get": False, "tracks": [], "owner": "admin"}

# 1. a full run: every song found, and it stops once a pass finds nothing new
f = new_find(); links.FINDS["x1"] = f
links.run_find(f)
full = list(asked)
names = [t["title"] for t in f["tracks"]]
check(f["state"] == "done" and names == ["One", "Two", "Three", "Four", "Five", "Six"], f"all six songs found {f['state']} {names} {f.get('message')}")
check(len(full) < len(set(full)) + 1 and len(full) <= 40, f"no bit asked twice, and not the whole mix at a bit a minute and more ({len(full)} asked)")
check("bits" not in f, "done: what was asked isn't kept")

# 2. restarted halfway: carries on, asks Shazam only for the rest
asked.clear()
f = new_find(); links.FINDS["x1"] = f
stop_after = len(full) // 2
real_rec = links.recognizer
class Restart(Exception): pass
def stopping():
    r = real_rec()
    def rec(wav, off):
        if len(asked) >= stop_after:
            f["cancel"] = True                       # the server going down for an update
            raise Restart()
        return r(wav, off)
    return rec
links.recognizer = stopping
try:
    links.run_find(f)
except Exception:
    pass
before = dict(f.get("bits") or {})
check(len(before) >= stop_after - 4 and f["state"] != "done", f"stopped halfway, what was heard kept ({len(before)} bits)")
saved = json.loads(json.dumps(f))                    # through finder.json, like a restart
saved.pop("cancel", None); saved["state"] = "queued"; saved.pop("listening_started", None)
links.recognizer = counting
asked.clear()
links.FINDS["x1"] = saved
links.run_find(saved)
again = [o for o in asked if str(o) in before]
check(saved["state"] == "done" and [t["title"] for t in saved["tracks"]] == names, f"after the restart: the same songs {saved['state']} {[t['title'] for t in saved['tracks']]}")
check(not again, f"Shazam isn't asked again for bits answered before the restart {again}")
check(len(asked) + len(before) <= len(full) + 6, f"the run in two halves asks about as much as one ({len(before)} + {len(asked)} vs {len(full)})")
print(f"   asked: one run {len(full)}, halves {len(before)} + {len(asked)}")
print(f"RESUME OK {n}" if not bad else f"RESUME FAILED {len(bad)}/{n}")
os._exit(1 if bad else 0)
