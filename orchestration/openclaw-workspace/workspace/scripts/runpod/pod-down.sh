#!/bin/bash
# Stop the Chadpocalypse GPU pod to save money
# Usage: pod-down.sh
# NOTE: Stopped pods lose their GPU. Next time, pod-up.sh will create a fresh pod.

STATE_FILE="$HOME/.openclaw/workspace/scripts/runpod/pod-state.json"
API="https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY"

if [ -z "$RUNPOD_API_KEY" ]; then
  echo "ERROR: RUNPOD_API_KEY not set."
  exit 1
fi

POD_ID=$(jq -r '.pod_id' "$STATE_FILE" 2>/dev/null)
if [ -z "$POD_ID" ] || [ "$POD_ID" = "null" ]; then
  echo "No active pod found."
  exit 0
fi

echo "Terminating pod $POD_ID..."
curl -s -X POST "$API" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"mutation { podTerminate(input: { podId: \\\"$POD_ID\\\" }) }\"}" > /dev/null

rm -f "$STATE_FILE"
echo "Pod $POD_ID terminated. Volume data will need to re-warm on next pod-up."
echo "Saved ~\$0.39/hr."
