#!/usr/bin/env bash
# Install the Claude worker on a Linux server with Docker. Run as root from a copy of the Umculo repo:
#   sudo bash tools/claude-worker/install.sh
# Safe to run again (it updates the files and restarts what changed). See README.md in this folder.
set -euo pipefail
SRC=$(cd "$(dirname "$0")" && pwd)
DIR=${WORKER_DIR:-/opt/claude-worker}
SUBNET=${WORKER_SUBNET:-172.30.250.0/24}
UPDATE=${UMCULO_UPDATE_SCRIPT:-/root/umculo-update.sh}
REVIEW_AT=${REVIEW_AT:-04:17}

[ "$(id -u)" = 0 ] || { echo "Run as root (sudo bash $0)"; exit 1; }
command -v docker >/dev/null || { echo "Docker isn't installed"; exit 1; }
docker compose version >/dev/null || { echo "Docker Compose v2 is needed"; exit 1; }
command -v iptables >/dev/null || { echo "iptables is needed for the firewall rules"; exit 1; }
[ -f "$UPDATE" ] || echo "Note: $UPDATE doesn't exist; deploys will fail until it does (or set UMCULO_UPDATE_SCRIPT)."

echo "Installing to $DIR"
mkdir -p "$DIR/work" "$DIR/deploy" "$DIR/host"
cp -r "$SRC/Dockerfile" "$SRC/docker-compose.yml" "$SRC/worker-CLAUDE.md" "$SRC/review-prompt.md" "$SRC/bin" "$DIR/"
cp "$SRC/host/"* "$DIR/host/" && chmod 755 "$DIR/host/"* "$DIR/bin/"*
chown 1000:1000 "$DIR/work" "$DIR/deploy"
chmod 750 "$DIR/deploy"
tz=$(timedatectl show -p Timezone --value 2>/dev/null || echo Africa/Johannesburg)
printf 'TZ=%s\nWORKER_SUBNET=%s\n' "$tz" "$SUBNET" > "$DIR/.env"

unit() { cat > "/etc/systemd/system/$1"; }
unit claude-worker-firewall.service <<EOF
[Unit]
Description=Keep the Claude worker container off the home network
After=docker.service
PartOf=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
Environment=WORKER_SUBNET=$SUBNET
ExecStart=$DIR/host/firewall
[Install]
WantedBy=multi-user.target docker.service
EOF
unit umculo-deploy.path <<EOF
[Unit]
Description=Watch for Umculo deploy requests from the Claude worker
[Path]
PathExists=$DIR/deploy/request
Unit=umculo-deploy.service
[Install]
WantedBy=multi-user.target
EOF
unit umculo-deploy.service <<EOF
[Unit]
Description=Deploy Umculo (requested by the Claude worker)
[Service]
Type=oneshot
Environment=WORKER_DIR=$DIR UMCULO_UPDATE_SCRIPT=$UPDATE
ExecStart=$DIR/host/deploy-run
TimeoutStartSec=25min
EOF
unit umculo-review.service <<EOF
[Unit]
Description=Nightly Umculo review by the Claude worker
After=docker.service
[Service]
Type=oneshot
ExecStart=/usr/bin/docker exec claude-worker umculo-review
TimeoutStartSec=3h
EOF
unit umculo-review.timer <<EOF
[Unit]
Description=Nightly Umculo review by the Claude worker
[Timer]
OnCalendar=*-*-* $REVIEW_AT:00
Persistent=true
RandomizedDelaySec=5min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now claude-worker-firewall.service umculo-deploy.path umculo-review.timer

echo "Building and starting the container (the first build takes a few minutes)…"
(cd "$DIR" && docker compose up -d --build)
systemctl restart claude-worker-firewall.service

cat <<EOF

Installed. Next, sign in (you type every sign-in yourself):

    docker exec -it claude-worker worker-setup

Then open the Claude app → Code (or claude.ai/code) and pick the session named umculo-server-…

Useful:
    docker exec -it claude-worker tmux attach -t rc     # see Remote Control (leave with Ctrl-b d)
    systemctl list-timers umculo-review.timer          # when the next review runs
    systemctl start umculo-review.service              # run a review now
    ls $DIR/work/reports                               # review reports
    cat $DIR/deploy/last.log                           # the last deploy
EOF
