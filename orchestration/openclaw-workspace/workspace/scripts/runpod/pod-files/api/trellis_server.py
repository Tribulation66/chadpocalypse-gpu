#!/usr/bin/env python3
"""
Chadpocalypse TRELLIS.2 API Server
Image-to-3D mesh generation.
Port 8000 | Docs at /docs
"""
import os, io, gc, uuid, time, traceback
os.environ['OPENCV_IO_ENABLE_OPENEXR'] = '1'
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

import torch
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

app = FastAPI(title="Chadpocalypse TRELLIS.2 API", version="1.0.0")

PIPELINE = None
OUTPUT_DIR = Path("/workspace/outputs/meshes")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_pipeline():
    global PIPELINE
    if PIPELINE is None:
        print("[TRELLIS2] Loading pipeline (first time downloads ~12GB)...")
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        PIPELINE = Trellis2ImageTo3DPipeline.from_pretrained("microsoft/TRELLIS.2-4B")
        PIPELINE.cuda()
        print("[TRELLIS2] Ready!")
    return PIPELINE


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "model_loaded": PIPELINE is not None,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
    }


@app.post("/generate")
async def generate_mesh(
    image: UploadFile = File(...),
    seed: int = Form(42),
    target_face_count: int = Form(10000),
    texture_size: int = Form(2048),
    remesh: bool = Form(True),
):
    job_id = str(uuid.uuid4())[:8]
    start = time.time()

    try:
        from PIL import Image as PILImage
        import o_voxel

        pil_image = PILImage.open(io.BytesIO(await image.read())).convert("RGBA")
        print(f"[{job_id}] Generating mesh from {pil_image.size} image...")

        pipeline = get_pipeline()

        with torch.no_grad():
            mesh = pipeline.run(pil_image, seed=seed)[0]
            mesh.simplify(16777216)

            glb = o_voxel.postprocess.to_glb(
                vertices=mesh.vertices,
                faces=mesh.faces,
                attr_volume=mesh.attrs,
                coords=mesh.coords,
                attr_layout=mesh.layout,
                voxel_size=mesh.voxel_size,
                aabb=[[-0.5,-0.5,-0.5],[0.5,0.5,0.5]],
                decimation_target=target_face_count,
                texture_size=texture_size,
                remesh=remesh,
                remesh_band=1,
                remesh_project=0,
                verbose=True,
            )

        output_path = OUTPUT_DIR / f"{job_id}.glb"
        glb.export(str(output_path), extension_webp=True)

        elapsed = round(time.time() - start, 1)
        print(f"[{job_id}] Done! {elapsed}s → {output_path}")

        del mesh, glb; gc.collect(); torch.cuda.empty_cache()

        return JSONResponse({
            "job_id": job_id,
            "glb_url": f"/meshes/{job_id}.glb",
            "generation_time_s": elapsed,
            "target_face_count": target_face_count,
            "texture_size": texture_size,
            "seed": seed,
        })

    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@app.get("/meshes/{filename}")
async def download_mesh(filename: str):
    fp = OUTPUT_DIR / filename
    if not fp.exists():
        raise HTTPException(404, "Not found")
    return FileResponse(fp, media_type="model/gltf-binary", filename=filename)


@app.get("/list")
async def list_meshes():
    files = sorted(OUTPUT_DIR.glob("*.glb"), key=lambda f: f.stat().st_mtime, reverse=True)
    return {"meshes": [{"filename": f.name, "url": f"/meshes/{f.name}", "size_mb": round(f.stat().st_size/1e6, 2)} for f in files[:50]]}


if __name__ == "__main__":
    print("=" * 50)
    print("  Chadpocalypse TRELLIS.2 API | Port 8000")
    print("=" * 50)
    print("[STARTUP] Pre-loading pipeline...")
    get_pipeline()
    uvicorn.run(app, host="0.0.0.0", port=8000)
