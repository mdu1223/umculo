"""Song finder: the songs heard and the published tracklist make one clean list (3.9.5).
Real case: Stay True Sounds Stream Vol.56 (Kid Fonque), 11 songs listed in the description, no times.
Usage: python3 test/unit_song_merge.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
import umculo as core, links
links.core = core
bad = []; n = 0
def check(ok, what):
    global n; n += 1
    if not ok: bad.append(what); print("FAIL", what)

HEARD = [(30, "isoulation", "GotMe", 3), (300, "Sooks & Dwson", "Before The Hunt", 4),
         (840, "Da Real Motive & Status Qou SA", "1000 Reasons", 1), (975, "Stones & Bones", "Amahloni (feat. TOSHI) [Eyeronik Remix]", 1),
         (1020, "Alexkid", "Love We Have (Kudbeu & Kana Remix)", 5), (1380, "RoneeDeep & WAPO Jije", "Memories", 3),
         (1695, "Chronical Deep", "Imali", 4), (1965, "PAGEZ, Dr. Feel & Beekay Monalayzzar", "Was You (feat. Kali Mija)", 4),
         (2325, "Thabo Tonick & Hypaphonik", "Basstrap", 2), (2820, "FKA MASH & Biishop", "Love In The Air", 1),
         (2910, "Italobox", "Under the Moon (Dance Mix)", 1)]
LISTED = [("isoulation", "GotMe"), ("!Sooks X Dwson", "BTH"), ("beatsbyhand", "Invited"), ("Alexkid", "Love We Have (Kudbeu & Kana Remix)"),
          ("Roneedeep", "Memories Revision ft WAPO Jije"), ("Chronical Deep", "Imali"),
          ("PAGEZ, Dr Feel, Beekay Monalayzzar", "Was You ft Kali Mija"), ("Thabo Tonick & Hypaphonik", "Basstrap "),
          ("Donna Allen", "He Is The Joy (Thabo Tonick Dub Print)"), ("Justin Martin", "Sad Piano (Charles Webster Remix)"),
          ("Atjazz", "Track 4 (Mix 2)")]
heard = [{"t": t + i * 60, "artist": a, "title": ti} for t, a, ti, k in HEARD for i in range(k)]
listed = [{"t": None, "artist": a, "title": t} for a, t in LISTED]
out = links.merge_tracks(listed, heard, 3453)
names = [t["title"] for t in out]
check(len(out) == 11, f"the 11 songs of the tracklist, no more {len(out)} {names}")
check(names[1] == "Before The Hunt" and out[1].get("listed_as") == "!Sooks X Dwson - BTH", f"BTH is Before The Hunt (the recognised name kept) {out[1]}")
check(sum(1 for x in names if x.startswith("Was You")) == 1 and sum(1 for x in names if x.startswith("Memories")) == 1,
      "'ft' without brackets, 'Dr Feel' / 'Dr. Feel', 'Revision': one song each")
for gone in ("1000 Reasons", "Love In The Air", "Under the Moon (Dance Mix)"):
    check(gone not in names, f"heard once and not on the tracklist: misheard, left out ({gone})")
order = [t["title"].split(" (")[0] for t in out]
check(order == ["GotMe", "Before The Hunt", "Invited", "Love We Have", "Memories", "Imali", "Was You", "Basstrap", "He Is The Joy",
                "Sad Piano", "Track 4"], f"in the tracklist's order {order}")
inv = out[2]
check(inv["t"] is None and 300 < inv.get("about", 0) < 1020, f"a song only listed is placed between its neighbours {inv}")
check(all(t.get("about", 0) < 3453 for t in out), "and inside the mix")
check([t["i"] for t in out] == list(range(11)), "numbered in order")

S = links.same_song
for a, b, want, what in (
        (("Alexkid", "Love We Have (Kudbeu & Kana Remix)"), ("Alexkid", "Love We Have (Atjazz Remix)"), False, "two remixes"),
        (("Alexkid", "Love We Have"), ("Someone Else", "Love We Have"), False, "two artists"),
        (("A Band", "Love"), ("A Band", "Love Again"), False, "a longer title"),
        (("Black Coffee", "WTF"), ("Black Coffee", "We Dance Again"), False, "initials that don't fit"),
        (("Black Coffee", "WDA"), ("Black Coffee", "We Dance Again"), True, "initials"),
        (("Shimza", "Bambelela (Extended Mix)"), ("Shimza", "Bambelela"), True, "extended mix"),
        (("Zakes Bantwini", "Osama ft Kasango"), ("Zakes Bantwini & Kasango", "Osama"), True, "ft in the title"),
        (("Beyoncé", "Halo"), ("Beyonce", "Halo"), True, "accents"),
        (("Culoe De Song", "Y.O.U"), ("Culoe De Song", "Webaba"), False, "a dotted title isn't initials")):
    got = S({"artist": a[0], "title": a[1]}, {"artist": b[0], "title": b[1]})
    check(got is want, f"same song? {what}: {a} / {b} -> {got}")

# without a tracklist, songs heard once stay (no list to check them against)
solo = links.merge_tracks([], [{"t": 60, "artist": "A", "title": "One"}, {"t": 400, "artist": "B", "title": "Two"}], 900)
check(len(solo) == 2, f"no tracklist: everything heard stays {solo}")
# tidy on a list found before 3.9.5 (no tracklist order kept): doubles and misheard go
old = [{"t": 300, "artist": "Sooks & Dwson", "title": "Before The Hunt", "from": "heard", "hits": 4},
       {"t": 840, "artist": "Da Real Motive", "title": "1000 Reasons", "from": "heard", "hits": 1},
       {"t": 1380, "artist": "RoneeDeep & WAPO Jije", "title": "Memories", "from": "heard", "hits": 3},
       {"t": 30, "artist": "isoulation", "title": "GotMe", "from": "both", "hits": 3},
       {"t": 1695, "artist": "Chronical Deep", "title": "Imali", "from": "both", "hits": 4},
       {"t": None, "artist": "!Sooks X Dwson", "title": "BTH", "from": "tracklist", "hits": 0, "owned": ""},
       {"t": None, "artist": "Roneedeep", "title": "Memories Revision ft WAPO Jije", "from": "tracklist", "hits": 0},
       {"t": None, "artist": "beatsbyhand", "title": "Invited", "from": "tracklist", "hits": 0}]
t2 = links.tidy(old, 3453)
check([t["title"] for t in t2] == ["GotMe", "Before The Hunt", "Memories", "Imali", "Invited"], f"an old list tidied {[t['title'] for t in t2]}")
check(links.tidy(t2, 3453) == t2, "tidying twice changes nothing")
# found before 3.9.5 (no tracklist order kept): the order is read again from the description kept with the mix
desc = "\n".join(f"{i + 1} - {a} - {t}" for i, (a, t) in enumerate(LISTED))
f = {"duration": 3453, "entry": {"description": desc}, "tracks": [{**t, "t": t.get("t")} for t in t2 if True] + [
    {"t": None, "artist": "Atjazz", "title": "Track 4 (Mix 2)", "from": "tracklist", "hits": 0}]}
for t in f["tracks"]:
    t.pop("n", None); t.pop("about", None)
links.order_by_tracklist(f)
t3 = links.tidy(f["tracks"], 3453)
inv = next(t for t in t3 if t["title"] == "Invited")
check(300 < (inv.get("about") or 0) < 1380 and [t["title"] for t in t3][2] == "Invited", f"an old list: placed by the description {[(t['title'], t.get('about')) for t in t3]}")
check(t3[-1]["title"] == "Track 4 (Mix 2)" and t3[-1].get("about"), "the last song of the tracklist at the end, with a time")
print(f"MERGE OK {n}" if not bad else f"MERGE FAILED {len(bad)}/{n}")
sys.exit(1 if bad else 0)
