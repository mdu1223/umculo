"""Radio: an endless station from an artist, a style, a mood or an era, in your words.

  "Kwaito from the 90s"   -> kwaito released 1990-1999: yours first, songs you don't know mixed in
  "chill sunday"          -> the styles that mood means (Deep House, R&B, Jazz...)
  "Kabza De Small"        -> the artist, artists close to them, in their style
When the station runs low, keep cooking carries on in the same style (the station's name is its hint).

GET /api/radio?q=<words>[&new=0..3]     -> {"name", "style", "songs": [...]}   new: how many of every 3 are new to you
GET /api/radio/stations                  -> {"stations": [{"q", "name", "sub"}]}   suggestions from your own library
"""
import random
import re
import time

core = None

MOODS = {
    "chill": ["Deep House", "R&B", "Lo-fi", "Jazz"], "relax": ["Deep House", "R&B", "Lo-fi", "Jazz"],
    "party": ["Amapiano", "Gqom", "Afro House", "Bacardi", "Afrobeats"], "turn up": ["Amapiano", "Gqom", "Bacardi"],
    "braai": ["Amapiano", "Kwaito", "Afro House", "Deep House", "Maskandi"], "gym": ["Gqom", "Hip Hop", "Electronic", "Techno"],
    "workout": ["Gqom", "Hip Hop", "Electronic", "Techno"], "run": ["Gqom", "Hip Hop", "Electronic"],
    "sunday": ["Gospel", "Deep House", "Jazz", "R&B"], "focus": ["Lo-fi", "Jazz", "Classical", "Deep House"],
    "work": ["Lo-fi", "Deep House", "Jazz"], "love": ["R&B", "Jazz"], "romance": ["R&B", "Jazz"], "romantic": ["R&B", "Jazz"],
    "sleep": ["Lo-fi", "Classical", "Jazz"], "drive": ["Deep House", "Afro House", "Hip Hop", "R&B"],
    "road trip": ["Afro House", "Amapiano", "Kwaito", "Afrobeats"], "dance": ["Amapiano", "Afro House", "House", "Gqom"],
}
OLD_SCHOOL = re.compile(r"\b(old ?school|throwbacks?|classics?|golden era)\b", re.I)


def me():
    return core.users.current()


def parse(text):
    """-> {"fams": [...], "years": (from, to) or None, "mood": word or "", "rest": words left over}"""
    t = " " + (text or "").lower() + " "
    years = None
    m = re.search(r"\b(?:(19|20)?(\d)0)'?s\b", t)
    if m:
        cent = m.group(1) or ("20" if m.group(2) in "012" else "19")
        y0 = int(cent + m.group(2) + "0")
        years = (y0, y0 + 9)
        t = t.replace(m.group(0), " ")
    m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", t)
    if not years and m:
        years = (int(m.group(1)), int(m.group(1)))
        t = t.replace(m.group(0), " ")
    if not years and OLD_SCHOOL.search(t):
        years = (1950, 2008)
    fams = []
    folded = core.cook.fold(t)
    for fam, _cat, words in core.cook.FAMILIES:
        if any(f" {w} " in folded for w in words) and fam not in fams:
            fams.append(fam)
            for w in words:
                folded = folded.replace(f" {w} ", " ")
    mood = ""
    for k, lst in MOODS.items():
        if f" {k} " in folded:
            mood = k
            if not fams:
                fams = list(lst)
            folded = folded.replace(f" {k} ", " ")
    rest = re.sub(r"\b(from|the|of|in|and|music|songs?|radio|station|mix|vibes?|some|me|play|old ?school|throwbacks?|classics?)\b", " ",
                  folded).strip()
    return {"fams": fams, "years": years, "mood": mood, "rest": re.sub(r"\s+", " ", rest)}


def lib_artist(name):
    """A song of yours by this artist, if you have one."""
    srv = core.server
    rows = core.cook.lib_rows(me())
    n = core.norm(name)
    return [r for r in rows if core.norm(core.musicdb.primary(r["artist"] or "")) == n or core.norm(r["albumartist"] or "") == n]


def library_part(fams, years, artists=None, n=60):
    rows = core.likes.rows_for(me())
    by_artist = core.cook.artist_styles(rows)
    hearts = set(core.likes.starred_ids(me()))
    near = {core.norm(a) for a in (artists or [])}
    out = []
    for r in rows:
        f, _c = core.cook.song_style(r, by_artist)
        if fams and f not in fams:
            continue
        if years and not (r["year"] and years[0] <= r["year"] <= years[1]):
            continue
        if near and core.norm(core.musicdb.primary(r["artist"] or "")) not in near and not fams:
            continue
        out.append(r)
    random.shuffle(out)
    out.sort(key=lambda r: 0 if r["id"] in hearts and random.random() < 0.6 else 1)
    return core.likes.spread(out, n)


