#!/usr/bin/env bash
# Sourced by setup.sh.
# The backup an update makes before it deploys (state_host_services.md §1.8).
#
#   - /var/backups/versa-agi/, root only: not in any account's home, so COA
#     cannot touch it and the full backup (versa-agi-backup) does not swallow it.
#   - One backup is kept. The new one is made completely, then the previous one
#     is removed. Unchanged files are shared with the previous one while both
#     exist, so the extra disk needed is what changed.
#   - The code, the databases, the settings and every agent's home.
#   - Free space is measured first. With too little, no backup is made, the
#     previous one stays, and a notice is left for Lifeline to send.
#
# Nothing here may stop an update: every function reports and returns.
# The VERSA_* variables exist for the tests.

UPDATE_BACKUP_ROOT="${VERSA_UPDATE_BACKUP_ROOT:-/var/backups/versa-agi}"
UPDATE_BACKUP_NOTICE="${VERSA_UPDATE_BACKUP_NOTICE:-/var/lib/versa-agi/update_backup_notice}"
# The same list the full backup leaves out of a home (backup.sh GLOBAL_EXCLUDES):
# caches, downloaded models and installed dependencies that are put back by the
# owner's tools or by setup.sh.
UPDATE_BACKUP_EXCLUDES=".ollama .gemini .cache .npm node_modules __pycache__ .local venv .vagrant .vagrant.d VirtualBox VMs"
# Free space asked for beyond the measured need: 10% and 100 MB.
UPDATE_BACKUP_MARGIN_BYTES="${VERSA_UPDATE_BACKUP_MARGIN_BYTES:-104857600}"

UPDATE_BACKUP_ITEMS=()
UPDATE_BACKUP_PATH=""
UPDATE_BACKUP_NOTE=""

# update_backup_reset — start a new list of what to back up.
update_backup_reset() {
  UPDATE_BACKUP_ITEMS=()
  UPDATE_BACKUP_PATH=""
  UPDATE_BACKUP_NOTE=""
}

# update_backup_add <source folder> <name in the backup> <plain|home|data>
#   plain  copied whole
#   home   copied without UPDATE_BACKUP_EXCLUDES
#   data   copied whole, each *.db through SQLite's own backup
update_backup_add() {
  [ -d "$1" ] || return 0
  UPDATE_BACKUP_ITEMS+=("$1|$2|${3:-plain}")
}

# update_backup_latest — the newest finished backup, or nothing.
update_backup_latest() {
  ls -1d "${UPDATE_BACKUP_ROOT}"/update-*/ 2>/dev/null | sort | tail -1 | sed 's:/$::'
}

_update_backup_human() {
  numfmt --to=iec "${1:-0}" 2>/dev/null || echo "${1:-0} bytes"
}

# _update_backup_rsync_args <kind> — the rsync options for one kind of item.
_update_backup_rsync_args() {
  local kind="$1" pattern
  case "${kind}" in
    home)
      for pattern in ${UPDATE_BACKUP_EXCLUDES}; do
        printf -- '--exclude=%s\n' "${pattern}"
      done
      ;;
    data)
      printf -- '--exclude=%s\n' '*.db' '*.db-wal' '*.db-shm'
      ;;
  esac
}

# update_backup_need_bytes [previous backup]
# Bytes this backup would newly write: what rsync would transfer (unchanged
# files are shared with the previous backup) plus the databases.
update_backup_need_bytes() {
  local previous="${1:-}" item source name kind total=0 bytes link
  local -a args
  for item in "${UPDATE_BACKUP_ITEMS[@]+${UPDATE_BACKUP_ITEMS[@]}}"; do
    IFS='|' read -r source name kind <<< "${item}"
    mapfile -t args < <(_update_backup_rsync_args "${kind}")
    link=()
    [ -n "${previous}" ] && [ -d "${previous}/${name}" ] && link=(--link-dest="${previous}/${name}")
    bytes=$(LC_ALL=C rsync -a --dry-run --stats "${args[@]+${args[@]}}" "${link[@]+${link[@]}}" \
              "${source}/" "${UPDATE_BACKUP_ROOT}/.measure/${name}/" 2>/dev/null \
            | sed -n 's/^Total transferred file size: \([0-9,]*\) bytes.*/\1/p' | tr -d ',')
    total=$(( total + ${bytes:-0} ))
    if [ "${kind}" = "data" ]; then
      bytes=$(find "${source}" -type f -name '*.db' -printf '%s\n' 2>/dev/null | awk '{ sum += $1 } END { print sum + 0 }')
      total=$(( total + ${bytes:-0} ))
    fi
  done
  echo "${total}"
}

# update_backup_free_bytes — free bytes on the disk that holds the backups.
update_backup_free_bytes() {
  if [ -n "${VERSA_UPDATE_BACKUP_FREE_BYTES:-}" ]; then
    echo "${VERSA_UPDATE_BACKUP_FREE_BYTES}"
    return 0
  fi
  df --output=avail -B1 "${UPDATE_BACKUP_ROOT}" 2>/dev/null | tail -1 | tr -d ' '
}

# _update_backup_notice <text> — leave a notice for Lifeline to send.
_update_backup_notice() {
  mkdir -p "$(dirname "${UPDATE_BACKUP_NOTICE}")" 2>/dev/null || true
  printf '%s\n' "$1" > "${UPDATE_BACKUP_NOTICE}" 2>/dev/null || true
  chmod 644 "${UPDATE_BACKUP_NOTICE}" 2>/dev/null || true
}

