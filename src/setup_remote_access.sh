#!/usr/bin/env bash
# ─── Versa AGi — Remote Access Setup (Client-side) ───────────────────────────
# Run on your LOCAL machine to set up SSH key auth and an inference tunnel
# to a remote Versa AGi server. Assumes user account exists on the server.
#
# Usage: ./setup_remote_access.sh <user>@<host> [key_path] [local_port]
#
# Example:
#   ./setup_remote_access.sh s7@192.168.4.102
#   ./setup_remote_access.sh s7@192.168.4.102 ~/.ssh/id_rsa 9090
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REMOTE_PORT=8081

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <user>@<host> [key_path] [local_port]" >&2
    exit 1
fi

TARGET="$1"
LOCAL_PORT="${3:-$REMOTE_PORT}"

# Auto-detect SSH key (prefer ed25519 > rsa)
if [[ $# -ge 2 ]]; then
    KEY_PATH="$2"
elif [[ -f "$HOME/.ssh/id_ed25519" ]]; then
    KEY_PATH="$HOME/.ssh/id_ed25519"
elif [[ -f "$HOME/.ssh/id_rsa" ]]; then
    KEY_PATH="$HOME/.ssh/id_rsa"
else
    echo "ERROR: No SSH key found. Specify path as second argument." >&2
    exit 1
fi

PUB_KEY="${KEY_PATH}.pub"
if [[ ! -f "$PUB_KEY" ]]; then
    echo "ERROR: Public key not found at $PUB_KEY" >&2
    exit 1
fi

echo ""
echo "─── Versa AGi — Remote Access Setup ───"
echo ""
echo "  Target:       $TARGET"
echo "  Key:          $KEY_PATH"
echo "  Local Port:   $LOCAL_PORT"
echo "  Remote Port:  $REMOTE_PORT"
echo ""

# ─── Step 1: Ensure SSH key is installed ──────────────────────────────────────

echo "  Step 1 — Checking SSH key auth..."

if ssh -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5 -i "$KEY_PATH" "$TARGET" "exit 0" 2>/dev/null; then
    echo "  ✓ SSH key already authorized"
else
    echo "  ● Key not yet authorized. Installing (password required once)..."
    ssh-copy-id \
        -o IdentitiesOnly=yes \
        -o PreferredAuthentications=password \
        -i "$KEY_PATH" \
        "$TARGET"

    # Verify it worked
    if ! ssh -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=5 -i "$KEY_PATH" "$TARGET" "exit 0" 2>/dev/null; then
        echo ""
        echo "  ✗ FAILED: Key auth not working after install." >&2
        exit 1
    fi
    echo "  ✓ SSH key installed and verified"
fi

# ─── Step 2: Kill any existing tunnel on this port ────────────────────────────

echo ""
echo "  Step 2 — Preparing tunnel on port $LOCAL_PORT..."

EXISTING_PID=$(lsof -ti :"$LOCAL_PORT" 2>/dev/null || true)
if [[ -n "$EXISTING_PID" ]]; then
    echo "  ● Killing existing process on port $LOCAL_PORT (PID: $EXISTING_PID)"
    kill "$EXISTING_PID" 2>/dev/null || true
    sleep 1
fi

# ─── Step 3: Start tunnel in background ──────────────────────────────────────

echo ""
echo "  Step 3 — Starting inference tunnel..."

ssh -o IdentitiesOnly=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -i "$KEY_PATH" \
    -L "$LOCAL_PORT":localhost:"$REMOTE_PORT" \
    -N -f \
    "$TARGET"

# Give it a moment to establish
sleep 2

# ─── Step 4: Verify tunnel is active ─────────────────────────────────────────

echo ""
echo "  Step 4 — Verifying tunnel..."

TUNNEL_PID=$(lsof -ti :"$LOCAL_PORT" 2>/dev/null || true)
if [[ -z "$TUNNEL_PID" ]]; then
    echo ""
    echo "  ✗ FAILED: Tunnel did not start. Port $LOCAL_PORT is not listening." >&2
    echo "  Check that the inference server is running on the remote host." >&2
    exit 1
fi

# Test the endpoint
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$LOCAL_PORT/v1/models" 2>/dev/null || echo "000")

echo ""
echo "  ╭────────────────────────────────────────────────────╮"
echo "  │  ✓ Inference Tunnel Active                         │"
echo "  ├────────────────────────────────────────────────────┤"
echo "  │                                                    │"
echo "  │  Tunnel PID:   $TUNNEL_PID"
echo "  │  Endpoint:     http://localhost:$LOCAL_PORT/v1"
echo "  │  Health:       HTTP $HTTP_STATUS"
echo "  │                                                    │"
echo "  │  ── Cursor / OpenAI-Compatible IDE Setup ────────  │"
echo "  │                                                    │"
echo "  │  Base URL:     http://localhost:$LOCAL_PORT/v1"
echo "  │  API Key:      (use your INFERENCE_MASTER_KEY)     │"
echo "  │                                                    │"
echo "  │  ── Manage ──────────────────────────────────────  │"
echo "  │                                                    │"
echo "  │  Stop tunnel:  kill $TUNNEL_PID"
echo "  │  Restart:      $0 $*"
echo "  │                                                    │"
echo "  ╰────────────────────────────────────────────────────╯"
echo ""

if [[ "$HTTP_STATUS" == "000" ]]; then
    echo "  ⚠ Warning: Tunnel is open but inference server may not be running."
    echo "    Verify the server is listening on port $REMOTE_PORT on the remote host."
    echo ""
fi
