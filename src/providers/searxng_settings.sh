#!/bin/bash
# ─────────────────────────────────────────────────
# Versa AGi — SearXNG settings.yml (state_web_access.md §1.2)
#
# Sourced by searxng.sh. One function prints the whole file, so the same text
# is written on install and on every update, and the tests can read it.
# ─────────────────────────────────────────────────

# A key or URL that is safe to put inside double quotes in YAML.
# Usage: searxng_safe_value <value>   → exit 0 when safe (blank is safe)
searxng_safe_value() {
  case "$1" in
    *[!A-Za-z0-9_.:/@+=-]*) return 1 ;;
    *) return 0 ;;
  esac
}

# Usage: searxng_settings_yaml <secret_key> [brave_api_key] [exa_api_key] [contact_url]
#   brave_api_key  Brave Search API key. Adds the `braveapi` engine and turns
#                  off the `brave` engine that reads Brave's public pages.
#   exa_api_key    Exa API key. Adds the `exaapi` engine.
#   contact_url    Sent to Wikipedia and Wikidata, which ask every client to
#                  say who it is. Blank: those two engines do not load.
searxng_settings_yaml() {
  local secret_key="$1"
  local brave_key="${2:-}"
  local exa_key="${3:-}"
  local contact_url="${4:-}"

  local contact_line='contact_url: false'
  if [ -n "${contact_url}" ]; then
    contact_line="contact_url: \"${contact_url}\""
  fi

  cat << SETTINGS_EOF
# Managed by Versa AGi (providers/searxng.sh). Written again on every setup
# and update run: change [search] in setup.ini, not this file.
use_default_settings: true

general:
  instance_name: "Versa AGi Search"
  debug: false
  ${contact_line}
  enable_metrics: false

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "en"
  formats:
    - html
    - json

server:
  secret_key: "${secret_key}"
  bind_address: "0.0.0.0"
  port: 8080
  limiter: false
  public_instance: false

ui:
  static_use_hash: true
  default_theme: simple

# One agent search is not interactive: give slow engines time to answer.
outgoing:
  request_timeout: 6.0
  max_request_timeout: 10.0

# SearXNG ships with Brave and DuckDuckGo as its only large web engines.
# When both are blocked a search returns nothing, so more are turned on.
engines:
  - name: google
    disabled: false
  - name: bing
    disabled: false
  - name: qwant
    disabled: false
  - name: yahoo
    disabled: false
SETTINGS_EOF

  if [ -n "${brave_key}" ]; then
    cat << SETTINGS_EOF
  # Brave Search API (official, keyed). The page-reading engine is turned off
  # so the same source is not asked twice.
  - name: braveapi
    engine: braveapi
    shortcut: brapi
    api_key: "${brave_key}"
    results_per_page: 20
    inactive: false
  - name: brave
    disabled: true
SETTINGS_EOF
  fi

  if [ -n "${exa_key}" ]; then
    cat << SETTINGS_EOF
  # Exa API (official, keyed).
  - name: exaapi
    engine: exaapi
    shortcut: exa
    api_key: "${exa_key}"
    results_per_page: 10
    search_type: auto
    content_mode: highlights
    inactive: false
SETTINGS_EOF
  fi
}

# The secret already in a settings.yml, so an update keeps it. Blank when none.
# Usage: searxng_existing_secret <settings.yml>
searxng_existing_secret() {
  [ -f "$1" ] || return 0
  sed -n 's/^[[:space:]]*secret_key:[[:space:]]*"\([A-Za-z0-9]*\)".*/\1/p' "$1" | head -1
}
