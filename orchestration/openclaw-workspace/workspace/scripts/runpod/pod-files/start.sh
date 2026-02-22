#!/bin/bash
echo "=========================================="
echo "  Chadpocalypse GPU Pod Starting"
echo "=========================================="

mkdir -p /workspace/logs

# Install Python deps (cached after first run)
pip install fastapi uvicorn python-multipart aiofiles \
    diffusers transformers accelerate sentencepiece protobuf 2>/dev/null

# Start TRELLIS.2 API on port 8000
if [ -f /workspace/api/trellis_server.py ]; then
  echo "[START] Launching TRELLIS.2 API on :8000..."
  cd /workspace && nohup python api/trellis_server.py \
    > /workspace/logs/trellis.log 2>&1 &
fi

# Start Image Gen API on port 8001
if [ -f /workspace/api/imagegen_server.py ]; then
  echo "[START] Launching ImageGen API on :8001..."
  cd /workspace && nohup python api/imagegen_server.py \
    > /workspace/logs/imagegen.log 2>&1 &
fi

echo "[START] APIs launching (model load takes 1-5 min on first call)"
echo "[START] Logs: /workspace/logs/"
sleep infinity
