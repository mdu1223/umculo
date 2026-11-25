You are doing the nightly review of Umculo, unattended (nobody can answer questions). You're in /work/nightly,
a fresh copy of main. Follow ~/.claude/CLAUDE.md and the repo's CLAUDE.md.

1. Look at what changed since the last reviewed commit (git log, git diff). Read the changed code carefully
   and look for real bugs: crashes, wrong results, race conditions, security holes (secrets in logs, missing
   permission checks, unsafe input handling), and pages that break on a phone.
2. Run the checks: `python3 -m compileall -q app`, `python3 -m pyflakes app` (report only real problems),
   `python3 test/page_check.py` and `bash test/run_api_tests.sh`. A failing test is a finding.
3. Fix what you're sure about, with a test where it makes sense, on a branch named `nightly/<date>`.
   Run all the tests again. If they pass, push the branch and open a pull request with `gh pr create`
   explaining each fix in plain words. Never push to main and never deploy.
4. For problems you didn't fix (unsure, too big, needs a decision), open one GitHub issue titled
   "Nightly review <date>" listing them, each with the file and line and why it matters. Skip the issue
   if there's nothing to report.
5. Don't invent work: if the code is fine and the tests pass, say so in one line and stop.

Finish with a short summary: what you checked, what you fixed (with the pull request link), what you found
but left (with the issue link).
