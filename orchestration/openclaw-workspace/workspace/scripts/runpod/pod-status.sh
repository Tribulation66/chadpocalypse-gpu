#!/bin/bash
# Check Chadpocalypse GPU pod status
# Usage: pod-status.sh

STATE_FILE="$HOME/.openclaw/workspace/scripts/runpod/pod-state.json"
API="https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY"

if [ -z "$RUNPOD_API_KEY" ]; then
  echo "ERROR: RUNPOD_API_KEY not set."
  exit 1
fi

POD_ID=$(jq -r '.pod_id' "$STATE_FILE" 2>/dev/null)
if [ -z "$POD_ID" ] || [ "$POD_ID" = "null" ]; then
  echo "No active pod tracked. Run pod-up.sh to create one."
  exit 0
fi

RESULT=$(curl -s -X POST "$API" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"{ pod(input: { podId: \\\"$POD_ID\\\" }) { id name desiredStatus machine { gpuDisplayName } runtime { uptimeInSeconds } containerDiskInGb volumeInGb } }\"}")

STATUS=$(echo "$RESULT" | jq -r '.data.pod.desiredStatus // "NOT_FOUND"')
GPU=$(echo "$RESULT" | jq -r '.data.pod.machine.gpuDisplayName // "?"')
UPTIME=$(echo "$RESULT" | jq -r '.data.pod.runtime.uptimeInSeconds // 0')

echo "Pod: $POD_ID"
echo "Status: $STATUS"
echo "GPU: $GPU"
echo "Uptime: ${UPTIME}s"

if [ "$STATUS" = "RUNNING" ]; then
  IMAGEGEN_URL="https://${POD_ID}-8001.proxy.runpod.net"
  HEALTH=$(curl -s --max-time 10 "$IMAGEGEN_URL/health" 2>/dev/null)
  if [ -n "$HEALTH" ]; then
    echo "ImageGen API: OK"
    echo "$HEALTH" | jq -r '"  Model loaded: \(.loaded_model // "none") | Active jobs: \(.active_jobs // 0)"'
  else
    echo "ImageGen API: Not responding (may still be loading)"
  fi
fi
