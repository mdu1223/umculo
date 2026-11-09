"""Fake Deezer, Navidrome, slskd, Prowlarr and SABnzbd on one port, for local testing."""
import json
import os
import re
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = sys.argv[2]                      # fake /data root
PORT = int(sys.argv[1])
SLSKD_DL = os.path.join(ROOT, "Music", "Soulseek")
SAB_DONE = os.path.join(ROOT, "downloads", "complete")
STATE = {"transfers": {}, "sab_polls": 0, "scans": 0}


def make_audio(path, codec_args, seconds=225):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    *codec_args, path], check=True)


TRACK = {"id": 111, "title": "Machine Gun", "title_version": "", "duration": 225,
         "preview": f"http://localhost:{PORT}/preview.mp3",
         "artist": {"id": 5, "name": "FreakSA"},
         "album": {"id": 77, "title": "Machine Gun", "cover_medium": f"http://localhost:{PORT}/cover.jpg",
                   "cover_xl": f"http://localhost:{PORT}/cover.jpg"}}
TRACK2 = {**TRACK, "id": 112, "title": "Ride", "duration": 240}
REL = {**TRACK, "id": 220, "title": "City Lights", "artist": {"id": 6, "name": "Mr Shane SA"}}


FEED = f"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel><title>Deep Sessions</title><itunes:author>DJ Mock</itunes:author>
<itunes:image href="http://localhost:{PORT}/cover.jpg"/>
<item><title>Deep Sessions 042</title><guid>ep-42</guid><link>https://www.podomatic.com/podcasts/deepshizzol/episodes/2026-10-30T10_00_00-07_00</link>
<pubDate>Thu, 30 Oct 2026 10:00:00 +0000</pubDate><itunes:duration>25:00</itunes:duration>
<enclosure url="http://localhost:{PORT}/ep/42.mp3" type="audio/mpeg" length="12000000"/>
<description><![CDATA[Tracklist:<br>01. Kabza De Small - Track A<br>02. Freak SA - Ride<br>03. DJ Tracklist Only - Hidden Gem<br>Follow me on instagram @djmock]]></description></item>
<item><title>Deep Sessions 041</title><guid>ep-41</guid><link>https://www.podomatic.com/podcasts/deepshizzol/episodes/2026-10-23T10_00_00-07_00</link>
<pubDate>Thu, 23 Oct 2026 10:00:00 +0000</pubDate><itunes:duration>1300</itunes:duration>
<enclosure url="http://localhost:{PORT}/ep/41.mp3" type="audio/mpeg" length="11000000"/>
<description>No tracklist this week</description></item>
</channel></rss>"""


def fake(n):
    """A believable catalogue song: n picks the artist (10 songs each) and the rank."""
    return {"id": 900000 + n, "title": f"Song {n}", "title_version": "", "duration": 200 + n % 60, "rank": (n * 7919) % 1000000,
            "readable": True, "artist": {"id": 3000 + n // 3, "name": f"Artist {n // 3}"},
            "album": {"id": 4000 + n, "title": f"Album {n}", "cover_medium": f"http://localhost:{PORT}/cover.jpg",
                      "cover_xl": f"http://localhost:{PORT}/cover.jpg"}}


ND_LIB = os.environ.get("MOCK_ND_LIBRARY") == "1"
ND = {"libs": [{"id": 1, "name": "Music", "path": "/music"}], "users": {}, "access": {}, "playlists": []}


def _id(x):
    import hashlib
    return hashlib.sha1(x.encode()).hexdigest()[:10]


def nd_roots(user):
    """Which folders this Navidrome user sees: (disk folder, library path prefix)."""
    if user in ("mdu", ""):
        out = [os.path.join(ROOT, "Music")]
        fam = os.path.join(ROOT, "Family")
        out += [os.path.join(fam, d) for d in (os.listdir(fam) if os.path.isdir(fam) else [])]
        return out
    u = next((x for x in ND["users"].values() if x["userName"] == user), None)
    if not u:
        return []
    out = []
    for lid in ND["access"].get(u["id"], [1]):
        lib = next((l for l in ND["libs"] if l["id"] == lid), None)
        if lib:
            out.append(os.path.join(ROOT, "Music") if lib["path"] == "/music" else os.path.join(ROOT, "Family", lib["path"].rsplit("/", 1)[-1]))
    return out


def nd_songs(user):
    import mutagen
    songs = []
    for root in nd_roots(user):
        for d, _s, fs in os.walk(root):
            for f in sorted(fs):
                if not f.lower().endswith((".mp3", ".flac", ".m4a", ".opus")):
                    continue
                full = os.path.join(d, f)
                rel = os.path.relpath(full, root)
                stem = os.path.splitext(f)[0]
                artist, title = stem.split(" - ", 1) if " - " in stem else (os.path.basename(os.path.dirname(d)) or "Unknown", stem)
                album = os.path.basename(d)
                try:
                    br = int((mutagen.File(full).info.bitrate or 0) / 1000)
                except Exception:  # noqa: BLE001
                    br = 0
                songs.append({"id": _id(full), "title": title, "artist": artist, "artistId": _id(artist), "album": album,
                              "albumId": _id(d), "path": rel, "suffix": f.rsplit(".", 1)[-1].lower(), "bitRate": br,
                              "size": os.path.getsize(full), "duration": 30, "coverArt": _id(d), "playCount": 1,
                              "track": 0, "year": 2024})
                if "Spirit" in f:
                    songs[-1].update(starred="2025-03-01T10:00:00Z", userRating=5, playCount=7, played="2025-03-02T10:00:00Z")
    return songs


def nd_lib(p, q):
    user = q.get("u", [""])[0]
    songs = nd_songs(user)
    if p == "/rest/search3":
        qq = q.get("query", [""])[0].lower()
        off = int(q.get("songOffset", ["0"])[0])
        hits = [s for s in songs if not qq or qq in (s["title"] + " " + s["artist"]).lower()]
        return {"searchResult3": {"song": hits[off:off + int(q.get("songCount", ["20"])[0])]}}
    def albums():
        al = {}
        for s in songs:
            a = al.setdefault(s["albumId"], {"id": s["albumId"], "name": s["album"], "artist": s["artist"], "artistId": s["artistId"],
                                              "songCount": 0, "coverArt": s["coverArt"], "year": 2024, "duration": 0})
            a["songCount"] += 1
        return list(al.values())
    if p == "/rest/getArtists":
        ars = {}
        for a in albums():
            x = ars.setdefault(a["artistId"], {"id": a["artistId"], "name": a["artist"], "albumCount": 0})
            x["albumCount"] += 1
        return {"artists": {"index": [{"name": "#", "artist": sorted(ars.values(), key=lambda x: x["name"])}]}}
    if p == "/rest/getArtist":
        i = q.get("id", [""])[0]
        al = [a for a in albums() if a["artistId"] == i]
        return {"artist": {"id": i, "name": al[0]["artist"], "album": al}} if al else {}
    if p == "/rest/getAlbum":
        i = q.get("id", [""])[0]
        a = next((x for x in albums() if x["id"] == i), None)
        return {"album": {**a, "song": [s for s in songs if s["albumId"] == i]}} if a else {}
    if p == "/rest/getSong":
        s = next((x for x in songs if x["id"] == q.get("id", [""])[0]), None)
        return {"song": s} if s else {}
    if p == "/rest/getAlbumList2":
        return {"albumList2": {"album": albums()[:int(q.get("size", ["10"])[0])]}}
    if p == "/rest/getPlaylists" and not ND.get("seeded") and user == "mdu":
        ND["seeded"] = True
        ND["playlists"].append({"id": "rt1", "name": "Road Trip", "owner": "mdu", "songCount": 2, "songs": [x["id"] for x in songs[:2]]})
    if p == "/rest/getPlaylist":
        pl = next((x for x in ND["playlists"] if x["id"] == q.get("id", [""])[0]), None)
        return {"playlist": {**pl, "entry": [x for x in songs if x["id"] in pl.get("songs", [])]}} if pl else {}
    if p == "/rest/getStarred2":
        return {"starred2": {"song": [x for x in songs if x.get("starred")], "album": [], "artist": []}}
    if p == "/rest/getPlaylists":
        return {"playlists": {"playlist": [x for x in ND["playlists"] if x["owner"] == user or user == "mdu"]}}
    if p == "/rest/createPlaylist":
        ND["playlists"].append({"id": _id(user + q["name"][0]), "name": q["name"][0], "owner": user, "songCount": len(q.get("songId", []))})
        print("ND CREATE PLAYLIST", user, q["name"][0], len(q.get("songId", [])), flush=True)
        return {}
    if p == "/rest/deletePlaylist":
        ND["playlists"] = [x for x in ND["playlists"] if x["id"] != q["id"][0]]
        return {}
    return None


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def j(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def raw(self, data, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_DELETE(self):  # noqa: N802
        print("DELETE", self.path, flush=True)
        self.j({})

    def do_PUT(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        d = json.loads(self.rfile.read(n) or b"{}")
        m = re.match(r"^/api/user/([^/]+)/library$", self.path)
        if m:
            ND["access"][m.group(1)] = d["libraryIds"]
            print("ND ACCESS", m.group(1), d["libraryIds"], flush=True)
            return self.j([])
        print("ND PUT", self.path, sorted(d), flush=True)
        self.j({})

    def do_GET(self):  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        # ---- Deezer
        if p == "/search":
            qq = q.get("q", [""])[0].lower()
            if "song c" in qq:
                return self.j({"data": [{**TRACK, "id": 300, "title": "Song C", "artist": {"id": 9, "name": "Unknown Guy"}}]})
            if "ride" in qq and "machine" not in qq:
                return self.j({"data": [TRACK2]})
            return self.j({"data": [TRACK, TRACK2]})
        if p.startswith("/track/"):
            tid = p.rsplit("/", 1)[-1]
            if tid == "300":
                return self.j({**TRACK, "id": 300, "title": "Song C", "artist": {"id": 9, "name": "Unknown Guy"}})
            return self.j(TRACK2 if tid == "112" else TRACK)
        if p == "/search/album":
            return self.j({"data": [{"id": 77, "title": "Machine Gun", "nb_tracks": 2, "cover_xl": f"http://localhost:{PORT}/cover.jpg",
                                     "artist": {"id": 5, "name": "FreakSA"}}]})
        if p == "/search/artist":
            return self.j({"data": [{"id": 5, "name": "FreakSA", "nb_album": 1}]})
        if re.match(r"^/album/\d+$", p):
            return self.j({"id": 77, "title": "Machine Gun", "artist": {"id": 5, "name": "FreakSA"}, "release_date": "2025-05-01",
                           "cover_xl": f"http://localhost:{PORT}/cover.jpg", "tracks": {"data": [TRACK, TRACK2]}})
        if re.match(r"^/artist/\d+$", p):
            return self.j({"id": 5, "name": "FreakSA", "nb_album": 1, "picture_xl": f"http://localhost:{PORT}/cover.jpg"})
        if re.match(r"^/artist/\d+/albums$", p):
            import datetime as _d
            al = [{"id": 77, "title": "Machine Gun", "release_date": "2025-05-01", "record_type": "album",
                   "cover_medium": f"http://localhost:{PORT}/cover.jpg"}]
            if os.path.exists(os.path.join(ROOT, "new_release")):
                al.insert(0, {"id": 78, "title": "Brand New", "release_date": _d.date.today().isoformat(), "record_type": "single",
                              "cover_medium": f"http://localhost:{PORT}/cover.jpg"})
            if os.path.exists(os.path.join(ROOT, "new_release2")):
                al.insert(0, {"id": 79, "title": "Another One", "release_date": _d.date.today().isoformat(), "record_type": "ep",
                              "cover_medium": f"http://localhost:{PORT}/cover.jpg"})
            return self.j({"data": al})
        if re.match(r"^/artist/\d+/related$", p):
            return self.j({"data": [{"id": 6, "name": "Mr Shane SA"}, {"id": 8, "name": "Kabza De Small"}]})
        if re.match(r"^/artist/\d+/top$", p):
            return self.j({"data": [REL]})
        if p in ("/chart/0/tracks",) or re.match(r"^/playlist/\d+/tracks$", p):
            base = 5000 if p.startswith("/chart") else int(p.split("/")[2]) * 100
            return self.j({"data": [fake(base + n) for n in range(40)]})
        if p == "/search/playlist":
            term = q.get("q", [""])[0]
            return self.j({"data": [{"id": 70 + len(term) % 5, "title": term + " hits", "nb_tracks": 40, "fans": 900},
                                    {"id": 80 + len(term) % 5, "title": term + " deep", "nb_tracks": 30, "fans": 100}]})
        if p == "/cover.jpg":
            with open(os.path.join(ROOT, "cover.jpg"), "rb") as fh:
                return self.raw(fh.read(), "image/jpeg")
        if p == "/preview.mp3":
            with open(os.path.join(ROOT, "preview.mp3"), "rb") as fh:
                return self.raw(fh.read(), "audio/mpeg")
        # ---- podcasts (RSS, iTunes lookup, PodOmatic) and episode audio
        if p in ("/feed.xml", "/podomatic/deepshizzol.xml"):
            return self.raw(FEED.encode(), "application/rss+xml")
        if p == "/lookup" and q.get("id", [""])[0] == "555":
            return self.j({"results": [{"wrapperType": "track", "trackName": "Ride", "artistName": "FreakSA",
                                        "trackTimeMillis": 240000, "collectionName": "Ride",
                                        "artworkUrl100": f"http://localhost:{PORT}/100x100bb.jpg"}]})
        if p == "/lookup" and q.get("id", [""])[0] == "777":
            return self.j({"results": [{"wrapperType": "collection", "collectionName": "Gun EP", "artistName": "FreakSA"},
                                       {"wrapperType": "track", "trackName": "Machine Gun", "artistName": "FreakSA"},
                                       {"wrapperType": "track", "trackName": "Ride", "artistName": "FreakSA"}]})
        if p == "/applepl":
            data = [{"data": {"sections": [{"id": "playlist-detail-header"}, {"id": "track-list - 1", "items": [
                {"title": "Machine Gun", "artistName": "FreakSA", "duration": 226000},
                {"title": "Ride", "subtitleLinks": [{"title": "FreakSA"}], "duration": 240000}]}]}}]
            html = ('<html><head><meta property="og:title" content="Amapiano Grooves on Apple Music">'
                    '<script type="application/json" id="serialized-server-data">' + json.dumps(data) + '</script></head></html>')
            return self.raw(html.encode(), "text/html")
        if p.startswith("/embed/track/"):
            data = {"props": {"pageProps": {"state": {"data": {"entity": {"type": "track", "name": "Ride",
                    "artists": [{"name": "FreakSA"}], "duration": 240000}}}}}}
            return self.raw(('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>').encode(), "text/html")
        if p.startswith("/embed/playlist/"):
            data = {"props": {"pageProps": {"state": {"data": {"entity": {"name": "Sp List", "trackList": [
                {"title": "Machine Gun", "subtitle": "FreakSA", "duration": 226000},
                {"title": "Ride", "subtitle": "FreakSA", "duration": 240000}]}}}}}}
            return self.raw(('<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + '</script>').encode(), "text/html")
        if p == "/lookup":
            return self.j({"resultCount": 3, "results": [
                {"wrapperType": "track", "kind": "podcast", "collectionName": "Deep Sessions", "artistName": "DJ Mock",
                 "feedUrl": f"http://localhost:{PORT}/feed.xml", "artworkUrl600": f"http://localhost:{PORT}/cover.jpg"},
                {"wrapperType": "podcastEpisode", "trackId": 1000111, "trackName": "Deep Sessions 042",
                 "episodeGuid": "ep-42", "episodeUrl": f"http://localhost:{PORT}/ep/42.mp3", "releaseDate": "2026-10-30T00:00:00Z"},
                {"wrapperType": "podcastEpisode", "trackId": 1000110, "trackName": "Deep Sessions 041",
                 "episodeGuid": "ep-41", "episodeUrl": f"http://localhost:{PORT}/ep/41.mp3"}]})
        m = re.match(r"^/ep/(\d+)\.mp3$", p)
        if m:
            f = os.path.join(ROOT, f"ep{m.group(1)}.mp3")
            if not os.path.exists(f):
                make_audio(f, ["-c:a", "libmp3lame", "-b:a", "64k"], 1500 if m.group(1) == "42" else 1300)
            with open(f, "rb") as fh:
                return self.raw(fh.read(), "audio/mpeg")
        if p == "/single.mp3":
            f = os.path.join(ROOT, "single.mp3")
            if not os.path.exists(f):
                make_audio(f, ["-c:a", "libmp3lame", "-b:a", "320k"], 200)
            with open(f, "rb") as fh:
                return self.raw(fh.read(), "audio/mpeg")
        # ---- Navidrome
        if p == "/api/library":
            return self.j(ND["libs"])
        if p == "/api/user":
            return self.j(list(ND["users"].values()))
        if re.match(r"^/api/user/[^/]+$", p):
            return self.j(ND["users"].get(p.rsplit("/", 1)[-1], {}))
        if ND_LIB and p.startswith("/rest/"):
            r = nd_lib(p, q)
            if r is not None:
                return self.j({"subsonic-response": {"status": "ok", **r}})
        if p == "/rest/search3":
            return self.j({"subsonic-response": {"status": "ok", "searchResult3": {"song": [
                {"id": "s1", "title": "Ride", "artist": "FreakSA", "album": "Ride", "duration": 240,
                 "suffix": "mp3", "bitRate": 128, "path": "Spotify Playlists/x/FreakSA - Ride.mp3"}]}}})
        if p == "/rest/ping":
            ok = q.get("u", [""])[0] == "mdu"
            return self.j({"subsonic-response": {"status": "ok" if ok else "failed", "version": "1.16.1"}})
        if p == "/rest/getPlaylists":
            return self.j({"subsonic-response": {"status": "ok", "playlists": {"playlist": [{"id": "pl1", "name": "Real List", "songCount": 3}]}}})
        if p == "/rest/getAlbumList2":
            if q.get("f", [""])[0] == "json":
                return self.j({"subsonic-response": {"status": "ok", "albumList2": {"album": [
                    {"id": "al1", "name": "Ride", "artist": "FreakSA", "playCount": 12}]}}})
            return self.raw(b'<?xml version="1.0"?><subsonic-response status="ok" version="1.16.1"><albumList2/></subsonic-response>', "text/xml")
        if p == "/rest/getStarred2":
            return self.j({"subsonic-response": {"status": "ok", "starred2": {"song": [
                {"id": "s1", "title": "Ride", "artist": "FreakSA", "played": "2025-01-01T00:00:00Z", "duration": 240}]}}})
        if p == "/rest/stream":
            with open(os.path.join(ROOT, "preview.mp3"), "rb") as fh:
                return self.raw(fh.read(), "audio/mpeg")
        if p == "/rest/deletePlaylist":
            print("ND DELETE PLAYLIST", q.get("id"), flush=True)
            return self.j({"subsonic-response": {"status": "ok"}})
        if p == "/rest/star":
            STATE["nd_star"] = q.get("id")
            return self.j({"subsonic-response": {"status": "ok"}})
        if p == "/rest/startScan":
            STATE["scans"] += 1
            return self.j({"subsonic-response": {"status": "ok"}})
        # ---- slskd
        if re.match(r"^/api/v0/searches/[^/]+$", p):
            return self.j({"isComplete": True})
        if p.endswith("/responses"):
            q = STATE.get("searches", {}).get(p.split("/")[-2], "")
            if "ride" in q:
                return self.j([{"username": "wavking", "hasFreeUploadSlot": True, "uploadSpeed": 3_000_000,
                                "files": [{"filename": "Music\\Freak SA - Ride.flac", "size": 40_000_000,
                                           "bitDepth": 24, "sampleRate": 44100, "length": 240}]}])
            if "machine" not in q:
                return self.j([])
            return self.j([
                {"username": "deepcollector", "hasFreeUploadSlot": True, "uploadSpeed": 2_000_000, "queueLength": 0,
                 "files": [{"filename": "@@music\\FreakSA\\Freak SA & Mr Shane SA - Machine Gun.flac",
                            "size": 31_000_000, "bitDepth": 16, "sampleRate": 44100, "length": 226},
                           {"filename": "@@music\\FreakSA\\cover.jpg", "size": 1000}]},
                {"username": "amapianoking", "hasFreeUploadSlot": False, "uploadSpeed": 300_000, "queueLength": 4,
                 "files": [{"filename": "Downloads\\FreakSA - Machine Gun (Radio Edit).mp3", "size": 7_000_000,
                            "bitRate": 320, "length": 190}]},
                {"username": "random", "hasFreeUploadSlot": True, "uploadSpeed": 900_000,
                 "files": [{"filename": "Other Artist - Machine Gun.mp3", "size": 5_000_000, "bitRate": 192}]},
            ])
        m = re.match(r"^/api/v0/transfers/downloads/([^/]+)$", p)
        if m:
            user = urllib.parse.unquote(m.group(1))
            t = STATE["transfers"].get(user)
            if not t:
                return self.j({"username": user, "directories": []})
            t["polls"] += 1
            if user == os.environ.get("STUCK"):
                return self.j({"username": user, "directories": [{"directory": "x", "files": [{
                    "id": "t1", "filename": t["filename"], "state": "Queued, Remotely", "percentComplete": 0}]}]})
            done = t["polls"] >= 3
            if done and not t["written"]:
                base = t["filename"].replace("\\", "/").split("/")
                codec = ["-c:a", "libmp3lame", "-b:a", "320k"] if base[-1].endswith(".mp3") else ["-c:a", "flac"]
                make_audio(os.path.join(SLSKD_DL, base[-2], base[-1]), codec, 240 if "Ride" in base[-1] else 226)
                t["written"] = True
            return self.j({"username": user, "directories": [{"directory": "x", "files": [{
                "filename": t["filename"], "state": "Completed, Succeeded" if done else "InProgress",
                "percentComplete": 100 if done else 40 * t["polls"]}]}]})
        # ---- Prowlarr
        if p == "/api/v1/search":
            return self.j([
                {"protocol": "usenet", "title": "FreakSA_and_Mr_Shane_SA-Machine_Gun-(GRP123)-WEB-2025-320",
                 "size": 9_000_000, "indexer": "NZBgeek", "grabs": 4, "guid": "g1", "indexerId": 1,
                 "downloadUrl": f"http://localhost:{PORT}/nzb/1"},
                {"protocol": "usenet", "title": "VA-Deep_House_Sessions-WEB-2024", "size": 900_000_000},
                {"protocol": "torrent", "title": "FreakSA - Machine Gun [FLAC]", "size": 30_000_000}])
        if p.startswith("/nzb/"):
            return self.raw(b'<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb"></nzb>',
                            "application/x-nzb")
        # ---- LRCLIB
        if p == "/api/get":
            if "ride" in q.get("track_name", [""])[0].lower():
                return self.j({"syncedLyrics": "[00:01.00] Ride with me\n[00:05.00] all night"})
            return self.j({"statusCode": 404}, 404)
        if p == "/api/search":
            return self.j([])
        # ---- SABnzbd
        if p == "/api":
            mode = q.get("mode", [""])[0]
            if mode == "queue":
                STATE["sab_polls"] += 1
                if STATE["sab_polls"] < 2:
                    return self.j({"queue": {"slots": [{"nzo_id": "SABnzbd_nzo_1", "percentage": "55"}]}})
                return self.j({"queue": {"slots": []}})
            if mode == "history":
                folder = os.path.join(SAB_DONE, "FreakSA_and_Mr_Shane_SA-Machine_Gun-WEB-2025")
                if not os.path.exists(folder):
                    make_audio(os.path.join(folder, "01-freaksa_and_mr_shane_sa-machine_gun.mp3"),
                               ["-c:a", "libmp3lame", "-b:a", "320k"], 225)
                    make_audio(os.path.join(folder, "02-freaksa-another_song.mp3"),
                               ["-c:a", "libmp3lame", "-b:a", "320k"], 200)
                return self.j({"history": {"slots": [{"nzo_id": "SABnzbd_nzo_1", "status": "Completed",
                                                      "storage": folder}]}})
        return self.j({"error": "mock: unknown " + p}, 404)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        p = urllib.parse.urlparse(self.path).path
        if p == "/ntfy":
            print("NTFY", body.decode(), flush=True)
            return self.j({})
        if p == "/auth/login":
            d = json.loads(body)
            return self.j({"token": "T", "id": "admin"} if d.get("username") == "mdu" else {"error": "bad"}, 200 if d.get("username") == "mdu" else 401)
        if p == "/api/user":
            d = json.loads(body)
            uid = "nd-" + d["userName"]
            ND["users"][uid] = {"id": uid, "userName": d["userName"], "name": d.get("name"), "isAdmin": False}
            print("ND CREATE USER", d["userName"], flush=True)
            return self.j({"id": uid})
        if p == "/api/library":
            d = json.loads(body)
            lid = len(ND["libs"]) + 1
            ND["libs"].append({"id": lid, "name": d["name"], "path": d["path"]})
            print("ND CREATE LIBRARY", d["path"], flush=True)
            return self.j({"id": lid})
        if p == "/api/v0/searches":
            sid = "s%d" % (len(STATE.setdefault("searches", {})) + 1)
            STATE["searches"][sid] = json.loads(body)["searchText"]
            return self.j({"id": sid})
        m = re.match(r"^/api/v0/transfers/downloads/([^/]+)$", p)
        if m:
            user = urllib.parse.unquote(m.group(1))
            files = json.loads(body)
            STATE["transfers"][user] = {"filename": files[0]["filename"], "polls": 0, "written": False}
            return self.j({})
        if p == "/api":
            ok = b"addfile" in body and b"<nzb" in body
            return self.j({"status": ok, "nzo_ids": ["SABnzbd_nzo_1"] if ok else []})
        return self.j({"error": "mock: unknown POST " + p}, 404)


if __name__ == "__main__":
    os.makedirs(ROOT, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=orange:s=300x300", "-frames:v", "1",
                    os.path.join(ROOT, "cover.jpg")], check=True)
    make_audio(os.path.join(ROOT, "preview.mp3"), ["-c:a", "libmp3lame", "-b:a", "128k"], 30)
    print("mock ready", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
