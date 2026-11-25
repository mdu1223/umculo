# Claude Code worker for Umculo: reviews, tests and fixes the code from its own container.
# It runs as an ordinary user, sees only /work (the code), /deploy (the deploy request box) and its own home.
FROM python:3.13-slim-bookworm

ARG NODE_MAJOR=22
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update -qq \
 && apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg git tmux jq ripgrep procps less \
      ffmpeg sqlite3 openssh-client \
 && install -d -m 0755 /etc/apt/keyrings \
 && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/githubcli.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update -qq && apt-get install -y -qq --no-install-recommends nodejs gh \
 && rm -rf /var/lib/apt/lists/*

# what Umculo's tests need (the same as its CI), plus a checker
RUN pip install --no-cache-dir mutagen "segno==1.6.6" "shazamio==0.9.0" "yt-dlp[default]" py-opensonic pyflakes

# a browser for the screen-by-screen tests (test/ui_*.js)
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/tools/browsers NODE_PATH=/opt/tools/node_modules
RUN mkdir -p /opt/tools && cd /opt/tools && npm init -y >/dev/null && npm install --no-audit --no-fund playwright@1 \
 && npx playwright install --with-deps chromium && chmod -R a+rX /opt/tools

RUN useradd -m -u 1000 -s /bin/bash claude && mkdir -p /work /deploy && chown claude:claude /work /deploy
COPY bin/ /usr/local/bin/
COPY worker-CLAUDE.md review-prompt.md /opt/worker/
RUN chmod 755 /usr/local/bin/worker-start /usr/local/bin/worker-setup /usr/local/bin/umculo-deploy /usr/local/bin/umculo-review \
 && chmod -R a+rX /opt/worker

USER claude
WORKDIR /work
# Claude Code (the native installer; it keeps itself up to date in the home volume)
RUN curl -fsSL https://claude.ai/install.sh | bash
ENV PATH=/home/claude/.local/bin:$PATH

CMD ["/usr/local/bin/worker-start"]
