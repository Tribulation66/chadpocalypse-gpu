#!/bin/bash
# Spin up the Chadpocalypse GPU pod (or resume if stopped)
# Uses GraphQL API (podFindAndDeployOnDemand) which searches ALL datacenters
# Usage: pod-up.sh

TEMPLATE_ID="${RUNPOD_TEMPLATE_ID:-dwhwpzs6ij}"
POD_NAME="chadpocalypse-gpu"
GPU_TYPE="NVIDIA A40"
CONTAINER_DISK=100
VOLUME_DISK=100
STATE_FILE="$HOME/.openclaw/workspace/scripts/runpod/pod-state.json"
API="https://api.runpod.io/graphql?api_key=$RUNPOD_API_KEY"

if [ -z "$RUNPOD_API_KEY" ]; then
  echo "ERROR: RUNPOD_API_KEY not set. Source ~/.bashrc first."
  exit 1
fi

# Check if we already have a running pod
if [ -f "$STATE_FILE" ]; then
  POD_ID=$(jq -r '.pod_id' "$STATE_FILE" 2>/dev/null)
  if [ -n "$POD_ID" ] && [ "$POD_ID" != "null" ]; then
    STATUS=$(curl -s -X POST "$API" \
      -H "Content-Type: application/json" \
      -d "{\"query\":\"{ pod(input: { podId: \\\"$POD_ID\\\" }) { id desiredStatus } }\"}" \
      | jq -r '.data.pod.desiredStatus // "NOT_FOUND"')

    if [ "$STATUS" = "RUNNING" ]; then
      echo "Pod $POD_ID is already running."
      cat "$STATE_FILE"
      exit 0
    elif [ "$STATUS" = "EXITED" ] || [ "$STATUS" = "STOPPED" ]; then
      echo "Old pod $POD_ID is stopped. GPU likely unavailable."
      echo "Terminating old pod and creating fresh one..."
      curl -s -X POST "$API" \
        -H "Content-Type: application/json" \
        -d "{\"query\":\"mutation { podTerminate(input: { podId: \\\"$POD_ID\\\" }) }\"}" > /dev/null
      rm -f "$STATE_FILE"
      sleep 3
    else
      echo "Pod $POD_ID status: $STATUS. Creating new pod..."
      rm -f "$STATE_FILE"
    fi
  fi
fi

# Create new pod using podFindAndDeployOnDemand (searches ALL datacenters)
echo "Creating new pod with $GPU_TYPE..."
RESULT=$(curl -s -X POST "$API" \
  -H "Content-Type: application/json" \
  -d "{\"query\":\"mutation { podFindAndDeployOnDemand(input: { name: \\\"$POD_NAME\\\", templateId: \\\"$TEMPLATE_ID\\\", gpuTypeId: \\\"$GPU_TYPE\\\", gpuCount: 1, containerDiskInGb: $CONTAINER_DISK, volumeInGb: $VOLUME_DISK, volumeMountPath: \\\"/workspace\\\", startJupyter: false, startSsh: true }) { id name desiredStatus machine { gpuDisplayName } } }\"}")

POD_ID=$(echo "$RESULT" | jq -r '.data.podFindAndDeployOnDemand.id // empty')

if [ -z "$POD_ID" ]; then
  ERROR=$(echo "$RESULT" | jq -r '.errors[0].message // empty')
  echo "ERROR: Failed to create pod."
  if [ -n "$ERROR" ]; then
    echo "Reason: $ERROR"
  else
    echo "$RESULT" | jq .
  fi
  exit 1
fi

echo "Pod created: $POD_ID"

# Save initial state
echo "{\"pod_id\": \"$POD_ID\", \"name\": \"$POD_NAME\", \"gpu\": \"$GPU_TYPE\"}" > "$STATE_FILE"

# Wait for pod to be running
echo "Waiting for pod to be ready..."
for i in $(seq 1 60); do
  STATUS=$(curl -s -X POST "$API" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"{ pod(input: { podId: \\\"$POD_ID\\\" }) { id desiredStatus runtime { uptimeInSeconds ports { ip isIpPublic privatePort publicPort type } } } }\"}" \
    | jq -r '.data.pod.desiredStatus // "PENDING"')

  if [ "$STATUS" = "RUNNING" ]; then
    echo ""
    echo "=== POD IS READY ==="
    echo "Pod ID: $POD_ID"
    echo "API Base: https://${POD_ID}-8000.proxy.runpod.net"
    echo "Image Gen: https://${POD_ID}-8001.proxy.runpod.net"
    echo "===================="

    # Update state file with URLs
    cat > "$STATE_FILE" << STATEOF
{
  "pod_id": "$POD_ID",
  "name": "$POD_NAME",
  "gpu": "$GPU_TYPE",
  "status": "RUNNING",
  "api_url": "https://${POD_ID}-8000.proxy.runpod.net",
  "imagegen_url": "https://${POD_ID}-8001.proxy.runpod.net"
}
STATEOF
    cat "$STATE_FILE"

    echo ""
    echo "NOTE: APIs take 1-5 min to load on first boot."
    echo "Check health: curl https://${POD_ID}-8001.proxy.runpod.net/health"
    exit 0
  fi

  echo "  Status: $STATUS (attempt $i/60)..."
  sleep 10
done

echo "ERROR: Pod did not become ready in 10 minutes"
exit 1
