---
name: chadpocalypse-pipeline
description: "Manage the Chadpocalypse 3D character asset pipeline. Generate reference images from text descriptions, create 3D meshes from approved images, and manage the RunPod GPU pod. Use this skill when the user talks about generating characters, creating meshes, managing the GPU pod, or anything related to the Chadpocalypse game asset pipeline."
metadata: { "openclaw": { "emoji": "🎮" } }
---

# Chadpocalypse 3D Asset Pipeline

You are managing an automated 3D character creation pipeline for the game Chadpocalypse. The pipeline lives on a RunPod GPU pod that you control via bash scripts.

## Available Scripts

All scripts are in `~/scripts/runpod/` (which is `~/.openclaw/workspace/scripts/runpod/`):

| Script | Purpose |
|--------|---------|
| `pod-up.sh` | Start or resume the GPU pod (~2-5 min to boot) |
| `pod-down.sh` | Stop the pod (saves money, preserves data) |
| `pod-status.sh` | Check if pod is running |
| `generate-image.sh "prompt" [model] [seed] [num]` | Generate reference images from text |
| `generate-mesh.sh /path/to/image.png [faces] [seed]` | Generate 3D GLB from image |

Pod state is tracked in `~/scripts/runpod/pod-state.json`.
Generated images go to `~/outputs/images/`.
Generated meshes go to `~/outputs/meshes/`.

## Image Generation Models

Three models are available, each with different strengths:

| Model ID | Speed | Quality | Best For |
|----------|-------|---------|----------|
| `flux2-klein` | ~1s/image (4 steps) | Very good, 4B params | Fast iteration, default choice |
| `flux1-schnell` | ~2s/image (4 steps) | Good, battle-tested | Reliable fallback |
| `sd35-large` | ~5s/image (28 steps) | Excellent, 8.1B params | Different aesthetic, best prompt adherence |

When generating, try `flux2-klein` first for speed. If the style doesn't match, try `sd35-large` for a different look. Generate 4 images per model with different seeds so the user has variety to choose from.

## Workflow

### When the user asks to generate a character:

1. **Check pod status** first with `pod-status.sh`
2. If pod is not running, **start it** with `pod-up.sh` and tell the user "Starting GPU pod, takes 2-5 minutes..."
3. **Generate images**: Run `generate-image.sh` with a detailed prompt
   - Always add these style suffixes: "game character, full body, T-pose or A-pose, clean background, concept art, ULTRAKILL style, low poly aesthetic"
   - Default model is `flux2-klein` (fastest). Use `sd35-large` if user wants different style.
   - Example: `generate-image.sh "angel of death, six dark wings, flaming sword, game character, T-pose, clean background, ULTRAKILL style" flux2-klein 42 4`
   - Generate with at least 2 different seeds for variety
4. **Send images to user** for approval
5. **Wait for approval** — user picks their favorite image
6. **Generate mesh** from approved image with `generate-mesh.sh`
   - Default 10000 faces for game-ready mesh
   - Offer variants: 5000 (low), 10000 (mid), 50000 (high detail)
7. **Send mesh info** to user — file size, face count, generation time
8. If user approves, mark character as done in the queue

### When the user asks to stop/save money:

- Run `pod-down.sh` to stop the pod
- Remind them: "Pod stopped. Your data is saved on the volume. Resume anytime."

### When the user asks about progress/queue:

- Check `~/outputs/` for completed assets
- Report how many of the 279 characters are done

## Art Style Notes

The game's aesthetic is inspired by ULTRAKILL and Megabonk — pixelated textures on low-poly meshes. When generating prompts for reference images, aim for:
- Bold, graphic character designs
- Clear silhouettes
- Exaggerated proportions
- Biblical/apocalyptic themes
- Retro/pixel-art influenced but rendered in 3D concept art style

## Important

- **Always check pod status before trying to generate anything**
- **Always stop the pod when the user is done** to save money (~$0.39/hr for A40)
- The pod takes 2-5 minutes to start. Warn the user.
- If the API is not responding on a running pod, the API server may need to be started manually. Tell the user.
- Image generation is fast (seconds). Mesh generation takes 15-60 seconds per mesh.
