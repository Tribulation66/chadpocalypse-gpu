#!/usr/bin/env python3
"""
Chadpocalypse Image Generation API
Supports: FLUX.2 klein 4B, FLUX.1 schnell, SD 3.5 Large
Port 8001 | Docs at /docs

Models load on first use and swap on demand (only one in VRAM at a time).
"""
import os, io, gc, uuid, time, base64, traceback
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import torch
from pathlib import Path
from PIL import Image
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List
import uvicorn

app = FastAPI(title="Chadpocalypse ImageGen API", version="1.0.0")
OUTPUT_DIR = Path("/workspace/outputs/images")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Global state ──
CURRENT_MODEL = None  # name of loaded model
CURRENT_PIPE = None   # diffusers pipeline

MODELS = {
    "flux2-klein": {
        "repo": "black-forest-labs/FLUX.2-klein-4B",
        "pipeline_cls": "Flux2KleinPipeline",
        "steps": 4,
        "guidance": 1.0,
        "dtype": "bfloat16",
        "vram_gb": 13,
        "license": "Apache 2.0",
    },
    "flux1-schnell": {
        "repo": "black-forest-labs/FLUX.1-schnell",
        "pipeline_cls": "FluxPipeline",
        "steps": 4,
        "guidance": 0.0,
        "dtype": "bfloat16",
        "vram_gb": 15,
        "license": "Apache 2.0",
    },
    "sd35-large": {
        "repo": "stabilityai/stable-diffusion-3.5-large",
        "pipeline_cls": "StableDiffusion3Pipeline",
        "steps": 28,
        "guidance": 3.5,
        "dtype": "bfloat16",
        "vram_gb": 18,
        "license": "Stability Community",
    },
}


def load_model(name: str):
    global CURRENT_MODEL, CURRENT_PIPE

    if CURRENT_MODEL == name and CURRENT_PIPE is not None:
        return CURRENT_PIPE

    # Unload current
    if CURRENT_PIPE is not None:
        print(f"[IMGGEN] Unloading {CURRENT_MODEL}...")
        del CURRENT_PIPE
        CURRENT_PIPE = None
        CURRENT_MODEL = None
        gc.collect()
        torch.cuda.empty_cache()

    cfg = MODELS[name]
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
        pipe = StableDiffusion3Pipeline.from_pretrained(cfg["repo"], torch_dtype=dtype)
    else:
        raise ValueError(f"Unknown pipeline: {cfg['pipeline_cls']}")

    pipe = pipe.to("cuda")
    CURRENT_PIPE = pipe
    CURRENT_MODEL = name
    print(f"[IMGGEN] {name} loaded and ready!")
    return pipe


# ── Request / Response ──
class GenRequest(BaseModel):
    prompt: str = Field(..., description="Text prompt")
    model: str = Field("flux2-klein", description="Model: flux2-klein | flux1-schnell | sd35-large")
    seed: int = Field(42)
    width: int = Field(1024)
    height: int = Field(1024)
    num_images: int = Field(4, ge=1, le=8)
    steps: Optional[int] = Field(None, description="Override default inference steps")
    guidance_scale: Optional[float] = Field(None, description="Override default guidance")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "loaded_model": CURRENT_MODEL,
        "available_models": list(MODELS.keys()),
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
    }


@app.post("/generate")
async def generate(req: GenRequest):
    if req.model not in MODELS:
        raise HTTPException(400, f"Unknown model '{req.model}'. Choose: {list(MODELS.keys())}")

    cfg = MODELS[req.model]
    steps = req.steps or cfg["steps"]
    guidance = req.guidance_scale if req.guidance_scale is not None else cfg["guidance"]
    job_id = str(uuid.uuid4())[:8]
    start = time.time()

    try:
        pipe = load_model(req.model)
        results = []

        for i in range(req.num_images):
            img_seed = req.seed + i
            generator = torch.Generator(device="cuda").manual_seed(img_seed)

            image = pipe(
                prompt=req.prompt,
                height=req.height,
                width=req.width,
                num_inference_steps=steps,
                guidance_scale=guidance,
                generator=generator,
            ).images[0]

            filename = f"{job_id}_s{img_seed}_{req.model}.png"
            filepath = OUTPUT_DIR / filename
            image.save(str(filepath))

            results.append({
                "filename": filename,
                "url": f"/images/{filename}",
                "seed": img_seed,
            })
            print(f"[{job_id}] Image {i+1}/{req.num_images} saved: {filename}")

        elapsed = round(time.time() - start, 1)

        return JSONResponse({
            "job_id": job_id,
            "model": req.model,
            "prompt": req.prompt,
            "images": results,
            "generation_time_s": elapsed,
            "steps": steps,
            "guidance_scale": guidance,
        })

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@app.get("/images/{filename}")
async def get_image(filename: str):
    fp = OUTPUT_DIR / filename
    if not fp.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(fp, media_type="image/png")


@app.get("/list")
async def list_images():
    files = sorted(OUTPUT_DIR.glob("*.png"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {"images": [{"filename": f.name, "url": f"/images/{f.name}", "size_kb": round(f.stat().st_size/1024)} for f in files[:50]]}


if __name__ == "__main__":
    print("=" * 50)
    print("  Chadpocalypse Image Gen API")
    print("  Port 8001 | Models: flux2-klein, flux1-schnell, sd35-large")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8001)
