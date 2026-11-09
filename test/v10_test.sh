#!/bin/bash
# v1.0: accounts (admin, family member in a separate household), approvals, per-person folders,
# sharing, library view and deleting, best effort.
S=$1; rm -rf $S && mkdir -p $S; M=$S/data/Music
mkf() { mkdir -p "$(dirname "$1")"; ffmpeg -v error -y -f lavfi -i "sine=d=8" $2 "$1"; }
mkf "$M/Umculo/Kabza De Small/Kabza De Small - Track A.flac" "-c:a flac"
mkf "$M/Umculo/Kabza De Small/Kabza De Small - Track B.mp3" "-c:a libmp3lame -b:a 128k"
mkf "$M/Albums/Mas Musiq/Shared Album/Mas Musiq - One.mp3" "-c:a libmp3lame -b:a 320k"
mkf "$M/Albums/Mas Musiq/Shared Album/Mas Musiq - Two.mp3" "-c:a libmp3lame -b:a 320k"
mkdir -p $M/_playlists; printf '#EXTM3U\n../Umculo/Kabza De Small/Kabza De Small - Track A.flac\n' > $M/_playlists/Family\ Faves.m3u8
MOCK_ND_LIBRARY=1 python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=mdu NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M FAMILY_DIR=$S/data/Family SPOTIFY_DIR="$M/Spotify Playlists" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo \
 MIXES_DIR=$M/meta_podcasts STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek BATCH_SOURCES=soulseek SEARCH_SECONDS=1 \
 LRCLIB_API=$U NTFY_URL=$U/ntfy SHARE_SCAN_WAIT=1 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 2
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
CA="-b $S/admin.jar -c $S/admin.jar"; CM="-b $S/kid.jar -c $S/kid.jar"
A() { curl -s $CA localhost:8097$1; }; PA() { curl -s $CA -X POST localhost:8097$1 -d "$2"; }
K() { curl -s $CM localhost:8097$1; }; PK() { curl -s $CM -X POST localhost:8097$1 -d "$2"; }
echo "--- open before setup:"; curl -s localhost:8097/api/me | J "(d['user']['open'], d['accounts'])"
echo "--- set up the admin:"; PA /api/setup '{"username":"mdu","password":"secret123","name":"Mdu"}'; echo
curl -s localhost:8097/api/me | J "d.get('error')"; A /api/me | J "(d['user']['username'], d['user']['is_admin'])"
echo "--- wrong password:"; curl -s -X POST localhost:8097/api/login -d '{"username":"mdu","password":"nope"}'; echo
echo "--- page loads without a session:"; curl -s -o /dev/null -w "%{http_code}\n" localhost:8097/
echo "--- add Thando, separate folders, asks first:"
PA /api/users '{"name":"Thando","username":"thando","password":"thando123","mode":"separate","preset":"ask"}' | J "(d['username'], d['perms'], d['folder'].split('/')[-1], d['nd'], d['password'])"
grep "ND CREATE\|ND ACCESS" $S/mock.log
echo "--- Thando signs in:"; PK /api/login '{"username":"thando","password":"thando123"}' | J "d['user']['name']"
K /api/me | J "(d['user']['is_admin'], d['house'])"
echo "--- Thando can't open settings or logs:"; K /api/settings | J "d"; K /api/logs | J "d"
echo "--- Thando sees no playlists of yours, an empty library:"; K /api/pl | J "[p['name'] for p in d['playlists']]"; K /api/lib | J "(d['stats']['songs'], d['me'])"
echo "--- Thando asks for a song:"; C=$(PK /api/check '{"text":"FreakSA - Ride"}' | J "d['id']"); sleep 3
PK /api/get "{\"check_id\":\"$C\",\"candidate_id\":\"best\"}" | J "d"; grep -c "Thando asks for" $S/mock.log
AID=$(A /api/approvals | J "d['approvals'][0]['id']"); A /api/approvals | J "[(a['name'], a['what'], a['state']) for a in d['approvals']]"
echo "--- you say yes:"; PA /api/approvals/$AID/yes '' | J "d"; sleep 10
K /api/requests | J "[(r['target']['title'], r['state'], r['who']) for r in d['requests']]"
A /api/requests | J "[(r['target']['title'], r['who']) for r in d['requests']]"
find $S/data/Family -type f | sed "s#$S/data/##" | sort
echo "--- Thando's library now:"; K /api/lib/artists | J "[(a['name'], a['albums']) for a in d['artists']]"
echo "--- you share an album and a playlist with Thando:"
TID=$(A /api/users | J "[u['id'] for u in d['users'] if u['username']=='thando'][0]")
PA /api/lib/share "{\"user\":\"$TID\",\"folder\":\"Albums/Mas Musiq/Shared Album\",\"on\":true}" > /dev/null
PL=$(A /api/pl | J "[p['id'] for p in d['playlists'] if p['name']=='Family Faves'][0]"); PATHP="$M/_playlists/Family Faves.m3u8"
PA /api/users/$TID/share "{\"kind\":\"playlist\",\"item\":\"$PATHP\",\"on\":true}" > /dev/null; sleep 6
find $S/data/Family/thando/Shared -type f | sed "s#$S/data/Family/thando/##" | sort
K /api/lib/artists | J "sorted((a['name'], a['albums']) for a in d['artists'])"
K /api/pl | J "[(p['name'], p['owner'] is None) for p in d['playlists']]"
grep "ND CREATE PLAYLIST" $S/mock.log
echo "--- Thando can't change your playlist or delete music:"; PK /api/pl/$PL/rename '{"name":"x"}' | J "d"
AL=$(K /api/lib/artists | J "[a['id'] for a in d['artists'] if a['name']=='Mas Musiq'][0]"); ALB=$(K /api/lib/artist/$AL | J "d['albums'][0]['id']")
PK /api/lib/delete "{\"kind\":\"album\",\"id\":\"$ALB\"}" | J "d"
echo "--- you let Thando download and delete:"; PA /api/users/$TID '{"preset":"full"}' | J "d['perms']"
PK /api/lib/delete "{\"kind\":\"album\",\"id\":\"$ALB\"}" | J "d"; ls "$M/Albums/Mas Musiq/Shared Album" | wc -l; A /api/users | J "[u['shared_albums'] for u in d['users'] if u['username']=='thando']"
echo "--- you delete an artist from your library:"; KA=$(A /api/lib/artists | J "[a['id'] for a in d['artists'] if a['name']=='Kabza De Small'][0]")
A /api/lib/delete-preview/artist/$KA | J "d"; PA /api/lib/delete "{\"kind\":\"artist\",\"id\":\"$KA\"}" | J "d"; ls "$M/Umculo" ; cat "$M/_playlists/Family Faves.m3u8"
echo "--- Thando changes password:"; PK /api/users/me '{"password":"newpass1","current":"wrong"}' | J "d"; PK /api/users/me '{"password":"newpass1","current":"thando123"}' | J "d['username']"
grep "ND PUT" $S/mock.log | tail -1; K /api/me | J "d.get('error')"
echo "--- best effort setting:"; PA /api/settings '{"best_effort":false}' | J "d.get('best_effort', d)"
echo "--- remove Thando, keep music:"; PA /api/users/$TID/remove '{}' | J "d"; ls $S/data/Family
grep -iE "traceback" -A8 $S/app.log | head -30
kill $AP $MP
