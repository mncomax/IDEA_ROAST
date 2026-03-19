#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Deploy Idea Roast on the server: pull sources, rebuild images, recreate stack.
# Run from repo root or via: ./scripts/deploy.sh
# Requires: git, Docker Compose v2, .env next to docker-compose.yml
# -----------------------------------------------------------------------------

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Git: pull latest"
git pull --ff-only

echo "==> Docker: build images (base + production overrides)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml build

echo "==> Docker: stop and remove current stack"
docker compose -f docker-compose.yml -f docker-compose.prod.yml down

echo "==> Docker: start stack (detached)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

echo "==> Docker: status"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

echo "==> Done."
