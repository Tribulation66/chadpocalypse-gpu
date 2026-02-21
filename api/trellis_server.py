#!/usr/bin/env python3
"""
Chadpocalypse TRELLIS.2 API Server (Async + Webhook)

Image-to-3D mesh generation.
Port 8000 | Docs at /docs

Features:
- Async job submission (instant response, no timeout)
- Webhook callback when job completes
- Fallback polling via /status/{job_id} with ETA
"""

import os, io, gc, uuid, time, traceback, threading
os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import torch
import httpx
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from typing import Optional, Dict
import uvicorn

app = FastAPI(title="Chadpocalypse TRELLIS.2 API", version="2.0.0")
PIPELINE = None
PIPELINE_LOCK = threading.Lock()
OUTPUT_DIR = Path("/workspace/outputs/meshes")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Temp dir for uploaded images awaiting processing
UPLOAD_DIR = Path("/workspace/outputs/mesh_inputs")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Job tracking
JOBS: Dict[str, dict] = {}

AVG_MESH_GENERATION_SECONDS = 90  # Typical time for one mesh


def get_pipeline():
    global PIPELINE
    if PIPELINE is None:
        print("[TRELLIS2] Loading pipeline (first time downloads ~12GB)...")
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        PIPELINE = Trellis2ImageTo3DPipeline.from_pretrained("microsoft/TRELLIS.2-4B")
        PIPELINE.cuda()
        print("[TRELLIS2] Ready!")
    return PIPELINE


def send_webhook(webhook_url: str, webhook_token: str, payload: dict):
    """Send job completion notification."""
    try:
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
            body = payload
            headers = {"Content-Type": "application/json"}
            if webhook_token:
                headers["Authorization"] = f"Bearer {webhook_token}"

        with httpx.Client(timeout=15) as client:
            resp = client.post(webhook_url, json=body, headers=headers)
            print(f"[WEBHOOK] Sent to {webhook_url} -> {resp.status_code}")
    except Exception as e:
        print(f"[WEBHOOK] Failed: {e}")


def format_webhook_message(payload: dict) -> str:
    """Format job result for the AI agent."""
    job = payload
    status = job.get("status", "unknown")
    job_id = job.get("job_id", "?")

    if status == "complete":
        base_url = job.get("base_url", "")
        glb_url = f"{base_url}{job['glb_url']}" if base_url else job["glb_url"]
        return (
            f"[TRELLIS Mesh Complete] job_id={job_id}\n"
            f"Mesh generated in {job.get('generation_time_s', '?')}s\n"
            f"Faces: {job.get('target_face_count', '?')}\n"
            f"GLB file: {glb_url}"
        )
    elif status == "failed":
        return (
            f"[TRELLIS Mesh Failed] job_id={job_id}\n"
            f"Error: {job.get('error', 'Unknown error')}"
        )
    else:
        return f"[TRELLIS Mesh Update] job_id={job_id} status={status}"


def run_mesh_job(job_id: str, job_data: dict):
    """Background thread: generates mesh and fires webhook on completion."""
    job = JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = time.time()

    try:
        from PIL import Image as PILImage
        import o_voxel

        # Load the saved image
        image_path = job_data["image_path"]
        pil_image = PILImage.open(image_path).convert("RGBA")
        print(f"[{job_id}] Generating mesh from {pil_image.size} image...")

        with PIPELINE_LOCK:
            pipeline = get_pipeline()
            with torch.no_grad():
                mesh = pipeline.run(pil_image, seed=job_data["seed"])[0]

            mesh.simplify(16777216)
            glb = o_voxel.postprocess.to_glb(
                vertices=mesh.vertices, faces=mesh.faces,
                attr_volume=mesh.attrs, coords=mesh.coords,
                attr_layout=mesh.layout, voxel_size=mesh.voxel_size,
                aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
                decimation_target=job_data["target_face_count"],
                texture_size=job_data["texture_size"],
                remesh=job_data["remesh"],
                remesh_band=1, remesh_project=0, verbose=True,
            )

            output_path = OUTPUT_DIR / f"{job_id}.glb"
            glb.export(str(output_path), extension_webp=True)

            del mesh, glb
            gc.collect()
            torch.cuda.empty_cache()

        elapsed = round(time.time() - job["started_at"], 1)
        job.update({
            "status": "complete",
            "glb_url": f"/meshes/{job_id}.glb",
            "generation_time_s": elapsed,
            "completed_at": time.time(),
        })
        print(f"[{job_id}] Mesh complete! {elapsed}s -> {output_path}")

        # Cleanup temp image
        try:
            os.remove(image_path)
        except Exception:
            pass

    except Exception as e:
        traceback.print_exc()
        job.update({
            "status": "failed",
            "error": str(e),
            "completed_at": time.time(),
        })
        print(f"[{job_id}] Mesh job failed: {e}")

    # Fire webhook if configured
    webhook_url = job_data.get("webhook_url")
    if webhook_url:
        webhook_payload = {
            "job_id": job_id,
            "status": job["status"],
            "glb_url": job.get("glb_url"),
            "generation_time_s": job.get("generation_time_s"),
            "target_face_count": job_data["target_face_count"],
            "texture_size": job_data["texture_size"],
            "seed": job_data["seed"],
            "error": job.get("error"),
            "base_url": job_data.get("base_url", ""),
        }
        send_webhook(
            webhook_url,
            job_data.get("webhook_token", ""),
            webhook_payload,
        )


