#!/usr/bin/env python3
"""
Chadpocalypse Image Generation API (Async + Webhook)

Supports: FLUX.2 klein 4B, FLUX.1 schnell, SD 3.5 Large
Port 8001 | Docs at /docs

Features:
- Async job submission (instant response, no timeout)
- Webhook callback when job completes (zero polling needed)
- Fallback polling via /status/{job_id} with ETA
- Models load on first use and swap on demand (one in VRAM at a time)
- IP-Adapter reference image support for SD 3.5 Large
"""

import os, io, gc, uuid, time, traceback, threading
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import torch
import httpx
from pathlib import Path
from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
import uvicorn

app = FastAPI(title="Chadpocalypse ImageGen API", version="3.0.0")

OUTPUT_DIR = Path("/workspace/outputs/images")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Global state ──
CURRENT_MODEL = None
CURRENT_PIPE = None
IP_ADAPTER_LOADED = False  # Track whether IP-Adapter is currently loaded on the pipe
MODEL_LOCK = threading.Lock()

# Job tracking
JOBS: Dict[str, dict] = {}

# ── IP-Adapter config for SD 3.5 Large ──
IP_ADAPTER_CONFIG = {
    "image_encoder_id": "google/siglip-so400m-patch14-384",
    "ip_adapter_id": "guiyrt/InstantX-SD3.5-Large-IP-Adapter-diffusers",
    "default_scale": 0.5,
}

MODELS = {
    "flux2-klein": {
        "repo": "black-forest-labs/FLUX.2-klein-4B",
        "pipeline_cls": "Flux2KleinPipeline",
        "steps": 4,
        "guidance": 1.0,
        "dtype": "bfloat16",
        "vram_gb": 13,
        "license": "Apache 2.0",
        "avg_seconds_per_image": 8,
        "supports_ip_adapter": False,
    },
    "flux1-schnell": {
        "repo": "black-forest-labs/FLUX.1-schnell",
        "pipeline_cls": "FluxPipeline",
        "steps": 4,
        "guidance": 0.0,
        "dtype": "bfloat16",
        "vram_gb": 15,
        "license": "Apache 2.0",
        "avg_seconds_per_image": 25,
        "supports_ip_adapter": False,
    },
    "sd35-large": {
        "repo": "stabilityai/stable-diffusion-3.5-large",
        "pipeline_cls": "StableDiffusion3Pipeline",
        "steps": 28,
        "guidance": 3.5,
        "dtype": "bfloat16",
        "vram_gb": 18,
        "license": "Stability Community",
        "avg_seconds_per_image": 35,
        "supports_ip_adapter": True,
    },
}


