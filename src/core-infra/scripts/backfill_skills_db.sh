#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Skills DB Backfill (deprecated wrapper)
#
# Delegates to reconcile_skills_db.py. Prefer:
#   sudo ./setup.sh --update
# or:
#   sudo python3 reconcile_skills_db.py
# ─────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${SCRIPT_DIR}/reconcile_skills_db.py" "$@"
