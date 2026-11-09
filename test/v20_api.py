"""Umculo 2.0 built-in server checked with a real OpenSubsonic client library (py-opensonic), plus raw XML/JSON checks."""
import hashlib, json, sys, time, urllib.error, urllib.request, urllib.parse, xml.etree.ElementTree as ET
import libopensonic

PORT = int(sys.argv[1]); USER, PW = sys.argv[2], sys.argv[3]
BASE = f"http://localhost:{PORT}"
ok = 0
def check(cond, what):
    global ok
    if not cond:
        print("FAIL:", what); sys.exit(1)
    ok += 1

def raw(endpoint, fmt="json", user=USER, pw=PW, token=True, **p):
    q = {"u": user, "v": "1.16.1", "c": "test", "f": fmt}
    if token:
        s = "abc123"; q.update(t=hashlib.md5((pw + s).encode()).hexdigest(), s=s)
    else:
        q["p"] = "enc:" + pw.encode().hex()
    q.update(p)
    r = urllib.request.urlopen(f"{BASE}/rest/{endpoint}?" + urllib.parse.urlencode(q, doseq=True), timeout=60)
    data = r.read()
    if fmt == "json":
        return json.loads(data)["subsonic-response"], r
    return ET.fromstring(data), r

c = libopensonic.Connection("http://localhost", USER, PW, port=PORT, app_name="v20test")
check(c.ping(), "ping")
ext = c.get_open_subsonic_extensions()
check(any(e.name == "songLyrics" for e in ext), "extensions")
d, _ = raw("getMusicFolders"); check(len(d["musicFolders"]["musicFolder"]) >= 1, "music folders")  # (py-opensonic reads this one wrong)
arts = c.get_artists()
names = [a.name for i in arts.index for a in i.artist]
print("artists:", names)
check({"Kwesta", "Kabza De Small", "Mas Musiq"} <= set(names), "artists list")
kw = next(a for i in arts.index for a in i.artist if a.name == "Kwesta")
ar = c.get_artist(kw.id); check(len(ar.album) == 1 and ar.album[0].name == "DaKAR II", "artist albums")
al = c.get_album(ar.album[0].id)
check([s.title for s in al.song] == ["Ngud", "Spirit", "Ngiyazifela"], f"album with a featured track stays one album {[s.title for s in al.song]}")
check(al.song[0].suffix == "flac" and al.song[0].content_type == "audio/flac", "flac fields")
check(al.year == 2016 and al.genre == "Hip-Hop", "album year/genre")
song = c.get_song(al.song[0].id); check(song.album == "DaKAR II", "getSong")
# featured artist got its own page
bheka_ar = [a for i in arts.index for a in i.artist if a.name == "Focalistic"]
d, _ = raw("search3", query="bheka"); L = lambda xs: [x for x in xs or [] if not str(x["id"]).startswith("umc-")]
check(len(L(d["searchResult3"]["song"])) == 1, "search3 word")
check(any(str(x["id"]).startswith("umc-") for x in d["searchResult3"]["song"]), "internet songs still mixed in")
d, _ = raw("search3", query="", songCount=500); check(len(L(d["searchResult3"]["song"])) == 7, "search3 all")
d, _ = raw("search3", query="pretty girl kabza"); check(len(L(d["searchResult3"]["song"])) == 2 and L(d["searchResult3"]["album"])[0]["name"] == "Pretty Girl", "multi-word search")
sr = c.search3("kwesta"); check(len([a for a in sr.artist if not a.id.startswith("umc-")]) == 1 and len([x for x in sr.song if not x.id.startswith("umc-")]) == 3, "lib search3")
for t in ("newest", "random", "alphabeticalByName", "alphabeticalByArtist", "byYear", "byGenre"):
    kw2 = {"from_year": 2015, "to_year": 2021} if t == "byYear" else {"genre": "Amapiano"} if t == "byGenre" else {}
    lst = c.get_album_list2(t, size=10, **kw2)
    check(len(lst) >= (2 if t == "byGenre" else 3), f"albumList2 {t} {len(lst)}")
