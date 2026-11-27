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

# one line to Shazam: the mix you're playing first, then every mix's first pass, then the rest; at most two at once
import threading
links.FINDS.clear()
G = links.Gate()
order, lock = [], threading.Lock()
bg_refine = {"id": "r", "state": "listening"}
bg_first = {"id": "p", "state": "listening"}
playing = {"id": "w", "state": "listening", "watched": time.time()}
G.busy = G.SLOTS                                   # Shazam busy: everyone queues
def ask(f, rnd, name):
    if G.acquire(f, lambda: rnd):
        with lock: order.append(name)
        time.sleep(0.05); G.release()
ts = [threading.Thread(target=ask, args=a) for a in ((bg_refine, 2, "refine"), (bg_first, 0, "first"), (playing, 3, "playing"),
                                                      (bg_refine, 2, "refine2"))]
for t in ts: t.start(); time.sleep(0.05)
for _ in range(G.SLOTS): G.release()              # Shazam free again
for t in ts: t.join(10)
check(order[:2] == ["playing", "first"] and set(order[2:]) == {"refine", "refine2"}, f"the mix you're playing, then first passes {order}")
peak = []
def ask2(i):
    if G.acquire({"id": str(i)}):
        with lock: peak.append(G.busy)
        time.sleep(0.05); G.release()
ts = [threading.Thread(target=ask2, args=(i,)) for i in range(8)]
for t in ts: t.start()
for t in ts: t.join(10)
check(len(peak) == 8 and max(peak) <= G.SLOTS, f"never more than {G.SLOTS} lookups at once {peak}")
links.PACER.until = time.time() + 1.0             # Shazam asked us to wait: nobody asks before then
t0 = time.time(); G.acquire({"id": "z"}); G.release()
check(time.time() - t0 >= 0.9, "and nobody asks while Shazam says wait")
links.PACER.until = 0
cancelled = {"id": "c", "cancel": True}
check(G.acquire(cancelled) is False and not G.waiting, "a stopped mix leaves the line")

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
