#!/bin/bash

# Heartbeat check for Chadpocalypse GPU Pod
# Automatically pauses the pod if it's running and the agent has been idle for an hour (implied by heartbeat trigger).

STATE_FILE="$HOME/.openclaw/workspace/scripts/runpod/pod-state.json"
POD_DOWN_SCRIPT="$HOME/.openclaw/workspace/scripts/runpod/pod-down.sh"
POD_STATUS_SCRIPT="$HOME/.openclaw/workspace/scripts/runpod/pod-status.sh"

# Ensure RUNPOD_API_KEY is sourced
if [ -f "$HOME/.bashrc" ]; then
  source "$HOME/.bashrc"
fi

if [ -f "$STATE_FILE" ]; then
  # Get current pod status
  POD_STATUS=$(bash -c 'source ~/.bashrc && "$POD_STATUS_SCRIPT"' 2>/dev/null)
  CURRENT_STATUS=$(echo "$POD_STATUS" | jq -r '.status // "UNKNOWN"')
  POD_ID=$(echo "$POD_STATUS" | jq -r '.id // "null"')

  if [ "$CURRENT_STATUS" = "RUNNING" ]; then
    echo "[HEARTBEAT] GPU pod $POD_ID is running and no activity detected. Pausing to save costs."
    bash -c 'source ~/.bashrc && "$POD_DOWN_SCRIPT"'
    echo "[HEARTBEAT] Pod $POD_ID paused."
  else
    # If pod is not running, just acknowledge heartbeat
    echo "HEARTBEAT_OK"
  fi
else
  # No pod state file, nothing to do
  echo "HEARTBEAT_OK"
fi
