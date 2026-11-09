#!/bin/bash
# v0.4: settings + 320 profile conversion + prioritise/pause routes.
S=$1; rm -rf $S && mkdir -p $S
M=$S/data/Music; SP="$M/Spotify Playlists"
mk() { mkdir -p "$(dirname "$1")"; ffmpeg -v error -y -f lavfi -i "sine=frequency=300:duration=$3" -c:a libmp3lame -b:a 128k "$1";
       python3 -c "import sys; from mutagen.easyid3 import EasyID3; from mutagen.id3 import ID3; ID3().save(sys.argv[1]); e=EasyID3(sys.argv[1]); e['artist']=sys.argv[2].split(' - ')[0]; e['title']=sys.argv[2].split(' - ')[1]; e.save()" "$1" "$2"; }
mk "$SP/Test Mix/FreakSA - Ride.mp3" "FreakSA - Ride" 240
mk "$SP/Test Mix/FreakSA - Machine Gun.mp3" "FreakSA - Machine Gun" 226
printf 'FreakSA - Ride.mp3\nFreakSA - Machine Gun.mp3\n' > "$SP/Test Mix/Test Mix.m3u"
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$SP" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek,usenet BATCH_SOURCES=soulseek,usenet BATCH_WORKERS=1 SEARCH_SECONDS=1 \
 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
A="curl -s localhost:8097"; P() { curl -s -X POST localhost:8097$1 -d "$2"; }
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
echo "--- settings:"; P /api/settings '{"quality":"320","batch_sources":["soulseek","usenet"],"sub_hours":12}' | J "(d['quality'], d['batch_sources'], d['sub_hours'])"
P /api/settings '{"quality":"999"}'; echo
cat $S/config/settings.json; echo
P /api/playlists/upgrade "{\"path\": \"$SP/Test Mix/Test Mix.m3u\"}" > /dev/null
JID=$($A/api/playlists | J "d['jobs'][0]['id']")
P /api/playlists/jobs/$JID/prioritise; P /api/playlists/jobs/$JID/pause; echo
$A/api/playlists | J "[(j['name'], j['paused'], j['priority']) for j in d['jobs']]"
sleep 3; $A/api/playlists | J "[(j['name'], j['counts']) for j in d['jobs']]"
P /api/playlists/jobs/$JID/resume; echo
for i in $(seq 90); do sleep 1; $A/api/playlists | J "any(j['running'] for j in d['jobs'])" | grep -q False && break; done
$A/api/playlists/jobs/$JID | J "[(t['title'], t['state'], t['new_quality'], t['new_file'].split('/')[-1]) for t in d['tracks']]"
for f in $M/Umculo/*/*; do python3 -c "import mutagen,sys; m=mutagen.File(sys.argv[1]); print(sys.argv[1].split('/')[-1], m.info.bitrate//1000, m.get('TIT2'), bool(m.tags.getall('APIC')) if hasattr(m.tags,'getall') else '')" "$f"; done
echo "--- check view profile:"; C=$(P /api/check '{"text":"FreakSA - Ride"}' | J "d['id']"); sleep 3; $A/api/check/$C | J "(d['profile'], d['cap'], [(c['quality'], c['score']) for c in d['candidates']])"
grep -iE "traceback|error" $S/app.log | head
kill $AP $MP