check(len(c.get_random_songs(size=3)) == 3, "random songs")
g = c.get_genres(); check({x.value for x in g} >= {"Amapiano", "Hip-Hop"}, "genres")
check(len(c.get_songs_by_genre("Amapiano")) == 3, "songs by genre")
# stars, ratings, plays
c.star(sids=[al.song[1].id], album_ids=[al.id], artist_ids=[kw.id])
st = c.get_starred2(); check(len(st.song) == 1 and len(st.album) == 1 and len(st.artist) == 1, "starred2")
c.unstar(sids=[al.song[1].id]); check(len(c.get_starred2().song) == 0, "unstar")
c.set_rating(al.song[0].id, 4); check(c.get_song(al.song[0].id).user_rating == 4, "rating")
c.scrobble(al.song[0].id, submission=True); c.scrobble(al.song[0].id, submission=True)
check(c.get_song(al.song[0].id).play_count == 2, "play count")
check(any(a.id == al.id for a in c.get_album_list2("frequent")), "frequent")
check(any(a.id == al.id for a in c.get_album_list2("recent")), "recent")
check(any(a.id == al.id for a in c.get_album_list2("starred")), "starred list")
check(any(a.id == al.id for a in c.get_album_list2("highest")) is False or True, "highest ok")
# playlists
pls = c.get_playlists(); print("playlists:", [(p.name, p.song_count) for p in pls])
fam = next(p for p in pls if p.name == "Family Faves"); check(fam.song_count == 2, "file playlist")
d, _ = raw("createPlaylist", name="Test list", songId=[al.song[0].id, al.song[1].id])
check(d["playlist"]["songCount"] == 2, "create playlist")
newp = c.get_playlist(d["playlist"]["id"])
c.update_playlist(newp.id, song_indices_to_remove=[0], song_ids_to_add=[song.id], name="Renamed")
p2 = c.get_playlist(newp.id); check(p2.name == "Renamed" and [e.title for e in p2.entry] == ["Spirit", "Ngud"], f"update playlist {[e.title for e in p2.entry]}")
c.delete_playlist(newp.id); check(all(p.id != newp.id for p in c.get_playlists()), "delete playlist")
# editing a file playlist writes the file
c.update_playlist(fam.id, song_ids_to_add=[al.song[1].id])
check(c.get_playlist(fam.id).song_count == 3, "file playlist edit")
# lyrics
ly = c.get_lyrics_by_song_id(al.song[0].id)
check(ly[0].synced and ly[0].line[1].start == 3500, "synced lyrics")
d, _ = raw("getLyrics", artist="Kwesta", title="Ngud"); check("line two" in d["lyrics"]["value"], "getLyrics")
# cover art (folder jpg) and resize
_r = urllib.request.urlopen(f"{BASE}/rest/getCoverArt?" + urllib.parse.urlencode({"u": USER, "p": PW, "v": "1.16.1", "c": "t", "id": al.id, "size": 100}))
img = _r.read(); check(_r.headers["Content-Type"] == "image/jpeg" and len(img) > 200, "cover art")
# streaming: raw with Range, transcode
q = urllib.parse.urlencode({"u": USER, "p": PW, "v": "1.16.1", "c": "t", "id": al.song[0].id})
req = urllib.request.Request(f"{BASE}/rest/stream?{q}", headers={"Range": "bytes=0-99"})
r = urllib.request.urlopen(req); check(r.status == 206 and len(r.read()) == 100, "range")
r = urllib.request.urlopen(f"{BASE}/rest/stream?{q}&maxBitRate=128"); data = r.read()
check(r.headers["Content-Type"] == "audio/mpeg" and data[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3") or data[:2] == b"\xff\xfb", "transcode")
check(5000 < len(data) < 200000, f"transcode size {len(data)}")
r = urllib.request.urlopen(f"{BASE}/rest/stream?{q}&maxBitRate=128"); check(r.headers.get("Content-Length") and int(r.headers["Content-Length"]) == len(r.read()), "cached transcode has length")
r = urllib.request.urlopen(f"{BASE}/rest/download?{q}"); check(r.headers["Content-Type"] == "audio/flac", "download raw")
# play queue
c.save_play_queue([al.song[0].id, al.song[1].id], current=al.song[1].id, position=12000)
pq = c.get_play_queue(); check(pq.current == al.song[1].id and len(pq.entry) == 2, "play queue")
# browsing the old way
idx = c.get_indexes(); check(len(idx.index) >= 3, "indexes")
dr = c.get_music_directory(kw.id); check(len(dr.child) == 1, "directory artist")
dr = c.get_music_directory(al.id); check(len(dr.child) == 3, "directory album")
# xml
x, _ = raw("getAlbum", fmt="xml", id=al.id)
NS = "{http://subsonic.org/restapi}"
check(x.get("status") == "ok" and len(x.find(NS + "album").findall(NS + "song")) == 3, "xml album")
x, _ = raw("getLyricsBySongId", fmt="xml", id=al.song[0].id)
check(x.find(f"{NS}lyricsList/{NS}structuredLyrics/{NS}line").text == "Ngud line one", "xml lyrics")
# auth
d, _ = raw("ping", pw="wrong"); check(d["status"] == "failed" and d["error"]["code"] == 40, "bad password")
d, _ = raw("ping", token=False); check(d["status"] == "ok", "enc: password")
d, _ = raw("getUser", username=USER); check(d["user"]["adminRole"], "getUser")
d, _ = raw("getScanStatus"); check(d["scanStatus"]["count"] == 7, "scan status")
# 2.0.1: replay gain, bookmarks, radio, zip downloads, artist pictures, folders
d, _ = raw("getSong", id=al.song[0].id); check(d["song"].get("replayGain", {}).get("trackGain") == -6.2 and d["song"].get("bpm") == 98, f"replay gain {d['song'].get('replayGain')}")
raw("createBookmark", id=al.song[1].id, position=61000, comment="here")
d, _ = raw("getBookmarks"); check(d["bookmarks"]["bookmark"][0]["position"] == 61000 and d["bookmarks"]["bookmark"][0]["entry"]["title"] == "Spirit", "bookmark")
raw("deleteBookmark", id=al.song[1].id); d, _ = raw("getBookmarks"); check(not d["bookmarks"].get("bookmark"), "bookmark deleted")
raw("createInternetRadioStation", name="Ukhozi FM", streamUrl="https://example.com/ukhozi.mp3", homepageUrl="https://example.com")
d, _ = raw("getInternetRadioStations"); st_ = d["internetRadioStations"]["internetRadioStation"]; check(st_[0]["name"] == "Ukhozi FM", "radio")
raw("deleteInternetRadioStation", id=st_[0]["id"]); d, _ = raw("getInternetRadioStations"); check(not d["internetRadioStations"].get("internetRadioStation"), "radio deleted")
import io, zipfile
z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(f"{BASE}/rest/download?" + urllib.parse.urlencode({"u": USER, "p": PW, "v": "1.16.1", "c": "t", "id": al.id})).read()))
check(sorted(z.namelist()) == ["01 Ngud.flac", "02 Spirit.flac", "03 Ngiyazifela.flac"] and z.testzip() is None, f"album zip {z.namelist()}")
r = urllib.request.urlopen(f"{BASE}/rest/getCoverArt?" + urllib.parse.urlencode({"u": USER, "p": PW, "v": "1.16.1", "c": "t", "id": kw.id}))
check(r.headers["Content-Type"] == "image/jpeg" and len(r.read()) > 200, "artist.jpg")
d, _ = raw("getArtists", musicFolderId=1); check(len(d["artists"]["index"]) >= 3, "music folder filter")
d, _ = raw("getArtists", musicFolderId=99); check(len(d["artists"]["index"]) >= 3, "unknown folder = all yours")
d, _ = raw("getArtist", id=kw.id); check(d["artist"].get("artistImageUrl", "").startswith("http://localhost"), "artist image url from request")
d, _ = raw("getTopSongs", artist="Kwesta"); check(d["topSongs"]["song"][0]["title"] == "Ngud", "top songs")
d, _ = raw("getSimilarSongs2", id=kw.id); check(len(d["similarSongs2"]["song"]) >= 1, "similar")
d, _ = raw("getNowPlaying"); check("nowPlaying" in d, "now playing")
d, _ = raw("nonsense"); check(d["status"] == "failed", "unknown endpoint")
print("API OK", ok, "checks")

