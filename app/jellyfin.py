"""Jellyfin-compatible music API (experimental), so Jellyfin music apps such as Finamp, Jellify and
Feishin's Jellyfin mode can use Umculo as their server. Same library, logins, stars, plays and playlists
as the Subsonic side. Only music: no video, no TV, no live TV.

Point the app at Umculo's address (the same one Arpeggi uses) and sign in with your Umculo login.
"""
import hashlib
import json
import random
import re
import secrets
import time
import urllib.parse

core = None
TICKS = 10_000_000
VERSION = "10.10.3"                 # the Jellyfin API version these answers follow
PREFIXES = ("/system/", "/users", "/items", "/artists", "/audio/", "/sessions", "/playlists", "/musicgenres", "/genres",
            "/userviews", "/useritems", "/userfavoriteitems", "/userplayeditems", "/branding/", "/quickconnect/", "/displaypreferences/",
            "/songs/", "/albums/", "/library/", "/plugins", "/localization/", "/startup/", "/studios", "/persons")


def is_jellyfin(path):
    p = path.split("?")[0].lower()
    if p.startswith("/jellyfin/"):
        p = p[len("/jellyfin"):]
    return p.startswith(PREFIXES)


def db():
    return core.musicdb


def srv():
    return core.server


def server_id():
    return hashlib.md5(b"umculo-server").hexdigest()


def user_guid(u):
    return hashlib.md5(("umculo-user:" + srv().uid(u)).encode()).hexdigest()


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(t or 0))


# --------------------------------------------------------------------------- who is asking
def token_of(h, qs):
    for k in ("X-Emby-Token", "X-MediaBrowser-Token"):
        if h.headers.get(k):
            return h.headers[k]
    for k in ("X-Emby-Authorization", "Authorization"):
        m = re.search(r'Token="([^"]+)"', h.headers.get(k) or "")
        if m:
            return m.group(1)
    for k in ("api_key", "apikey", "x-emby-token"):
        if qs.get(k):
            return qs[k]
    return ""


def user_for_token(tok):
    if not tok:
        return None
    r = db().q("SELECT v FROM kv WHERE k=?", ("jftok:" + tok,), one=True)
    if not r:
        t = core.share.token_info(tok)                 # an Umculo access key works too
        if t:
            return core.users.USERS.get(t.get("user")) or core.users.admin_user() or core.users.OPEN
        return None
    rec = json.loads(r["v"])
    uid = rec["user"]
    users = core.users
    u = (users.admin_user() or users.OPEN) if uid == "admin" else users.USERS.get(uid)
    if rec.get("app"):                                 # signed in with an app password: only while it exists
        if not u or rec["app"] not in (u.get("devices") or {}):
            return None
        core.security.used(u, rec["app"])
    elif not core.security.main_password_allowed():
        return None                                    # signed in with the normal password: home and Tailscale only
    return u


def login(body):
    name, pw = body.get("Username") or body.get("username") or "", body.get("Pw") or body.get("pw") or body.get("Password") or ""
    try:
        u = srv().check_login({"u": [name], "p": [pw]})
    except srv().ApiError:
        return None
    app = next((did for did, sec in core.security.app_secrets(u) if sec == pw), None)
    tok = secrets.token_hex(16)
    db().x("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", ("jftok:" + tok, json.dumps({"user": srv().uid(u), "at": time.time(), "app": app})))
    return u, tok


def user_dto(u):
    admin = core.users.is_admin(u)
    return {"Name": (u or {}).get("username") or "admin", "ServerId": server_id(), "Id": user_guid(u), "HasPassword": True,
            "HasConfiguredPassword": True, "HasConfiguredEasyPassword": False, "EnableAutoLogin": False,
            "LastLoginDate": iso(time.time()), "LastActivityDate": iso(time.time()),
            "Configuration": {"PlayDefaultAudioTrack": True, "DisplayMissingEpisodes": False, "GroupedFolders": [], "OrderedViews": [],
                              "LatestItemsExcludes": [], "MyMediaExcludes": [], "HidePlayedInLatest": False, "RememberAudioSelections": True,
                              "EnableNextEpisodeAutoPlay": True},
            "Policy": {"IsAdministrator": admin, "IsHidden": False, "IsDisabled": False, "EnableAudioPlaybackTranscoding": True,
                       "EnableMediaPlayback": True, "EnableContentDownloading": True, "EnableAllFolders": True, "EnablePlaylistManagement": True,
                       "EnableRemoteAccess": True, "EnableUserPreferenceAccess": True, "AuthenticationProviderId": "Umculo",
                       "PasswordResetProviderId": "Umculo", "SyncPlayAccess": "None"}}


