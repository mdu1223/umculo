#!/bin/bash
# Runs the API test suites against a fresh Umculo with made-up sample music and mock services.
# Needs: python3, ffmpeg, mutagen, segno, shazamio, curl.  Usage: bash test/run_api_tests.sh
cd "$(dirname "$0")/.." || exit 1
OUT=${TMPDIR:-/tmp}/umculo-tests; FAIL=0
run() {  # name, port, command...
  local name=$1 port=$2; shift 2; local S=$OUT/$name
  bash test/v20_env.sh "$S" "$port" >/dev/null 2>&1
  for i in $(seq 60); do grep -q "library scan" "$S/app.log" 2>/dev/null && break; sleep 0.5; done
  local r; r=$("$@" "$S" 2>&1 | grep -v "^Unclosed\|^client_session" | tail -1)
  kill $(cat "$S/pids" 2>/dev/null) 2>/dev/null; sleep 1
  case "$r" in *OK*) echo "PASS $name: $r";; *) echo "FAIL $name: $r"; FAIL=1;; esac
}
run api      8098 python3 test/v20_api.py 8098 mdu ndpass
run family   8099 python3 test/v20_family.py 8099
run api21    8100 python3 test/v21_api.py 8100 mdu ndpass
run family22 8101 python3 test/v22_family.py 8101
run security 8102 python3 test/v23_security.py 8102
exit $FAIL
