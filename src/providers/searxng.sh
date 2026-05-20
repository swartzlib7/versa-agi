#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — SearXNG Provider (Docker)
#
# Installs SearXNG as a Docker container for agent
# web search capabilities. Agents access via HTTP
# (agictl search web) — no Docker dependency for agents.
#
# Requirements: Docker installed
# Listens on: http://127.0.0.1:8888 (local only)
#
# Usage:
#   sudo ./searxng.sh              # Install
#   sudo ./searxng.sh --uninstall  # Remove
# ─────────────────────────────────────────────────

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
provider_require_root

# ─── Constants ──────────────────────────────────────
CONTAINER_NAME="searxng"
SEARXNG_PORT=8888
SEARXNG_CONFIG_DIR="/etc/searxng"
SEARXNG_IMAGE="searxng/searxng:latest"

# ─── Uninstall ──────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
  echo ""
  echo "  ─── SearXNG Provider — Uninstall ──────────"
  echo ""

  if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
    read -p "  Remove SearXNG container and config? [y/N]: " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then echo "  Cancelled."; exit 0; fi
    echo ""
  fi

  # Stop and remove container
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
    docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    docker rm "${CONTAINER_NAME}" 2>/dev/null || true
    ok "Container '${CONTAINER_NAME}' removed"
  else
    info "Container '${CONTAINER_NAME}' not found — already removed"
  fi

  # Remove config directory
  if [ -d "${SEARXNG_CONFIG_DIR}" ]; then
    rm -rf "${SEARXNG_CONFIG_DIR}"
    ok "Config removed (${SEARXNG_CONFIG_DIR})"
  fi

  # Disable in setup.ini
  provider_ini_set search enabled false
  ok "Search disabled in setup.ini"

  echo ""
  echo "  ✅ SearXNG removed."
  echo "  Agents will no longer have web search capability."
  echo ""
  exit 0
fi

# ─── Pre-flight Check ──────────────────────────────
echo ""
echo "  ─── SearXNG Provider — Install ──────────────"
echo ""
echo "  SearXNG is a privacy-respecting metasearch engine"
echo "  that provides web search capabilities to agents."
echo ""
echo "  Runs as: Docker container (${CONTAINER_NAME})"
echo "  Binds to: http://127.0.0.1:${SEARXNG_PORT} (local only)"
echo ""

if [ -z "${VERSA_SETUP_PARENT:-}" ]; then
  read -p "  Install SearXNG? [Y/n]: " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Nn]$ ]]; then echo "  SearXNG setup cancelled."; exit 0; fi
  echo ""
fi

# ─── Check Docker ─────────────────────────────────
if ! command -v docker &>/dev/null; then
  error "Docker is not installed. Install Docker first, then re-run this script."
fi

# ─── Check if already running ─────────────────────
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
  # Verify it responds
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://127.0.0.1:${SEARXNG_PORT}/search?q=test&format=json" 2>/dev/null || echo "000")
  if [ "${HTTP_CODE}" = "200" ]; then
    ok "SearXNG already running on port ${SEARXNG_PORT}"
    provider_ini_set search enabled true
    provider_ini_set search searxng_url "http://localhost:${SEARXNG_PORT}"
    ok "setup.ini [search] enabled=true"
    exit 0
  else
    warn "Container exists but not responding — recreating..."
    docker stop "${CONTAINER_NAME}" 2>/dev/null || true
    docker rm "${CONTAINER_NAME}" 2>/dev/null || true
  fi
elif docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
  info "Stopped container found — removing and recreating..."
  docker rm "${CONTAINER_NAME}" 2>/dev/null || true
fi

# ─── Step 1: Create config directory ──────────────
mkdir -p "${SEARXNG_CONFIG_DIR}"

# ─── Step 2: Generate settings.yml ────────────────
if [ ! -f "${SEARXNG_CONFIG_DIR}/settings.yml" ]; then
  info "Generating settings.yml..."
  SECRET_KEY="$(openssl rand -hex 32)"
  cat > "${SEARXNG_CONFIG_DIR}/settings.yml" << SETTINGS_EOF
