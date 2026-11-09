#!/bin/bash
# v1.0 UI environment: an empty Umculo (no accounts yet) with a small library, left running.
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
echo "$AP $MP" > $S/pids
mkf "$M/Albums/Kwesta/DaKAR II/Kwesta - Ngud.flac" "-c:a flac"; mkf "$M/Albums/Kwesta/DaKAR II/Kwesta - Spirit.flac" "-c:a flac"
mkf "$M/Albums/Kwesta/Spirit/Kwesta - Spirit (feat. Wale).mp3" "-c:a libmp3lame -b:a 256k"
echo READY
