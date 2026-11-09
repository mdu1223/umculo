#!/bin/bash
# v0.6: For You shelves (DJ mixes, because-you-play, rediscover), weekly mix download + stream modes, feedback, web stream.
S=$1; rm -rf $S && mkdir -p $S/data/Music; M=$S/data/Music
cat > $S/shazam.json <<'J'
[[0, 300, "Kabza De Small", "Track A"], [300, 700, "FreakSA", "Ride"], [700, 1500, "Unknown Guy", "Song C"]]
J
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$M/Spotify Playlists" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek,usenet BATCH_SOURCES=soulseek BATCH_WORKERS=2 SEARCH_SECONDS=1 \
 ITUNES_API=$U PODOMATIC_FEED="$U/podomatic/{channel}.xml" UMCULO_TEST_SHAZAM=$S/shazam.json UMCULO_TEST_STREAM_URL=$U/single.mp3 \
 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
A="curl -s localhost:8097"; P() { curl -s -X POST localhost:8097$1 -d "$2"; }
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
echo "--- subscribe (auto song finder on new episodes):"
SID=$(P /api/link '{"url":"https://www.podomatic.com/podcasts/deepshizzol"}' | J "d['id']")
P /api/link/subscribe "{\"show\":\"$SID\",\"latest\":true}"; echo
for i in $(seq 60); do sleep 1; $A/api/mixes | J "[f['state'] for f in d['finds']]" | grep -q done && break; done
$A/api/mixes | J "[(f['title'], f['state'], f['count'], f['file']) for f in d['finds']]"
echo "--- for you:"; P /api/foryou/refresh; echo
for i in $(seq 60); do sleep 1; $A/api/foryou | J "d['building']" | grep -q False && break; done
$A/api/foryou | J "'\n'.join(f\"[{s['title']}] \" + ', '.join(f\"{i['artist']} - {i['title']} ({i['why']})\" for i in s['items']) for s in d['shelves'])"
$A/api/foryou | J "d['artists']"
echo "--- weekly (download):"; P /api/foryou/weekly; echo; sleep 6
$A/api/foryou | J "(d['weekly']['mode'], [s['title'] for s in d['weekly']['songs']], d['weekly']['progress'])"
echo "--- feedback:"; K=$($A/api/foryou | J "d['shelves'][0]['items'][0]['key']"); P /api/foryou/feedback "{\"key\":\"$K\",\"like\":false}"; echo
$A/api/foryou | J "[i['key'] for s in d['shelves'] for i in s['items']].count('$K')"
echo "--- weekly (stream):"; P /api/settings '{"weekly_mode":"stream"}' | J "d['weekly_mode']"; P /api/foryou/weekly; sleep 6
$A/api/foryou | J "(d['weekly']['mode'], [s['title'] for s in d['weekly']['songs']])"
R="http://localhost:8097/rest"; AU="u=mdu&t=abc&s=salt&v=1.16.1&c=amperfy"
curl -s "$R/getPlaylists?$AU&f=json" | J "[(p['id'], p['name'], p['songCount']) for p in d['subsonic-response']['playlists']['playlist']]"
curl -s "$R/getPlaylist?$AU&id=umc-pl-weekly" | head -c 300; echo
DID=$($A/api/foryou | J "d['weekly']['songs'][0]['deezer_id']")
echo "--- web stream:"; curl -s -o /dev/null -w "%{http_code} %{content_type} %{size_download}\n" localhost:8097/api/stream/$DID
curl -s -o /dev/null -w "nd: %{http_code} %{content_type}\n" localhost:8097/api/ndstream/s1
grep -iE "traceback|failed" $S/app.log | head
kill $AP $MP
