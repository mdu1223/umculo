# Claude worker: Claude Code on your server, for Umculo

Runs [Claude Code](https://code.claude.com/docs) around the clock on the server, in its own container, so you can
work on Umculo from the Claude app on your phone or at claude.ai/code (Remote Control), and so it reviews the
code every night by itself. Needs a Claude Pro or Max plan (or Team/Enterprise).

## What it can and can't do
- **Inside its container it needs no approval for anything**: it edits code, runs the tests, uses git and GitHub.
- **It can't reach the rest of the server.** It runs as an ordinary user with no extra privileges, sees only its
  own folders, and a firewall rule stops it from reaching your home network, the server's other services
  and other containers. The internet is open (it needs Claude, GitHub and package downloads).
- **It can deploy Umculo, and only that**: `umculo-deploy` drops a request in a folder; the server notices and runs
  your update script. The script pulls `main` from GitHub, so only code that's on GitHub (with passing tests)
  can go live, and every deploy is visible there. At most one deploy every 2 minutes.
- **The nightly review never deploys**: it runs the tests, reviews new commits, opens a pull request with fixes
  and an issue for anything it left, and GitHub tells you.

Deploying means the code it pushes runs inside Umculo, with Umculo's access (its settings, your music). That's
the trade-off of letting it deploy. Read the nightly pull requests before merging them.

## Install
On the server, as root, from a copy of this repository:

    git clone https://github.com/mdu1223/umculo.git /opt/umculo-src     # or use the copy you have
    sudo bash /opt/umculo-src/tools/claude-worker/install.sh

Options (environment variables): `UMCULO_UPDATE_SCRIPT` (default `/root/umculo-update.sh`), `REVIEW_AT`
(default `04:17`), `WORKER_DIR` (default `/opt/claude-worker`), `WORKER_SUBNET` (default `172.30.250.0/24`),
`WORKER_MEMORY` / `WORKER_CPUS` in `/opt/claude-worker/.env` (defaults 6g / 3).

Then sign in. You type every sign-in yourself:

    docker exec -it claude-worker worker-setup

It sets the git name, signs in to GitHub with a fine-grained token limited to the Umculo repository, signs in
to Claude (open the link on your phone or PC, paste the code back), and turns on Remote Control.

## Use
- **From your phone**: Claude app → Code, pick the session named `umculo-server-…` (or claude.ai/code).
  Ask for a change; ask it to deploy when you're happy.
- **Reviews**: `ls /opt/claude-worker/work/reports`; run one now with `systemctl start umculo-review.service`.
- **Watch it**: `docker exec -it claude-worker tmux attach -t rc` (leave with Ctrl-b then d).
- **Last deploy**: `cat /opt/claude-worker/deploy/last.log`.

## When something's off
- *The session doesn't show up in the app*: attach to the tmux window and look; if it says it isn't signed in,
  run `worker-setup` again. Sign-ins expire: Claude warns 3 days before; renew by running `claude` then `/login`.
- *Deploys don't happen*: `systemctl status umculo-deploy.path umculo-deploy.service`.
- *Turn it all off*: `cd /opt/claude-worker && docker compose down` and
  `systemctl disable --now umculo-review.timer umculo-deploy.path claude-worker-firewall.service`.