# --------------------------------------------------------------------------- shaping
def user_data(a, item):
    a = dict(a) if a else {}
    return {"IsFavorite": bool(a.get("starred")), "PlayCount": a.get("plays") or 0, "Played": bool(a.get("plays")),
            "LastPlayedDate": iso(a["played"]) if a.get("played") else None, "PlaybackPositionTicks": 0, "Key": item}


def song_dto(r, a):
    ex = srv().extra(r)
    aid, aaid = r["artist_id"], r["albumartist_id"]
    d = {"Name": r["title"], "Id": r["id"], "ServerId": server_id(), "Type": "Audio", "MediaType": "Audio", "IsFolder": False,
         "RunTimeTicks": int((r["duration"] or 0) * TICKS), "Album": r["album"], "AlbumId": r["album_id"], "ParentId": r["album_id"],
         "AlbumArtist": r["albumartist"], "AlbumArtists": [{"Name": r["albumartist"], "Id": aaid}],
         "Artists": [db().primary(r["artist"])] if r["artist"] else [], "ArtistItems": [{"Name": db().primary(r["artist"]), "Id": aid}],
         "IndexNumber": r["track"] or None, "ParentIndexNumber": r["disc"] or 1, "ProductionYear": r["year"] or None,
         "PremiereDate": f"{r['year']}-01-01T00:00:00.0000000Z" if r["year"] else None,
         "Genres": srv().genres(r["genre"]), "GenreItems": [{"Name": g, "Id": db().h("genre", g.lower())} for g in srv().genres(r["genre"])],
         "ImageTags": {"Primary": r["album_id"]}, "AlbumPrimaryImageTag": r["album_id"], "ImageBlurHashes": {},
         "BackdropImageTags": [], "LocationType": "FileSystem", "Container": r["suffix"], "DateCreated": iso(r["created"]),
         "HasLyrics": bool(r["lyrics"]), "UserData": user_data(a, r["id"]), "Path": r["rel"],
         "NormalizationGain": (ex.get("rg") or {}).get("trackGain"), "CanDownload": True, "ChannelId": None,
         "MediaSources": [{"Id": r["id"], "Container": r["suffix"], "Size": r["size"], "Bitrate": (r["bitrate"] or 0) * 1000,
                           "Path": r["rel"], "Protocol": "File", "Type": "Default", "Name": r["title"], "IsRemote": False,
                           "RunTimeTicks": int((r["duration"] or 0) * TICKS), "SupportsDirectPlay": True, "SupportsDirectStream": True,
                           "SupportsTranscoding": True, "ReadAtNativeFramerate": False, "IgnoreDts": False, "IgnoreIndex": False,
                           "GenPtsInput": False, "IsInfiniteStream": False, "RequiresOpening": False, "RequiresClosing": False,
                           "RequiresLooping": False, "SupportsProbing": True,
                           "MediaStreams": [{"Type": "Audio", "Codec": r["suffix"], "BitRate": (r["bitrate"] or 0) * 1000, "Index": 0,
                                             "SampleRate": r["samplerate"] or None, "Channels": r["channels"] or 2, "BitDepth": r["bitdepth"] or None,
                                             "IsDefault": True, "IsExternal": False, "IsInterlaced": False, "IsTextSubtitleStream": False,
                                             "SupportsExternalStream": False, "Language": None}],
                           "DefaultAudioStreamIndex": 0}]}
    return {k: v for k, v in d.items() if v is not None}


def album_dto(r, a):
    d = {"Name": r["name"], "Id": r["id"], "ServerId": server_id(), "Type": "MusicAlbum", "IsFolder": True, "MediaType": "Unknown",
         "AlbumArtist": r["artist"], "AlbumArtists": [{"Name": r["artist"], "Id": r["artist_id"]}],
         "Artists": [r["artist"]], "ArtistItems": [{"Name": r["artist"], "Id": r["artist_id"]}], "ParentId": r["artist_id"],
         "ChildCount": r["songs"], "SongCount": r["songs"], "RunTimeTicks": int((r["duration"] or 0) * TICKS),
         "ProductionYear": r["year"] or None, "PremiereDate": f"{r['year']}-01-01T00:00:00.0000000Z" if r["year"] else None,
         "Genres": srv().genres(r["genre"]), "ImageTags": {"Primary": r["id"]}, "ImageBlurHashes": {}, "BackdropImageTags": [],
         "DateCreated": iso(r["created"]), "LocationType": "FileSystem", "UserData": user_data(a, r["id"]), "CanDownload": True}
    return {k: v for k, v in d.items() if v is not None}