def load_model(name: str, need_ip_adapter: bool = False):
    """Load a model, swapping out current if needed. Thread-safe.
    
    If need_ip_adapter=True and the model supports it, also loads the
    IP-Adapter weights and image encoder.
    """
    global CURRENT_MODEL, CURRENT_PIPE, IP_ADAPTER_LOADED

    cfg = MODELS[name]
    
    # Check if we can reuse the current pipe
    if CURRENT_MODEL == name and CURRENT_PIPE is not None:
        # If we need IP-Adapter but it's not loaded, we need to load it
        if need_ip_adapter and cfg["supports_ip_adapter"] and not IP_ADAPTER_LOADED:
            _load_ip_adapter_on_pipe(CURRENT_PIPE)
        return CURRENT_PIPE

    # Unload current
    if CURRENT_PIPE is not None:
        print(f"[IMGGEN] Unloading {CURRENT_MODEL}...")
        if IP_ADAPTER_LOADED:
            try:
                CURRENT_PIPE.unload_ip_adapter()
            except Exception:
                pass
            IP_ADAPTER_LOADED = False
        del CURRENT_PIPE
        CURRENT_PIPE = None
        CURRENT_MODEL = None
        gc.collect()
        torch.cuda.empty_cache()

    dtype = torch.bfloat16 if cfg["dtype"] == "bfloat16" else torch.float16
    print(f"[IMGGEN] Loading {name} ({cfg['repo']})...")

    if cfg["pipeline_cls"] == "Flux2KleinPipeline":
        from diffusers import Flux2KleinPipeline
        pipe = Flux2KleinPipeline.from_pretrained(cfg["repo"], torch_dtype=dtype)
    elif cfg["pipeline_cls"] == "FluxPipeline":
        from diffusers import FluxPipeline
        pipe = FluxPipeline.from_pretrained(cfg["repo"], torch_dtype=dtype)
    elif cfg["pipeline_cls"] == "StableDiffusion3Pipeline":
        from diffusers import StableDiffusion3Pipeline
        if need_ip_adapter and cfg["supports_ip_adapter"]:
            # Load with image encoder from the start for IP-Adapter
            from transformers import SiglipVisionModel, SiglipImageProcessor
            feature_extractor = SiglipImageProcessor.from_pretrained(
                IP_ADAPTER_CONFIG["image_encoder_id"], torch_dtype=dtype
            )
            image_encoder = SiglipVisionModel.from_pretrained(
                IP_ADAPTER_CONFIG["image_encoder_id"], torch_dtype=dtype
            )
            pipe = StableDiffusion3Pipeline.from_pretrained(
                cfg["repo"],
                torch_dtype=dtype,
                feature_extractor=feature_extractor,
                image_encoder=image_encoder,
            )
        else:
            pipe = StableDiffusion3Pipeline.from_pretrained(cfg["repo"], torch_dtype=dtype)
    else:
        raise ValueError(f"Unknown pipeline: {cfg['pipeline_cls']}")

    # Flux2Klein can't use cpu_offload (meta tensor issue), load direct to GPU
    if cfg['pipeline_cls'] == 'Flux2KleinPipeline':
        pipe = pipe.to('cuda')
    else:
        pipe.enable_model_cpu_offload()

    # Load IP-Adapter if needed
    if need_ip_adapter and cfg["supports_ip_adapter"]:
        _load_ip_adapter_on_pipe(pipe)

    CURRENT_PIPE = pipe
    CURRENT_MODEL = name
    print(f"[IMGGEN] {name} loaded and ready! (IP-Adapter: {IP_ADAPTER_LOADED})")
    return pipe


def _load_ip_adapter_on_pipe(pipe):
    """Load IP-Adapter weights onto an already-loaded SD3.5 pipeline."""
    global IP_ADAPTER_LOADED
    try:
        print(f"[IMGGEN] Loading IP-Adapter from {IP_ADAPTER_CONFIG['ip_adapter_id']}...")
        pipe.load_ip_adapter(IP_ADAPTER_CONFIG["ip_adapter_id"])
        # Exclude image encoder from CPU offload to avoid errors
        if hasattr(pipe, '_exclude_from_cpu_offload'):
            pipe._exclude_from_cpu_offload.append("image_encoder")
        IP_ADAPTER_LOADED = True
        print(f"[IMGGEN] IP-Adapter loaded successfully!")
    except Exception as e:
        print(f"[IMGGEN] WARNING: Failed to load IP-Adapter: {e}")
        traceback.print_exc()
        IP_ADAPTER_LOADED = False


def download_reference_image(url: str) -> Image.Image:
    """Download a reference image from a URL and return as PIL Image."""
    print(f"[IMGGEN] Downloading reference image from {url[:100]}...")
    
    # Check if it's a local file path on the pod
    if url.startswith("/workspace/") or url.startswith("/content/"):
        local_path = Path(url)
        if local_path.exists():
            img = Image.open(local_path).convert("RGB")
            print(f"[IMGGEN] Loaded local reference image: {url}")
            return img
        else:
            raise FileNotFoundError(f"Local reference image not found: {url}")
    
    # Download from URL (Discord CDN, etc.)
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
    
    # Resize to 1024x1024 for best IP-Adapter results
    img = img.resize((1024, 1024), Image.LANCZOS)
    print(f"[IMGGEN] Reference image downloaded and resized to 1024x1024")
    return img


