#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════
# Versa AGi — SYCL Model Registry Manager
# ═══════════════════════════════════════════════════════
# Interactive script for managing the [sycl_models] section
# of models.ini. Can be used standalone or sourced from
# setup_local.sh via --inline mode.
#
# Usage:
#   sudo ./manage_registry.sh              # Interactive management
#   sudo ./manage_registry.sh --list       # Display registry only
#   source ./manage_registry.sh --inline   # Load arrays + show menu (for setup_local.sh)
#
# When sourced with --inline, exports:
#   _REG_NAMES[]   — Model names (e.g. "gemma4:e4b")
#   _REG_REPOS[]   — HuggingFace repos
#   _REG_FILES[]   — GGUF filenames
#   _REG_SIZES[]   — GGUF sizes in GB
#   _REG_COUNT     — Number of registered models
# ═══════════════════════════════════════════════════════

set -euo pipefail

# ─── Models.ini Paths ──────────────────────────────────
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_MODELS_INI_SOURCE="${_SCRIPT_DIR}/models.ini"
_MODELS_INI_DEPLOYED="/etc/versa-agi/models.ini"

# ─── Formatting ────────────────────────────────────────
_BOLD=$'\033[1m'
_DIM=$'\033[2m'
_CYAN=$'\033[36m'
_GREEN=$'\033[32m'
_YELLOW=$'\033[33m'
_RED=$'\033[31m'
_RESET=$'\033[0m'

# ─── Registry Arrays ──────────────────────────────────
_REG_NAMES=()
_REG_REPOS=()
_REG_FILES=()
_REG_SIZES=()
_REG_COUNT=0