def artist_dto(r, a):
    return {"Name": r["name"], "Id": r["id"], "ServerId": server_id(), "Type": "MusicArtist", "IsFolder": True, "MediaType": "Unknown",
            "AlbumCount": r["albums"], "ChildCount": r["albums"], "SongCount": r["songs"], "SortName": r["sort"],
            "ImageTags": {"Primary": r["id"]}, "ImageBlurHashes": {}, "BackdropImageTags": [], "LocationType": "FileSystem",
            "UserData": user_data(a, r["id"])}


def playlist_dto(u, r):
    rows = srv().pl_songs(u, r["id"])
    return {"Name": r["name"], "Id": r["id"], "ServerId": server_id(), "Type": "Playlist", "IsFolder": True, "MediaType": "Audio",
            "ChildCount": len(rows), "RunTimeTicks": int(sum(x["duration"] or 0 for x in rows) * TICKS),
            "ImageTags": {"Primary": r["id"]} if rows else {}, "ImageBlurHashes": {}, "BackdropImageTags": [],
            "DateCreated": iso(r["created"]), "CanDelete": r["owner"] == srv().uid(u), "UserData": user_data({}, r["id"]),
            "Overview": r["comment"] or None}


def genre_dto(name, n):
    return {"Name": name, "Id": db().h("genre", name.lower()), "ServerId": server_id(), "Type": "MusicGenre", "IsFolder": True,
            "ChildCount": n, "SongCount": n, "ImageTags": {}, "BackdropImageTags": [], "UserData": user_data({}, name)}


def items(dtos, total=None, start=0):
    return {"Items": dtos, "TotalRecordCount": len(dtos) if total is None else total, "StartIndex": start}


def view_id():
    return db().h("view", "music")


def views(u):
    return items([{"Name": "Music", "Id": view_id(), "ServerId": server_id(), "Type": "CollectionFolder", "CollectionType": "music",
                   "IsFolder": True, "ImageTags": {}, "BackdropImageTags": [], "ChildCount": 1,
                   "UserData": user_data({}, view_id())}])


# --------------------------------------------------------------------------- finding things
def kind_of(i):
    for t in ("song", "album", "artist", "playlist"):
        if db().q(f"SELECT 1 FROM {t} WHERE id=?", (i,), one=True):
            return t
    return "genre" if genre_name(i) else None


def genre_name(gid):
    for r in db().q("SELECT DISTINCT genre FROM song WHERE genre != ''"):
        for g in srv().genres(r["genre"]):
            if db().h("genre", g.lower()) == gid:
                return g
    return None


def one(u, i):
    k = kind_of(i)
    a = srv().anns(u, [i])
    if k == "song":
        r = db().q(f"SELECT * FROM song WHERE id=? AND {srv().lib_sql(u)}", (i,), one=True)
        return song_dto(r, a.get(i)) if r else None
    if k == "album":
        r = db().q(f"SELECT * FROM album WHERE id=? AND {srv().lib_sql(u)}", (i,), one=True)
        return album_dto(r, a.get(i)) if r else None
    if k == "artist":
        r = db().q("SELECT * FROM artist WHERE id=?", (i,), one=True)
        return artist_dto(r, a.get(i)) if r else None
    if k == "playlist":
        r = next((x for x in srv().visible_playlists(u) if x["id"] == i), None)
        return playlist_dto(u, r) if r else None
    if i == view_id():
        return views(u)["Items"][0]
    g = genre_name(i)
    return genre_dto(g, 0) if g else None


SORT = {"sortname": "{t}.name", "name": "{t}.name", "datecreated": "{t}.created", "playcount": "ann.plays", "dateplayed": "ann.played",
        "random": "RANDOM()", "productionyear": "{t}.year", "premieredate": "{t}.year", "album": "song.album", "albumartist": "{t}.artist",
        "artist": "{t}.artist", "indexnumber": "song.track", "parentindexnumber": "song.disc", "communityrating": "ann.rating",
        "criticrating": "ann.rating", "runtime": "{t}.duration", "datelastcontentadded": "{t}.created", "isfavoriteorliked": "ann.starred"}


