#!/usr/bin/env bash
# Run Claude Code on the server itself, as an administrator you reach through Remote Control.
# Run as root:  sudo bash tools/claude-admin/install.sh      (safe to run again)
# Options: ADMIN_USER (default claude-admin), ADMIN_MODE (auto | bypassPermissions | default; default auto)
set -euo pipefail
SRC=$(cd "$(dirname "$0")" && pwd)
U=${ADMIN_USER:-claude-admin}
MODE=${ADMIN_MODE:-auto}
case "$MODE" in auto|bypassPermissions|default|acceptEdits) ;; *) echo "ADMIN_MODE must be auto, bypassPermissions, acceptEdits or default"; exit 1;; esac
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo bash $0)"; exit 1; }

need() { command -v "$1" >/dev/null 2>&1; }
missing=""; for t in tmux git curl sudo; do need "$t" || missing="$missing $t"; done
if [ -n "$missing" ]; then
  echo "Installing:$missing"
  if need dnf; then dnf install -y -q $missing
  elif need apt-get; then apt-get update -qq && apt-get install -y -qq $missing
  elif need pacman; then pacman -S --noconfirm --needed $missing
  elif need zypper; then zypper -n install $missing
  elif need apk; then apk add $missing
  elif need yum; then yum install -y -q $missing
  else echo "Install these with your system's package manager first:$missing"; exit 1; fi
  for t in $missing; do need "$t" || { echo "Couldn't install $t; install it and run this again."; exit 1; }; done
fi

# the user: its own account, allowed sudo without a password and Docker
id "$U" >/dev/null 2>&1 || useradd -m -s /bin/bash -c "Claude server admin" "$U"
getent group docker >/dev/null && usermod -aG docker "$U"
install -d -m 750 /etc/sudoers.d
grep -Eq '^[@#]includedir /etc/sudoers.d' /etc/sudoers || echo '#includedir /etc/sudoers.d' >> /etc/sudoers && visudo -c >/dev/null
printf '%s ALL=(ALL) NOPASSWD: ALL\n' "$U" > "/etc/sudoers.d/$U.tmp"
chmod 440 "/etc/sudoers.d/$U.tmp"
visudo -cf "/etc/sudoers.d/$U.tmp" >/dev/null && mv "/etc/sudoers.d/$U.tmp" "/etc/sudoers.d/$U"
H=$(getent passwd "$U" | cut -d: -f6)

# Claude Code (keeps itself up to date), its instructions and its starting mode
[ -x "$H/.local/bin/claude" ] || sudo -iu "$U" bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
install -d -o "$U" -g "$U" -m 700 "$H/.claude"
install -o "$U" -g "$U" -m 644 "$SRC/admin-CLAUDE.md" "$H/.claude/CLAUDE.md"
sudo -iu "$U" python3 - "$MODE" <<'EOF' 2>/dev/null || true
import json, os, sys
p = os.path.expanduser("~/.claude/settings.json")
try: s = json.load(open(p))
except Exception: s = {}
s.setdefault("permissions", {})["defaultMode"] = sys.argv[1]
json.dump(s, open(p, "w"), indent=2)
EOF
install -d -m 755 /var/log/claude-admin && touch /var/log/claude-admin/changes.log

# Remote Control, kept running in a tmux window called "admin"
cat > /usr/local/bin/claude-admin-loop <<EOF
#!/usr/bin/env bash
cd "\$HOME"
while true; do
  if [ ! -s "\$HOME/.claude/.credentials.json" ]; then
    echo "Not signed in yet. On the server run:  sudo -iu $U claude   (sign in, then /exit)"; sleep 60; continue
  fi
  "\$HOME/.local/bin/claude" remote-control --permission-mode $MODE --remote-control-session-name-prefix server-admin
  echo "Remote Control stopped (exit \$?); starting again in 15 s."; sleep 15
done
EOF
chmod 755 /usr/local/bin/claude-admin-loop
cat > /etc/systemd/system/claude-admin.service <<EOF
[Unit]
Description=Claude server admin (Remote Control)
After=network-online.target docker.service
Wants=network-online.target
[Service]
Type=forking
User=$U
WorkingDirectory=$H
Environment=HOME=$H PATH=$H/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin
ExecStart=/usr/bin/tmux new-session -d -s admin -x 200 -y 50 /usr/local/bin/claude-admin-loop
ExecStop=/usr/bin/tmux kill-session -t admin
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable claude-admin.service >/dev/null
systemctl restart claude-admin.service

cat <<EOF

Installed (mode: $MODE). Now, on the server:

  1. Sign in:            sudo -iu $U claude          (pick your Claude subscription, open the link, paste the code,
                                                      answer the questions, then /exit)
  2. Turn on Remote Control the first time:
                         sudo -iu $U tmux attach -t admin    (answer y; leave with Ctrl-b then d)

Then in the Claude app → Code, pick the session named server-admin-…
What it changed is listed in /var/log/claude-admin/changes.log.
To stop it: systemctl disable --now claude-admin.service
EOF
