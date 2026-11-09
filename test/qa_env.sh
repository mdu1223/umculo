#!/bin/bash
# Starts Umculo against the fake services with a realistic library, then leaves both running.
# Usage: test/qa_env.sh <dir>   -> writes <dir>/pids
S=$1; rm -rf $S && mkdir -p $S; M=$S/data/Music; SP="$M/Spotify Playlists"
mk() { mkdir -p "$(dirname "$1")"; ffmpeg -v error -y -f lavfi -i "sine=frequency=300:duration=$3" $4 "$1";
  python3 -c "
import sys,mutagen; f=mutagen.File(sys.argv[1],easy=True)
if f.tags is None: f.add_tags()
a,t=sys.argv[2].split(' - '); f['artist']=a; f['title']=t; f.save()" "$1" "$2"; }
mk "$SP/Sunday/FreakSA - Machine Gun.mp3" "FreakSA - Machine Gun" 226 "-c:a libmp3lame -b:a 128k"
mk "$SP/Sunday/Nobody - Unknown Song.mp3" "Nobody - Unknown Song" 200 "-c:a libmp3lame -b:a 128k"
mk "$SP/Sunday/Kabza De Small - Track A.flac" "Kabza De Small - Track A" 300 "-c:a flac"
mk "$SP/Sunday/Unknown Guy - Song C.mp3" "Unknown Guy - Song C" 120 "-c:a libmp3lame -b:a 320k"
mk "$SP/Sunday/Mas Musiq - Shared.mp3" "Mas Musiq - Shared" 90 "-c:a libmp3lame -b:a 256k"
printf '#EXTM3U\nFreakSA - Machine Gun.mp3\nNobody - Unknown Song.mp3\nKabza De Small - Track A.flac\nUnknown Guy - Song C.mp3\nMas Musiq - Shared.mp3\n*\nGone - Missing.mp3\n' > "$SP/Sunday/Sunday.m3u"
mk "$SP/Jazz/Miles - So What.flac" "Miles - So What" 60 "-c:a flac"
printf '#EXTM3U\nMiles - So What.flac\n../Sunday/Mas Musiq - Shared.mp3\n' > "$SP/Jazz/Jazz.m3u"
cat > $S/shazam.json <<'J'
[[0, 300, "Kabza De Small", "Track A"], [300, 700, "FreakSA", "Ride"], [700, 1500, "Unknown Guy", "Song C"]]
J
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=mdu NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$SP" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo SAB_COMPLETE_DIR=$S/data/downloads/complete CONFIG_DIR=$S/config \
 SOURCES=soulseek,usenet BATCH_SOURCES=soulseek BATCH_WORKERS=2 SEARCH_SECONDS=1 NTFY_URL=$U/ntfy LRCLIB_API=$U \
 ITUNES_API=$U SPOTIFY_EMBED=$U/embed PODOMATIC_FEED="$U/podomatic/{channel}.xml" UMCULO_TEST_SHAZAM=$S/shazam.json FINDER_STEP_SECONDS=60 \
 STREAM_WAIT_SECONDS=5 SLSK_PAUSE_SECONDS=20 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
echo "$AP $MP" > $S/pids
sleep 2
A="curl -s localhost:8097"; P() { curl -s -X POST localhost:8097$1 -d "$2"; }
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
# a song from search, an upgrade, an import, a mix, a subscription, two recipes
C=$(P /api/check '{"text":"FreakSA - Ride"}' | J "d['id']"); sleep 3
P /api/get "{\"check_id\":\"$C\",\"candidate_id\":\"best\"}" > /dev/null
P /api/playlists/upgrade "{\"path\": \"$SP/Sunday/Sunday.m3u\"}" > /dev/null
P /api/playlists/import '{"name":"Road Trip","text":"FreakSA - Ride\nUnknown Guy - Song C\nNo One - Ghost Song"}' > /dev/null
SH=$(P /api/link '{"url":"https://podcasts.apple.com/za/podcast/deep-sessions/id123456?i=1000111"}'); SID=$(echo "$SH" | J "d['id']")
P /api/link/find "{\"show\":\"$SID\",\"entry\":\"ep-42\"}" > /dev/null
P /api/link/subscribe "{\"show\":\"$SID\"}" > /dev/null
P /api/recipes '{"template":"charts"}' > /dev/null; P /api/recipes '{"template":"deep","mode":"download"}' > /dev/null
for i in $(seq 120); do sleep 1; $A/api/pl | J "any(p['status']=='working' for p in d['playlists'])" | grep -q False && break; done
sleep 3
echo READY
