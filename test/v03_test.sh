#!/bin/bash
# v0.3: manual pick, job notification, links (RSS / Apple / PodOmatic / direct), song finder, subscriptions.
S=$1; rm -rf $S && mkdir -p $S
M=$S/data/Music; SP="$M/Spotify Playlists"
mk() { mkdir -p "$(dirname "$1")"; ffmpeg -v error -y -f lavfi -i "sine=frequency=300:duration=$3" -c:a libmp3lame -b:a 128k "$1";
       python3 -c "import sys; from mutagen.easyid3 import EasyID3; from mutagen.id3 import ID3; ID3().save(sys.argv[1]); e=EasyID3(sys.argv[1]); e['artist']=sys.argv[2].split(' - ')[0]; e['title']=sys.argv[2].split(' - ')[1]; e.save()" "$1" "$2"; }
mk "$SP/Test Mix/FreakSA - Machine Gun.mp3" "FreakSA - Machine Gun" 226
mk "$SP/Test Mix/Nobody - Unknown Song.mp3" "Nobody - Unknown Song" 200
printf 'FreakSA - Machine Gun.mp3\nNobody - Unknown Song.mp3\n' > "$SP/Test Mix/Test Mix.m3u"
cat > $S/shazam.json <<'J'
[[0, 300, "Kabza De Small", "Track A"], [300, 700, "FreakSA", "Ride"], [700, 1500, "Unknown Guy", "Song C"]]
J
python3 test/mock_services.py 9997 $S/data > $S/mock.log 2>&1 & MP=$!
for i in $(seq 30); do grep -q ready $S/mock.log && break; sleep 0.5; done
U=http://localhost:9997
env MUSIC_SERVER=${MUSIC_SERVER:-navidrome} PORT=8097 NAVIDROME_URL=$U NAVIDROME_USER=m NAVIDROME_PASSWORD=x SLSKD_URL=$U PROWLARR_URL=$U PROWLARR_KEY=k SABNZBD_URL=$U SABNZBD_KEY=k DEEZER_API=$U \
 MUSIC_DIR=$M SPOTIFY_DIR="$SP" PLAYLISTS_DIR=$M/_playlists SLSKD_DOWNLOADS=$M/Soulseek LIBRARY_DIR=$M/Umculo MIXES_DIR=$M/meta_podcasts \
 STAGING_DIR=$S/data/downloads/umculo SAB_COMPLETE_DIR=$S/data/downloads/complete CONFIG_DIR=$S/config \
 SOURCES=soulseek,usenet BATCH_SOURCES=soulseek BATCH_WORKERS=1 SEARCH_SECONDS=1 NTFY_URL=$U/ntfy \
 ITUNES_API=$U PODOMATIC_FEED="$U/podomatic/{channel}.xml" UMCULO_TEST_SHAZAM=$S/shazam.json FINDER_STEP_SECONDS=60 \
 python3 -I app/umculo.py > $S/app.log 2>&1 & AP=$!
sleep 1.5
A="curl -s localhost:8097"
P() { curl -s -X POST localhost:8097$1 -d "$2"; }
J() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1"; }
waitjobs() { for i in $(seq 120); do sleep 1; $A/api/playlists | J "any(j['running'] for j in d['jobs'])" | grep -q False && break; done; }

echo "=== 1. upgrade, then choose a copy by hand"
P /api/playlists/upgrade "{\"path\": \"$SP/Test Mix/Test Mix.m3u\"}" > /dev/null; waitjobs
JID=$($A/api/playlists | J "d['jobs'][0]['id']")
$A/api/playlists/jobs/$JID | J "[(t['i'],t['title'],t['state'],t['new_quality']) for t in d['tracks']]"
C=$(P /api/playlists/jobs/$JID/tracks/0/check | J "d['id']"); sleep 4
CAND=$($A/api/check/$C | J "[c for c in d['candidates'] if 'Radio' in c['name']][0]['id']")
P /api/playlists/jobs/$JID/tracks/0/pick "{\"check_id\":\"$C\",\"candidate_id\":\"$CAND\"}"; echo; waitjobs; sleep 1
$A/api/playlists/jobs/$JID | J "[(t['title'],t['state'],t['new_quality'],t['message'],t['new_file'].split('/')[-1]) for t in d['tracks']]"
grep NTFY $S/mock.log

echo "=== 2. Apple Podcasts episode link"
SH=$(P /api/link '{"url":"https://podcasts.apple.com/za/podcast/deep-sessions/id123456?i=1000111"}')
echo "$SH" | J "(d['kind'], d['title'], d['author'], d['focus'], len(d['entries']), d['subscribable'], d['entries'][0]['date'], d['entries'][0]['duration'])"
SID=$(echo "$SH" | J "d['id']")
FID=$(P /api/link/find "{\"show\":\"$SID\",\"entry\":\"ep-42\",\"keep\":true}" | J "d['id']")
for i in $(seq 90); do sleep 1; $A/api/finds/$FID | J "d['state']" | grep -qE "done|failed" && break; done
$A/api/finds/$FID | J "(d['state'], d['message'], d['file'])"
$A/api/finds/$FID | J "'\n'.join(f\"  {t['t']} {t['artist']} - {t['title']} [{t['from']} x{t['hits']}] owned={t['owned']!r}\" for t in d['tracks'])"
P /api/finds/$FID/get '{"tracks":[0,1,3],"name":"Deep Sessions 042 songs"}'; echo; waitjobs
$A/api/playlists | J "[(j['name'], j['counts']) for j in d['jobs']]"
cat "$M/_playlists/Deep Sessions 042 songs.m3u8"

echo "=== 3. PodOmatic show link, subscribe with latest"
SH=$(P /api/link '{"url":"https://www.podomatic.com/podcasts/deepshizzol"}')
echo "$SH" | J "(d['kind'], d['title'], len(d['entries']), d.get('focus'))"
SID=$(echo "$SH" | J "d['id']")
P /api/link/subscribe "{\"show\":\"$SID\",\"latest\":true}"; echo
sleep 8
$A/api/mixes | J "[(s['title'], s['fetched'], s['error']) for s in d['subscriptions']]"
$A/api/requests | J "[(r['target']['title'], r['state'], r['candidate']['quality'], r['file'].split('/Music/')[-1]) for r in d['requests']]"
echo "--- PodOmatic episode link focuses the episode:"
P /api/link '{"url":"https://deepshizzol.podomatic.com/entry/2026-10-23T10_00_00-07_00"}' | J "d['focus']"

echo "=== 4. direct audio link (generic yt-dlp path)"
SH=$(P /api/link "{\"url\":\"$U/single.mp3\"}"); echo "$SH" | J "(d['kind'], d['entries'][0]['quality'], d['entries'][0]['duration'])"
SID=$(echo "$SH" | J "d['id']"); EID=$(echo "$SH" | J "d['entries'][0]['id']")
P /api/link/get "{\"show\":\"$SID\",\"entry\":\"$EID\"}" > /dev/null; sleep 8
$A/api/requests | J "[(r['target']['artist'], r['target']['title'], r['state'], r['candidate']['quality'], r['file'].split('/Music/')[-1]) for r in d['requests']][:1]"
echo "=== bad link:"; P /api/link '{"url":"notalink"}'; echo
echo "=== files:"; find $M/meta_podcasts $M/Umculo -type f | sed "s#$M/##"
echo "=== log errors:"; grep -iE 'traceback|error' $S/app.log | head
kill $AP $MP