def send_webhook(webhook_url: str, webhook_token: str, payload: dict):
    """Send job completion notification to OpenClaw webhook."""
    try:
        # Format message for OpenClaw /hooks/wake
        if "/hooks/" in webhook_url:
            # OpenClaw webhook format
            body = {
                "text": format_webhook_message(payload),
                "mode": "now",
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {webhook_token}",
            }
        else:
            # Generic webhook - send raw payload
            body = payload
            headers = {
                "Content-Type": "application/json",
            }
            if webhook_token:
                headers["Authorization"] = f"Bearer {webhook_token}"

        with httpx.Client(timeout=15) as client:
            resp = client.post(webhook_url, json=body, headers=headers)
            print(f"[WEBHOOK] Sent to {webhook_url} -> {resp.status_code}")
    except Exception as e:
        print(f"[WEBHOOK] Failed to send to {webhook_url}: {e}")


def format_webhook_message(payload: dict) -> str:
    """Format job result as a readable message for the AI agent."""
    job = payload
    status = job.get("status", "unknown")
    job_id = job.get("job_id", "?")

    if status == "complete":
        images = job.get("images", [])
        model = job.get("model", "?")
        prompt = job.get("prompt", "?")
        elapsed = job.get("generation_time_s", "?")
        base_url = job.get("base_url", "")
        ref_used = job.get("reference_image_used", False)

        img_lines = []
        for img in images:
            url = f"{base_url}{img['url']}" if base_url else img["url"]
            img_lines.append(f"  - {img['filename']} (seed {img['seed']}): {url}")

        ref_line = f"\nReference Image: Yes (scale={job.get('ip_adapter_scale', 'N/A')})" if ref_used else ""

        return (
            f"[ImageGen Job Complete] job_id={job_id}\n"
            f"Model: {model}\n"
            f"Prompt: {prompt}{ref_line}\n"
            f"Generated {len(images)} images in {elapsed}s\n"
            f"Images:\n" + "\n".join(img_lines)
        )
    elif status == "failed":
        return (
            f"[ImageGen Job Failed] job_id={job_id}\n"
            f"Error: {job.get('error', 'Unknown error')}"
        )
    else:
        return f"[ImageGen Job Update] job_id={job_id} status={status}"


def run_generation_job(job_id: str, req_data: dict):
    """Background thread: generates images and fires webhook on completion."""
    job = JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = time.time()

    try:
        model_name = req_data["model"]
        cfg = MODELS[model_name]
        steps = req_data.get("steps") or cfg["steps"]
        guidance = req_data.get("guidance_scale")
        if guidance is None:
            guidance = cfg["guidance"]

        # Download reference image if provided
        ref_image = None
        ref_url = req_data.get("reference_image_url")
        if ref_url:
            if not cfg["supports_ip_adapter"]:
                raise ValueError(
                    f"Model '{model_name}' does not support reference images. "
                    f"Use 'sd35-large' for reference image support."
                )
            ref_image = download_reference_image(ref_url)

        with MODEL_LOCK:
            pipe = load_model(model_name, need_ip_adapter=ref_image is not None)

            # Set IP-Adapter scale if using reference image
            if ref_image is not None and IP_ADAPTER_LOADED:
                scale = req_data.get("ip_adapter_scale") or IP_ADAPTER_CONFIG["default_scale"]
                pipe.set_ip_adapter_scale(scale)
                print(f"[{job_id}] IP-Adapter scale set to {scale}")

            results = []
            for i in range(req_data["num_images"]):
                img_seed = req_data["seed"] + i
                generator = torch.Generator(device="cuda").manual_seed(img_seed)

                # Build pipe kwargs
                pipe_kwargs = dict(
                    prompt=req_data["prompt"],
                    height=req_data["height"],
                    width=req_data["width"],
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                    generator=generator,
                )

                # Add reference image if available
                if ref_image is not None and IP_ADAPTER_LOADED:
                    pipe_kwargs["ip_adapter_image"] = ref_image

                image = pipe(**pipe_kwargs).images[0]

                filename = f"{job_id}_s{img_seed}_{model_name}.png"
                filepath = OUTPUT_DIR / filename
                image.save(str(filepath))
                results.append({
                    "filename": filename,
                    "url": f"/images/{filename}",
                    "seed": img_seed,
                })
                job["images_completed"] = i + 1
                print(f"[{job_id}] Image {i+1}/{req_data['num_images']} saved: {filename}")

        elapsed = round(time.time() - job["started_at"], 1)
        job.update({
            "status": "complete",
            "images": results,
            "generation_time_s": elapsed,
            "completed_at": time.time(),
        })
        print(f"[{job_id}] Job complete! {len(results)} images in {elapsed}s")

    except Exception as e:
        traceback.print_exc()
        job.update({
            "status": "failed",
            "error": str(e),
            "completed_at": time.time(),
        })
        print(f"[{job_id}] Job failed: {e}")

    # Fire webhook if configured
    webhook_url = req_data.get("webhook_url")
    if webhook_url:
        webhook_payload = {
            "job_id": job_id,
            "status": job["status"],
            "model": req_data["model"],
            "prompt": req_data["prompt"],
            "images": job.get("images", []),
            "generation_time_s": job.get("generation_time_s"),
            "error": job.get("error"),
            "base_url": req_data.get("base_url", ""),
            "reference_image_used": req_data.get("reference_image_url") is not None,
            "ip_adapter_scale": req_data.get("ip_adapter_scale"),
        }
        send_webhook(
            webhook_url,
            req_data.get("webhook_token", ""),
            webhook_payload,
        )


