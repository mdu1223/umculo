FROM python:3.13-alpine
RUN apk add --no-cache ffmpeg nodejs \
 && pip install --no-cache-dir "yt-dlp[default]" bgutil-ytdlp-pot-provider mutagen "shazamio==0.9.0" "segno==1.6.6"
WORKDIR /app
COPY app/ /app/
ENV PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
EXPOSE 8095
HEALTHCHECK --interval=60s --timeout=5s CMD wget -qO- http://127.0.0.1:${PORT:-8095}/health || exit 1
# yt-dlp is refreshed on every start: YouTube and SoundCloud change often
CMD ["sh", "-c", "pip install -q --no-cache-dir -U 'yt-dlp[default]' bgutil-ytdlp-pot-provider >/dev/null 2>&1 || true; exec python -I /app/umculo.py"]
