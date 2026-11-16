"""Cook: a mix from one song, always in that song's style.

  Cook locally  - songs from your own library that sound like the seed: same style first
                  (its genre family), the artist, artists close to them, then the wider category
                  (Afro House -> house) only if the style alone is thin. Nothing from another style.
  Just cook     - songs you don't know yet (not in your library, never played), in the same style:
                  artists close to the seed's artist and their friends, plus songs from playlists
                  built around that style, kept to the seed's catalogue genre.

Styles come from genre tags, folded into families ("afro tech", "Afro-House" -> Afro House).
A song without a tag takes its artist's usual style. Long DJ mixes and podcast episodes never
end up in a cook.

GET /api/cook/local/<song id>       -> {"songs": [...], "style": "Afro House"}
GET /api/cook/new/<song id>         -> same, songs from the internet ("internet": true)
GET /api/cook/new/dz/<deezer id>    -> Just cook from a song you're streaming
"""
import concurrent.futures as cf
import os
import random
import re
import urllib.parse

core = None

# family, category, words that mean it (checked in this order: specific before general)
FAMILIES = [
    ("Amapiano", "SA dance", ["amapiano", "private school", "piano house sa", "yanos", "log drum"]),
    ("Bacardi", "SA dance", ["bacardi"]),
    ("Gqom", "SA dance", ["gqom", "3 step", "3step", "sgubhu"]),
    ("Kwaito", "SA dance", ["kwaito"]),
    ("Lekompo", "SA traditional", ["lekompo"]),
    ("Maskandi", "SA traditional", ["maskandi", "maskanda", "mbaqanga", "isicathamiya"]),
    ("Afro House", "House", ["afro house", "afrohouse", "afro tech", "afrotech", "tribal house", "organic house", "afro deep"]),
    ("Deep House", "House", ["deep house", "soulful house", "soulful", "deep tech", "lounge house"]),
    ("House", "House", ["tech house", "funky house", "progressive house", "jackin", "garage", "house"]),
    ("Afrobeats", "Afro", ["afrobeats", "afrobeat", "afropop", "afro pop", "naija", "alte", "highlife", "afro fusion"]),
    ("Hip Hop", "Hip Hop", ["hip hop", "hiphop", "rap", "trap", "drill", "motswako", "grime", "boom bap"]),
    ("R&B", "R&B", ["r b", "rnb", "r and b", "neo soul", "soul", "contemporary r"]),
    ("Gospel", "Gospel", ["gospel", "worship", "praise", "christian"]),
    ("Jazz", "Jazz", ["jazz", "bossa nova", "swing", "bebop"]),
    ("Reggae", "Reggae", ["reggae", "dancehall", "dub", "ragga", "ska"]),
    ("Techno", "Electronic", ["techno", "minimal"]),
    ("Electronic", "Electronic", ["edm", "electro", "electronic", "trance", "dubstep", "drum and bass", "dnb", "breakbeat", "dance"]),
    ("Disco", "Disco", ["disco", "funk", "boogie", "nu disco"]),
    ("Lo-fi", "Chill", ["lo fi", "lofi", "chillhop", "chill", "ambient", "downtempo"]),
    ("Pop", "Pop", ["k pop", "kpop", "pop"]),
    ("Rock", "Rock", ["rock", "metal", "punk", "indie", "alternative", "grunge", "emo"]),
    ("Classical", "Classical", ["classical", "orchestra", "opera", "baroque", "piano solo"]),
    ("Country", "Country", ["country", "bluegrass", "americana"]),
    ("Latin", "Latin", ["latin", "reggaeton", "salsa", "bachata", "cumbia"]),
    ("Blues", "Blues", ["blues"]),
]
# Deezer's own genre names, which are coarser
DZ_GENRES = {"african music": ("Afrobeats", "Afro"), "rap/hip hop": ("Hip Hop", "Hip Hop"), "r&b": ("R&B", "R&B"),
             "dance": ("Electronic", "Electronic"), "electro": ("Electronic", "Electronic")}
