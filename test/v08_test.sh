#!/bin/bash
# v0.8: one playlist list (rename, watch, delete keep/delete songs, remove a song), queue delete, recipes, stations, logs, gzip.
S=$1; rm -rf $S && mkdir -p $S; M=$S/data/Music; SP="$M/Spotify Playlists"
mk() { mkdir -p "$(dirname "$1")"; ffmpeg -v error -y -f lavfi -i "sine=frequency=300:duration=$3" -c:a $4 "$1"; }
mk "$SP/Chill/A - One.mp3" x 30 libmp3lame; mk "$SP/Chill/B - Two.mp3" x 30 libmp3lame; mk "$SP/Chill/C - Shared.mp3" x 30 libmp3lame
mk "$SP/Party/C - Shared.mp3" x 30 libmp3lame
printf '#EXTM3U\nA - One.mp3\nB - Two.mp3\n*\nC - Shared.mp3\n' > "$SP/Chill/Chill.m3u"
printf '#EXTM3U\n../Chill/C - Shared.mp3\n' > "$SP/Party/Party.m3u"
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$SP" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek,usenet SEARCH_SECONDS=1 LRCLIB_API=$U \
 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
A="curl -s localhost:8097"; P() { curl -s -X POST localhost:8097$1 -d "$2"; }
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
ID() { $A/api/pl?fresh=1 | J "[p['id'] for p in d['playlists'] if p['name']=='$1'][0]"; }
echo "--- list:"; $A/api/pl | J "[(p['name'], p['folder'], p['tracks'], p['status']) for p in d['playlists']]"
C=$(ID Chill)
echo "--- detail (no '*' row):"; $A/api/pl/$C | J "[(r['title'], r['state']) for r in d['rows']]"
echo "--- unwatch / watch:"; P /api/pl/$C/unwatch; echo; $A/api/pl?fresh=1 | J "[(p['name'], p['status']) for p in d['playlists']]"
P /api/pl/$C/watch > /dev/null
echo "--- rename:"; P /api/pl/$C/rename '{"name":"Chill Vibes"}'; echo; ls "$SP/Chill"; C=$(ID "Chill Vibes")
echo "--- remove one song and delete its file:"; K=$($A/api/pl/$C | J "[r['key'] for r in d['rows'] if r['title']=='Two'][0]")
P /api/pl/$C/remove "{\"key\":\"$K\",\"delete\":true}"; echo; cat "$SP/Chill/Chill Vibes.m3u"; ls "$SP/Chill"
echo "--- delete preview + delete with songs (Shared stays: Party uses it):"; $A/api/pl/$C/delete-preview; echo
P /api/pl/$C/delete '{"songs":true}'; echo; find "$SP" -type f | sort
grep "ND DELETE" $S/mock.log
echo "--- queue: delete a finished download from the server:"
CK=$(P /api/check '{"text":"FreakSA - Ride"}' | J "d['id']"); sleep 3
R=$(P /api/get "{\"check_id\":\"$CK\",\"candidate_id\":\"best\"}" | J "d['id']"); sleep 10
F=$($A/api/requests | J "[r['file'] for r in d['requests'] if r['id']=='$R'][0]"); ls "$F"
P /api/requests/$R/delete '{"file":true}'; echo; ls "$F" 2>&1 | tail -1
echo "--- recipes:"; $A/api/recipes | J "(len(d['templates']), list(d['ingredients']), len(d['genres']))"
P /api/recipes/preview '{"ingredients":{"trending":10,"vibe":10},"vibe":"amapiano","popularity":"hits"}' | J "(len(d['songs']), d['songs'][0]['why'], d['songs'][-1]['why'])"
RID=$(P /api/recipes '{"template":"fresh"}' | J "d['id']"); sleep 4
$A/api/recipes/$RID | J "(d['name'], d['count'], d['running'], d['message'], d['days'])"
P /api/recipes/$RID/save '{"ingredients":{"vibe":12},"vibe":"gqom","popularity":"gems","mode":"stream","days":[1,3]}' | J "(d['ingredients'], d['days'])"
P /api/recipes/$RID/run > /dev/null; sleep 4; $A/api/recipes/$RID | J "(d['count'], len(set(s['key'] for s in d['songs'])))"
echo "--- subsonic sees the recipe:"; $A/rest/getPlaylists?u=mdu\&p=x\&v=1.16.1\&c=t\&f=json | J "[p['name'] for p in d['subsonic-response']['playlists']['playlist']]"
echo "--- station:"; $A/api/station?vibe=Afro%20House\&popularity=mix | J "len(d['songs'])"
echo "--- logs:"; $A/api/logs?only=playlists | J "(len(d['lines']) > 0, d['last'] > 0)"
echo "--- gzip:"; curl -s -H 'Accept-Encoding: gzip' -o /dev/null -w "%{size_download} " localhost:8097/; curl -s -o /dev/null -w "%{size_download}\n" localhost:8097/
curl -s -I -H 'If-None-Match: x' localhost:8097/ | grep -i etag
grep -iE "traceback" -A5 $S/app.log | head -30
kill $AP $MP
