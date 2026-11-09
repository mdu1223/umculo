"""2.1: Jellyfin API (as Finamp uses it), listening log + Wrapped, instant mix, lyrics, favourites, importers."""
import http.server, json, plistlib, sys, threading, time, urllib.error, urllib.parse, urllib.request, datetime
PORT, S = int(sys.argv[1]), sys.argv[2]
B = f"http://localhost:{PORT}"
ok = 0
def check(c, what):
    global ok
    if not c:
        print("FAIL:", what); sys.exit(1)
    ok += 1
def req(path, body=None, method=None, headers=None, raw=False):
    h = {"Content-Type": "application/json", **(headers or {})}
    r = urllib.request.Request(B + path, data=json.dumps(body).encode() if body is not None else None, headers=h,
                               method=method or ("POST" if body is not None else "GET"))
    try:
        resp = urllib.request.urlopen(r, timeout=60); data = resp.read()
        return (resp.status, data if raw else (json.loads(data) if data else None), resp.headers)
    except urllib.error.HTTPError as e:
        return (e.code, e.read().decode()[:200], e.headers)
AUTH = 'MediaBrowser Client="Finamp", Device="iPhone", DeviceId="abc", Version="0.9.11"'
# ---- Jellyfin: Finamp's sign-in flow
c, info, _ = req("/System/Info/Public"); check(c == 200 and info["ProductName"] == "Jellyfin Server" and info["Id"], "public info")
c, r, _ = req("/Users/AuthenticateByName", {"Username": "mdu", "Pw": "wrong"}, headers={"X-Emby-Authorization": AUTH}); check(c == 401, "bad login")
c, r, _ = req("/Users/AuthenticateByName", {"Username": "mdu", "Pw": "ndpass"}, headers={"X-Emby-Authorization": AUTH})
check(c == 200 and r["AccessToken"] and r["User"]["Id"], "login")
TOK, UID = r["AccessToken"], r["User"]["Id"]
H = {"X-Emby-Authorization": AUTH + f', Token="{TOK}"'}
c, r, _ = req(f"/Users/{UID}/Views", headers=H); check(r["Items"][0]["CollectionType"] == "music", "views")
VIEW = r["Items"][0]["Id"]
c, r, _ = req(f"/Users/{UID}/Items?ParentId={VIEW}&IncludeItemTypes=MusicAlbum&Recursive=true&SortBy=SortName&SortOrder=Ascending&Limit=100", headers=H)
names = [x["Name"] for x in r["Items"]]; print("albums:", names); check(r["TotalRecordCount"] == len(names) >= 4 and "DaKAR II" in names, "albums")
dakar = next(x for x in r["Items"] if x["Name"] == "DaKAR II")
c, r, _ = req(f"/Users/{UID}/Items?ParentId={dakar['Id']}&IncludeItemTypes=Audio&SortBy=ParentIndexNumber,IndexNumber,SortName", headers=H)
check([x["Name"] for x in r["Items"]] == ["Ngud", "Spirit", "Ngiyazifela"], f"album tracks {[x['Name'] for x in r['Items']]}")
song = r["Items"][0]; check(song["RunTimeTicks"] > 0 and song["MediaSources"][0]["Container"] == "flac" and song["HasLyrics"], "song fields")
c, r, _ = req(f"/Artists/AlbumArtists?ParentId={VIEW}&UserId={UID}&Limit=100", headers=H)
check("Kwesta" in [x["Name"] for x in r["Items"]], "album artists")
kw = next(x for x in r["Items"] if x["Name"] == "Kwesta")
c, r, _ = req(f"/Users/{UID}/Items?ParentId={kw['Id']}&IncludeItemTypes=MusicAlbum", headers=H); check(len(r["Items"]) == 1, "artist albums")
c, r, _ = req(f"/Users/{UID}/Items?SearchTerm=pretty&IncludeItemTypes=Audio,MusicAlbum,MusicArtist&Recursive=true", headers=H)
check(any(x["Type"] == "Audio" for x in r["Items"]) and any(x["Type"] == "MusicAlbum" for x in r["Items"]), "search")
c, r, _ = req(f"/Users/{UID}/Items/{song['Id']}", headers=H); check(r["Id"] == song["Id"], "single item")
c, r, _ = req(f"/Users/{UID}/Items/Latest?IncludeItemTypes=MusicAlbum&Limit=10", headers=H); check(isinstance(r, list) and r, "latest")
c, r, _ = req(f"/MusicGenres?UserId={UID}", headers=H); check({"Hip-Hop", "Amapiano"} <= {x["Name"] for x in r["Items"]}, "genres")
g = next(x for x in r["Items"] if x["Name"] == "Amapiano")
c, r, _ = req(f"/Users/{UID}/Items?GenreIds={g['Id']}&IncludeItemTypes=Audio&Recursive=true", headers=H); check(len(r["Items"]) == 3, f"genre filter {len(r['Items'])}")
c, data, hd = req(f"/Items/{dakar['Id']}/Images/Primary?maxHeight=200&tag={dakar['Id']}", headers=H, raw=True); check(c == 200 and len(data) > 200, "image")
c, data, hd = req(f"/Audio/{song['Id']}/universal?UserId={UID}&DeviceId=abc&Container=flac,mp3&MaxStreamingBitrate=999999999&api_key={TOK}", raw=True)
check(c == 200 and hd["Content-Type"] == "audio/flac", "direct play")
c, data, hd = req(f"/Audio/{song['Id']}/universal?UserId={UID}&Container=mp3&MaxStreamingBitrate=128000&TranscodingContainer=mp3&AudioCodec=mp3&api_key={TOK}", raw=True)
check(c == 200 and hd["Content-Type"] == "audio/mpeg", "transcode")
c, r, _ = req(f"/Audio/{song['Id']}/Lyrics", headers=H); check(r["Lyrics"][1]["Start"] == 3500 * 10000, "lyrics")
c, r, _ = req(f"/Users/{UID}/FavoriteItems/{song['Id']}", {}, headers=H); check(r["IsFavorite"], "favourite")
c, r, _ = req(f"/Users/{UID}/Items?Filters=IsFavorite&IncludeItemTypes=Audio&Recursive=true", headers=H); check(len(r["Items"]) == 1, "favourites list")
c, r, _ = req(f"/Users/{UID}/FavoriteItems/{song['Id']}", method="DELETE", headers=H); check(not r["IsFavorite"], "unfavourite")
c, r, _ = req("/Sessions/Playing", {"ItemId": song["Id"]}, headers=H); check(c == 204, "now playing")
c, r, _ = req("/Sessions/Playing/Stopped", {"ItemId": song["Id"], "PositionTicks": 5 * 10**7}, headers=H)
c, r, _ = req(f"/Users/{UID}/Items/{song['Id']}", headers=H); check(r["UserData"]["PlayCount"] == 1, f"played via session {r['UserData']}")
c, r, _ = req(f"/Items/{song['Id']}/InstantMix?UserId={UID}&Limit=20", headers=H); check(r["Items"] and r["Items"][0]["Id"] == song["Id"], "instant mix")
c, r, _ = req("/Playlists", {"Name": "Finamp list", "Ids": [song["Id"]], "UserId": UID}, headers=H); pid = r["Id"]; check(pid, "create playlist")
c, _, _ = req(f"/Playlists/{pid}/Items?Ids={dakar['Id']}", {}, headers=H)
c, r, _ = req(f"/Playlists/{pid}/Items?UserId={UID}", headers=H); check(len(r["Items"]) == 1 or len(r["Items"]) == 2, "playlist items")
c, _, _ = req(f"/Playlists/{pid}/Items?EntryIds={r['Items'][0]['PlaylistItemId']}", method="DELETE", headers=H)
c, r2, _ = req(f"/Playlists/{pid}/Items?UserId={UID}", headers=H); check(len(r2["Items"]) == len(r["Items"]) - 1, "remove from playlist")
c, r, _ = req(f"/Users/{UID}/Items?IncludeItemTypes=Playlist&Recursive=true", headers=H); check("Finamp list" in [x["Name"] for x in r["Items"]], "playlists list")
c, _, _ = req(f"/Items/{pid}", method="DELETE", headers=H); check(c == 204, "delete playlist")
c, r, _ = req(f"/Users/{UID}/Items?IncludeItemTypes=MusicArtist&Recursive=true&SortBy=SortName", headers=H); check(len(r["Items"]) >= 3, "artists via items")
c, r, _ = req("/Items?IncludeItemTypes=Audio&Recursive=true&Limit=2&StartIndex=1&SortBy=SortName", headers=H); check(len(r["Items"]) == 2 and r["StartIndex"] == 1 and r["TotalRecordCount"] >= 7, "paging")
c, r, _ = req("/Items", headers={}); check(c == 401, "needs token")
print("JELLYFIN OK", ok)
# ---- web player plays, Wrapped, lyrics, mix, favourites
def web(p, body=None):
    c, r, _ = req(p, body); return r
