#!/bin/bash
# v0.7: access keys, share links (Spotify/Apple/Deezer/text), Apple playlist, follow, recognize, status, recheck.
S=$1; rm -rf $S && mkdir -p $S/data/Music; M=$S/data/Music
cat > $S/shazam.json <<'J'
[[0, 300, "Kabza De Small", "Track A"], [300, 700, "FreakSA", "Ride"]]
J
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$M/Spotify Playlists" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek,usenet BATCH_SOURCES=soulseek SEARCH_SECONDS=1 UMCULO_PASSWORD=pw \
 ITUNES_API=$U SPOTIFY_EMBED=$U/embed UMCULO_TEST_SHAZAM=$S/shazam.json LRCLIB_API=$U python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
A="curl -s -u admin:pw localhost:8097"; P() { curl -s -u admin:pw -X POST localhost:8097$1 -d "$2"; }
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
echo "--- access key:"; TOK=$(P /api/tokens '{"name":"iPhone Shortcut"}' | J "d['token']"); echo ${TOK:0:8}...
curl -s -o /dev/null -w "no key: %{http_code}\n" localhost:8097/api/requests
curl -s -o /dev/null -w "bearer: %{http_code}\n" -H "Authorization: Bearer $TOK" localhost:8097/api/requests
curl -s -o /dev/null -w "query: %{http_code}\n" "localhost:8097/api/requests?token=$TOK"
curl -s -o /dev/null -w "wrong: %{http_code}\n" -H "Authorization: Bearer umc_nope" localhost:8097/api/requests
echo "--- share:"
for t in "https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC?si=x" "https://music.apple.com/za/album/ride/777?i=555" "https://www.deezer.com/en/track/112" "Look at this: FreakSA - Machine Gun"; do
  curl -s -X POST -H "Authorization: Bearer $TOK" localhost:8097/api/share -d "{\"url\":\"$t\"}" | J "d['message']"; done
curl -s "localhost:8097/api/share?token=$TOK&url=https%3A%2F%2Fmusic.apple.com%2Fza%2Falbum%2Fgun-ep%2F777" | J "d['message']"
curl -s -X POST -H "Authorization: Bearer $TOK" localhost:8097/api/share -d '{"url":"https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}' | J "d['message']"
sleep 15
$A/api/requests | J "[(r['target']['artist'], r['target']['title'], r['state']) for r in d['requests']]"
$A/api/playlists | J "[(j['name'], j['counts']) for j in d['jobs']]"
echo "--- apple playlist page:"; cd app && python3 -c "
import sys; sys.path.insert(0,'.')
import umculo as u, share as s; s.core=u
print(s.apple_playlist('$U/applepl'))"; cd ..
echo "--- follow:"; P /api/follows '{"url":"https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"}'; echo; $A/api/follows | J "[(f['name'], f['count'], f['job']) for f in d['follows']]"
echo "--- recognize:"; ffmpeg -v error -y -f lavfi -i sine=d=6 -c:a aac $S/clip.m4a; curl -s -u admin:pw -X POST --data-binary @$S/clip.m4a -H "Content-Type: audio/mp4" localhost:8097/api/recognize; echo
echo "--- status:"; $A/api/status?fresh=1 | J "(d['overall'], [(c['name'], c['state'], c['detail'][:70]) for c in d['checks']], len(d['errors']))"
grep -iE "traceback" -A5 $S/app.log | head -20
kill $AP $MP