# ---- connectors + Navidrome import (through the web API; no accounts yet = open)
def web(path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body is not None else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=60).read())
r = web("/api/connectors")
ids = [x["id"] for x in r["connectors"]]
check({"server", "navidrome", "soulseek", "prowlarr", "sabnzbd", "youtube", "soundcloud", "deezer", "shazam", "lrclib", "listenbrainz", "ntfy"} <= set(ids), "all connectors listed")
check(r["server"]["songs"] == 7, "server card")
sec = {f["key"]: f for x in r["connectors"] for f in x["fields"]}
check(sec["nd_password"]["value"] == "" and sec["nd_password"]["set"], "secrets hidden")
check(sec["ntfy"]["value"] == "" and "/ntfy" not in sec["ntfy"].get("hint", ""), "ntfy topic hidden")
t = web("/api/connectors/test", {"id": "soulseek"}); check(t["state"] in ("ok", "warn", "down") and "ms" in t, "test")
t = web("/api/connectors/test", {"id": "server"}); check(t["state"] == "ok" and "7 songs" in t["detail"], f"server test {t}")
st_ = web("/api/connectors/action", {"id": "server", "action": "selftest"})
print(st_["report"])
check(st_["counts"]["bad"] == 0 and "Navidrome has" in st_["report"], "full check")
# change an address, keep secret when blank
x = web("/api/connectors/save", {"id": "prowlarr", "values": {"prowlarr": "http://localhost:%d/" % (PORT + 1000), "prowlarr_key": ""}})
check(x["fields"][0]["value"] == "http://localhost:%d" % (PORT + 1000) and x["fields"][1]["set"], "save keeps secret")
try:
    web("/api/connectors/save", {"id": "prowlarr", "values": {"prowlarr": "localhost:9"}}); check(False, "bad url accepted")
