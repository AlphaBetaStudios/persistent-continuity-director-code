# Persistent Continuity Director — code-only workflow

By AlphaBeta Studios. ComfyUI custom nodes and a browser workspace for planning, running, pausing, and resuming multi-clip video jobs with actual-frame handoffs and bounded continuity review.

This repository contains application code, a generic API graph example, licenses, and dependency information. It contains no project records, rendered clips, source photographs, models, prompts for an AlphaBeta production, or prior Git history.

## Install

1. Place this entire `comfyui_alpha_scene_director` folder in your ComfyUI installation's `custom_nodes` directory so `__init__.py` sits directly inside the folder.
2. Use that ComfyUI installation's Python environment. Install packages from `requirements.txt` only if they are missing; do not replace its torch build. FFmpeg and FFprobe must be available for video endpoint extraction.
3. Restart ComfyUI and refresh the browser. Add **Alpha Scene Director** and **Alpha Scene Checkpoint** from **AlphaBeta Studios / Scene Director**. The package also supplies **Alpha H3 Duration to Frames**.
4. Open the Scene Director editor. Connect your own working first/last-frame video graph, choose the director, checkpoint, and video output nodes, and upload only source images you own or are licensed to use. Validate the plan before running. Start with two clips and inspect the generated files and saved receipts.

`examples/h3-api-workflow.json` is a generic request wrapper containing an API graph under `workflow`. It assumes an existing MiniMax H3 node pack and matching locally installed model files. Replace `YOUR_INPUT_IMAGE.png` and the example prompt before running. It is a graph template, not a bundled model, media set, or portable installer.

The optional continuity gate requires a configured local scorer. Installing the UI alone does not prove identity preservation or whole-video continuity. A rejected clip pauses for review rather than silently becoming the next checkpoint.

## License

The application code is MIT licensed. See `LICENSE` and `THIRD_PARTY_NOTICES.md`. Model and media rights are separate.
