#!/bin/bash
# v0.9: follow artists + new releases (+auto download), album download, Mixtapes playlist.
S=$1; rm -rf $S && mkdir -p $S; M=$S/data/Music
mkdir -p $M/meta_podcasts/Show; for n in 1 2; do ffmpeg -v error -f lavfi -i sine=d=5 -c:a libmp3lame "$M/meta_podcasts/Show/Episode $n.mp3"; done
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=mdu NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$M/Spotify Playlists" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek BATCH_SOURCES=soulseek SEARCH_SECONDS=1 LRCLIB_API=$U NTFY_URL=$U/ntfy \
 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 2
A="curl -s localhost:8097"; P() { curl -s -X POST localhost:8097$1 -d "$2"; }
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
echo "--- artist search + page:"; $A/api/artists/search?q=freaksa | J "[(a['id'], a['name'], a['following']) for a in d['artists']]"
$A/api/artist/5 | J "(d['name'], len(d['top']), [(a['title'], a['type'], a['date']) for a in d['albums']], [r['name'] for r in d['related']], d['following'])"
echo "--- follow (nothing new yet):"; P /api/artists/follow '{"id":"5"}' | J "(d['name'], d['auto'])"; $A/api/artists | J "([f['name'] for f in d['following']], d['releases'])"
echo "--- a new single comes out; check:"; touch $S/data/new_release; P /api/artists/5/check; echo
$A/api/artists | J "[(r['title'], r['type'], r['artist']) for r in d['releases']]"; grep -c "New single from FreakSA" $S/mock.log
echo "--- auto on: the next release downloads by itself"; P /api/artists/5/auto '{"on":true}' > /dev/null; touch $S/data/new_release2
P /api/artists/5/check; echo; $A/api/artists | J "[(a['album']['id'], a['total']) for a in d['albums']]"; grep -c "Downloading it now" $S/mock.log
echo "--- album download:"; JID=$(P /api/album/get '{"id":"77"}' | J "d['job']"); sleep 3
$A/api/artist/5 | J "[(a['title'], a['job']) for a in d['albums']]"; sleep 25
$A/api/artists | J "[(a['album']['title'], a['total'], a['counts'], a['running']) for a in d['albums']]"
ls $M/Umculo/*/ 2>/dev/null; ls $M/_playlists 2>/dev/null
$A/api/pl | J "[p['name'] for p in d['playlists']]"
echo "--- album page shows progress:"; $A/api/artist/5 | J "[(a['title'], a['job']) for a in d['albums']]"
echo "--- Mixtapes playlist:"; sleep 32; cat "$M/_playlists/Mixtapes.m3u8"
ID=$($A/api/pl?fresh=1 | J "[p['id'] for p in d['playlists'] if p['name']=='Mixtapes'][0]"); P /api/pl/$ID/rename '{"name":"My Mixes"}' | J "d['ok']"
ffmpeg -v error -f lavfi -i sine=d=5 -c:a libmp3lame "$M/meta_podcasts/Show/Episode 3.mp3"
curl -s localhost:8097/api/pl?fresh=1 > /dev/null; sleep 1
python3 -c "import sys; sys.path.insert(0,'app')" ; ls $M/_playlists
grep -iE "traceback" -A6 $S/app.log | head -20
kill $AP $MP
