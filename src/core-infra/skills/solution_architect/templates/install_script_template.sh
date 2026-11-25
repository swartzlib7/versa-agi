#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# {STACK_NAME} Installation Script
# ═══════════════════════════════════════════════════════════════
#
# Purpose:  {PURPOSE}
# Date:     {DATE}
# OS:       Ubuntu 24.04 LTS (x86_64)
# Author:   COA (Versa AGi Solution Architect)
# PU:       {PU_NAME}
#
# Stack:
#   - {COMPONENT_1} {VERSION_1}
#   - {COMPONENT_2} {VERSION_2}
#
# Usage:    sudo bash install_{STACK_SLUG}.sh
# Rollback: See "Rollback" section at bottom (uncomment to run)
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

# ─── Colors ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; exit 1; }

# ─── Prerequisites ────────────────────────────────────────────
echo "Checking prerequisites..."

[ "$(id -u)" -eq 0 ] || fail "This script must be run with sudo"

# Check OS
if ! grep -q "Ubuntu 24.04" /etc/os-release 2>/dev/null; then
    warn "Expected Ubuntu 24.04 — detected: $(lsb_release -ds 2>/dev/null || echo 'unknown')"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] || exit 1
fi

# Check disk space (require at least 2GB free)
AVAIL_MB=$(df -m / | awk 'NR==2 {print $4}')
[ "${AVAIL_MB}" -ge 2048 ] || fail "Insufficient disk space: ${AVAIL_MB}MB available, need 2048MB"

ok "Prerequisites verified"

# ─── Step 1: System Update ────────────────────────────────────
echo ""
echo "Step 1: Updating package index..."
apt update -qq
ok "Package index updated"

# ─── Step 2: Install {COMPONENT_1} ───────────────────────────
echo ""
echo "Step 2: Installing {COMPONENT_1} {VERSION_1}..."

# TODO: Replace with actual installation commands
# Example for Node.js via NodeSource:
#   curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
#   apt install -y nodejs
#   node --version && ok "Node.js installed: $(node --version)"

# ─── Step 3: Install {COMPONENT_2} ───────────────────────────
echo ""
echo "Step 3: Installing {COMPONENT_2} {VERSION_2}..."

# TODO: Replace with actual installation commands

# ─── Step 4: Configure Services ──────────────────────────────
echo ""
echo "Step 4: Configuring services..."

# TODO: Create systemd service files, configure ports, set permissions

# ─── Step 5: Health Check ─────────────────────────────────────
echo ""
echo "Step 5: Running health checks..."

# TODO: Verify each component is running and accessible
# Example:
#   systemctl is-active --quiet postgresql && ok "PostgreSQL: running"

# ─── Summary ──────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo " Installation Complete"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo " Components installed:"
echo "   • {COMPONENT_1} {VERSION_1}"
echo "   • {COMPONENT_2} {VERSION_2}"
echo ""
echo " Next steps:"
echo "   1. Verify services: systemctl status {service_name}"
echo "   2. Test connectivity: curl http://localhost:{port}"
echo ""

# ─── Rollback (uncomment to run) ─────────────────────────────
# echo "Rolling back installation..."
#
# # Stop services
# # systemctl stop {service_name} 2>/dev/null || true
# # systemctl disable {service_name} 2>/dev/null || true
#
# # Remove packages
# # apt remove -y {packages}
# # apt autoremove -y
#
# # Clean up config files
# # rm -rf /etc/{config_dir}
#
# echo "Rollback complete."
