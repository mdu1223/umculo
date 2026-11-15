"""Likes: a heart on every song, and "not for me".

  Like     - a song in your library gets starred (so it's a favourite in your music apps too);
             any song, library or internet, counts toward your taste: For You leans to its artist,
             Cook prefers it.
  Not for me - never suggested again, left out of every Cook, and skipped by smart mixes.

GET  /api/likes                 -> {"liked": ["n<song id>", "d<deezer id>"...], "disliked": [...]}
POST /api/like  {nd_id | deezer_id, artist, title, value: 1 | 0 | -1}

Smart mixes (made from your library, fresh every time you open them):
GET  /api/smart                 -> {"mixes": [{"id", "name", "sub", "cover", "songs"}]}
GET  /api/smart/<id>            -> {"name", "songs": [...]}
"""
import random
import re
import time

core = None


def me():
    return core.users.current()


def fy():
    return core.foryou


def ident(nd_id, deezer_id):
    return f"n{nd_id}" if nd_id else f"d{deezer_id}" if deezer_id else ""


def set_like(b):
    nd, dz = str(b.get("nd_id") or ""), str(b.get("deezer_id") or "")
    value = int(b.get("value") or 0)
    artist, title = b.get("artist") or "", b.get("title") or ""
    srv, u = core.server, me()
    if nd:
        r = srv.db.q(f"SELECT * FROM song WHERE id=? AND {srv.lib_sql(u)}", (nd,), one=True)
        if not r:
            raise LookupError("Song not found")
        artist, title = r["artist"], r["title"]
        srv.touch_ann(u, nd, "song", starred=time.time() if value > 0 else 0)
    if not (artist and title) or not (nd or dz):
        raise ValueError("Which song?")
    k = core.song_key(artist, title)
    rec = {"artist": artist, "title": title, "at": time.time(), "nd_id": nd, "deezer_id": dz}
    with fy().LOCK:
        s = fy().S()
        s["likes"].pop(k, None)
        s["dislikes"].pop(k, None)
        if value > 0:
            s["likes"][k] = rec
        elif value < 0:
            s["dislikes"][k] = rec
            for sh in s["shelves"]:
                sh["items"] = [i for i in sh["items"] if i.get("key") != k]
    fy().save()
    return {"ok": True, "id": ident(nd, dz), "value": value}


def starred_ids(u):
    srv = core.server
    return [r["item"] for r in srv.db.q("SELECT item FROM ann WHERE user=? AND kind='song' AND starred > 0 ORDER BY starred DESC",
                                        (srv.uid(u),))]


def state():
    liked = {"n" + i for i in starred_ids(me())} if core.builtin() else set()
    disliked = set()
    with fy().LOCK:
        s = fy().S()
        for x in s["likes"].values():
            i = ident(x.get("nd_id"), x.get("deezer_id"))
            if i and not i.startswith("n"):          # library likes are the stars above (unstar in an app = unlike)
                liked.add(i)
        for x in s["dislikes"].values():
            i = ident(x.get("nd_id"), x.get("deezer_id"))
            if i:
                disliked.add(i)
    return {"liked": sorted(liked), "disliked": sorted(disliked)}


def disliked_keys():
    with fy().LOCK:
        return set(fy().S()["dislikes"])


# --------------------------------------------------------------------------- smart mixes
def rows_for(u):
    rows = core.cook.lib_rows(u)
    banned = disliked_keys()
    if banned:
        rows = [r for r in rows if core.song_key(r["artist"], r["title"]) not in banned]
    return rows


def anns(u):
    srv = core.server
    return {r["item"]: r for r in srv.db.q("SELECT item, starred, plays, played FROM ann WHERE user=? AND kind='song'", (srv.uid(u),))}


def spread(rows, n=60):
    """A shuffled pick with no artist hogging it."""
    rows = list(rows)
    random.shuffle(rows)
    out, per = [], {}
    for r in rows:
        a = r["albumartist_id"]
        if per.get(a, 0) >= 4:
            continue
        per[a] = per.get(a, 0) + 1
        out.append(r)
        if len(out) >= n:
            break
    return core.cook.no_runs(out, lambda x: x["albumartist_id"])


def build(u, only=None):
    """-> [(id, name, sub, rows)] of every smart mix that has enough songs."""
    rows = rows_for(u)
    a = anns(u)
    now = time.time()
    out = []

    def add(mid, name, sub, pick, least=8):
        if only and only != mid:
            return
        pick = list(pick)
        if len(pick) >= least:
            out.append((mid, name, sub, pick))
    liked = [r for r in rows if (a.get(r["id"]) or {"starred": 0})["starred"]]
    add("liked", "Liked songs", "Every song you gave a heart", sorted(liked, key=lambda r: -a[r["id"]]["starred"]), 1)
    played = [r for r in rows if (a.get(r["id"]) or {"plays": 0})["plays"]]
    add("repeat", "On repeat", "What you play most", sorted(played, key=lambda r: -(a[r["id"]]["plays"] or 0))[:50], 10)
    add("fresh", "Fresh in", "Added in the last month", [r for r in sorted(rows, key=lambda r: -(r["created"] or 0)) if now - (r["created"] or 0) < 30 * 86400][:80], 5)
    old = [r for r in played if (a[r["id"]]["plays"] or 0) >= 2 and now - (a[r["id"]]["played"] or 0) > 60 * 86400]
    add("rediscover", "Rediscover", "Loved once, not played in months", old, 8)
    add("unheard", "Never played", "Songs of yours you haven't heard here yet", [r for r in rows if r["id"] not in a or not a[r["id"]]["plays"]], 15)
    by_artist = core.cook.artist_styles(rows)
    fams = {}
    for r in rows:
        fam, _cat = core.cook.song_style(r, by_artist)
        if fam:
            fams.setdefault(fam, []).append(r)
    for fam, lst in sorted(fams.items(), key=lambda x: -len(x[1]))[:8]:
        add("style-" + re.sub(r"[^a-z0-9]+", "-", fam.lower()), f"{fam} mix", f"{len(lst)} of your songs", lst, 12)
    return out


def view_rows(u, rows):
    srv = core.server
    an = srv.anns(u, [r["id"] for r in rows])
    return [core.libview.song_view(srv.song(r, an.get(r["id"]))) for r in rows]


def smart_list():
    if not core.builtin():
        return []
    return [{"id": mid, "name": name, "sub": sub, "songs": len(rows),
             "cover": next((r["album_id"] for r in rows if r["album_id"]), ""),
             "covers": list(dict.fromkeys(r["album_id"] for r in rows if r["album_id"]))[:4]}
            for mid, name, sub, rows in build(me())]


def smart(mid):
    u = me()
    got = build(u, only=mid)
    if not got:
        raise LookupError("Not enough songs for this mix yet")
    _id, name, sub, rows = got[0]
    if mid not in ("liked", "repeat", "fresh"):
        rows = spread(rows)
    return {"id": mid, "name": name, "comment": sub, "smart": True, "songs": view_rows(u, rows[:200])}


def handle_get(path, _qs):
    if path == "/api/likes":
        return state()
    if path == "/api/smart":
        return {"mixes": smart_list()}
    m = re.match(r"^/api/smart/([\w-]+)$", path)
    if m:
        return smart(m.group(1))
    return None


def handle_post(path, b):
    if path == "/api/like":
        return set_like(b)
    return None


def init(umculo):
    global core
    core = umculo
