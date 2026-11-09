#!/bin/bash
# Library care: split artist folders, duplicates, leftovers, covers, lyrics, backup, auto lyrics on new downloads.
S=$1; rm -rf $S && mkdir -p $S; M=$S/data/Music; SP="$M/Spotify Playlists"
mk() { mkdir -p "$(dirname "$1")"; ffmpeg -v error -y -f lavfi -i "sine=frequency=300:duration=$3" -c:a $4 "$1";
       python3 -c "
import sys,mutagen; f=mutagen.File(sys.argv[1],easy=True)
if f.tags is None: f.add_tags()
a,t=sys.argv[2].split(' - '); f['artist']=a; f['title']=t; f.save()" "$1" "$2"; }
mk "$M/Umculo/FreakSA/FreakSA - Ride.flac" "FreakSA - Ride" 240 flac
mk "$M/Umculo/Freak SA/Freak SA - Machine Gun.mp3" "Freak SA - Machine Gun" 226 libmp3lame
mk "$SP/Mix/FreakSA - Ride.mp3" "FreakSA - Ride" 240 libmp3lame
mk "$SP/Mix/Old - Leftover.mp3" "Old - Leftover" 100 libmp3lame
printf 'FreakSA - Ride.mp3\n' > "$SP/Mix/Mix.m3u"
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$SP" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo CONFIG_DIR=$S/config SOURCES=soulseek,usenet SEARCH_SECONDS=1 LRCLIB_API=$U BACKUP_DIR=$S/backup \
 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
A="curl -s localhost:8097"; P() { curl -s -X POST localhost:8097$1 -d "$2"; }
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
W() { for i in $(seq 60); do sleep 1; $A/api/library | J "d['busy']" | grep -q False && break; done; }
P /api/library/scan > /dev/null; sleep 1; W
$A/api/library | J "(d['message'], d['artists'], d['dupes'], d['leftovers_n'], d['no_cover_n'], d['folders'], d['disk']['free']>0)"
for f in artists dupes leftovers covers lyrics; do P /api/library/fix "{\"what\":\"$f\"}" > /dev/null; sleep 1; W; echo "$f: $($A/api/library | J "d['message']")"; done
$A/api/library | J "(d['artists_n'], d['dupes_n'], d['leftovers_n'], d['no_cover_n'])"
echo "--- files:"; find $M -type f | sed "s#$M/##" | sort
echo "--- m3u:"; cat "$SP/Mix/Mix.m3u"
python3 -c "import mutagen; f=mutagen.File('$M/Umculo/FreakSA/FreakSA - Ride.flac'); print('lyrics:', f.get('lyrics'), 'pics:', len(f.pictures))"
echo "--- backup:"; P /api/library/backup; echo; ls $S/backup; tar -tzf $S/backup/*.tar.gz
echo "--- auto lyrics on a new download:"; C=$(P /api/check '{"text":"FreakSA - Ride"}' | J "d['id']"); sleep 3
rm -f "$M/Umculo/FreakSA/FreakSA - Ride.flac"
P /api/get "{\"check_id\":\"$C\",\"candidate_id\":\"best\"}" > /dev/null; sleep 12
for f in $M/Umculo/*/*Ride*; do python3 -c "import mutagen,sys; f=mutagen.File(sys.argv[1]); print(sys.argv[1].split('/')[-1], f.get('lyrics'))" "$f"; done
grep -iE "traceback|failed" $S/app.log | head
kill $AP $MP