use_default_settings: true

general:
  instance_name: "Versa AGi Search"
  debug: false
  contact_url: false
  enable_metrics: false

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "en"
  formats:
    - html
    - json

server:
  secret_key: "${SECRET_KEY}"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
  public_instance: false

ui:
  static_use_hash: true
  default_theme: simple

redis:
  url: "redis://redis:6379/0"
SETTINGS_EOF
  ok "settings.yml generated (JSON API enabled, secret key randomized)"
else
  ok "settings.yml already exists — preserved"
fi

# ─── Step 3: Create docker-compose or run directly ─
info "Pulling SearXNG image..."
docker pull "${SEARXNG_IMAGE}" 2>&1 | tail -3

# Create a Redis container for SearXNG (if not exists)
REDIS_CONTAINER="searxng-redis"
if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${REDIS_CONTAINER}$"; then
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${REDIS_CONTAINER}$"; then
    docker rm "${REDIS_CONTAINER}" 2>/dev/null || true
  fi
  info "Starting Redis for SearXNG..."
  docker run -d \
    --name "${REDIS_CONTAINER}" \
    --restart unless-stopped \
    redis:alpine \
    redis-server --save "" --appendonly no \
    > /dev/null 2>&1
  ok "Redis container started"
else
  ok "Redis container already running"
fi

# ─── Step 4: Start SearXNG container ─────────────
info "Starting SearXNG container..."
docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
  --link "${REDIS_CONTAINER}":redis \
  -p "127.0.0.1:${SEARXNG_PORT}:8080" \
  -v "${SEARXNG_CONFIG_DIR}:/etc/searxng:rw" \
  -e "SEARXNG_BASE_URL=http://localhost:${SEARXNG_PORT}/" \
  "${SEARXNG_IMAGE}" \
  > /dev/null 2>&1
ok "SearXNG container started"

# ─── Step 5: Verify ──────────────────────────────
info "Verifying SearXNG..."
sleep 3

RETRIES=0
MAX_RETRIES=8
while [ ${RETRIES} -lt ${MAX_RETRIES} ]; do
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://127.0.0.1:${SEARXNG_PORT}/search?q=test&format=json" 2>/dev/null || echo "000")
  if [ "${HTTP_CODE}" = "200" ]; then
    ok "SearXNG is running on http://127.0.0.1:${SEARXNG_PORT} ✓"
    break
  fi
  RETRIES=$((RETRIES + 1))
  if [ ${RETRIES} -lt ${MAX_RETRIES} ]; then
    info "Waiting for SearXNG to start (attempt ${RETRIES}/${MAX_RETRIES})..."
    sleep 2
  fi
done

if [ ${RETRIES} -ge ${MAX_RETRIES} ]; then
  warn "SearXNG may not be responding yet (HTTP ${HTTP_CODE})"
  warn "Check: docker logs ${CONTAINER_NAME}"
fi

# ─── Step 6: Update setup.ini ────────────────────
provider_ini_set search enabled true
provider_ini_set search engine searxng
provider_ini_set search searxng_url "http://localhost:${SEARXNG_PORT}"
ok "setup.ini updated: [search] enabled=true, searxng_url=http://localhost:${SEARXNG_PORT}"

echo ""
echo "  ✅ SearXNG installed and running!"
echo ""
echo "  Agents can now search the web via:"
echo "    agictl search web \"<query>\""
echo ""
echo "  Management:"
echo "    Status:   docker ps -f name=${CONTAINER_NAME}"
echo "    Logs:     docker logs ${CONTAINER_NAME}"
echo "    Restart:  docker restart ${CONTAINER_NAME}"
echo "    Test:     curl 'http://localhost:${SEARXNG_PORT}/search?q=test&format=json'"
echo ""