# ── Request / Response Models ──

class GenRequest(BaseModel):
    prompt: str = Field(..., description="Text prompt")
    model: str = Field("flux1-schnell", description="Model: flux2-klein | flux1-schnell | sd35-large")
    seed: int = Field(42)
    width: int = Field(1024)
    height: int = Field(1024)
    num_images: int = Field(4, ge=1, le=8)
    steps: Optional[int] = Field(None, description="Override default inference steps")
    guidance_scale: Optional[float] = Field(None, description="Override default guidance")
    # Reference image (IP-Adapter) - SD 3.5 Large only
    reference_image_url: Optional[str] = Field(
        None,
        description="URL to a reference image for style/content guidance (SD 3.5 Large only). "
                    "Accepts Discord CDN URLs, web URLs, or local pod paths (/workspace/...)."
    )
    ip_adapter_scale: Optional[float] = Field(
        None,
        description="How strongly the reference image influences output (0.0-1.0, default 0.5). "
                    "Higher = more like reference, lower = more creative freedom."
    )
    # Webhook config
    webhook_url: Optional[str] = Field(None, description="URL to POST results when job completes (e.g. OpenClaw /hooks/wake)")
    webhook_token: Optional[str] = Field(None, description="Bearer token for webhook auth")
    base_url: Optional[str] = Field(None, description="Base URL to prepend to image paths in webhook (e.g. https://pod-id-8001.proxy.runpod.net)")


# ── Endpoints ──

@app.get("/health")
async def health():
    active_jobs = sum(1 for j in JOBS.values() if j["status"] == "running")
    return {
        "status": "ok",
        "loaded_model": CURRENT_MODEL,
        "ip_adapter_loaded": IP_ADAPTER_LOADED,
        "available_models": list(MODELS.keys()),
        "ip_adapter_models": [k for k, v in MODELS.items() if v["supports_ip_adapter"]],
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
        "active_jobs": active_jobs,
        "total_jobs": len(JOBS),
    }


@app.post("/generate")
async def generate(req: GenRequest):
    """Submit an async image generation job. Returns immediately with job_id."""
    if req.model not in MODELS:
        raise HTTPException(400, f"Unknown model '{req.model}'. Choose: {list(MODELS.keys())}")

    # Validate reference image usage
    if req.reference_image_url and not MODELS[req.model]["supports_ip_adapter"]:
        raise HTTPException(
            400,
            f"Model '{req.model}' does not support reference images. "
            f"Use 'sd35-large' for reference image support."
        )

    if req.ip_adapter_scale is not None and (req.ip_adapter_scale < 0.0 or req.ip_adapter_scale > 1.0):
        raise HTTPException(400, "ip_adapter_scale must be between 0.0 and 1.0")

    cfg = MODELS[req.model]
    job_id = str(uuid.uuid4())[:8]

    # Estimate completion time
    load_time = 0 if CURRENT_MODEL == req.model else 60
    if req.reference_image_url and not IP_ADAPTER_LOADED:
        load_time += 30  # Extra time for IP-Adapter + image encoder loading
    gen_time = cfg["avg_seconds_per_image"] * req.num_images
    eta_seconds = load_time + gen_time

    # Create job entry
    JOBS[job_id] = {
        "status": "queued",
        "model": req.model,
        "prompt": req.prompt,
        "num_images": req.num_images,
        "images_completed": 0,
        "images": [],
        "created_at": time.time(),
        "eta_seconds": eta_seconds,
        "webhook_url": req.webhook_url,
        "reference_image": req.reference_image_url is not None,
    }

    # Start background generation
    req_data = req.model_dump()
    thread = threading.Thread(target=run_generation_job, args=(job_id, req_data), daemon=True)
    thread.start()

    return JSONResponse({
        "job_id": job_id,
        "status": "queued",
        "model": req.model,
        "prompt": req.prompt,
        "num_images": req.num_images,
        "eta_seconds": eta_seconds,
        "reference_image": req.reference_image_url is not None,
        "ip_adapter_scale": req.ip_adapter_scale or (IP_ADAPTER_CONFIG["default_scale"] if req.reference_image_url else None),
        "webhook_configured": req.webhook_url is not None,
        "poll_url": f"/status/{job_id}",
        "message": f"Job submitted. {'Webhook will fire on completion.' if req.webhook_url else f'Poll /status/{job_id} after ~{eta_seconds}s.'}",
    })