def query(u, qs, forced_types=None):
    """GET /Items (and friends): what Jellyfin apps browse and search with."""
    me = srv().uid(u)
    types = forced_types or [t.strip().lower() for t in (qs.get("includeitemtypes") or "").split(",") if t.strip()]
    parent = qs.get("parentid") or ""
    start, limit = int(qs.get("startindex") or 0), int(qs.get("limit") or 0) or 100000
    term = (qs.get("searchterm") or qs.get("namestartswith") or "").strip()
    filters = (qs.get("filters") or "").lower()
    ids = [x for x in (qs.get("ids") or "").split(",") if x]
    order = (qs.get("sortorder") or "Ascending").split(",")[0].lower()
    sorts = [s.strip().lower() for s in (qs.get("sortby") or "").split(",") if s.strip()]
    artist_ids = [x for x in ((qs.get("artistids") or "") + "," + (qs.get("albumartistids") or "") + "," + (qs.get("contributingartistids") or "")).split(",") if x]
    album_ids = [x for x in (qs.get("albumids") or "").split(",") if x]
    genre_ids = [x for x in (qs.get("genreids") or "").split(",") if x]
    genres = [x for x in (qs.get("genres") or "").split("|") if x] + [g for g in (genre_name(x) for x in genre_ids) if g]
    if ids:
        dtos = [d for d in (one(u, i) for i in ids) if d]
        return items(dtos)
    pk = kind_of(parent) if parent and parent != view_id() else None
    if pk == "playlist":
        return playlist_items(u, parent, start, limit)
    if pk == "album" and not types:
        types = ["audio"]
    if pk == "artist" and not types:
        types = ["musicalbum"]
    if not types:
        types = ["musicalbum"] if not term else ["musicartist", "musicalbum", "audio"]
    out, total = [], 0
    for t in types:
        if t == "playlist":
            pls = [p for p in srv().visible_playlists(u) if not term or term.lower() in p["name"].lower()]
            total += len(pls)
            out += [playlist_dto(u, p) for p in pls]
            continue
        if t == "musicgenre":
            gs = genre_list(u, term)
            total += len(gs)
            out += gs
            continue
        table = {"audio": "song", "musicalbum": "album", "musicartist": "artist"}.get(t)
        if not table:
            continue
        where, args = [srv().lib_sql(u, f"{table}.lib")], [me]
        if table == "artist":
            where.append("artist.albums > 0")
        if term:
            for w in db().fold(term).split():
                where.append(f"{table}.search LIKE ?")
                args.append(f"%{w}%")
        if "isfavorite" in filters:
            where.append("ann.starred > 0")
        if "isplayed" in filters and "isunplayed" not in filters:
            where.append("ann.plays > 0")
        if "isunplayed" in filters:
            where.append("COALESCE(ann.plays, 0) = 0")
        if pk == "album":
            where.append("song.album_id = ?" if table == "song" else "album.id = ?")
            args.append(parent)
        if pk == "artist":
            col = {"song": "(song.albumartist_id = ? OR song.artist_id = ?)", "album": "album.artist_id = ?", "artist": "artist.id = ?"}[table]
            where.append(col)
            args += [parent] * col.count("?")
        if pk == "genre" or genres:
            gs = genres or [genre_name(parent)]
            col = f"{table}.genre" if table in ("song", "album") else "(SELECT genre FROM song WHERE song.albumartist_id = artist.id LIMIT 1)"
            where.append("(" + " OR ".join(f"lower({col}) LIKE ?" for _ in gs) + ")")
            args += [f"%{g.lower()}%" for g in gs]
        if artist_ids:
            col = {"song": "(song.albumartist_id IN ({q}) OR song.artist_id IN ({q}))", "album": "album.artist_id IN ({q})",
                   "artist": "artist.id IN ({q})"}[table].format(q=",".join("?" * len(artist_ids)))
            where.append(col)
            args += artist_ids * col.count("IN (")
        if album_ids and table == "song":
            where.append(f"song.album_id IN ({','.join('?' * len(album_ids))})")
            args += album_ids
        if qs.get("years"):
            ys = [int(y) for y in qs["years"].split(",") if y.strip().isdigit()]
            if ys and table in ("song", "album"):
                where.append(f"{table}.year IN ({','.join(str(y) for y in ys)})")
        base = f"FROM {table} LEFT JOIN ann ON ann.item = {table}.id AND ann.user = ? WHERE " + " AND ".join(where)
        n = db().q(f"SELECT COUNT(*) n {base}", args, one=True)["n"]
        total += n
        ob = []
        for s in sorts or (["parentindexnumber", "indexnumber", "sortname"] if table == "song" and pk == "album" else ["sortname"]):
            col = SORT.get(s)
            if not col:
                continue
            col = col.format(t=table)
            if table == "song":
                col = {"song.name": "song.title", "song.artist": "song.albumartist"}.get(col, col)
            if table != "song" and col.startswith("song."):
                continue
            if table == "artist" and col in ("artist.year", "artist.created", "artist.duration", "artist.artist"):
                continue
            ob.append(col if col == "RANDOM()" else f"{col} {'DESC' if order.startswith('desc') else 'ASC'}")
        natural = (not sorts or sorts[0] in ("sortname", "name")) and table in ("album", "artist") and pk != "album"
        if natural:                                   # "2 Pac" before "10 Years", "The Weeknd" with the W's
            rows = db().q(f"SELECT {table}.* {base}", args)
            rows = sorted(rows, key=lambda r: db().natural(r["name"]), reverse=order.startswith("desc"))[start:start + limit]
        else:
            rows = db().q(f"SELECT {table}.* {base} ORDER BY {', '.join(ob) or table + '.rowid'} LIMIT ? OFFSET ?", args + [limit, start])
        a = srv().anns(u, [r["id"] for r in rows])
        maker = {"song": song_dto, "album": album_dto, "artist": artist_dto}[table]
        out += [maker(r, a.get(r["id"])) for r in rows]
    return items(out, total, start)


