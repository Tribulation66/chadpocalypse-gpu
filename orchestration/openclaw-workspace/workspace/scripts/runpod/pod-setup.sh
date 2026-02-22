#!/bin/bash
# Upload API server files to the running pod (one-time setup)
# Usage: pod-setup.sh
# Requires: pod to be running, runpodctl configured

STATE_FILE="$HOME/.openclaw/workspace/scripts/runpod/pod-state.json"
POD_FILES="$HOME/.openclaw/workspace/scripts/runpod/pod-files"

if [ ! -f "$STATE_FILE" ]; then
  echo "ERROR: No pod state found. Run pod-up.sh first."
  exit 1
fi

POD_ID=$(jq -r '.pod_id' "$STATE_FILE")
echo "Setting up pod $POD_ID..."

# Use runpodctl to copy files to the pod
echo "Uploading start.sh..."
runpodctl send "$POD_FILES/start.sh" --podId "$POD_ID" --path /workspace/start.sh

echo "Uploading API servers..."
runpodctl send "$POD_FILES/api/trellis_server.py" --podId "$POD_ID" --path /workspace/api/trellis_server.py
runpodctl send "$POD_FILES/api/imagegen_server.py" --podId "$POD_ID" --path /workspace/api/imagegen_server.py

echo ""
echo "=== Setup complete ==="
echo "Files uploaded to pod. On next restart, /workspace/start.sh will auto-launch both APIs."
echo "To start them NOW, SSH into the pod and run: bash /workspace/start.sh &"