@app.get("/status/{job_id}")
async def job_status(job_id: str):
    """Check status of a generation job. Use as fallback if webhook not configured."""
    if job_id not in JOBS:
        raise HTTPException(404, f"Job '{job_id}' not found")

    job = JOBS[job_id]
    response = {
        "job_id": job_id,
        "status": job["status"],
        "model": job.get("model"),
        "prompt": job.get("prompt"),
        "images_completed": job.get("images_completed", 0),
        "num_images": job.get("num_images"),
    }

    if job["status"] == "running":
        elapsed = time.time() - job.get("started_at", job["created_at"])
        eta_remaining = max(0, job["eta_seconds"] - elapsed)
        response["elapsed_s"] = round(elapsed, 1)
        response["eta_remaining_s"] = round(eta_remaining, 1)

    elif job["status"] == "complete":
        response["images"] = job["images"]
        response["generation_time_s"] = job.get("generation_time_s")

    elif job["status"] == "failed":
        response["error"] = job.get("error")

    return JSONResponse(response)


@app.get("/images/{filename}")
async def get_image(filename: str):
    fp = OUTPUT_DIR / filename
    if not fp.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(fp, media_type="image/png")


@app.get("/list")
async def list_images():
    files = sorted(OUTPUT_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {
        "images": [
            {"filename": f.name, "url": f"/images/{f.name}", "size_kb": round(f.stat().st_size / 1024)}
            for f in files[:50]
        ]
    }


@app.get("/jobs")
async def list_jobs():
    """List recent jobs with their statuses."""
    recent = sorted(JOBS.items(), key=lambda x: x[1].get("created_at", 0), reverse=True)[:20]
    return {
        "jobs": [
            {
                "job_id": jid,
                "status": j["status"],
                "model": j.get("model"),
                "prompt": j.get("prompt", "")[:80],
                "num_images": j.get("num_images"),
                "images_completed": j.get("images_completed", 0),
                "reference_image": j.get("reference_image", False),
            }
            for jid, j in recent
        ]
    }


# ── Cleanup old jobs periodically ──

def cleanup_old_jobs():
    """Remove completed/failed jobs older than 1 hour to prevent memory leak."""
    cutoff = time.time() - 3600
    to_remove = [
        jid for jid, j in JOBS.items()
        if j["status"] in ("complete", "failed") and j.get("completed_at", 0) < cutoff
    ]
    for jid in to_remove:
        del JOBS[jid]
    if to_remove:
        print(f"[CLEANUP] Removed {len(to_remove)} old jobs")


@app.on_event("startup")
async def startup():
    import asyncio

    async def periodic_cleanup():
        while True:
            await asyncio.sleep(600)  # Every 10 minutes
            cleanup_old_jobs()

    asyncio.create_task(periodic_cleanup())


if __name__ == "__main__":
    print("=" * 50)
    print("  Chadpocalypse Image Gen API v3.0 (IP-Adapter)")
    print("  Port 8001 | Models: flux2-klein, flux1-schnell, sd35-large")
    print("  Reference images supported on: sd35-large")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8001)
