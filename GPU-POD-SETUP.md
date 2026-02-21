# Chadpocalypse GPU Pod Setup Guide

## Overview

This document describes the complete process for creating, configuring, and verifying a GPU pod on RunPod for the Chadpocalypse asset generation pipeline. Follow this guide whenever a new pod is needed (which is every time you start a work session, since stopped pods lose their GPU).

## Architecture

```
ClawdBot (VPS) → RunPod GPU Pod (A40 48GB)
                  ├── Port 8001: ImageGen API (FLUX, SD3.5)
                  └── Port 8000: TRELLIS.2 API (Image→3D mesh)
```

- **Template ID**: `dwhwpzs6ij` (camenduru/tostui-trellis2)
- **GPU**: NVIDIA A40 (48GB VRAM)
- **Container Disk**: 100GB
- **Volume Disk**: 100GB (mounted at /workspace, persistent across pod restarts)
- **Start Command**: `bash /workspace/start.sh`

## What Persists vs What Needs Reinstalling

### Persists on /workspace volume (survives pod termination):
- `/workspace/api/imagegen_server.py` — Async image generation API
- `/workspace/api/trellis_server.py` — Async mesh generation API  
- `/workspace/start.sh` — Auto-start script
- `/workspace/hf_cache/` — HuggingFace model weights (~49GB)
- `/workspace/hf_cache/token` — HuggingFace auth token backup
- `/workspace/outputs/` — Generated images and meshes

### Needs reinstalling on every new pod (container disk is ephemeral):
- Python packages: `fastapi uvicorn python-multipart aiofiles httpx diffusers transformers accelerate sentencepiece protobuf`
- HuggingFace auth: Restored from `/workspace/hf_cache/token` by `start.sh`

**NOTE**: `start.sh` handles both of these automatically on boot. No manual intervention needed.

## Lifecycle: Creating a New Pod

### Step 1: Run pod-up.sh
```bash
~/.openclaw/workspace/scripts/runpod/pod-up.sh
```

This script:
1. Checks if a pod is already running (reuses it if so)
2. Terminates any stopped/exited pod (GPU is gone anyway)
3. Creates a new pod using `podFindAndDeployOnDemand` (searches ALL RunPod datacenters)
4. Waits for RUNNING status
5. Saves pod info to `pod-state.json`

The script uses the **GraphQL API** which searches all datacenters for available GPUs. This is critical — the REST API only checks one datacenter and often fails.

### Step 2: Wait for APIs to Initialize
After the pod is RUNNING, `start.sh` auto-launches both APIs. This takes 1-5 minutes because:
- pip packages install (~30 seconds)
- HuggingFace auth restores from backup
- TRELLIS pipeline downloads/loads (~2 min on first boot)
- ImageGen models load on first request

### Step 3: Verify Health
```bash
curl https://<POD_ID>-8001.proxy.runpod.net/health
```
Expected response:
```json
{"status":"ok","loaded_model":null,"available_models":["flux2-klein","flux1-schnell","sd35-large"],"gpu":"NVIDIA A40","active_jobs":0,"total_jobs":0}
```

### Step 4: Test Image Generation
```bash
~/.openclaw/workspace/scripts/runpod/generate-image.sh "a red cube on white background" flux1-schnell 42 1
```
Expected: Returns a full URL like `https://<POD_ID>-8001.proxy.runpod.net/images/<filename>.png`

### Step 5: Delete Old Pod (if any)
Once the new pod is verified working, the old terminated pod can be cleaned up. `pod-up.sh` handles this automatically — it terminates the old stopped pod before creating a new one.

## Stopping a Pod (End of Session)

```bash
~/.openclaw/workspace/scripts/runpod/pod-down.sh
```

This **terminates** the pod entirely (not just stops it) because:
- Stopped pods can't resume (GPU gets taken by someone else)
- Stopped pods still incur storage costs
- It's cleaner to terminate and create fresh next time
- All important data is on the /workspace volume which gets recreated

**Cost**: ~$0.39/hr while running. Always terminate when done.

## Troubleshooting

### "Not enough free GPUs"
- The `podFindAndDeployOnDemand` mutation searches all datacenters. If it still fails, RunPod is genuinely out of A40s globally.
- Wait 5-10 minutes and try again, or try during off-peak hours.

### APIs not responding after pod starts
- Wait 3-5 minutes for `start.sh` to finish installing packages and launching servers.
- Check logs: SSH into pod and run `cat /workspace/logs/imagegen.log`

### "Cannot copy out of meta tensor" error
- This means HuggingFace authentication failed for a gated model.
- The HF token should auto-restore from `/workspace/hf_cache/token`.
- If not, manually login: `huggingface-cli login --token <TOKEN>`

### Model takes too long on first request
- First request to any model downloads weights (~15GB for flux1-schnell).
- Subsequent requests are fast because weights are cached in `/workspace/hf_cache/`.
- The async API handles this — the job stays queued while the model loads.

### 524 Timeout errors
- Should not happen with the async API. The `/generate` endpoint returns instantly.
- If it happens on `/generate`, the API server itself may not be running. Check logs.

## Available Models

| Model | ID | Speed | Quality | Access |
|-------|----|----|---------|--------|
| FLUX.1 Schnell | flux1-schnell | ~25s/img | Good | ✅ Approved |
| SD 3.5 Large | sd35-large | ~35s/img | High | ✅ Open |
| FLUX.2 Klein | flux2-klein | ~8s/img | Good | ❌ Needs HF approval |

Default: `flux1-schnell` (reliable, authenticated, good quality)

## API Reference

### ImageGen API (Port 8001)

**Submit Job**: `POST /generate`
```json
{
  "prompt": "a warrior angel with flaming sword",
  "model": "flux1-schnell",
  "seed": 42,
  "num_images": 4,
  "width": 1024,
  "height": 1024
}
```
Returns instantly with `job_id` and `eta_seconds`.

**Check Status**: `GET /status/{job_id}`
Returns `queued`, `running`, `complete`, or `failed`.
When complete, includes `images` array with URLs.

**Get Image**: `GET /images/{filename}`
Direct image download.

**Health Check**: `GET /health`

### TRELLIS.2 API (Port 8000)

**Submit Mesh Job**: `POST /generate` (multipart form)
- `image`: PNG file upload
- `seed`: int (default 42)
- `target_face_count`: int (default 10000)
- `texture_size`: int (default 2048)

Returns instantly with `job_id`.

**Check Status**: `GET /status/{job_id}`
**Download Mesh**: `GET /meshes/{filename}`
**Health Check**: `GET /health`

## Important Notes

- The pod ID changes every time a new pod is created. Always use `pod-state.json` for the current pod ID.
- Image URLs are public and accessible via the RunPod proxy: `https://<POD_ID>-8001.proxy.runpod.net/images/<filename>.png`
- Discord auto-previews these URLs when pasted in chat.
- The generate-image.sh script outputs full URLs ready for Discord.
