#!/bin/bash
# Shared stock-chat download picker for setup_local.sh (Ollama + SYCL).
# Source this file. It does not run as a standalone installer.
#
# Empty / default selection is gemma4:e4b only. Other stock keys stay in
# [catalog_library] for later `agictl model add` / `sycl import`.

# key|approx_GB|vram_hint
_STOCK_CHAT_PICK_ROWS=(
  "gemma4:e4b|5|fits ≤8 GB VRAM"
  "gemma4:26b|16|fits ≤12 GB VRAM"
  "gemma4:31b|18|needs ≥16 GB VRAM"
  "qwen3.6:35b|21|needs ≥24 GB VRAM"
  "qwen3.8:27b|23|needs ≥24 GB VRAM"
)

_stock_chat_pick_default_key() {
  echo "gemma4:e4b"
}

_stock_chat_pick_count() {
  echo "${#_STOCK_CHAT_PICK_ROWS[@]}"
}

_stock_chat_pick_key_at() {
  local idx="${1:-0}"
  local row="${_STOCK_CHAT_PICK_ROWS[$idx]:-}"
  echo "${row%%|*}"
}

# Parse a number list (space or comma) into a CSV of stock keys.
# Empty / invalid → default key only. First valid pick is the default model.
# Prints: "<default_key>|<csv>"
_parse_stock_chat_pick() {
  local raw="${1:-}"
  local default_key
  default_key="$(_stock_chat_pick_default_key)"
  raw="${raw//,/ }"
  raw="$(echo "${raw}" | tr -s '[:space:]' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

  if [ -z "${raw}" ]; then
    echo "${default_key}|${default_key}"
    return 0
  fi

  local -a picked=()
  local tok idx key seen k
  for tok in ${raw}; do
    case "${tok}" in
      ''|*[!0-9]*) continue ;;
    esac
    idx=$((tok - 1))
    if [ "${idx}" -lt 0 ] || [ "${idx}" -ge "${#_STOCK_CHAT_PICK_ROWS[@]}" ]; then
      continue
    fi
    key="$(_stock_chat_pick_key_at "${idx}")"
    [ -n "${key}" ] || continue
    seen=0
    for k in "${picked[@]+"${picked[@]}"}"; do
      if [ "${k}" = "${key}" ]; then
        seen=1
        break
      fi
    done
    if [ "${seen}" -eq 0 ]; then
      picked+=("${key}")
    fi
  done

  if [ "${#picked[@]}" -eq 0 ]; then
    echo "${default_key}|${default_key}"
    return 0
  fi

  local csv=""
  for k in "${picked[@]}"; do
    if [ -n "${csv}" ]; then
      csv="${csv},${k}"
    else
      csv="${k}"
    fi
  done
  echo "${picked[0]}|${csv}"
}

_print_stock_chat_pick_menu() {
  local i row key size hint mark
  echo ""
  echo "  Which stock chat models should setup download?"
  echo "  Other keys stay in the catalog for later:"
  echo "    sudo agictl model add <key>     (Ollama)"
  echo "    sudo agictl model sycl import   (Intel)"
  echo ""
  for i in "${!_STOCK_CHAT_PICK_ROWS[@]}"; do
    row="${_STOCK_CHAT_PICK_ROWS[$i]}"
    key="${row%%|*}"
    size="${row#*|}"
    hint="${size#*|}"
    size="${size%%|*}"
    mark=""
    if [ "${key}" = "$(_stock_chat_pick_default_key)" ]; then
      mark="  [default]"
    fi
    printf "    %d) %-14s — ~%s GB  (%s)%s\n" \
      "$((i + 1))" "${key}" "${size}" "${hint}" "${mark}"
  done
  echo ""
  echo "  Enter numbers (space or comma). Default: 1 ($(_stock_chat_pick_default_key) only)."
}

# Interactive prompt. Sets DEFAULT_MODEL and LOCAL_MODELS.
# Non-interactive callers can set VERSA_MODEL_PICK and skip read.
_prompt_stock_chat_downloads() {
  local raw parsed default_key csv
  default_key="$(_stock_chat_pick_default_key)"
  _print_stock_chat_pick_menu
  if [ -n "${VERSA_MODEL_PICK:-}" ]; then
    raw="${VERSA_MODEL_PICK}"
    echo "  Selection (preset): ${raw}"
  else
    read -p "  Selection [1]: " raw
  fi
  parsed="$(_parse_stock_chat_pick "${raw}")"
  default_key="${parsed%%|*}"
  csv="${parsed#*|}"
  DEFAULT_MODEL="${default_key}"
  LOCAL_MODELS="${csv}"
  SYCL_ACTIVE_MODEL="${default_key}"
  if declare -F ok >/dev/null 2>&1; then
    ok "Download selection: ${LOCAL_MODELS} (default ${DEFAULT_MODEL})"
  fi
}

# Write chosen keys into setup.ini copies (fresh install / reconfigure only).
_write_stock_chat_pick_to_ini() {
  local default_key="${1:-${DEFAULT_MODEL}}"
  local csv="${2:-${LOCAL_MODELS}}"
  local f
  for f in "${SCRIPT_DIR:-.}/setup.ini" "/etc/versa-agi/setup.ini"; do
    [ -f "${f}" ] || continue
    sed -i '/^\[local_ai\]/,/^\[/{s/^default_model=.*/default_model='"${default_key}"'/}' "${f}"
    sed -i '/^\[local_ai\]/,/^\[/{s/^local_models=.*/local_models='"${csv}"'/}' "${f}"
    sed -i '/^\[local_ai\]/,/^\[/{s/^sycl_active_model=.*/sycl_active_model='"${default_key}"'/}' "${f}"
    sed -i '/^\[model_routing\]/,/^\[/{s/^local=.*/local='"${default_key}"'/}' "${f}"
  done
}
