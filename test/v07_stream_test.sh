#!/bin/bash
# v0.7 streaming quality: 320 (quick Soulseek FLAC -> MP3 320), best (raw FLAC), 128, maxBitRate, star keeps streamed copy.
S=$1; rm -rf $S && mkdir -p $S/data/Music; M=$S/data/Music
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$M/Spotify Playlists" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek,usenet SEARCH_SECONDS=1 \
 UMCULO_TEST_STREAM_URL=$U/single.mp3 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
R="http://localhost:8097/rest"; AU="u=mdu&t=abc&s=salt&v=1.16.1&c=test"
P() { curl -s -X POST localhost:8097$1 -d "$2"; }
probe() { ffprobe -v error -show_entries format=format_name,bit_rate -of csv=p=0 $1; }
echo "--- 320 (default):"; time curl -s -o $S/a "$R/stream?$AU&id=umc-dz-112"; probe $S/a
echo "--- maxBitRate=128:"; curl -s -o $S/b "$R/stream?$AU&id=umc-dz-112&maxBitRate=128"; probe $S/b
P /api/settings '{"stream_quality":"best"}' > /dev/null
echo "--- best:"; curl -s -o $S/c "$R/stream?$AU&id=umc-dz-112"; probe $S/c
P /api/settings '{"stream_quality":"256"}' > /dev/null
echo "--- 256 for a song not on Soulseek (YouTube):"; curl -s -o $S/d "$R/stream?$AU&id=umc-dz-300"; probe $S/d
ls $S/data/downloads/umculo/stream
echo "--- web player label:"; curl -s -o /dev/null localhost:8097/api/stream/300; curl -s localhost:8097/api/streaminfo/d/300; echo
P /api/settings '{"stream_quality":"best"}' > /dev/null; curl -s -o /dev/null localhost:8097/api/stream/112; curl -s localhost:8097/api/streaminfo/d/112; echo
P /api/settings '{"stream_quality":"256"}' > /dev/null
echo "--- star keeps the streamed FLAC:"; curl -s "$R/star?$AU&f=json&id=umc-dz-112"; echo; sleep 4
find $M/Umculo -type f; grep -E "stream from|Saved|Traceback" $S/app.log | head
kill $AP $MP
