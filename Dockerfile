# -----------------------------------------------------------------------------
# Idea Roast Telegram bot — production-oriented image.
# Build context is filtered by .dockerignore (secrets, venv, local data, docs).
# -----------------------------------------------------------------------------

FROM python:3.12-slim

LABEL org.opencontainers.image.title="Idea Roast Bot" \
      org.opencontainers.image.description="Telegram bot (brainstorm / roast) with optional voice"

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user (fixed UID/GID for volume ownership expectations).
RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app --shell /bin/sh app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Entrypoint before app COPY so this layer stays cached when only code changes.
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

COPY . .

RUN mkdir -p /app/data /app/backups \
    && chown -R app:app /app

USER app

# Same marker file as Compose healthcheck (entrypoint touches before exec).
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD test -f /tmp/healthy || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
