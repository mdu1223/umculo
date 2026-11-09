#!/bin/bash
# End-to-end test of v0.2 playlist upgrade + import against the mock services.
S=$1; rm -rf $S && mkdir -p $S
M=$S/data/Music; SP="$M/Spotify Playlists"
mk() { mkdir -p "$(dirname "$1")"; ffmpeg -v error -y -f lavfi -i "sine=frequency=300:duration=$3" -c:a libmp3lame -b:a 128k "$1";
       python3 -c "import sys; from mutagen.easyid3 import EasyID3; from mutagen.id3 import ID3; ID3().save(sys.argv[1]); e=EasyID3(sys.argv[1]); e['artist']=sys.argv[2].split(' - ')[0]; e['title']=sys.argv[2].split(' - ')[1]; e.save()" "$1" "$2"; }
mk "$SP/Test Mix/FreakSA - Machine Gun.mp3" "FreakSA - Machine Gun" 226
mk "$SP/Test Mix/FreakSA - Ride.mp3" "FreakSA - Ride" 240
mk "$SP/Test Mix/Nobody - Unknown Song.mp3" "Nobody - Unknown Song" 200
printf 'FreakSA - Machine Gun.mp3\nFreakSA - Ride.mp3\nNobody - Unknown Song.mp3\n' > "$SP/Test Mix/Test Mix.m3u"
mk "$SP/Other Mix/FreakSA - Machine Gun.mp3" "FreakSA - Machine Gun" 226
printf 'FreakSA - Machine Gun.mp3\n' > "$SP/Other Mix/Other Mix.m3u"
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$SP" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo SAB_COMPLETE_DIR=$S/data/downloads/complete CONFIG_DIR=$S/config \
 SOURCES=soulseek,usenet BATCH_SOURCES=soulseek,usenet BATCH_WORKERS=1 SEARCH_SECONDS=1 \
 python3 app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
A="curl -s localhost:8097"
echo "--- free text 'FreakSA Ride' resolves to:"
C=$(curl -s -X POST localhost:8097/api/check -d '{"text":"FreakSA Ride"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
sleep 5; $A/api/check/$C | python3 -c "import json,sys; d=json.load(sys.stdin); t=d['target']; print(repr(t['artist']), repr(t['title']), t['duration'], [(c['source'],c['quality'],c['exact']) for c in d['candidates']])"
echo "--- local playlists:"; $A/api/playlists | python3 -c "import json,sys; [print(p['name'],p['tracks'],p['tiers']) for p in json.load(sys.stdin)['local']]"
curl -s -X POST localhost:8097/api/playlists/upgrade -d '{"all":true}'; echo
for i in $(seq 90); do sleep 1; R=$($A/api/playlists); echo "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if d['jobs'] and not any(j['running'] for j in d['jobs']) else 1)" && break; done
echo "$R" | python3 -c "import json,sys; [print('JOB',j['name'],j['counts']) for j in json.load(sys.stdin)['jobs']]"
for id in $(echo "$R" | python3 -c "import json,sys; print(' '.join(j['id'] for j in json.load(sys.stdin)['jobs']))"); do
  $A/api/playlists/jobs/$id | python3 -c "import json,sys; d=json.load(sys.stdin); [print('  ',t['artist'],'-',t['title'],'|',t['state'],'|',t['quality'],'->',t['new_quality'],'|',t['message']) for t in d['tracks']]"; done
echo "--- m3u files now:"; for f in "$SP"/*/*.m3u; do echo "[$f]"; cat "$f"; done
echo "--- old mp3s left in Spotify folder:"; find "$SP" -name '*.mp3'
echo "--- library:"; find $M/Umculo -type f
echo "--- every m3u entry resolves:"; python3 - "$SP" <<'PY'
import os,sys
for d,_,fs in os.walk(sys.argv[1]):
    for f in fs:
        if f.endswith('.m3u'):
            for l in open(os.path.join(d,f)):
                l=l.strip()
                if l and not l.startswith('#'): print(os.path.exists(os.path.normpath(os.path.join(d,l))), l)
PY
echo "--- import a list:"
curl -s -X POST localhost:8097/api/playlists/import -d '{"name":"Mixtape Test","text":"01 FreakSA - Ride\n02 FreakSA - Machine Gun (Original Mix)12\n03 Nope - Missing##"}'; echo
for i in $(seq 60); do sleep 1; R=$($A/api/playlists); echo "$R" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(0 if not any(j['running'] for j in d['jobs']) else 1)" && break; done
echo "$R" | python3 -c "import json,sys; [print('JOB',j['name'],j['counts']) for j in json.load(sys.stdin)['jobs'] if j['kind']=='import']"
cat "$M/_playlists/Mixtape Test.m3u8"
echo "--- restart resume check + log errors:"; grep -iE 'error|traceback|failed' $S/app.log | head
kill $AP $MP
