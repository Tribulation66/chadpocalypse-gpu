#!/bin/bash
# Generate a 3D mesh from a reference image
# Usage: generate-mesh.sh /path/to/image.png [target_faces] [seed]

STATE_FILE="$HOME/.openclaw/workspace/scripts/runpod/pod-state.json"
IMAGE_PATH="$1"
TARGET_FACES="${2:-10000}"
SEED="${3:-42}"
OUTPUT_DIR="$HOME/.openclaw/workspace/outputs/meshes"
mkdir -p "$OUTPUT_DIR"

if [ -z "$IMAGE_PATH" ] || [ ! -f "$IMAGE_PATH" ]; then
  echo "Usage: generate-mesh.sh /path/to/image.png [target_faces] [seed]"
  exit 1
fi

# Get pod URL
API_URL=$(jq -r '.api_url' "$STATE_FILE" 2>/dev/null)
if [ -z "$API_URL" ] || [ "$API_URL" = "null" ]; then
  echo "ERROR: No running pod found. Run pod-up.sh first."
  exit 1
fi

echo "Generating mesh from: $IMAGE_PATH"
echo "Target faces: $TARGET_FACES, Seed: $SEED"

# Call the TRELLIS.2 API
RESULT=$(curl -s -X POST "$API_URL/generate" \
  -F "image=@$IMAGE_PATH" \
  -F "seed=$SEED" \
  -F "target_face_count=$TARGET_FACES" \
  -F "texture_size=2048")

echo "$RESULT" | jq .

# Download the GLB
GLB_URL=$(echo "$RESULT" | jq -r '.glb_url // empty')
if [ -n "$GLB_URL" ]; then
  JOB_ID=$(echo "$RESULT" | jq -r '.job_id')
  FILENAME="${JOB_ID}.glb"
  curl -s -o "$OUTPUT_DIR/$FILENAME" "${API_URL}${GLB_URL}"
  echo "Saved: $OUTPUT_DIR/$FILENAME"
else
  echo "ERROR: No GLB URL in response"
  exit 1
fi
