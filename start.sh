#!/bin/bash
echo "=========================================="
echo "  Chadpocalypse GPU Pod Starting"
echo "=========================================="

# ── Persistent dirs ──
mkdir -p /workspace/logs /workspace/outputs/images /workspace/outputs/meshes /workspace/outputs/mesh_inputs

# ── Environment ──
export HF_HOME=/workspace/hf_cache
export PYTHONPATH="/content/TRELLIS.2:$PYTHONPATH"

# ── Restore HuggingFace auth from workspace backup ──
if [ -f /workspace/hf_cache/token ]; then
    mkdir -p /content/cache
    cp /workspace/hf_cache/token /content/cache/token 2>/dev/null
    cp -r /workspace/hf_cache/stored_tokens /content/cache/stored_tokens 2>/dev/null
    echo "[START] HuggingFace auth restored from workspace"
fi

# ── Install deps (fast - most already cached) ──
pip install --break-system-packages fastapi uvicorn python-multipart aiofiles httpx \
    diffusers transformers accelerate sentencepiece protobuf 2>/dev/null

# ── Launch TRELLIS.2 API (port 8000) ──
if [ -f /workspace/api/trellis_server.py ]; then
    echo "[START] Launching TRELLIS.2 API on :8000..."
    cd /content/TRELLIS.2 && HF_HOME=/workspace/hf_cache nohup python /workspace/api/trellis_server.py \
        > /workspace/logs/trellis.log 2>&1 &
fi

# ── Launch ImageGen API (port 8001) ──
if [ -f /workspace/api/imagegen_server.py ]; then
    echo "[START] Launching ImageGen API on :8001..."
    cd /workspace && HF_HOME=/workspace/hf_cache nohup python api/imagegen_server.py \
        > /workspace/logs/imagegen.log 2>&1 &
fi

echo "[START] APIs launching (model load takes 1-5 min on first call)"
echo "[START] Logs: /workspace/logs/"
sleep infinity