lib = web("/api/lib/search?q=spirit")["songs"][0]
check(web("/api/listen", {"kind": "lib", "id": lib["id"], "played": 5, "duration": 200})["counted"] is False, "skip not counted")
check(web("/api/listen", {"kind": "lib", "id": lib["id"], "played": 120, "duration": 200})["counted"], "play counted")
check(web("/api/listen", {"kind": "dz", "id": "111", "played": 200, "duration": 225, "title": "Machine Gun", "artist": "FreakSA"})["counted"], "internet play")
w = web("/api/wrapped?period=month"); print("wrapped:", {k: w.get(k) for k in ("label", "plays", "minutes", "artists", "streak", "new_artists")})
check(w["plays"] >= 3 and w["top_artists"][0]["name"] in ("Kwesta", "FreakSA") and w["internet"] == 1, "wrapped")
check(web("/api/wrapped?period=year")["plays"] == w["plays"] and web("/api/wrapped?period=week&back=5")["empty"], "periods")
ly = web(f"/api/lyrics/lib/{web('/api/lib/search?q=ngud')['songs'][0]['id']}"); check(ly["synced"] and ly["lines"][0]["value"] == "Ngud line one", "lyrics api")
mx = web(f"/api/mix/lib/{lib['id']}"); check(len(mx["songs"]) >= 3 and mx["songs"][0]["id"] == lib["id"], "mix api")
mi = web("/api/mix/dz/111"); check("songs" in mi, "internet mix")
check(web("/api/pl/fav", {"id": "abc", "on": True})["favs"] == ["abc"] and web("/api/pl/favs")["favs"] == ["abc"], "playlist favourite")
print("LISTENING OK", ok)
# ---- importers: fake Plex and Jellyfin, and an iTunes file
songs = web("/api/lib/search?q=")  # noqa
class Fake(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def j(self, o):
        d = json.dumps(o).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(d))); self.end_headers(); self.wfile.write(d)
    def do_GET(self):
        p = self.path
        if p.startswith("/library/sections/") and "/all" in p:
            return self.j({"MediaContainer": {"Metadata": [
                {"title": "Bheka", "grandparentTitle": "Kabza De Small", "originalTitle": "Kabza De Small, Focalistic", "parentTitle": "Pretty Girl", "duration": 6000,
                 "viewCount": 9, "lastViewedAt": 1700000000, "userRating": 10, "Media": [{"Part": [{"file": "/plexmusic/Albums/Kabza De Small/Pretty Girl/02 Bheka.mp3"}]}]},
                {"title": "Not Here", "grandparentTitle": "Nobody", "duration": 1000, "viewCount": 3}]}})
        if p.startswith("/library/sections"):
            return self.j({"MediaContainer": {"Directory": [{"key": "1", "type": "artist", "title": "Music"}]}})
        if p.startswith("/playlists/9/items"):
            return self.j({"MediaContainer": {"Metadata": [{"title": "Zaka", "grandparentTitle": "Mas Musiq", "duration": 6000}]}})
        if p.startswith("/playlists"):
            return self.j({"MediaContainer": {"Metadata": [{"ratingKey": "9", "title": "Plex Faves", "smart": False}]}})
        if "/Users/u1/Items" in p and "Playlist" in p:
            return self.j({"Items": [{"Id": "pl1", "Name": "Jelly Mix"}]})
        if p.startswith("/Playlists/pl1/Items"):
            return self.j({"Items": [{"Name": "Ngud", "Artists": ["Kwesta"], "RunTimeTicks": 6 * 10**7, "MediaType": "Audio"}]})
        if "/Users/u1/Items" in p:
            return self.j({"Items": [{"Name": "Pretty Girl", "Artists": ["Kabza De Small"], "Album": "Pretty Girl", "RunTimeTicks": 6 * 10**7,
                                      "Path": "/media/x/01 Pretty Girl.mp3", "UserData": {"PlayCount": 4, "IsFavorite": True, "LastPlayedDate": "2025-01-02T03:04:05.0000000Z"}}]})
        self.send_response(404); self.end_headers()
    def do_POST(self):
        if self.path == "/Users/AuthenticateByName":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            return self.j({"AccessToken": "t", "User": {"Id": "u1"}})
        self.send_response(404); self.end_headers()
