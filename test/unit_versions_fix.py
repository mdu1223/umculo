"""Wrong versions, the fix: a family member's song is fixed as them (into their folder), any quality will do,
finished fixes leave the list, songs not got come back saying why, and the repair of fixes from before 3.9.3.
Usage: python3 test/unit_versions_fix.py"""
import os, sys, tempfile, threading, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import versions as V
bad = []; n = 0
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)

T = tempfile.mkdtemp()
lib, fam = os.path.join(T, "Music", "Umculo"), os.path.join(T, "Family")
def touch(p, data=b"x"):
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "wb").write(data); return p
within = lambda p, root: os.path.normpath(p).startswith(os.path.normpath(root) + os.sep)
ADMIN, KHAYA = {"id": "a1", "username": "mdu", "role": "admin"}, {"id": "k1", "username": "khaya", "role": "user"}
local = threading.local(); local.user = ADMIN
made, moved_refs = [], []
def new_job(name, kind, m3u, tracks):
    jid = f"j{len(made)}"
    core.JOBS[jid] = {"id": jid, "name": name, "kind": kind, "tracks": tracks, "created": len(made) + 10, "user": local.user["id"]}
    for i, t in enumerate(tracks): t.update(i=i, state="pending", new_file="", message="")
    made.append(core.JOBS[jid]); return jid
users = types.SimpleNamespace(LOCAL=local, current=lambda: local.user, can=lambda what: True, admin_user=lambda: ADMIN,
                              by_name=lambda name: {"mdu": ADMIN, "khaya": KHAYA}.get(name),
                              folder=lambda u: os.path.join(fam, u["username"]),
                              music_root=lambda uid: os.path.join(fam, "khaya", "Music") if uid == "k1" else lib)
core = types.SimpleNamespace(CONFIG={"family": fam, "library": lib}, JOBS={}, JOB_LOCK=threading.Lock(), users=users,
                             within=within, new_job=new_job, file_quality=lambda p: ("lossy", "MP3 320"),
                             replace_everywhere=lambda a, b: moved_refs.append((a, b)), save_jobs=lambda: None,
                             request_scan=lambda: None, log=lambda *a: None, plural=lambda k, w: f"{k} {w}s",
                             _save_json=lambda *a: None)
V.core = core

# 1. fix: grouped by whose folder the song is in, any quality, the copy's quality known, a placeholder artist mended
mine = touch(os.path.join(lib, "Caiphus Semenya", "Caiphus Semenya - Angelina.mp3"))
his = touch(os.path.join(fam, "khaya", "Music", "Lionel Richie", "Lionel Richie - Stuck on You.flac"))
V.RESULTS.clear()
V.RESULTS[mine] = {"verdict": "wrong", "id": "m", "artist": "artist", "title": "Angelina", "duration": 200, "method": V.METHOD}
V.RESULTS[his] = {"verdict": "wrong", "id": "h", "artist": "Lionel Richie", "title": "Stuck on You", "duration": 190, "method": V.METHOD}
r = V.fix(["m", "h"])
check(r["queued"] == 2 and len(made) == 2, f"two songs, two owners, two jobs {r} {len(made)}")
owners = {j["tracks"][0]["title"]: j["user"] for j in made}
check(owners == {"Angelina": "a1", "Stuck on You": "k1"}, f"each fixed as the person whose folder it's in {owners}")
check(local.user is ADMIN, "back to the person who pressed it")
ta = next(j for j in made if j["user"] == "a1")["tracks"][0]
check(ta["artist"] == "Caiphus Semenya" and ta["any_quality"] and ta["quality"] == "MP3 320" and ta["replace"],
      f"placeholder artist mended, any quality, its quality known {ta}")
check(V.fix(["m"])["queued"] == 0, "not twice while it's being fixed")

# 2. settle: done -> off the list; not got -> back, saying why; still going -> still fixing, with its progress
newa = touch(os.path.join(lib, "Caiphus Semenya", "Caiphus Semenya - Angelina.flac"))
ta.update(state="done", new_file=newa, message="Upgraded from MP3 320")
th = next(j for j in made if j["user"] == "k1")["tracks"][0]
th.update(state="downloading", message="Downloading from Soulseek")
v = V.view()
ids = {x["id"]: x for x in v["suspects"]}
check("m" not in ids, f"fixed: off the list {list(ids)}")
check(ids.get("h", {}).get("fixing") and "Soulseek" in (ids["h"].get("fix_note") or ""), f"still going, with what it's doing {ids.get('h')}")
th.update(state="kept", message="Waiting for your quality: only OPUS 133 found (best effort is off)")
x = {x["id"]: x for x in V.view()["suspects"]}["h"]
check(not x["fixing"] and "OPUS 133" in x["fix_note"], f"not got: back, saying why {x}")

# 3. repair (before 3.9.3): khaya's song had gone into the main library -> back in his folder; held-back ones retried
made.clear(); core.JOBS.clear(); moved_refs.clear()
wrong_in_lib = touch(os.path.join(lib, "Lyfe Jennings", "Lyfe Jennings - Must Be Nice.flac"), b"right")
gone = os.path.join(fam, "khaya", "Music", "Lyfe Jennings", "Lyfe Jennings - Must Be Nice.mp3")   # deleted by the old fix
core.JOBS["old"] = {"id": "old", "name": "Right versions", "created": 1, "user": "a1", "tracks": [
    {"replace": True, "state": "done", "file": gone, "new_file": wrong_in_lib, "title": "Must Be Nice"},
    {"replace": True, "state": "kept", "file": his, "new_file": "", "title": "Stuck on You",
     "message": "Waiting for your quality: only OPUS 133 found (best effort is off)"}]}
V.RESULTS.clear()
V.RESULTS[his] = {"verdict": "wrong", "id": "h", "artist": "Lionel Richie", "title": "Stuck on You", "fixing": True, "method": V.METHOD}
V.repair()
back = os.path.join(fam, "khaya", "Music", "Lyfe Jennings", "Lyfe Jennings - Must Be Nice.flac")
check(os.path.exists(back) and not os.path.exists(wrong_in_lib), "the family member's song is back in their folder")
check(moved_refs == [(wrong_in_lib, back)], f"and playlists follow it {moved_refs}")
check(len(made) == 1 and made[0]["user"] == "k1" and made[0]["tracks"][0]["any_quality"], f"the held-back song is got again, as its owner {made}")
V.repair()
check(len(made) == 1, "the repair does nothing the second time")
print(f"VFIX OK {n}" if not bad else f"VFIX FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