def genre_list(u, term=""):
    counts = {}
    for r in db().q(f"SELECT genre, COUNT(*) n FROM song WHERE {srv().lib_sql(u)} AND genre != '' GROUP BY genre"):
        for g in srv().genres(r["genre"]):
            counts[g] = counts.get(g, 0) + r["n"]
    return [genre_dto(g, n) for g, n in sorted(counts.items(), key=lambda kv: db().natural(kv[0])) if not term or term.lower() in g.lower()]


def playlist_items(u, pid, start=0, limit=100000):
    rows = srv().pl_songs(u, pid)
    a = srv().anns(u, [r["id"] for r in rows])
    dtos = []
    for pos, r in enumerate(rows):
        d = song_dto(r, a.get(r["id"]))
        d["PlaylistItemId"] = f"{pid}-{pos}"
        dtos.append(d)
    return items(dtos[start:start + limit], len(dtos), start)


def latest(u, qs):
    t = (qs.get("includeitemtypes") or "MusicAlbum").lower()
    lim = int(qs.get("limit") or 20)
    if "audio" in t:
        rows = db().q(f"SELECT * FROM song WHERE {srv().lib_sql(u)} ORDER BY created DESC LIMIT ?", (lim,))
        a = srv().anns(u, [r["id"] for r in rows])
        return [song_dto(r, a.get(r["id"])) for r in rows]
    rows = db().q(f"SELECT * FROM album WHERE {srv().lib_sql(u)} ORDER BY created DESC LIMIT ?", (lim,))
    a = srv().anns(u, [r["id"] for r in rows])
    return [album_dto(r, a.get(r["id"])) for r in rows]


def mix(u, seed, lim):
    k = kind_of(seed)
    if k == "playlist":
        rows = srv().pl_songs(u, seed)
        seed = random.choice(rows)["id"] if rows else seed
    elif k == "genre":
        g = genre_name(seed)
        r = db().q(f"SELECT id FROM song WHERE {srv().lib_sql(u)} AND lower(genre) LIKE ? ORDER BY RANDOM() LIMIT 1", (f"%{g.lower()}%",), one=True)
        seed = r["id"] if r else seed
    rows = srv().instant_mix(u, seed, lim)
    a = srv().anns(u, [r["id"] for r in rows])
    return items([song_dto(r, a.get(r["id"])) for r in rows])


def lyrics(u, sid):
    r = db().q(f"SELECT * FROM song WHERE id=? AND {srv().lib_sql(u)}", (sid,), one=True)
    if not r:
        return None
    text = srv().lyrics_text(r["path"])
    if not text:
        return None
    s = srv().structured(text, r)
    return {"Metadata": {"Artist": r["artist"], "Title": r["title"], "Album": r["album"], "IsSynced": s["synced"]},
            "Lyrics": [{"Text": x["value"], **({"Start": x["start"] * 10000} if "start" in x else {})} for x in s["line"]]}