# _update_backup_copy_databases <source> <dest>
# Each database through SQLite's own backup: a consistent copy even while a
# cycle is still writing. A file SQLite cannot back up is copied as it is.
_update_backup_copy_databases() {
  local source="$1" dest="$2" db rel
  while IFS= read -r db; do
    rel="${db#"${source}"/}"
    mkdir -p "$(dirname "${dest}/${rel}")"
    if ! sqlite3 "${db}" ".backup '${dest}/${rel}'" > /dev/null 2>&1; then
      cp -a "${db}" "${dest}/${rel}" 2>/dev/null || return 1
    fi
    chown --reference="${db}" "${dest}/${rel}" 2>/dev/null || true
    chmod --reference="${db}" "${dest}/${rel}" 2>/dev/null || true
  done < <(find "${source}" -type f -name '*.db' 2>/dev/null)
  return 0
}

# update_backup_run <timestamp> [old location]
# Returns 0 backup made, 2 skipped for space, 3 the copy failed.
# UPDATE_BACKUP_PATH is the backup that exists afterwards (new, or the previous
# one); UPDATE_BACKUP_NOTE says what happened, for setup's line.
update_backup_run() {
  local stamp="$1" legacy="${2:-}"
  local previous need free want incoming final item source name kind old
  local -a args link

  mkdir -p "${UPDATE_BACKUP_ROOT}" 2>/dev/null
  chmod 700 "${UPDATE_BACKUP_ROOT}" 2>/dev/null || true
  # A copy an interrupted run left behind is never a backup.
  rm -rf "${UPDATE_BACKUP_ROOT}"/.incoming-* 2>/dev/null || true

  previous=$(update_backup_latest)
  UPDATE_BACKUP_PATH="${previous}"
  need=$(update_backup_need_bytes "${previous}")
  want=$(( need + need / 10 + UPDATE_BACKUP_MARGIN_BYTES ))
  free=$(update_backup_free_bytes)
  free=${free:-0}

  # Backups an earlier version left in the old place: keep its newest, free the rest.
  if [ "${free}" -lt "${want}" ] && [ -n "${legacy}" ] && [ -d "${legacy}" ]; then
    while IFS= read -r old; do
      [ -n "${old}" ] && rm -rf "${old}"
    done < <(ls -1dt "${legacy}"/*/ 2>/dev/null | tail -n +2)
    free=$(update_backup_free_bytes)
    free=${free:-0}
  fi

  if [ "${free}" -lt "${want}" ]; then
    local kept="There is no earlier backup."
    [ -n "${previous}" ] && kept="The earlier backup is still there (${previous})."
    [ -z "${previous}" ] && [ -n "${legacy}" ] && [ -n "$(ls -1d "${legacy}"/*/ 2>/dev/null | head -1)" ] \
      && kept="An earlier backup is still in ${legacy}."
    UPDATE_BACKUP_NOTE="not enough free disk: about $(_update_backup_human "${want}") needed, $(_update_backup_human "${free}") free"
    _update_backup_notice "⚠️ Versa AGi update ${stamp}: no backup was made before the update, because the disk is too full. About $(_update_backup_human "${want}") was needed at ${UPDATE_BACKUP_ROOT} and $(_update_backup_human "${free}") was free. The update went ahead. ${kept} Free some disk space and run the update again to get a current backup."
    return 2
  fi

  incoming="${UPDATE_BACKUP_ROOT}/.incoming-${stamp}"
  final="${UPDATE_BACKUP_ROOT}/update-${stamp}"
  mkdir -p "${incoming}"
  for item in "${UPDATE_BACKUP_ITEMS[@]+${UPDATE_BACKUP_ITEMS[@]}}"; do
    IFS='|' read -r source name kind <<< "${item}"
    mapfile -t args < <(_update_backup_rsync_args "${kind}")
    link=()
    [ -n "${previous}" ] && [ -d "${previous}/${name}" ] && link=(--link-dest="${previous}/${name}")
    mkdir -p "${incoming}/${name}"
    local copied=0
    rsync -a "${args[@]+${args[@]}}" "${link[@]+${link[@]}}" "${source}/" "${incoming}/${name}/" 2>/dev/null || copied=$?
    # 24: a file went away while it was being read (a log that rotated). Not a failed backup.
    [ "${copied}" -eq 24 ] && copied=0
    if [ "${copied}" -eq 0 ] && [ "${kind}" = "data" ]; then
      _update_backup_copy_databases "${source}" "${incoming}/${name}" || copied=1
    fi
    if [ "${copied}" -ne 0 ]; then
      rm -rf "${incoming}"
      UPDATE_BACKUP_NOTE="the copy of ${source} failed"
      _update_backup_notice "⚠️ Versa AGi update ${stamp}: the backup before the update could not be completed (copying ${source} failed). The update went ahead. $([ -n "${previous}" ] && echo "The earlier backup is still there (${previous})." || echo "There is no earlier backup.") Check the disk at ${UPDATE_BACKUP_ROOT} and run the update again to get a current backup."
      return 3
    fi
  done

  mv "${incoming}" "${final}"
  chmod 700 "${final}" 2>/dev/null || true
  # The new backup exists: the earlier ones go, here and in the old place.
  for old in "${UPDATE_BACKUP_ROOT}"/update-*/; do
    [ -d "${old}" ] || continue
    [ "${old%/}" = "${final}" ] || rm -rf "${old}"
  done
  if [ -n "${legacy}" ] && [ -d "${legacy}" ]; then
    rm -rf "${legacy}"
  fi
  rm -f "${UPDATE_BACKUP_NOTICE}" 2>/dev/null || true
  UPDATE_BACKUP_PATH="${final}"
  UPDATE_BACKUP_NOTE="$(_update_backup_human "${need}") newly written"
  return 0
}