fs = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake); threading.Thread(target=fs.serve_forever, daemon=True).start()
FB = f"http://127.0.0.1:{fs.server_address[1]}"
def wait_job():
    for _ in range(60):
        j = web("/api/migrate")["job"]
        if j["state"] != "running": return j
        time.sleep(0.5)
    return j
c, r, _ = req("/api/migrate/plex", {"url": FB, "token": "x", "person": "admin"}); j = wait_job(); print("plex:", j["message"])
check(j["state"] == "done" and j["result"]["songs"] == 1 and j["result"]["playlists"] == 1 and j["result"]["missed"] == 1, "plex import")
c, r, _ = req("/api/migrate/jellyfin", {"url": FB, "username": "a", "password": "b", "person": "admin"}); j = wait_job(); print("jellyfin:", j["message"])
check(j["state"] == "done" and j["result"]["songs"] == 1 and j["result"]["playlists"] == 1, "jellyfin import")
lib_songs = {s["title"]: s for s in web("/api/lib/search?q=a")["songs"] + web("/api/lib/search?q=e")["songs"]}
xml = plistlib.dumps({"Tracks": {"1": {"Track ID": 1, "Name": "Zaka", "Artist": "Mas Musiq", "Total Time": 6000, "Play Count": 12, "Rating": 80, "Loved": True,
                                      "Play Date UTC": datetime.datetime(2025, 5, 1), "Location": "file:///Users/m/Music/Mas%20Musiq%20-%20Zaka.m4a"}},
                      "Playlists": [{"Name": "Library", "Master": True, "Playlist Items": [{"Track ID": 1}]}, {"Name": "Road", "Playlist Items": [{"Track ID": 1}]}]}).decode()
