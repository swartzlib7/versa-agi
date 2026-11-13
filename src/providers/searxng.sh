#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — SearXNG Provider (Docker)
#
# Installs SearXNG as a Docker container for agent
# web search capabilities. Agents access via HTTP
# (agictl search web) — no Docker dependency for agents.
#
# Requirements: Docker Engine (auto-installed on Ubuntu/Debian if missing)
# Listens on: http://127.0.0.1:8888 (local only)
#
# Runs on install and on every `setup.sh --update`: writes settings.yml again,
# pulls the image, and recreates the container when either changed.
# Optional keyed engines: [search] brave_api_key / exa_api_key in setup.ini.
#
# Usage:
#   sudo ./searxng.sh              # Install
#   sudo ./searxng.sh --uninstall  # Remove
# ─────────────────────────────────────────────────

set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/searxng_settings.sh"
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

  docker rm -f "searxng-redis" > /dev/null 2>&1 || true

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

# ─── Ensure Docker (same auto-install path as setup_local Intel SYCL) ─
provider_ensure_docker

# ─── Step 1: settings.yml (managed; written on every run) ─────────
# The engine set and the keyed engines come from this script and [search] in
# setup.ini (state_web_access.md §1.2). The secret key of an existing install
# is kept. A changed file or a newer image recreates the container below.
mkdir -p "${SEARXNG_CONFIG_DIR}"
SETTINGS_FILE="${SEARXNG_CONFIG_DIR}/settings.yml"

SECRET_KEY="$(searxng_existing_secret "${SETTINGS_FILE}")"
[ -n "${SECRET_KEY}" ] || SECRET_KEY="$(openssl rand -hex 32)"

BRAVE_API_KEY="$(provider_ini_get search brave_api_key)"
EXA_API_KEY="$(provider_ini_get search exa_api_key)"
SEARCH_CONTACT_URL="$(provider_ini_get search contact_url)"
if ! searxng_safe_value "${BRAVE_API_KEY}"; then
  warn "[search] brave_api_key has characters a key cannot have — ignored"
  BRAVE_API_KEY=""
fi
if ! searxng_safe_value "${EXA_API_KEY}"; then
  warn "[search] exa_api_key has characters a key cannot have — ignored"
  EXA_API_KEY=""
fi
if ! searxng_safe_value "${SEARCH_CONTACT_URL}"; then
  warn "[search] contact_url is not a plain URL or mailto: address — ignored"
  SEARCH_CONTACT_URL=""
fi

SETTINGS_NEW="$(mktemp)"
searxng_settings_yaml "${SECRET_KEY}" "${BRAVE_API_KEY}" "${EXA_API_KEY}" "${SEARCH_CONTACT_URL}" > "${SETTINGS_NEW}"
SETTINGS_CHANGED=false
if ! cmp -s "${SETTINGS_NEW}" "${SETTINGS_FILE}" 2>/dev/null; then
  SETTINGS_CHANGED=true
  if [ -f "${SETTINGS_FILE}" ] && ! grep -q "^# Managed by Versa AGi" "${SETTINGS_FILE}"; then
    cp -p "${SETTINGS_FILE}" "${SETTINGS_FILE}.before-managed"
    info "Earlier settings.yml kept as settings.yml.before-managed"
  fi
  cp "${SETTINGS_NEW}" "${SETTINGS_FILE}"
  ok "settings.yml written (engines: google, bing, qwant, yahoo added to the defaults)"
else
  ok "settings.yml unchanged"
fi
rm -f "${SETTINGS_NEW}"
# The file can hold API keys. The container runs as searxng (uid 977).
chown 977:977 "${SETTINGS_FILE}" 2>/dev/null || true
chmod 640 "${SETTINGS_FILE}" 2>/dev/null || true
[ -n "${BRAVE_API_KEY}" ] && ok "Brave Search API engine on (key from setup.ini); Brave page engine off"
[ -n "${EXA_API_KEY}" ] && ok "Exa API engine on (key from setup.ini)"
[ -z "${SEARCH_CONTACT_URL}" ] && info "Wikipedia and Wikidata need [search] contact_url in setup.ini to load"

