#!/bin/bash
# v0.5: Subsonic server - auth, pass-through, search with internet results (JSON + XML), virtual album/artist,
# streaming an internet song with Range, cover art, star -> download, scrobble.
S=$1; rm -rf $S && mkdir -p $S/data/Music
M=$S/data/Music
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$M/Spotify Playlists" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek,usenet SEARCH_SECONDS=1 UMCULO_PASSWORD=webpass \
 UMCULO_TEST_STREAM_URL=$U/single.mp3 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
R="http://localhost:8097/rest"; AU="u=mdu&t=abc&s=salt&v=1.16.1&c=amperfy"
echo "--- wrong user:"; curl -s "$R/ping.view?u=bad&p=x&f=json"; echo; curl -s "$R/search3?u=bad&p=x&f=json&query=gun"; echo
echo "--- web page still needs its password:"; curl -s -o /dev/null -w "%{http_code}\n" localhost:8097/api/requests
echo "--- passthrough XML:"; curl -s "$R/getAlbumList2.view?$AU&type=newest"; echo
echo "--- search3 json:"; curl -s "$R/search3.view?$AU&f=json&query=machine+gun" | python3 -c "
import json,sys; r=json.load(sys.stdin)['subsonic-response']; sr=r['searchResult3']
print(r['status'], [(s['id'], s['title'], s['artist'], s.get('album')) for s in sr['song']]); print([a['id'] for a in sr.get('album',[])], [a['id'] for a in sr.get('artist',[])])"
echo "--- search3 xml:"; curl -s "$R/search3?$AU&query=machine%20gun&artistCount=0&albumCount=0" | head -c 700; echo
echo "--- empty query (sync) has no internet items:"; curl -s "$R/search3?$AU&f=json&query=" | python3 -c "import json,sys; print(json.load(sys.stdin)['subsonic-response']['searchResult3'])"
echo "--- album / artist / song:"; curl -s "$R/getAlbum?$AU&f=json&id=umc-al-77" | python3 -c "import json,sys; a=json.load(sys.stdin)['subsonic-response']['album']; print(a['name'], a['artist'], [s['title'] for s in a['song']])"
curl -s "$R/getArtist?$AU&id=umc-ar-5" | head -c 400; echo
curl -s "$R/getSong?$AU&f=json&id=umc-dz-112" | python3 -c "import json,sys; print(json.load(sys.stdin)['subsonic-response']['song']['title'])"
echo "--- cover:"; curl -s -o /dev/null -w "%{http_code} %{content_type} %{size_download}\n" "$R/getCoverArt?$AU&id=umc-dz-112&size=300"
echo "--- stream (first time fetches):"; time curl -s -o $S/s1 -w "%{http_code} %{content_type} %{size_download}\n" "$R/stream?$AU&id=umc-dz-112"
echo "--- range:"; curl -s -o /dev/null -D - -H "Range: bytes=1000-1999" "$R/stream?$AU&id=umc-dz-112" | grep -iE "^HTTP|content-range|content-length"
ffprobe -v error -show_entries format=format_name,duration -of csv=p=0 $S/s1
echo "--- real stream passthrough:"; curl -s -o /dev/null -w "%{http_code} %{content_type} %{size_download}\n" "$R/stream?$AU&id=s1"
echo "--- star internet + real song:"; curl -s "$R/star?$AU&f=json&id=umc-dz-112&id=s1"; echo
for i in $(seq 40); do sleep 1; curl -s -u admin:webpass localhost:8097/api/requests | grep -q '"ready"' && break; done
curl -s -u admin:webpass localhost:8097/api/requests | python3 -c "import json,sys; print([(r['target']['artist'], r['target']['title'], r['state'], r['candidate']['quality']) for r in json.load(sys.stdin)['requests']])"
echo "--- scrobble:"; curl -s "$R/scrobble?$AU&f=json&id=umc-dz-112&submission=true"; echo; cat $S/config/subsonic.json; echo
echo "--- after download, streams the library copy:"; curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "$R/stream?$AU&id=umc-dz-112"
echo "--- playlists:"; curl -s "$R/getPlaylists?$AU&f=json"; echo
grep -iE "traceback|failed" $S/app.log | head
kill $AP $MP