# --------------------------------------------------------------------------- web
def image_user():
    with core.users.LOCK:
        people = list(core.users.USERS.values())
    return next((p for p in people if p.get("role") == "admin"), None) or core.users.OPEN


def handle(h, method):
    u_ = urllib.parse.urlparse(h.path)
    path = u_.path
    if path.lower().startswith("/jellyfin/"):
        path = path[len("/jellyfin"):]
    lp = path.lower().rstrip("/")
    qs = {k.lower(): v[-1] for k, v in urllib.parse.parse_qs(u_.query, keep_blank_values=True).items()}
    body = {}
    if method in ("POST", "DELETE"):
        n = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(n) if n else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}

    def send(obj, code=200):
        data = json.dumps(obj).encode()
        h.send_response(code)
        h.send_header("Content-Type", "application/json; charset=utf-8")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def none(code=204):
        h.send_response(code)
        h.send_header("Content-Length", "0")
        h.end_headers()

    info = {"LocalAddress": (f"{h.headers.get('X-Forwarded-Proto') or 'http'}://{h.headers.get('X-Forwarded-Host') or h.headers.get('Host')}"
                     if h.headers.get("Host") else core.CONFIG.get("public_url") or ""), "ServerName": "Umculo",
            "Version": VERSION, "ProductName": "Jellyfin Server", "OperatingSystem": "Linux", "Id": server_id(), "StartupWizardCompleted": True}
    try:
        if lp in ("/system/info/public",):
            return send(info)
        if lp == "/system/ping":
            return send("Jellyfin Server")
        if lp in ("/branding/configuration",):
            return send({"LoginDisclaimer": "", "CustomCss": "", "SplashscreenEnabled": False})
        if lp == "/branding/css" or lp == "/branding/css.css":
            h.send_response(200)
            h.send_header("Content-Type", "text/css")
            h.send_header("Content-Length", "0")
            h.end_headers()
            return
        if lp == "/quickconnect/enabled":
            return send(False)
        if lp == "/users/public":
            return send([])
        if lp == "/users/authenticatebyname" and method == "POST":
            got = login(body)
            if not got:
                return send({"error": "Wrong username or password"}, 401)
            u, tok = got
            return send({"User": user_dto(u), "AccessToken": tok, "ServerId": server_id(),
                         "SessionInfo": {"Id": tok[:16], "UserId": user_guid(u), "UserName": u.get("username", "admin"),
                                         "Client": "", "DeviceName": "", "DeviceId": "", "ApplicationVersion": "", "IsActive": True,
                                         "SupportsMediaControl": False, "SupportsRemoteControl": False, "PlayableMediaTypes": ["Audio"]}})
        u = user_for_token(token_of(h, qs))
        if u is None and method in ("GET", "HEAD") and re.match(r"^/(items|artists|musicgenres|genres)/[^/]+/images/", lp):
            u = image_user()                          # Jellyfin serves artwork to any request; apps load it as plain URLs
        if u is None:
            return send({"error": "Sign in first"}, 401)
        prev = core.users.current()
        core.users.LOCAL.user = u
        try:
            return route(h, method, lp, path, qs, body, u, send, none, info)
        finally:
            core.users.LOCAL.user = prev
    except srv().ApiError as e:
        return send({"error": str(e)}, 404 if e.code == 70 else 400)
    except Exception as e:  # noqa: BLE001
        core.log("jellyfin api", method, path, "failed:", e)
        return send({"error": str(e)[:200]}, 500)