MIN_SECS = int(os.environ.get("COOK_MIN_SECONDS", "45"))   # a cook is songs: no intros or skits,
MAX_SECS = 15 * 60                                         # and no hour-long DJ mixes


def fold(genre):
    g = re.sub(r"[^a-z0-9& ]+", " ", (genre or "").lower().replace("&", " ").replace("-", " ").replace("_", " "))
    return " " + re.sub(r"\s+", " ", g).strip() + " "


def style(genre):
    """'Afro-House; Deep House' -> ('Afro House', 'House'); unknown -> (None, None)."""
    if not genre:
        return None, None
    g0 = (genre or "").lower().strip()
    if g0 in DZ_GENRES:
        return DZ_GENRES[g0]
    for part in re.split(r"[;/,|]+", genre):
        g = fold(part)
        if g.strip() in ("", "other", "unknown", "misc", "various", "music"):
            continue
        for fam, cat, words in FAMILIES:
            if any(f" {w} " in g or (len(w) > 4 and w in g) for w in words):
                return fam, cat
    return None, None


# --------------------------------------------------------------------------- your library
def lib_rows(u):
    srv = core.server
    root = core.CONFIG["mixes"].rstrip("/") + "/"
    return srv.db.q(f"""SELECT * FROM song WHERE {srv.lib_sql(u)} AND duration BETWEEN ? AND ?
                        AND substr(path, 1, ?) != ?""", (MIN_SECS, MAX_SECS, len(root), root))


def artist_styles(rows):
    """artist id -> their usual style, from the songs of theirs that have a tag."""
    count = {}
    for r in rows:
        fam, cat = style(r["genre"])
        if fam:
            for a in {r["albumartist_id"], r["artist_id"]}:
                c = count.setdefault(a, {})
                c[(fam, cat)] = c.get((fam, cat), 0) + 1
    return {a: max(c.items(), key=lambda x: x[1])[0] for a, c in count.items()}


def song_style(r, by_artist):
    fam, cat = style(r["genre"])
    if fam:
        return fam, cat
    return by_artist.get(r["albumartist_id"]) or by_artist.get(r["artist_id"]) or (None, None)


def related_names(artist, depth=1):
    """Artists close to this one, from the catalogue (names, folded)."""
    try:
        aid = core.server.dz_artist(core.musicdb.primary(artist))
    except Exception:  # noqa: BLE001
        aid = None
    if not aid:
        return [], None
    first = (core.dz_get(f"/artist/{aid}/related?limit=25", 86400 * 7) or {}).get("data") or []
    out = list(first)
    if depth > 1:
        with cf.ThreadPoolExecutor(6) as ex:
            for lst in ex.map(lambda a: (core.dz_get(f"/artist/{a['id']}/related?limit=8", 86400 * 7) or {}).get("data") or [], first[:6]):
                out += lst[:4]
    seen, uniq = set(), []
    for a in out:
        if a.get("id") and a["id"] != aid and a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)
    return uniq, aid


def no_runs(items, artist_of):
    """Shuffled, but never the same artist twice in a row."""
    head, rest = items[:1], items[1:]
    random.shuffle(rest)
    out, last = list(head), [artist_of(head[0])] if head else []
    while rest:
        i = next((k for k, x in enumerate(rest) if artist_of(x) not in last[-2:]), 0)
        x = rest.pop(i)
        out.append(x)
        last.append(artist_of(x))
    return out


def context_style(rows_by_id, by_artist, ctx_ids, hint="", seed=None):
    """The style of what's been playing, not of one song: the playlist's name counts most ("Kwaito Classics"),
    then a vote across the queue. -> (family, category, artists played) or (None, None, artists)."""
    votes, cats, artists = {}, {}, []
    hf, hc = style(hint) if hint else (None, None)
    if hf:
        votes[hf] = votes.get(hf, 0) + 5
        cats[hf] = hc
    for i in ctx_ids:
        x = rows_by_id.get(i)
        if not x:
            continue
        artists.append(x["artist"])
        f, c = song_style(x, by_artist)
        if f:
            votes[f] = votes.get(f, 0) + 1
            cats[f] = c
    if seed is not None and seed["id"] not in ctx_ids:
        artists.append(seed["artist"])
        f, c = song_style(seed, by_artist)
        if f:
            votes[f] = votes.get(f, 0) + 1
            cats[f] = c
    if not votes:
        return None, None, artists
    fam, n = max(votes.items(), key=lambda v: v[1])
    total = sum(votes.values())
    return (fam, cats[fam], artists) if n * 10 >= total * 4 else (None, None, artists)


