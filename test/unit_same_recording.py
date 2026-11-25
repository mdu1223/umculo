"""The wrong-version check on real measurements (test/versions_real.json): the same recording must score as the same,
other songs from the same album as different. Usage: python3 test/unit_same_recording.py"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import songvideo as sv  # noqa: E402
import versions  # noqa: E402
d = json.load(open(os.path.join(os.path.dirname(__file__), "versions_real.json")))
env = lambda h: [b / 50 for b in bytes.fromhex(h)]  # noqa: E731
bad = []
for kind, pairs in (("same", d["same"]), ("different", d["different"])):
    for p, s in pairs:
        r = sv.same_recording(env(d["previews"][p]), env(d["songs"][s]))
        ok = r >= versions.SAME if kind == "same" else r < versions.DIFFERENT
        print(f"{'ok ' if ok else 'BAD'} {p} in {s}: {r:.2f} (should be {kind})")
        if not ok:
            bad.append(p)
print("SAME RECORDING OK" if not bad else "SAME RECORDING FAILED")
sys.exit(1 if bad else 0)
