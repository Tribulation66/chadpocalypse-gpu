# Chadpocalypse Pipeline Setup Log

Tracking the step-by-step setup of the automated 3D character asset pipeline.

## Status: IN PROGRESS

---

## Steps Completed

### Step 2A: Install RunPod Tools
- **Status:** ✅ COMPLETE (pre-installed)
- **runpodctl version:** 1.14.15
- **API Key:** Configured in ~/.bashrc
- **Verified:** `runpodctl get pod` returns empty list (no active pods)

### Step 2B: Create RunPod Template
- **Status:** ✅ COMPLETE (pre-existing)
- **Template ID:** `dwhwpzs6ij`
- **Template Name:** `chadpocalypse-gpu`
- **Container Image:** `camenduru/tostui-trellis2`
- **GPU Type:** NVIDIA A40 (48GB VRAM)
- **Container Disk:** 100GB
- **Volume Disk:** 100GB (mounted at /workspace)
- **Start Command:** `bash /workspace/start.sh`
- **Verified via API:** Confirmed active in account

### Step 2C: Helper Scripts
- **Status:** ✅ COMPLETE (pre-installed and updated)
- **Location:** `~/.openclaw/workspace/scripts/runpod/`
- **Scripts:**
  - `pod-up.sh` (NEW: uses GraphQL to find & deploy GPU)
  - `pod-down.sh` (NEW: terminates pod instead of stopping)
  - `pod-status.sh` (check pod status)
  - `generate-image.sh` (NEW: async API, outputs public URLs)
  - `generate-mesh.sh` (generate 3D mesh)
  - `pod-setup.sh` (utility)
- **Permissions:** All executable (chmod +x)
- **Reference:** `GPU-POD-SETUP.md` provides complete guide.

---

### Step 2F: Set Up the API Servers on the Pod
- **Status:** PARTIAL COMPLETE (API scripts exist and are auto-launched by `start.sh`)
  - **Image Generation API (Port 8001):** ✅ Running and Healthy (Async with Webhook/Polling)
  - **TRELLIS.2 API (Port 8000):** ❌ Not Responding (502 error) - Pending Hugging Face approval.
- **Verification:**
  - ImageGen API `https://<POD_ID>-8001.proxy.runpod.net/health` returns `status: ok`.
  - TRELLIS.2 API `https://<POD_ID>-8000.proxy.runpod.net/health` returns `502`.
- **Next Steps:** Trellis API will be fully operational once Hugging Face access is granted.

---

## Notes & Configuration

### Operational Rules
1. **Always check pod status** before trying to generate anything
2. **Always stop the pod** when done (~$0.39/hr for A40)
3. **Pod startup time:** 2-5 minutes (warn user)
4. **API not responding?** API server may need manual start on running pod
5. **Image generation:** Fast (seconds)
6. **Mesh generation:** 15-60 seconds per mesh
7. **Cost saving:** If no message for 1 hour and pod is running, automatically pause the pod.

### API Key
- Stored in `~/.bashrc` as `RUNPOD_API_KEY`
- Also configured in `runpodctl`

### Template
- ID: `dwhwpzs6ij`
- Image: `camenduru/tostui-trellis2`

---

## Troubleshooting

*(Issues encountered and solutions)*
