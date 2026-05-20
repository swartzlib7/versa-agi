#!/bin/bash
# Logo preview — reads from logo.txt
BCYAN='\033[1;38;2;0;255;204m'
BOLD='\033[1m'
DIM='\033[2m'
WHITE='\033[1;37m'
RESET='\033[0m'

# SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# LOGO_FILE="${SCRIPT_DIR}/src/core-infra/logo.txt"
LOGO_FILE="logo.txt"

echo ""
echo -e "${BCYAN}"
if [ -f "${LOGO_FILE}" ]; then
  cat "${LOGO_FILE}"
else
  echo "  logo.txt not found at ${LOGO_FILE}"
fi
echo -e "${RESET}"
echo -e "         ${BOLD}${WHITE}V E R S A   A G i${RESET}"
echo -e "         ${DIM}Agentic General infrastructure${RESET}"
echo ""