def top_artists(names, k=3):
    count = {}
    for a in names:
        for p in (core.split_artists(a) or [a])[:1]:
            p = core.musicdb.primary(p)
            count[p] = count.get(p, 0) + 1
    return [a for a, _n in sorted(count.items(), key=lambda x: -x[1])[:k]]


def related_many(names):
    """Artists close to the ones that have been playing; those close to several come first."""
    seen, score = {}, {}
    for a in names:
        rel, _aid = related_names(a)
        for x in rel:
            seen[x["id"]] = x
            score[x["id"]] = score.get(x["id"], 0) + 1
    return [seen[i] for i in sorted(score, key=lambda i: -score[i])]


def seed_row(u, seed):
    srv = core.server
    return srv.db.q(f"SELECT * FROM song WHERE id=? AND {srv.lib_sql(u)}", (seed,), one=True) or \
        srv.db.q(f"SELECT * FROM song WHERE album_id=? AND {srv.lib_sql(u)} ORDER BY RANDOM() LIMIT 1", (seed,), one=True) or \
        srv.db.q(f"SELECT * FROM song WHERE (albumartist_id=? OR artist_id=?) AND {srv.lib_sql(u)} ORDER BY RANDOM() LIMIT 1",
                 (seed, seed), one=True)


def cook_local(u, seed, n=50, ctx=None, hint=""):
    """-> (rows, style label). The seed first, then songs of the same style from your library.
    With ctx (what's been playing) and a hint (the playlist's name), the style is the queue's, not one song's."""
    r = seed_row(u, seed)
    if not r:
        return [], None
    rows = lib_rows(u)
    banned = core.likes.disliked_keys()               # "not for me" songs never get cooked
    if banned:
        rows = [x for x in rows if x["id"] == r["id"] or core.song_key(x["artist"], x["title"]) not in banned]
    hearts = set(core.likes.starred_ids(u))            # songs you gave a heart come up more
    by_artist = artist_styles(rows)
    fam, cat = song_style(r, by_artist)
    ctx_artists = []
    if ctx or hint:
        by_id = {x["id"]: x for x in rows}
        cf_, cc_, ctx_artists = context_style(by_id, by_artist, ctx or [], hint, r)
        if cf_:
            fam, cat = cf_, cc_
    if not fam:                                   # nothing tagged: ask the catalogue what this artist plays
        fam, cat = dz_style_of(r["artist"], r["title"])
    heads = top_artists(ctx_artists or [r["artist"]])
    rel = related_many(heads)
    rel_names = {core.norm(a["name"]) for a in rel} | {core.norm(a) for a in heads}
    mine = {r["albumartist_id"], r["artist_id"]}
    year = r["year"] or 0
    same, close, wide = [], [], []
    for x in rows:
        if x["id"] == r["id"]:
            continue
        f, c = song_style(x, by_artist)
        is_mine = x["albumartist_id"] in mine or x["artist_id"] in mine
        is_close = core.norm(core.musicdb.primary(x["artist"])) in rel_names
        if fam:
            if f == fam:
                (close if (is_mine or is_close) else same).append(x)
            elif f is None and (is_mine or is_close):
                close.append(x)                  # untagged, but the artist is right there
            elif cat and c == cat:
                wide.append(x)
        elif is_mine or is_close:                # no style known at all: stay with the artist's circle
            close.append(x)
    era = lambda x: 0 if year and x["year"] and abs(x["year"] - year) <= 6 else 1
    lucky = lambda x: random.random() - (0.4 if x["id"] in hearts else 0)
    same.sort(key=lucky)
    same.sort(key=era)
    close.sort(key=lucky)
    picked, seen = [r], {r["id"]}

    def add(lst, limit):
        k = 0
        for x in lst:
            if k >= limit:
                break
            if x["id"] not in seen:
                seen.add(x["id"])
                picked.append(x)
                k += 1
    own = [x for x in close if x["albumartist_id"] in mine or x["artist_id"] in mine]
    add(own, max(3, n // 6))                      # some of the artist
    add([x for x in close if x not in own], n // 2)   # artists close to them
    add(same, n - len(picked))                   # the rest of the style
    widened = 0
    if len(picked) < min(n, 20) and wide:         # thin: widen to the category (Afro House -> house), never further
        random.shuffle(wide)
        before = len(picked)
        add(wide, min(n, 20) - len(picked))
        widened = len(picked) - before
    out = no_runs(picked, lambda x: x["albumartist_id"])
    label = fam if not widened or widened * 2 < len(picked) or not cat or cat == fam else f"{fam} and more {cat}"
    return out[:n], label


def dz_style_of(artist, title):
    """The catalogue's genre for a song: (family, category), coarse but never empty-handed."""
    try:
        hits = core.deezer_search(f"{artist} {title}", limit=3)
    except Exception:  # noqa: BLE001
        hits = []
    for d in hits:
        g = dz_album_genres(d.get("album_id"))
        if g:
            return style(g[0])
    return None, None


def dz_album_genres(album_id):
    if not album_id:
        return []
    a = core.dz_get(f"/album/{album_id}", 86400 * 30) or {}
    return [g.get("name", "") for g in ((a.get("genres") or {}).get("data") or []) if g.get("name")]


# --------------------------------------------------------------------------- songs you don't know
def known_keys(u):
    srv = core.server
    keys = set()
    for x in srv.db.q(f"SELECT artist, title FROM song WHERE {srv.lib_sql(u)}"):
        keys.add(core.song_key(x["artist"], x["title"]))
    for x in srv.db.q("SELECT DISTINCT artist, title FROM listen WHERE user=?", (srv.uid(u),)):
        keys.add(core.song_key(x["artist"], x["title"]))
    return keys


def cook_new(u, artist, title, n=40, fam=None, cat=None, seeds=None):
    """-> (items, style label). Songs from the internet you don't have and haven't played, in the same style.
    seeds: [(artist, title)] of what's been playing, so the style is the queue's, not one song's."""
    if not fam:
        fam, cat = dz_style_of(artist, title)
    seeds = (seeds or [])[:4] or [(artist, title)]
    seed_genres = set()
    for a, t in seeds:
        try:
            d = next(iter(core.deezer_search(f"{a} {t}", limit=1)), None)
        except Exception:  # noqa: BLE001
            d = None
        if d:
            seed_genres |= set(dz_album_genres(d.get("album_id")))
    heads = top_artists([a for a, _t in seeds] + [artist])
    rel = related_many(heads) if len(heads) > 1 else related_names(artist, depth=1 if fam else 2)[0]
    pool = []

    def tops(a):
        data = (core.dz_get(f"/artist/{a['id']}/top?limit=15", 86400) or {}).get("data") or []
        return [core.foryou.item_from_dz(d, f"Close to {artist}") for d in data[:15]]
    with cf.ThreadPoolExecutor(8) as ex:
        for lst in ex.map(tops, rel[:30]):
            random.shuffle(lst)
            pool += lst[:4]
    style_pool = []
    if fam:                                       # songs from playlists built around this exact style
        q = fam if fam != "R&B" else "rnb"
        pls = (core.dz_get("/search/playlist?limit=8&q=" + urllib.parse.quote(q), 86400) or {}).get("data") or []
        pls = sorted([p for p in pls if (p.get("nb_tracks") or 0) >= 15], key=lambda p: -(p.get("fans") or 0))[:3]
        with cf.ThreadPoolExecutor(3) as ex:
            for lst in ex.map(lambda p: (core.dz_get(f"/playlist/{p['id']}/tracks?limit=100", 86400) or {}).get("data") or [], pls):
                style_pool += [core.foryou.item_from_dz(d, f"{fam}") for d in lst if d.get("readable", True)]
        random.shuffle(style_pool)
    if seed_genres:                               # keep the artist pool to the seed's catalogue genre
        albums = list({i.get("album_id") for i in pool if i.get("album_id")})[:80]
        with cf.ThreadPoolExecutor(8) as ex:
            genres = dict(zip(albums, ex.map(dz_album_genres, albums)))
        pool = [i for i in pool if not genres.get(i.get("album_id")) or seed_genres & set(genres[i.get("album_id")])]
    known = known_keys(u)
    with core.foryou.LOCK:
        banned = set(core.foryou.STATE["dislikes"])
    seed_key = core.song_key(artist, title)
    picked, keys, per = [], {seed_key}, {}

    def take(lst, limit):
        k = 0
        for it in lst:
            if k >= limit or not it.get("deezer_id"):
                continue
            a = core.norm((core.split_artists(it["artist"]) or [it["artist"]])[0])
            if it["key"] in keys or it["key"] in known or it["key"] in banned or per.get(a, 0) >= 2:
                continue
            keys.add(it["key"])
            per[a] = per.get(a, 0) + 1
            picked.append(it)
            k += 1
    random.shuffle(pool)
    if style_pool:                                # half from the artists' circle, half from playlists of exactly this style
        take(pool, n // 2)
        take(style_pool, n - len(picked))
    take(pool, n - len(picked))
    out = no_runs(picked, lambda i: core.norm(i["artist"]))
    return out[:n], fam


# --------------------------------------------------------------------------- web
def me():
    return core.users.current()


def handle_get(path, qs):
    m = re.match(r"^/api/cook/(local|new)/(dz/)?([^/]+)$", path)
    if not m:
        return None
    kind, from_dz, sid = m.groups()
    n = max(10, min(100, int((qs.get("n") or ["50"])[0] or 50)))
    ctx = [x for x in (qs.get("ctx") or [""])[0].split(",") if re.match(r"^[\w-]{1,40}$", x)][:20]
    hint = (qs.get("hint") or [""])[0][:120]
    srv = core.server
    if kind == "local":
        if from_dz:
            raise ValueError("Cook locally starts from a song in your library")
        rows, label = cook_local(me(), sid, n, ctx, hint)
        if len(rows) < 2:
            raise LookupError("Not enough of your songs in this style yet. Try Just cook.")
        a = srv.anns(me(), [r["id"] for r in rows])
        return {"songs": [core.libview.song_view(srv.song(r, a.get(r["id"]))) for r in rows], "style": label}
    if from_dz:
        d = core.dz_get(f"/track/{sid}", 86400) or {}
        artist, title = (d.get("artist") or {}).get("name", ""), d.get("title", "")
        fam = cat = None
    else:
        r = seed_row(me(), sid)
        if not r:
            raise LookupError("Song not found")
        artist, title = r["artist"], r["title"]
    seeds = []
    rows = lib_rows(me())
    by_artist, by_id = artist_styles(rows), {x["id"]: x for x in rows}
    if not from_dz:
        fam, cat = song_style(r, by_artist)
    if ctx or hint:
        cf_, cc_, _a = context_style(by_id, by_artist, ctx, hint, r if not from_dz else None)
        if cf_:
            fam, cat = cf_, cc_
        seeds = [(by_id[i]["artist"], by_id[i]["title"]) for i in ctx[-6:] if i in by_id]
    if not artist:
        raise LookupError("Song not found")
    items, label = cook_new(me(), artist, title, n, fam, cat, seeds)
    if not items:
        raise LookupError("Couldn't find new songs in this style right now")
    core.subsonic.remember(items)
    return {"songs": items, "style": label, "internet": True}


def init(umculo):
    global core
    core = umculo
