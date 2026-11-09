#!/bin/bash
# v2.0: Umculo as the music server. Tagged library, Navidrome mock with stars/plays to import.
S=$1; PORT=${2:-8098}; rm -rf $S && mkdir -p $S; M=$S/data/Music
mkf() { mkdir -p "$(dirname "$1")"; ffmpeg -v error -y -f lavfi -i "sine=d=6" $2 "$1"; [ -n "$3" ] && python3 -c '
import sys, mutagen
f = mutagen.File(sys.argv[1], easy=True); f.add_tags() if f.tags is None else None
for k, v in zip(("title","artist","album","tracknumber","date","genre"), sys.argv[2].split("|")): f[k] = v
f.save()' "$1" "$3"; }
T() { echo "$1|$2|$3|$4|$5|$6"; }
mkf "$M/Albums/Kwesta/DaKAR II/01 Ngud.flac" "-c:a flac" "$(T Ngud Kwesta 'DaKAR II' 1 2016 Hip-Hop)"
mkf "$M/Albums/Kwesta/DaKAR II/02 Spirit.flac" "-c:a flac" "$(T Spirit Kwesta 'DaKAR II' 2 2016 Hip-Hop)"
mkf "$M/Albums/Kabza De Small/Pretty Girl/01 Pretty Girl.mp3" "-c:a libmp3lame -b:a 320k" "$(T 'Pretty Girl' 'Kabza De Small' 'Pretty Girl' 1 2021 Amapiano)"
mkf "$M/Albums/Kabza De Small/Pretty Girl/02 Bheka.mp3" "-c:a libmp3lame -b:a 128k" "$(T Bheka 'Kabza De Small, Focalistic' 'Pretty Girl' 2 2021 Amapiano)"
mkf "$M/Umculo/Mas Musiq/Mas Musiq - Zaka.m4a" "-c:a aac -b:a 256k" "$(T Zaka 'Mas Musiq' Zaka 1 2020 Amapiano)"
mkf "$M/Umculo/Unknown/Untagged Song.mp3" "-c:a libmp3lame -b:a 192k" ""
ffmpeg -v error -y -f lavfi -i "color=c=orange:s=600x600" -frames:v 1 "$M/Albums/Kwesta/DaKAR II/cover.jpg"
mkf "$M/Albums/Kwesta/DaKAR II/03 Ngiyazifela.flac" "-c:a flac" "$(T Ngiyazifela 'Cassper Nyovest feat. Kwesta' 'DaKAR II' 3 2016 Hip-Hop)"
ffmpeg -v error -y -f lavfi -i "color=c=blue:s=400x400" -frames:v 1 "$M/Albums/Kwesta/artist.jpg"
python3 -c 'import sys,mutagen; f=mutagen.File(sys.argv[1]); f["REPLAYGAIN_TRACK_GAIN"]="-6.20 dB"; f["BPM"]="98"; f.save()' "$M/Albums/Kwesta/DaKAR II/01 Ngud.flac"
printf '[00:01.00]Ngud line one\n[00:03.50]line two\n' > "$M/Albums/Kwesta/DaKAR II/01 Ngud.lrc"
mkdir -p $M/_playlists; printf '#EXTM3U\n../Albums/Kwesta/DaKAR II/01 Ngud.flac\n../Umculo/Mas Musiq/Mas Musiq - Zaka.m4a\n' > "$M/_playlists/Family Faves.m3u8"
MOCK_ND_LIBRARY=1 python3 test/mock_services.py $((PORT+1000)) $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:$((PORT+1000))
env PORT=$PORT MUSIC_SERVER=builtin NAVIDROME_URL=$U NAVIDROME_USER=mdu NAVIDROME_PASSWORD=ndpass SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M FAMILY_DIR=$S/data/Family SPOTIFY_DIR="$M/Spotify Playlists" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo \
 MIXES_DIR=$M/meta_podcasts STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek BATCH_SOURCES=soulseek SEARCH_SECONDS=1 \
 LRCLIB_API=$U NTFY_URL=$U/ntfy SHARE_SCAN_WAIT=1 ITUNES_API=$U PODOMATIC_FEED="$U/podomatic/{channel}.xml" python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
echo "$AP $MP" > $S/pids
for i in $(seq 40); do curl -s localhost:$PORT/rest/ping.view?u=mdu\&p=ndpass\&f=json | grep -q '"ok"' && break; sleep 0.5; done
echo READY
