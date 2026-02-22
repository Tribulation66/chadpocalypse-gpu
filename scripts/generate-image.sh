#!/bin/bash
# Generate reference images from a text prompt (Async v2.0)
# Usage: generate-image.sh "prompt" [model] [seed] [num_images]
#
# Models: flux1-schnell (default, reliable), sd35-large (different style)
# Returns full public URLs that auto-preview in Discord.

STATE_FILE="$HOME/.openclaw/workspace/scripts/runpod/pod-state.json"
PROMPT="$1"
MODEL="${2:-flux1-schnell}"
SEED="${3:-$RANDOM}"
NUM="${4:-4}"

if [ -z "$PROMPT" ]; then
  echo "Usage: generate-image.sh \"prompt\" [model] [seed] [num_images]"
  echo "Models: flux1-schnell | sd35-large"
  exit 1
fi

POD_ID=$(jq -r '.pod_id' "$STATE_FILE" 2>/dev/null)
if [ -z "$POD_ID" ] || [ "$POD_ID" = "null" ]; then
  echo "ERROR: No running pod. Run pod-up.sh first."
  exit 1
fi

API_URL="https://${POD_ID}-8001.proxy.runpod.net"

echo "Generating $NUM images with $MODEL..."
echo "Prompt: $PROMPT"

# Submit job (returns instantly - no timeout possible)
SUBMIT=$(curl -s --max-time 30 -X POST "$API_URL/generate" \
  -H "Content-Type: application/json" \
  -d "{\"prompt\":\"$PROMPT\",\"model\":\"$MODEL\",\"seed\":$SEED,\"num_images\":$NUM,\"width\":1024,\"height\":1024}")

JOB_ID=$(echo "$SUBMIT" | jq -r '.job_id // empty')
ETA=$(echo "$SUBMIT" | jq -r '.eta_seconds // 120')

if [ -z "$JOB_ID" ]; then
  echo "ERROR: Failed to submit job."
  echo "$SUBMIT"
  exit 1
fi

echo "Job $JOB_ID submitted. Waiting ${ETA}s before first check..."
sleep "$ETA"

# Poll until complete (check every 15s, max 40 attempts = 10 min)
for i in $(seq 1 40); do
  RESULT=$(curl -s --max-time 15 "$API_URL/status/$JOB_ID")
  STATUS=$(echo "$RESULT" | jq -r '.status // "unknown"')

  if [ "$STATUS" = "complete" ]; then
    echo ""
    echo "=== IMAGES READY ==="
    for img in $(echo "$RESULT" | jq -r '.images[].url'); do
      echo "${API_URL}${img}"
    done
    echo "===================="
    echo "Generation time: $(echo "$RESULT" | jq -r '.generation_time_s')s"
    exit 0
  elif [ "$STATUS" = "failed" ]; then
    echo "ERROR: $(echo "$RESULT" | jq -r '.error')"
    exit 1
  fi

  echo "  Still generating... (check $i)"
  sleep 15
done

echo "ERROR: Timed out after 10 minutes."
exit 1
