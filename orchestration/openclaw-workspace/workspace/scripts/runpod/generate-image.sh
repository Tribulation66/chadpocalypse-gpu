#!/bin/bash
# Generate reference images from a text prompt (Async v3.0)
# Usage: generate-image.sh "prompt" [model] [seed] [num_images] [reference_image_url] [ip_adapter_scale]
#
# Models: flux1-schnell (default, reliable), sd35-large (different style, supports reference images)
# Returns full public URLs that auto-preview in Discord.
#
# Reference images (SD 3.5 Large only):
#   generate-image.sh "a female warrior" sd35-large 42 4 "https://example.com/ref.png" 0.7
#   generate-image.sh "a female warrior" sd35-large 42 4 "/data/.openclaw/media/inbound/abc.png" 0.7

STATE_FILE="$HOME/.openclaw/workspace/scripts/runpod/pod-state.json"

# ── VPS file server for local->public URL conversion ──
VPS_MEDIA_BASE="http://srv1406477.hstgr.cloud:9090/media"

PROMPT="$1"
MODEL="${2:-flux1-schnell}"
SEED="${3:-$RANDOM}"
NUM="${4:-4}"
REF_URL="${5:-}"
IP_SCALE="${6:-}"

if [ -z "$PROMPT" ]; then
  echo "Usage: generate-image.sh \"prompt\" [model] [seed] [num_images] [reference_image_url] [ip_adapter_scale]"
  echo "Models: flux1-schnell | sd35-large"
  echo "Reference images: Only supported with sd35-large (ip_adapter_scale 0.0-1.0, default 0.5)"
  exit 1
fi

# ── Convert local paths to public URLs ──
if [ -n "$REF_URL" ]; then
  # If it's a local OpenClaw media path, convert to public URL
  if [[ "$REF_URL" == /data/.openclaw/media/* ]]; then
    # Strip the /data/.openclaw/media/ prefix and build public URL
    REL_PATH="${REF_URL#/data/.openclaw/media/}"
    REF_URL="${VPS_MEDIA_BASE}/${REL_PATH}"
    echo "Converted local path to public URL: $REF_URL"
  elif [[ "$REF_URL" != http* ]]; then
    echo "WARNING: reference_image_url '$REF_URL' is not a URL or known local path."
    echo "Expected: a URL starting with http(s):// or a local path like /data/.openclaw/media/inbound/file.png"
  fi
fi

POD_ID=$(jq -r '.pod_id' "$STATE_FILE" 2>/dev/null)
if [ -z "$POD_ID" ] || [ "$POD_ID" = "null" ]; then
  echo "ERROR: No running pod. Run pod-up.sh first."
  exit 1
fi

API_URL="https://${POD_ID}-8001.proxy.runpod.net"

echo "Generating $NUM images with $MODEL..."
echo "Prompt: $PROMPT"
if [ -n "$REF_URL" ]; then
  echo "Reference image: $REF_URL"
  echo "IP-Adapter scale: ${IP_SCALE:-0.5 (default)}"
fi

# Build JSON payload
JSON="{\"prompt\":\"$PROMPT\",\"model\":\"$MODEL\",\"seed\":$SEED,\"num_images\":$NUM,\"width\":1024,\"height\":1024"

if [ -n "$REF_URL" ]; then
  JSON="$JSON,\"reference_image_url\":\"$REF_URL\""
  if [ -n "$IP_SCALE" ]; then
    JSON="$JSON,\"ip_adapter_scale\":$IP_SCALE"
  fi
fi

JSON="$JSON}"

# Submit job (returns instantly - no timeout possible)
SUBMIT=$(curl -s --max-time 30 -X POST "$API_URL/generate" \
  -H "Content-Type: application/json" \
  -d "$JSON")

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
