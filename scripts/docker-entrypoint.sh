#!/bin/sh
# -----------------------------------------------------------------------------
# Container entrypoint: mark the bot as "started" for health checks, then run
# the application. /tmp/healthy is checked by Dockerfile HEALTHCHECK and
# docker-compose.prod.yml (non-root user can write to /tmp).
# -----------------------------------------------------------------------------
set -e
touch /tmp/healthy
exec python -m bot.main