def internet_part(q, fams, years, artist=None, n=30):
    try:
        if artist:
            items, _l = core.cook.cook_new(me(), artist, "", n, fams[0] if fams else None, None)
            return items
        terms = []
        dec = f" {str(years[0])[2:]}s" if years and years[1] - years[0] == 9 else ""
        for f in (fams or [q])[:3]:
            terms.append(f + dec)
        songs = []
        for term in terms:
            songs += core.recipes.station(term, "mix")[: max(10, n // len(terms))]
        known = core.cook.known_keys(me())
        with core.foryou.LOCK:
            banned = set(core.foryou.STATE["dislikes"])
        return [s for s in songs if s.get("key") not in known and s.get("key") not in banned]
    except Exception as ex:  # noqa: BLE001
        core.log("radio: internet songs failed:", ex)
        return []


def build(q, new_share=1):
    q = (q or "").strip()[:120]
    if not q:
        raise ValueError("Say what you'd like: an artist, a style, a mood or an era")
    p = parse(q)
    artist, related = None, []
    if not p["fams"] and p["rest"]:                     # no style or mood in it: an artist
        artist = q if not p["years"] else p["rest"]
        mine = lib_artist(artist)
        if mine:
            artist = mine[0]["artist"]
            by = core.cook.artist_styles(core.cook.lib_rows(me()))
            f, _c = core.cook.song_style(mine[0], by)
            if f:
                p["fams"] = [f]
        else:
            try:
                f, _c = core.cook.dz_style_of(artist, "")
                if f:
                    p["fams"] = [f]
            except Exception:  # noqa: BLE001
                pass
        rel, _ = core.cook.related_names(artist)
        related = [artist] + [a["name"] for a in rel]
    lib = library_part(p["fams"], p["years"], related if artist else None)
    if artist:                                          # the artist and their circle come first, in the style
        near = {core.norm(a) for a in related}
        lib.sort(key=lambda r: 0 if core.norm(core.musicdb.primary(r["artist"] or "")) in near else 1)
    srv = core.server
    an = srv.anns(me(), [r["id"] for r in lib])
    lib_items = [{**core.libview.song_view(srv.song(r, an.get(r["id"]))), "lib": True} for r in lib]
    want_new = max(0, min(3, int(new_share)))
    net = internet_part(q, p["fams"], p["years"], artist) if want_new or len(lib_items) < 15 else []
    random.shuffle(net)
    songs, li, ni = [], 0, 0
    pattern = [True] * (3 - want_new) + [False] * want_new if lib_items and net else ([True] if lib_items else [False])
    while (li < len(lib_items) or ni < len(net)) and len(songs) < 80:
        for from_lib in pattern:
            if from_lib and li < len(lib_items):
                songs.append(lib_items[li]); li += 1
            elif not from_lib and ni < len(net):
                songs.append(net[ni]); ni += 1
            elif li < len(lib_items):
                songs.append(lib_items[li]); li += 1
            elif ni < len(net):
                songs.append(net[ni]); ni += 1
    if not songs:
        raise LookupError("Nothing found for that yet. Try other words, or a style you have")
    name = (artist + " radio") if artist else q[:1].upper() + q[1:]
    style = ", ".join(p["fams"][:3]) + (f" · {p['years'][0]}–{p['years'][1]}" if p["years"] else "")
    return {"name": name, "style": style, "songs": songs}


def stations():
    """Suggestions from your own library: your styles, the decades you have most of them in, your most played artists."""
    if not core.builtin():
        return []
    rows = core.likes.rows_for(me())
    by_artist = core.cook.artist_styles(rows)
    count, dec = {}, {}
    for r in rows:
        f, _c = core.cook.song_style(r, by_artist)
        if not f:
            continue
        count[f] = count.get(f, 0) + 1
        if r["year"] and r["year"] < time.gmtime().tm_year - 8:
            k = (f, r["year"] // 10 * 10)
            dec[k] = dec.get(k, 0) + 1
    out = [{"q": f, "name": f, "sub": f"{n} of yours, and more"} for f, n in sorted(count.items(), key=lambda x: -x[1])[:6]]
    for (f, d), n in sorted(dec.items(), key=lambda x: -x[1])[:3]:
        if n >= 12:
            out.append({"q": f"{f} from the {str(d)[2:]}s", "name": f"{f} from the {str(d)[2:]}s", "sub": f"{n} of yours"})
    srv = core.server
    top = srv.db.q("""SELECT song.artist AS a, SUM(ann.plays) AS p FROM ann JOIN song ON song.id = ann.item
                      WHERE ann.user=? AND ann.kind='song' AND ann.plays > 0 GROUP BY song.albumartist_id ORDER BY p DESC LIMIT 4""",
                   (srv.uid(me()),))
    for t in top:
        a = core.musicdb.primary(t["a"] or "")
        if a:
            out.append({"q": a, "name": f"{a} radio", "sub": "Them and artists like them"})
    for m in ("chill", "party", "sunday", "workout"):
        out.append({"q": m, "name": m.title(), "sub": ", ".join(MOODS[m][:3])})
    return out


def handle_get(path, qs):
    if path == "/api/radio":
        return build((qs.get("q") or [""])[0], int((qs.get("new") or ["1"])[0] or 1))
    if path == "/api/radio/stations":
        return {"stations": stations()}
    return None


def init(umculo):
    global core
    core = umculo