c, r, _ = req("/api/migrate/itunes", {"xml": xml, "person": "admin"}); j = wait_job(); print("itunes:", j["message"])
check(j["state"] == "done" and j["result"]["songs"] == 1 and j["result"]["playlists"] == 1, "itunes import")
U = "u=mdu&p=ndpass&v=1.16.1&c=t&f=json"
zak = json.load(urllib.request.urlopen(f"{B}/rest/search3?{U}&query=zaka"))["subsonic-response"]["searchResult3"]["song"]
zak = [s for s in zak if not s["id"].startswith("umc-")][0]; check(zak.get("playCount") == 12 and zak.get("userRating") == 4 and zak.get("starred"), f"itunes data {zak}")
# ---- 2.1.1: covers open to Jellyfin apps, wrong-password lockout from the internet only, bad requests answered
c, data, _ = req(f"/Items/{dakar['Id']}/Images/Primary?maxHeight=200", raw=True); check(c == 200 and len(data) > 200, "jellyfin cover without a token")
c, r, _ = req(f"/Users/{UID}/Items?IncludeItemTypes=Audio&Recursive=true"); check(c == 401, "items still need a token")
NET = {"X-Forwarded-For": "8.8.4.4"}
for i in range(8):
    c, r, _ = req("/api/login", {"username": "mdu", "password": "guess%d" % i}, headers=NET)
c, r, _ = req("/api/login", {"username": "mdu", "password": "ndpass"}, headers=NET); check(c == 401 and "Too many" in str(r), f"internet lockout {c} {r}")
c, r, _ = req("/rest/ping?u=mdu&p=ndpass&v=1.16.1&c=t&f=json", headers=NET); check("Too many" in json.dumps(r), "subsonic lockout too")
c, r, _ = req("/rest/ping?u=mdu&p=ndpass&v=1.16.1&c=t&f=json"); check(r["subsonic-response"]["status"] == "ok", "home network never locked out")
c, r, _ = req("/api/login", {"username": "jessie", "password": "ndpass"}, headers={"X-Forwarded-For": "1.0.0.1"}); check(c == 401 and "Wrong" in str(r), "other people unaffected")
print("ALL OK", ok)