# ── Endpoints ──

@app.get("/health")
async def health():
    active_jobs = sum(1 for j in JOBS.values() if j["status"] == "running")
    return {
        "status": "ok",
        "model_loaded": PIPELINE is not None,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
        "active_jobs": active_jobs,
        "total_jobs": len(JOBS),
    }


@app.post("/generate")
async def generate_mesh(
    image: UploadFile = File(...),
    seed: int = Form(42),
    target_face_count: int = Form(10000),
    texture_size: int = Form(2048),
    remesh: bool = Form(True),
    webhook_url: Optional[str] = Form(None),
    webhook_token: Optional[str] = Form(None),
    base_url: Optional[str] = Form(None),
):
    """Submit an async mesh generation job. Returns immediately with job_id."""
    job_id = str(uuid.uuid4())[:8]

    # Save uploaded image to temp location
    image_path = str(UPLOAD_DIR / f"{job_id}_input.png")
    contents = await image.read()
    with open(image_path, "wb") as f:
        f.write(contents)

    # Estimate time
    load_time = 0 if PIPELINE is not None else 120  # ~2min for first load
    eta_seconds = load_time + AVG_MESH_GENERATION_SECONDS

    # Create job entry
    JOBS[job_id] = {
        "status": "queued",
        "created_at": time.time(),
        "eta_seconds": eta_seconds,
        "seed": seed,
        "target_face_count": target_face_count,
        "webhook_url": webhook_url,
    }

    # Start background generation
    job_data = {
        "image_path": image_path,
        "seed": seed,
        "target_face_count": target_face_count,
        "texture_size": texture_size,
        "remesh": remesh,
        "webhook_url": webhook_url,
        "webhook_token": webhook_token,
        "base_url": base_url,
    }
    thread = threading.Thread(target=run_mesh_job, args=(job_id, job_data), daemon=True)
    thread.start()

    return JSONResponse({
        "job_id": job_id,
        "status": "queued",
        "eta_seconds": eta_seconds,
        "webhook_configured": webhook_url is not None,
        "poll_url": f"/status/{job_id}",
        "message": f"Mesh job submitted. {'Webhook will fire on completion.' if webhook_url else f'Poll /status/{job_id} after ~{eta_seconds}s.'}",
    })


@app.get("/status/{job_id}")
async def job_status(job_id: str):
    """Check status of a mesh generation job."""
    if job_id not in JOBS:
        raise HTTPException(404, f"Job '{job_id}' not found")

    job = JOBS[job_id]
    response = {
        "job_id": job_id,
        "status": job["status"],
    }

    if job["status"] == "running":
        elapsed = time.time() - job.get("started_at", job["created_at"])
        eta_remaining = max(0, job["eta_seconds"] - elapsed)
        response["elapsed_s"] = round(elapsed, 1)
        response["eta_remaining_s"] = round(eta_remaining, 1)

    elif job["status"] == "complete":
        response["glb_url"] = job["glb_url"]
        response["generation_time_s"] = job.get("generation_time_s")

    elif job["status"] == "failed":
        response["error"] = job.get("error")

    return JSONResponse(response)


@app.get("/meshes/{filename}")
async def download_mesh(filename: str):
    fp = OUTPUT_DIR / filename
    if not fp.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(fp, media_type="model/gltf-binary", filename=filename)


@app.get("/list")
async def list_meshes():
    files = sorted(OUTPUT_DIR.glob("*.glb"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {
        "meshes": [
            {"filename": f.name, "url": f"/meshes/{f.name}", "size_mb": round(f.stat().st_size / 1e6, 2)}
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
                "seed": j.get("seed"),
                "target_face_count": j.get("target_face_count"),
            }
            for jid, j in recent
        ]
    }


# ── Cleanup old jobs ──

def cleanup_old_jobs():
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
            await asyncio.sleep(600)
            cleanup_old_jobs()

    asyncio.create_task(periodic_cleanup())


if __name__ == "__main__":
    print("=" * 50)
    print("  Chadpocalypse TRELLIS.2 API v2.0 (Async + Webhook)")
    print("  Port 8000")
    print("=" * 50)
    print("[STARTUP] Pre-loading pipeline...")
    get_pipeline()
    uvicorn.run(app, host="0.0.0.0", port=8000)