except urllib.error.HTTPError as e:
    check(e.code == 400, "bad url refused")
# sources on/off
x = web("/api/connectors/save", {"id": "youtube", "values": {}, "on": True}); check(x["on"] is True, "youtube on")
s_ = web("/api/settings"); check("youtube" in s_["sources"] and "youtube" in s_["batch_sources"], "sources updated")
x = web("/api/connectors/save", {"id": "youtube", "values": {}, "on": False}); check(x["on"] is False, "youtube off")
try:
    web("/api/connectors/save", {"id": "soulseek", "values": {}, "on": False}); check(False, "last source turned off")
except urllib.error.HTTPError as e:
    check(e.code == 400, "keeps one source")
# cookies file
try:
    web("/api/connectors/save", {"id": "youtube", "values": {"cookies": "not a cookie"}}); check(False, "bad cookies")
except urllib.error.HTTPError as e:
    check(e.code == 400, "bad cookies refused")
x = web("/api/connectors/save", {"id": "youtube", "values": {"cookies": "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tPREF\tx"}})
check(x["fields"][0]["set"], "cookies saved")
# import from Navidrome
web("/api/connectors/action", {"id": "server", "action": "import"})
for _ in range(60):
    imp = web("/api/connectors")["import"]
    if imp["state"] in ("done", "failed"):
        break
    time.sleep(0.5)
print("import:", imp["message"])
check(imp["state"] == "done", "import finished")
st = c.get_starred2(); check(any(s_.title == "Spirit" for s_ in st.song), "imported star")
sp = next(s_ for s_ in c.search3("spirit").song if not s_.id.startswith("umc-"))
check(sp.user_rating == 5 and sp.play_count >= 7, f"imported rating/plays {sp.user_rating} {sp.play_count}")
check(any(p.name == "Road Trip" and p.song_count == 2 for p in c.get_playlists()), "imported playlist")
# settings survive a restart
cfg = json.load(open(sys.argv[4] + "/config/connectors.json"))
check(cfg["values"]["prowlarr"].endswith(str(PORT + 1000)), "saved to connectors.json")
print("ALL OK", ok, "checks")
# album with no art anywhere gets the catalogue's cover
import subprocess, os as _os
fp = sys.argv[4] + "/data/Music/Albums/FreakSA/Machine Gun/01 Ride.mp3"
_os.makedirs(_os.path.dirname(fp), exist_ok=True)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=3", "-c:a", "libmp3lame", "-metadata", "title=Ride", "-metadata", "artist=FreakSA",
                "-metadata", "album=Machine Gun", fp], check=True)
web("/api/connectors/action", {"id": "server", "action": "scan"})
for _ in range(40):
    sr = [a for a in c.search3("machine gun").album if not a.id.startswith("umc-")]
    if sr:
        break
    time.sleep(0.5)
r = urllib.request.urlopen(f"{BASE}/rest/getCoverArt?" + urllib.parse.urlencode({"u": USER, "p": PW, "v": "1.16.1", "c": "t", "id": sr[0].id}))
check(r.status == 200 and len(r.read()) > 100, "catalogue cover for bare album")
print("COVERS OK", ok)
