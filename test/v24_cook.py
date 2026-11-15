"""v2.4 Cook: styles fold correctly, Cook locally stays in style, Just cook brings songs you don't have,
Library lists DJ mixes. Usage: python3 test/v24_cook.py PORT STATE_DIR"""
import json, os, sys, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import cook  # noqa: E402
PORT = sys.argv[1]; B = f"http://localhost:{PORT}"; bad = []; n = 0
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)
def web(path):
    try:
        with urllib.request.urlopen(B + path, timeout=60) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e: return json.loads(e.read() or b"{}")
for g, fam in [("Afro-House", "Afro House"), ("Afro Tech", "Afro House"), ("Soulful House", "Deep House"), ("Amapiano", "Amapiano"),
               ("3-Step", "Gqom"), ("Hip-Hop", "Hip Hop"), ("Rap/Hip Hop", "Hip Hop"), ("Neo-Soul", "R&B"), ("Other", None), ("", None)]:
    check(cook.style(g)[0] == fam, f"style {g!r} -> {fam}")
check(cook.style("Afro House")[1] == cook.style("Deep House")[1] == "House", "afro and deep house share a category")
songs = web("/rest/search3.view?u=mdu&p=ndpass&f=json&query=&songCount=200")["subsonic-response"]["searchResult3"]["song"]
genre = {s["id"]: s.get("genre") or "" for s in songs}
seed = next(s for s in songs if s["title"] == "Ngud")
r = web(f"/api/cook/local/{seed['id']}")
check(r.get("style") == "Hip Hop", "seed style is hip hop")
check(r["songs"][0]["id"] == seed["id"], "the seed plays first")
check(len(r["songs"]) >= 3 and all(cook.style(genre.get(s["id"]))[0] in ("Hip Hop", None) for s in r["songs"]), "nothing from another style")
check(not any("Amapiano" in genre.get(s["id"], "") for s in r["songs"]), "no amapiano in a hip hop cook")
amap = next(s for s in songs if genre[s["id"]] == "Amapiano")
r2 = web(f"/api/cook/local/{amap['id']}")
check(all(genre.get(s["id"]) in ("Amapiano",) for s in r2["songs"]), "amapiano cook stays amapiano")
nw = web(f"/api/cook/new/{seed['id']}?n=20")
check(nw.get("internet") and nw.get("songs"), "just cook returns internet songs")
have = {(s.get("artist", "").lower(), s["title"].lower()) for s in songs}
check(not any((x["artist"].lower(), x["title"].lower()) in have for x in nw.get("songs", [])), "just cook skips songs you have")
check(web("/api/cook/new/dz/111?n=10").get("songs") is not None, "just cook from a streamed song")
check("error" in web("/api/cook/local/nope"), "unknown song is an error, not a crash")
check(web("/api/lib/mixes").get("mixes") == [], "no mixes in this library yet")
check(isinstance(web("/api/lib/playlists").get("playlists"), list), "library lists playlists")
print(f"V24 OK {n}" if not bad else f"V24 FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
