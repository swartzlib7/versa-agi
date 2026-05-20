#!/bin/bash
# ─────────────────────────────────────────────────────
# Versa AGi — Watchdog (Stub)
#
# The immune system — audits agent behavior, monitors
# coa-owned OS processes, enforces approval workflows.
#
# STATUS: Stub for Iteration 0 (POC)
# Full implementation in Phase 2.
# ─────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR}/logs"
LOG_FILE="${LOG_DIR}/watchdog.log"

mkdir -p "${LOG_DIR}"

log() {
  echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [WATCHDOG] $*" >> "${LOG_FILE}"
}

log "Watchdog stub invoked — full implementation pending Phase 2"

# ─── POC: Basic Process List Check ──────────────────
# In production, this will:
# 1. Monitor coa-owned LangGraph Harness processes only
# The design isolates coa so it only runs approved tools via agictl.
# It should NEVER be executing unauthorized models natively.
# For now, just log active LangGraph Harness processes for the coa user
# (In a fully hardened system, >1 could trigger an auto-kill if unexpected)
COA_USER="${VERSA_COA_USER:-coa}"
GEMINI_PROCS=$(pgrep -c -u "${COA_USER}" -f "harness.agent_harness" 2>/dev/null | tr -d '[:space:]' || echo "0")
log "Active LangGraph Harness processes (${COA_USER}): ${GEMINI_PROCS}"

log "Watchdog stub complete"
