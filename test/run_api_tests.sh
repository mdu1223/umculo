#!/bin/bash
# Runs the API test suites against a fresh Umculo with made-up sample music and mock services.
# Needs: python3, ffmpeg, mutagen, segno, shazamio, curl.  Usage: bash test/run_api_tests.sh
cd "$(dirname "$0")/.." || exit 1
OUT=${TMPDIR:-/tmp}/umculo-tests; FAIL=0
run() {  # name, port, command...
  local name=$1 port=$2; shift 2; local S=$OUT/$name
  bash test/v20_env.sh "$S" "$port" >/dev/null 2>&1
  for i in $(seq 60); do grep -q "library scan" "$S/app.log" 2>/dev/null && break; sleep 0.5; done
  local all r; all=$("$@" "$S" 2>&1 | grep -v "^Unclosed\|^client_session"); r=$(echo "$all" | tail -1)
  kill $(cat "$S/pids" 2>/dev/null) 2>/dev/null; sleep 1
  case "$r" in *OK*) echo "PASS $name: $r";; *) echo "FAIL $name: $r"; echo "$all" | tail -40 | sed 's/^/    /'; FAIL=1;; esac
}
python3 test/page_check.py || FAIL=1
if python3 test/unit_same_recording.py >/dev/null 2>&1; then echo "PASS same-recording"; else echo "FAIL same-recording"; FAIL=1; fi
if python3 test/unit_versions_fix.py >/dev/null 2>&1; then echo "PASS versions-fix"; else echo "FAIL versions-fix"; python3 test/unit_versions_fix.py; FAIL=1; fi
if python3 test/unit_song_merge.py >/dev/null 2>&1; then echo "PASS song-merge"; else echo "FAIL song-merge"; python3 test/unit_song_merge.py; FAIL=1; fi
run api      8098 python3 test/v20_api.py 8098 mdu ndpass
run family   8099 python3 test/v20_family.py 8099
run api21    8100 python3 test/v21_api.py 8100 mdu ndpass
run family22 8101 python3 test/v22_family.py 8101
run security 8102 python3 test/v23_security.py 8102
run cook     8103 python3 test/v24_cook.py 8103
run video    8104 python3 test/v26_video.py 8104
TEST_SHAZAM=1 run player 8105 python3 test/v27_player.py 8105
run together 8106 python3 test/v28_together.py 8106
GIGS_API=http://127.0.0.1:11107 run life 8107 python3 test/v29_life.py 8107
INBOX_EVERY=2 SLSK_WATCH_EVERY=1 SLSK_OFF_AFTER=2 SLSK_CANARY_EVERY=1 SLSK_SUSPECT_AFTER=1 run sources 8108 python3 test/v30_sources.py 8108
TEST_SHAZAM=1 TEST_MIXES=$PWD/test/mixes_fixture.json run mixfeed 8110 python3 test/v31_mixfeed.py 8110
run versions  8111 python3 test/v32_versions.py 8111
run loudness 8112 python3 test/v33_loudness.py 8112
run playlists 8113 python3 test/v34_playlists.py 8113
run mixcovers 8114 python3 test/v35_mixcovers.py 8114
exit $FAIL