def route(h, method, lp, path, qs, body, u, send, none, info):
    me = srv().uid(u)
    parts = path.strip("/").split("/")
    lparts = [x.lower() for x in parts]
    if lp == "/system/info":
        return send({**info, "HasPendingRestart": False, "IsShuttingDown": False, "SupportsLibraryMonitor": True, "WebSocketPortNumber": 0,
                     "CanSelfRestart": False, "CanLaunchWebBrowser": False, "HasUpdateAvailable": False})
    if lp in ("/users/me",) or (len(lparts) == 2 and lparts[0] == "users" and lparts[1] == user_guid(u)):
        return send(user_dto(u))
    if lp == "/users":
        return send([user_dto(u)])
    if lp.startswith("/sessions"):
        if lp in ("/sessions/playing", "/sessions/playing/progress"):
            sid = body.get("ItemId") or ""
            if sid:
                srv().NOW_PLAYING[me] = (sid, time.time())
            return none()
        if lp == "/sessions/playing/stopped":
            sid = body.get("ItemId") or ""
            pos = (body.get("PositionTicks") or 0) / TICKS
            r = db().q("SELECT duration FROM song WHERE id=?", (sid,), one=True)
            if r and pos >= min(240, (r["duration"] or 0) * 0.5):
                srv().scrobble(u, sid, None, True)
            srv().NOW_PLAYING.pop(me, None)
            return none()
        if method == "GET":
            return send([])
        return none()
    if lp in ("/userviews",) or (len(lparts) == 3 and lparts[0] == "users" and lparts[2] == "views"):
        return send(views(u))
    if lp.endswith("/items/latest"):
        return send(latest(u, qs))
    if lp.endswith("/items/filters") or lp.endswith("/items/filters2"):
        g = genre_list(u)
        return send({"Genres": [{"Name": x["Name"], "Id": x["Id"]} for x in g], "Tags": [], "OfficialRatings": [],
                     "Years": sorted({r["year"] for r in db().q(f"SELECT DISTINCT year FROM album WHERE {srv().lib_sql(u)} AND year > 0")})})
    # favourites and plays
    for key, star in (("favoriteitems", True), ("userfavoriteitems", True)):
        if key in lparts:
            i = parts[lparts.index(key) + 1]
            srv().touch_ann(u, i, srv().kind_of(i), starred=time.time() if method == "POST" else 0)
            return send(user_data(srv().anns(u, [i]).get(i), i))
    for key in ("playeditems", "userplayeditems"):
        if key in lparts:
            i = parts[lparts.index(key) + 1]
            if method == "POST" and db().q("SELECT 1 FROM song WHERE id=?", (i,), one=True):
                srv().scrobble(u, i, None, True)
            return send(user_data(srv().anns(u, [i]).get(i), i))
    # pictures, audio, lyrics, mixes
    if "images" in lparts:
        i = parts[lparts.index("images") - 1]
        size = max(int(float(qs.get("maxwidth") or qs.get("fillwidth") or qs.get("width") or 0)),
                   int(float(qs.get("maxheight") or qs.get("fillheight") or qs.get("height") or 0)))
        if lparts[0] == "artists" and kind_of(i) != "artist":     # /Artists/{name}/Images
            r = db().q("SELECT id FROM artist WHERE lower(name)=lower(?)", (urllib.parse.unquote(i),), one=True)
            i = r["id"] if r else i
        return srv().cover(h, {"id": [i], "size": [str(size)] if size else []})
    if lparts[0] == "audio" and len(lparts) >= 3 and lparts[2].startswith(("universal", "stream", "main")):
        i = parts[1]
        p = {"id": [i]}
        cont = [c.split("|")[0].strip().lower() for c in (qs.get("container") or "").split(",") if c.strip()]
        r = db().q("SELECT suffix, bitrate FROM song WHERE id=?", (i,), one=True)
        maxbr = int(qs.get("maxstreamingbitrate") or qs.get("audiobitrate") or 0) // 1000
        static = qs.get("static", "").lower() == "true"
        if r and not static and ((cont and r["suffix"] not in cont and not any(c in ("flac", "mp3", "aac", "m4a", "opus", "ogg") and c == r["suffix"] for c in cont))
                                 or (maxbr and (r["bitrate"] or 0) > maxbr + 8)):
            p["format"] = ["opus" if (qs.get("audiocodec") or qs.get("transcodingcontainer") or "").lower() in ("opus", "ogg") else "mp3"]
            p["maxBitRate"] = [str(min(maxbr or 320, 320))]
        else:
            p["format"] = ["raw"]
        if qs.get("starttimeticks"):
            p["timeOffset"] = [str(int(int(qs["starttimeticks"]) / TICKS))]
        return srv().stream(h, u, p)
    if lparts[-1] == "download" and len(lparts) >= 3:
        return srv().stream(h, u, {"id": [parts[-2]]}, True)
    if lparts[-1] == "lyrics":
        out = lyrics(u, parts[-2])
        return send(out) if out else send({"error": "No lyrics"}, 404)
    if lparts[-1] == "instantmix":
        seed = parts[-2] if len(parts) >= 3 else qs.get("id", "")
        if lparts[0] == "musicgenres" and kind_of(seed) is None:
            seed = db().h("genre", urllib.parse.unquote(seed).lower())
        return send(mix(u, seed, int(qs.get("limit") or 50)))
    if lparts[-1] == "similar":
        return send(items([]))
    # playlists
    if lparts[0] == "playlists":
        if len(parts) == 1 and method == "POST":
            name = body.get("Name") or qs.get("name") or "New playlist"
            ids = body.get("Ids") or [x for x in (qs.get("ids") or "").split(",") if x]
            res = srv().call(u, "createPlaylist", {"name": [name], "songId": ids})
            return send({"Id": res["playlist"]["id"]})
        pid = parts[1]
        if len(parts) >= 3 and lparts[2] == "items":
            if method == "GET":
                return send(playlist_items(u, pid, int(qs.get("startindex") or 0), int(qs.get("limit") or 0) or 100000))
            r = srv().my_playlist(u, pid)
            cur = [x["song"] for x in db().q("SELECT song FROM plsong WHERE pid=? ORDER BY pos", (pid,))]
            if len(parts) >= 6 and lparts[4] == "move":               # /Playlists/{id}/Items/{entry}/Move/{index}
                entry, to = parts[3], int(parts[5])
                pos = int(entry.rsplit("-", 1)[-1]) if "-" in entry else cur.index(entry)
                cur.insert(to, cur.pop(pos))
            elif method == "POST":
                cur += [x for x in (qs.get("ids") or "").split(",") if x] + (body.get("Ids") or [])
            elif method == "DELETE":
                drop = {int(e.rsplit("-", 1)[-1]) for e in (qs.get("entryids") or "").split(",") if "-" in e}
                cur = [x for k, x in enumerate(cur) if k not in drop]
            srv().set_pl_songs(pid, cur)
            srv().write_playlist_file(dict(r), cur)
            return none()
        if method == "GET":
            d = one(u, pid)
            return send(d) if d else send({"error": "Not found"}, 404)
    if lparts[0] == "items" and len(parts) == 2 and method == "DELETE":
        srv().call(u, "deletePlaylist", {"id": [parts[1]]})
        return none()
    if lparts[0] == "items" and len(parts) == 2 and method == "POST":      # metadata edits: not here
        return none()
    # browsing
    if lp in ("/artists", "/artists/albumartists"):
        return send(query(u, qs, ["musicartist"]))
    if lparts[0] == "artists" and len(parts) == 2:
        r = db().q("SELECT * FROM artist WHERE id=? OR lower(name)=lower(?)", (parts[1], urllib.parse.unquote(parts[1])), one=True)
        return send(artist_dto(r, srv().anns(u, [r["id"]]).get(r["id"]))) if r else send({"error": "Not found"}, 404)
    if lp in ("/musicgenres", "/genres"):
        g = genre_list(u, qs.get("searchterm") or "")
        start, lim = int(qs.get("startindex") or 0), int(qs.get("limit") or 0) or 100000
        return send(items(g[start:start + lim], len(g), start))
    if lparts[0] in ("musicgenres", "genres") and len(parts) == 2:
        name = urllib.parse.unquote(parts[1])
        return send(genre_dto(name, 0))
    if lp == "/items" or (len(lparts) == 3 and lparts[0] == "users" and lparts[2] == "items"):
        return send(query(u, qs))
    if (len(lparts) == 4 and lparts[0] == "users" and lparts[2] == "items") or (len(lparts) == 2 and lparts[0] in ("items", "useritems")):
        d = one(u, parts[-1])
        return send(d) if d else send({"error": "Not found"}, 404)
    if lparts[0] == "displaypreferences":
        return send({"Id": parts[1] if len(parts) > 1 else "", "SortBy": "SortName", "SortOrder": "Ascending", "RememberIndexing": False,
                     "PrimaryImageHeight": 250, "PrimaryImageWidth": 250, "CustomPrefs": {}, "ScrollDirection": "Horizontal",
                     "ShowBackdrop": False, "RememberSorting": False, "ShowSidebar": False, "Client": qs.get("client", "")})
    if lparts[0] in ("plugins", "localization", "studios", "persons", "startup", "library"):
        return send([] if method == "GET" else {})
    return send({"error": f"{path} isn't supported by Umculo's Jellyfin API"}, 404)


def init(umculo):
    global core
    core = umculo