# ─── Step 2: image (pulled on every run so updates reach the engines) ─
info "Pulling SearXNG image..."
docker pull "${SEARXNG_IMAGE}" 2>&1 | tail -3 || warn "Image pull failed — using the image already on this machine"
IMAGE_NEW="$(docker image inspect --format '{{.Id}}' "${SEARXNG_IMAGE}" 2>/dev/null || echo "")"
IMAGE_RUNNING="$(docker inspect --format '{{.Image}}' "${CONTAINER_NAME}" 2>/dev/null || echo "")"

# ─── Step 3: keep a healthy, current container; otherwise recreate ─
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    "http://127.0.0.1:${SEARXNG_PORT}/search?q=test&format=json" 2>/dev/null || echo "000")
  if [ "${HTTP_CODE}" = "200" ] && [ "${SETTINGS_CHANGED}" = false ] \
     && [ -n "${IMAGE_NEW}" ] && [ "${IMAGE_NEW}" = "${IMAGE_RUNNING}" ]; then
    ok "SearXNG already running on port ${SEARXNG_PORT} (settings and image current)"
    provider_ini_set search enabled true
    provider_ini_set search searxng_url "http://localhost:${SEARXNG_PORT}"
    ok "setup.ini [search] enabled=true"
    exit 0
  fi
  if [ "${HTTP_CODE}" != "200" ]; then
    warn "Container exists but not responding — recreating..."
  elif [ "${SETTINGS_CHANGED}" = true ]; then
    info "Settings changed — recreating the container..."
  else
    info "Newer image — recreating the container..."
  fi
  docker stop "${CONTAINER_NAME}" 2>/dev/null || true
  docker rm "${CONTAINER_NAME}" 2>/dev/null || true
elif docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${CONTAINER_NAME}$"; then
  info "Stopped container found — removing and recreating..."
  docker rm "${CONTAINER_NAME}" 2>/dev/null || true
fi

# The rate limiter is off (local, single user), so no Redis/Valkey is needed.
# Remove the Redis container earlier versions started.
REDIS_CONTAINER="searxng-redis"
if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${REDIS_CONTAINER}$"; then
  docker rm -f "${REDIS_CONTAINER}" > /dev/null 2>&1 || true
  ok "Redis container removed (no longer used)"
fi

# ─── Step 4: Start SearXNG container ─────────────
info "Starting SearXNG container..."
docker run -d \
  --name "${CONTAINER_NAME}" \
  --restart unless-stopped \
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
elif command -v jq > /dev/null 2>&1; then
  # One real query: how many results, and which engines did not answer.
  PROBE="$(curl -s --max-time 20 "http://127.0.0.1:${SEARXNG_PORT}/search?q=weather&format=json" 2>/dev/null || echo "{}")"
  PROBE_RESULTS="$(echo "${PROBE}" | jq -r '(.results // []) | length' 2>/dev/null || echo "?")"
  PROBE_ENGINES="$(echo "${PROBE}" | jq -r '[(.results // [])[].engine] | unique | join(", ")' 2>/dev/null || echo "")"
  PROBE_BLOCKED="$(echo "${PROBE}" | jq -r '[(.unresponsive_engines // [])[] | "\(.[0]) (\(.[1]))"] | join(", ")' 2>/dev/null || echo "")"
  if [ "${PROBE_RESULTS}" = "0" ] || [ "${PROBE_RESULTS}" = "?" ]; then
    warn "Test search returned no results"
  else
    ok "Test search: ${PROBE_RESULTS} results from ${PROBE_ENGINES:-unknown engines}"
  fi
  [ -n "${PROBE_BLOCKED}" ] && warn "Engines that did not answer: ${PROBE_BLOCKED}"
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
