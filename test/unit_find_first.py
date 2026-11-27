"""Song finder: the mix you're playing goes first (3.9.9). Waiting behind other mixes, it starts at once;
while it's listened to, mixes in the background leave it most of Shazam's pace; it isn't listened to twice.
Usage: python3 test/unit_find_first.py"""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import umculo as core, links
links.core = core
bad = []; n = 0
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)

ran = []
real_run = links.run_find
links.run_find = lambda f, started=False: ran.append((f["id"], started))
links.log = lambda *a: None
q = {"id": "q1", "title": "Vol.54", "state": "queued", "entry": {}, "show_data": {}}
links.FINDS.clear(); links.FINDS["q1"] = q
links.watch(q); time.sleep(0.2)
check(ran == [("q1", True)] and q["listening_started"], f"played while waiting its turn: starts now {ran}")
links.watch(q); time.sleep(0.2)
check(len(ran) == 1, "and only once, however often the player asks")
links.run_find = real_run
check(links.run_find(q) is None and q["state"] == "queued", "its turn in the queue comes later: it's already going, nothing happens")

# background mixes make way while the one you're playing is listened to
slept = []
real_sleep = links.time.sleep
links.time.sleep = lambda s: slept.append(s)
bg = {"id": "b1", "state": "listening"}
fg = {"id": "f1", "state": "listening", "watched": time.time()}
links.FINDS.update(b1=bg, f1=fg)
links.make_way(bg)
check(slept == [8], f"a background mix waits while you play another {slept}")
slept.clear(); links.make_way(fg)
check(slept == [], "the one you're playing doesn't wait")
fg["watched"] = time.time() - 60; links.make_way(bg)
check(slept == [], "nobody playing: everyone at full pace")
links.time.sleep = real_sleep

# after a restart an unfinished mix starts over (the "already going" mark doesn't survive)
import json, tempfile
d = tempfile.mkdtemp(); core.CONFIG["config"] = d
json.dump([{"id": "r1", "title": "x", "state": "listening", "listening_started": True}], open(os.path.join(d, "finder.json"), "w"))
loaded = core._load_json("finder.json", [])
check(loaded and loaded[0].get("listening_started"), "(saved mid-listen)")
src = open(links.__file__).read()
check('f.pop("listening_started", None)' in src.split("def after_jobs")[0].split("Resumed after restart")[1][:200],
      "on loading, the mark is cleared so it starts over")
print(f"FINDFIRST OK {n}" if not bad else f"FINDFIRST FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
