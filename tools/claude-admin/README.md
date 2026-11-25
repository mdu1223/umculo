# Claude server admin

Claude Code running on the server itself, as the user `claude-admin` (passwordless `sudo`, Docker), reached
through Remote Control from the Claude app or claude.ai/code. Unlike the [Claude worker](../claude-worker),
which only works on Umculo's code inside a walled-off container, this one can change anything on the server.

It starts in **auto mode**: no routine prompts, but a separate check runs before each action and blocks what goes
beyond your request or looks driven by something Claude read (a web page, a log, a file). If it blocks something,
you're asked on your phone. Its instructions (`admin-CLAUDE.md`) make it back up files before editing, never
delete photos, music or data volumes, never print secrets, and log every change to
`/var/log/claude-admin/changes.log`.

## Install
On the server, as root, from a copy of this repository:

    sudo bash tools/claude-admin/install.sh
    sudo -iu claude-admin /home/claude-admin/.local/bin/claude                # sign in (link + code), answer the questions, /exit
    sudo -iu claude-admin tmux attach -t admin    # the first time: answer y, leave with Ctrl-b then d

Then in the Claude app → Code, pick the session named `server-admin-…`.

`ADMIN_MODE=bypassPermissions` (install again with it) turns every check off. Anthropic advises that only for
isolated machines without internet; on a server with your data, prefer auto mode.

## Stop or remove
    systemctl disable --now claude-admin.service          # stop it
    rm /etc/sudoers.d/claude-admin                         # take away its admin rights