# ═══════════════════════════════════════════════════════
# Parse [sycl_models] from models.ini into arrays
# ═══════════════════════════════════════════════════════
_load_registry() {
  _REG_NAMES=()
  _REG_REPOS=()
  _REG_FILES=()
  _REG_SIZES=()
  _REG_COUNT=0

  local ini_file=""
  # Prefer deployed copy, but fall back to source if deployed copy lacks [sycl_models]
  if [ -f "${_MODELS_INI_DEPLOYED}" ] && grep -q '^\[sycl_models\]' "${_MODELS_INI_DEPLOYED}" 2>/dev/null; then
    ini_file="${_MODELS_INI_DEPLOYED}"
  elif [ -f "${_MODELS_INI_SOURCE}" ] && grep -q '^\[sycl_models\]' "${_MODELS_INI_SOURCE}" 2>/dev/null; then
    ini_file="${_MODELS_INI_SOURCE}"
  elif [ -f "${_MODELS_INI_DEPLOYED}" ]; then
    # File exists but no [sycl_models] section — still try source
    if [ -f "${_MODELS_INI_SOURCE}" ]; then
      ini_file="${_MODELS_INI_SOURCE}"
    else
      ini_file="${_MODELS_INI_DEPLOYED}"
    fi
  elif [ -f "${_MODELS_INI_SOURCE}" ]; then
    ini_file="${_MODELS_INI_SOURCE}"
  else
    echo "  ${_RED}✗ models.ini not found at ${_MODELS_INI_DEPLOYED} or ${_MODELS_INI_SOURCE}${_RESET}"
    return 1
  fi

  local in_section=false
  while IFS= read -r line; do
    # Strip leading/trailing whitespace
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"

    # Skip empty lines and comments
    [[ -z "$line" || "$line" == \#* ]] && continue

    # Detect section headers
    if [[ "$line" == \[*\] ]]; then
      if [[ "$line" == "[sycl_models]" ]]; then
        in_section=true
      else
        # Exiting [sycl_models] on hitting another section
        $in_section && break
      fi
      continue
    fi

    if $in_section; then
      # Parse: key = repo,file,size_gb
      local key="${line%%=*}"
      local value="${line#*=}"
      key="${key#"${key%%[![:space:]]*}"}"
      key="${key%"${key##*[![:space:]]}"}"
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"

      if [[ -n "$key" && -n "$value" ]]; then
        local repo file size_gb
        IFS=',' read -r repo file size_gb <<< "$value"
        repo="${repo#"${repo%%[![:space:]]*}"}"
        repo="${repo%"${repo##*[![:space:]]}"}"
        file="${file#"${file%%[![:space:]]*}"}"
        file="${file%"${file##*[![:space:]]}"}"
        size_gb="${size_gb#"${size_gb%%[![:space:]]*}"}"
        size_gb="${size_gb%"${size_gb##*[![:space:]]}"}"

        _REG_NAMES+=("$key")
        _REG_REPOS+=("$repo")
        _REG_FILES+=("$file")
        _REG_SIZES+=("$size_gb")
      fi
    fi
  done < "$ini_file"

  _REG_COUNT=${#_REG_NAMES[@]}
}

# ═══════════════════════════════════════════════════════
# Display registry table
# ═══════════════════════════════════════════════════════
_display_registry() {
  echo ""
  echo "  ${_BOLD}SYCL Model Registry${_RESET}  ${_DIM}(from models.ini)${_RESET}"
  echo "  ─────────────────────────────────────────"

  if [ "${_REG_COUNT}" -eq 0 ]; then
    echo "  ${_YELLOW}No models registered.${_RESET}"
    echo "  ${_DIM}Use [a] to add a model.${_RESET}"
    echo ""
    return
  fi

  printf "  ${_DIM}%-3s %-16s %-8s %s${_RESET}\n" "#" "Model" "Size" "Repo"
  for i in $(seq 0 $((_REG_COUNT - 1))); do
    printf "  ${_CYAN}%-3s${_RESET} %-16s ~%-6s %s\n" \
      "$((i + 1)))" "${_REG_NAMES[$i]}" "${_REG_SIZES[$i]}GB" "${_REG_REPOS[$i]}"
  done
  echo ""
}

# ═══════════════════════════════════════════════════════
# Write a model entry to [sycl_models] in models.ini
# Args: $1=name $2=repo $3=file $4=size_gb
# ═══════════════════════════════════════════════════════
_write_sycl_entry() {
  local name="$1" repo="$2" file="$3" size_gb="$4"
  local entry="${name} = ${repo},${file},${size_gb}"

  for ini_file in "${_MODELS_INI_DEPLOYED}" "${_MODELS_INI_SOURCE}"; do
    [ -f "$ini_file" ] || continue

    # Check if key already exists in [sycl_models]
    if grep -q "^[[:space:]]*${name}[[:space:]]*=" "$ini_file" 2>/dev/null; then
      # Update existing entry
      sed -i "s|^[[:space:]]*${name}[[:space:]]*=.*|${entry}|" "$ini_file"
    else
      # Append to [sycl_models] section
      # Find the [sycl_models] line and append after last entry in that section
      if grep -q '^\[sycl_models\]' "$ini_file"; then
        # Find line number of [sycl_models] and the next section
        local section_line next_section_line last_line
        section_line=$(grep -n '^\[sycl_models\]' "$ini_file" | head -1 | cut -d: -f1)
        next_section_line=$(awk -v start="$((section_line + 1))" 'NR > start && /^\[/ { print NR; exit }' "$ini_file")

        if [ -n "$next_section_line" ]; then
          # Insert before the next section
          sed -i "${next_section_line}i\\${entry}" "$ini_file"
        else
          # Append at end of file
          echo "$entry" >> "$ini_file"
        fi
      else
        # Create the section
        {
          echo ""
          echo "[sycl_models]"
          echo "$entry"
        } >> "$ini_file"
      fi
    fi
  done
}

# ═══════════════════════════════════════════════════════
# Write a context_windows entry
# Args: $1=name $2=ctx_recommended $3=ctx_max
# ═══════════════════════════════════════════════════════
_write_context_entry() {
  local name="$1" recommended="$2" max_ctx="$3"
  local entry="${name} = ${recommended},${max_ctx}"

  for ini_file in "${_MODELS_INI_DEPLOYED}" "${_MODELS_INI_SOURCE}"; do
    [ -f "$ini_file" ] || continue

    if grep -q "^[[:space:]]*${name}[[:space:]]*=" "$ini_file" 2>/dev/null; then
      # Only update within [context_windows] section — use sed carefully
      sed -i "/^\[context_windows\]/,/^\[/ s|^[[:space:]]*${name}[[:space:]]*=.*|${entry}|" "$ini_file"
    else
      # Append to [context_windows] section
      if grep -q '^\[context_windows\]' "$ini_file"; then
        local section_line next_section_line
        section_line=$(grep -n '^\[context_windows\]' "$ini_file" | head -1 | cut -d: -f1)
        next_section_line=$(awk -v start="$((section_line + 1))" 'NR > start && /^\[/ { print NR; exit }' "$ini_file")

        if [ -n "$next_section_line" ]; then
          sed -i "${next_section_line}i\\${entry}" "$ini_file"
        else
          echo "$entry" >> "$ini_file"
        fi
      fi
    fi
  done
}

# ═══════════════════════════════════════════════════════
# Write a local_models display label entry
# Args: $1=name $2=label
# ═══════════════════════════════════════════════════════
_write_local_model_entry() {
  local name="$1" label="$2"
  local entry="${name} = ${label}"

  for ini_file in "${_MODELS_INI_DEPLOYED}" "${_MODELS_INI_SOURCE}"; do
    [ -f "$ini_file" ] || continue

    if grep -q "^[[:space:]]*${name}[[:space:]]*=" "$ini_file" 2>/dev/null; then
      sed -i "/^\[local_models\]/,/^\[/ s|^[[:space:]]*${name}[[:space:]]*=.*|${entry}|" "$ini_file"
    else
      if grep -q '^\[local_models\]' "$ini_file"; then
        local section_line next_section_line
        section_line=$(grep -n '^\[local_models\]' "$ini_file" | head -1 | cut -d: -f1)
        next_section_line=$(awk -v start="$((section_line + 1))" 'NR > start && /^\[/ { print NR; exit }' "$ini_file")

        if [ -n "$next_section_line" ]; then
          sed -i "${next_section_line}i\\${entry}" "$ini_file"
        else
          echo "$entry" >> "$ini_file"
        fi
      fi
    fi
  done
}

# ═══════════════════════════════════════════════════════
# Remove a model from [sycl_models] (and optionally context_windows + local_models)
# Args: $1=name
# ═══════════════════════════════════════════════════════
_remove_sycl_entry() {
  local name="$1"

  for ini_file in "${_MODELS_INI_DEPLOYED}" "${_MODELS_INI_SOURCE}"; do
    [ -f "$ini_file" ] || continue
    # Remove from [sycl_models]
    sed -i "/^\[sycl_models\]/,/^\[/ { /^[[:space:]]*${name}[[:space:]]*=/d }" "$ini_file"
  done
}

# ═══════════════════════════════════════════════════════
# Interactive: Add a new model
# ═══════════════════════════════════════════════════════
_interactive_add() {
  echo ""
  echo "  ${_BOLD}Add New SYCL Model${_RESET}"
  echo "  ─────────────────────────────────────────"
  echo "  ${_YELLOW}Prefer inspect-first chat import:${_RESET}"
  echo "    agictl model hf inspect <hf-url-or-hf://org/repo/file.gguf>"
  echo "    sudo agictl model sycl import <source> --name <key> --runtime chat"
  echo "  Media GGUFs (image/video pipelines) must not be added here."
  echo ""

  local name repo file size_gb ctx_rec ctx_max label

  read -rp "  Model name (e.g. llama4:8b): " name
  if [ -z "$name" ]; then
    echo "  ${_RED}✗ Name cannot be empty.${_RESET}"
    return 1
  fi

  # Check for duplicate
  for existing in "${_REG_NAMES[@]}"; do
    if [ "$existing" = "$name" ]; then
      echo "  ${_RED}✗ Model '${name}' already exists. Use [e] to edit.${_RESET}"
      return 1
    fi
  done

  read -rp "  HuggingFace repo (e.g. unsloth/Llama-4-8B-GGUF): " repo
  if [ -z "$repo" ]; then
    echo "  ${_RED}✗ Repo cannot be empty.${_RESET}"
    return 1
  fi

  read -rp "  GGUF filename (e.g. Llama-4-8B-Q4_K_M.gguf): " file
  if [ -z "$file" ]; then
    echo "  ${_RED}✗ Filename cannot be empty.${_RESET}"
    return 1
  fi

  read -rp "  Approximate GGUF size in GB (e.g. 5): " size_gb
  if [ -z "$size_gb" ]; then
    echo "  ${_RED}✗ Size cannot be empty.${_RESET}"
    return 1
  fi

  read -rp "  Recommended context (tokens, e.g. 32768) [32768]: " ctx_rec
  ctx_rec="${ctx_rec:-32768}"

  read -rp "  Max context (tokens, e.g. 131072) [131072]: " ctx_max
  ctx_max="${ctx_max:-131072}"

  read -rp "  Display label (e.g. Llama 4 8B — Dense, 128K context): " label
  if [ -z "$label" ]; then
    label="${name}"
  fi

  echo ""
  echo "  ${_BOLD}Summary:${_RESET}"
  echo "    Name:     ${name}"
  echo "    Repo:     ${repo}"
  echo "    File:     ${file}"
  echo "    Size:     ~${size_gb} GB"
  echo "    Context:  ${ctx_rec} / ${ctx_max}"
  echo "    Label:    ${label}"
  echo ""

  read -rp "  Save? [Y/n]: " confirm
  if [[ "${confirm,,}" =~ ^n ]]; then
    echo "  ${_YELLOW}Cancelled.${_RESET}"
    return 0
  fi

  _write_sycl_entry "$name" "$repo" "$file" "$size_gb"
  _write_context_entry "$name" "$ctx_rec" "$ctx_max"
  _write_local_model_entry "$name" "$label"

  echo "  ${_GREEN}✓ Model '${name}' registered in models.ini${_RESET}"

  # Reload arrays
  _load_registry
}

# ═══════════════════════════════════════════════════════
# Interactive: Edit an existing model
# ═══════════════════════════════════════════════════════
_interactive_edit() {
  echo ""
  read -rp "  Edit model # (1-${_REG_COUNT}): " idx
  idx=$((idx - 1))
  if [ "$idx" -lt 0 ] || [ "$idx" -ge "${_REG_COUNT}" ]; then
    echo "  ${_RED}✗ Invalid selection.${_RESET}"
    return 1
  fi

  local name="${_REG_NAMES[$idx]}"
  local old_repo="${_REG_REPOS[$idx]}"
  local old_file="${_REG_FILES[$idx]}"
  local old_size="${_REG_SIZES[$idx]}"

  echo ""
  echo "  ${_BOLD}Editing: ${name}${_RESET}"
  echo "  ${_DIM}Press Enter to keep current value.${_RESET}"
  echo ""

  read -rp "  Repo [${old_repo}]: " new_repo
  new_repo="${new_repo:-$old_repo}"

  read -rp "  File [${old_file}]: " new_file
  new_file="${new_file:-$old_file}"

  read -rp "  Size GB [${old_size}]: " new_size
  new_size="${new_size:-$old_size}"

  _write_sycl_entry "$name" "$new_repo" "$new_file" "$new_size"
  echo "  ${_GREEN}✓ Updated '${name}'${_RESET}"

  _load_registry
}

# ═══════════════════════════════════════════════════════
# Interactive: Delete a model
# ═══════════════════════════════════════════════════════
_interactive_delete() {
  echo ""
  read -rp "  Delete model # (1-${_REG_COUNT}): " idx
  idx=$((idx - 1))
  if [ "$idx" -lt 0 ] || [ "$idx" -ge "${_REG_COUNT}" ]; then
    echo "  ${_RED}✗ Invalid selection.${_RESET}"
    return 1
  fi

  local name="${_REG_NAMES[$idx]}"
  read -rp "  Remove '${name}' from SYCL registry? [y/N]: " confirm
  if [[ "${confirm,,}" =~ ^y ]]; then
    _remove_sycl_entry "$name"
    echo "  ${_GREEN}✓ Removed '${name}' from SYCL registry${_RESET}"
    echo "  ${_DIM}Note: [context_windows] and [local_models] entries preserved.${_RESET}"
    _load_registry
  else
    echo "  ${_YELLOW}Cancelled.${_RESET}"
  fi
}

# ═══════════════════════════════════════════════════════
# Interactive menu loop
# ═══════════════════════════════════════════════════════
_interactive_menu() {
  while true; do
    _display_registry
    echo "  ${_DIM}───────────────────────────────────────────${_RESET}"
    echo "  ${_DIM}Press Enter to continue with above models.${_RESET}"
    echo "  ${_DIM}Or: [a] Add  [e] Edit  [d] Delete${_RESET}"
    echo ""
    read -rp "  Action (a/e/d or Enter to continue): " choice

    case "${choice,,}" in
      a) _interactive_add || true ;;
      e)
        if [ "${_REG_COUNT}" -eq 0 ]; then
          echo "  ${_YELLOW}No models to edit. Use [a] to add one first.${_RESET}"
        else
          _interactive_edit || true
        fi
        ;;
      d)
        if [ "${_REG_COUNT}" -eq 0 ]; then
          echo "  ${_YELLOW}No models to delete.${_RESET}"
        else
          _interactive_delete || true
        fi
        ;;
      "") break ;;
      *) echo "  ${_YELLOW}Invalid choice.${_RESET}" ;;
    esac
  done
}

# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════
_registry_main() {
  local mode="${1:-interactive}"

  _load_registry

  case "$mode" in
    --list)
      _display_registry
      ;;
    --inline)
      # Just load arrays and show the interactive menu
      # Arrays are available to the caller after sourcing
      _interactive_menu
      ;;
    *)
      echo ""
      echo "  ${_BOLD}═══════════════════════════════════════════${_RESET}"
      echo "  ${_BOLD}  Versa AGi — SYCL Model Registry Manager${_RESET}"
      echo "  ${_BOLD}═══════════════════════════════════════════${_RESET}"
      _interactive_menu
      echo "  ${_GREEN}Done.${_RESET}"
      echo ""
      ;;
  esac
}

# Only run main if executed directly (not sourced)
# When sourced, the caller uses _load_registry and _interactive_menu directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  _registry_main "${1:-}"
else
  # Sourced mode: just run with the provided arg
  _registry_main "${1:-}"
fi
