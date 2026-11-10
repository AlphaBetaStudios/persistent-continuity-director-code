"""Pure plan compilation, prompt isolation and safe local persistence.

This module deliberately has no ComfyUI, network or subprocess dependency.
"""

from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile


MAX_SCENES = 12
MAX_CLIPS = 1000
MAX_FRAMES = 4097
MAX_FPS = 240
MAX_SEED = (1 << 53) - 1
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_NODES = 2000
MAX_TEXT = 50_000
CONTROL_TYPES = {"AlphaSceneDirector", "AlphaSceneCheckpoint"}
CONTROL_WIDGETS = ("first_image", "last_image", "positive", "negative", "seed", "frames", "fps", "run_id", "clip_id")
CINEMATIC_CHOICES = {
    "shot": ("Wide establishing shot", "Medium shot", "Close-up portrait", "Low-angle shot", "Overhead shot"),
    "camera": ("35mm lens, shallow depth of field", "Wide-angle lens, deep focus", "85mm portrait lens, soft background", "Handheld documentary camera"),
    "light": ("Soft window light", "Golden-hour backlight", "Hard noon sunlight", "Blue-hour practical lights"),
    "look": ("Warm cinematic film", "Cool science-fiction film", "Natural documentary color", "High-contrast noir"),
}


class PlanError(ValueError):
    """A plan, graph or saved artifact cannot safely be used."""


def _text(value, label, maximum=MAX_TEXT):
    if not isinstance(value, str) or len(value) > maximum or "\0" in value:
        raise PlanError(f"{label} must be text of at most {maximum} characters")
    return value.strip()


def _relative(relative):
    value = _text(relative, "Image or artifact path", 1024)
    if not value or value.startswith("/") or "\\" in value or ":" in value:
        raise PlanError("Paths must be relative to the configured input/output directory")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise PlanError("Path traversal and empty path components are not allowed")
    return value


def safe_path(root, relative) -> Path:
    """Resolve a relative path and reject both textual and symlink escapes."""
    value = _relative(relative)
    base = Path(root).resolve()
    path = (base / value).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise PlanError("Path escapes the configured directory") from exc
    return path


def _endpoint(value, label):
    value = _text(value, label, 1024)
    if value == "@previous":
        return value
    return _relative(value)


def _number(value, label, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanError(f"{label} must be a {'whole ' if integer else ''}number")
    if integer and not isinstance(value, int):
        raise PlanError(f"{label} must be a whole number")
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise PlanError(f"{label} must be between {minimum} and {maximum}")
    return value


def _scalar(value):
    return (value is None or isinstance(value, (str, bool, int, float))) and not (
        isinstance(value, float) and not math.isfinite(value))


def _overrides(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 200:
        raise PlanError("input_overrides must map at most 200 existing node IDs to widget values")
    result = {}
    for node_id, widgets in value.items():
        if not isinstance(node_id, str) or not node_id or len(node_id) > 128:
            raise PlanError("Override node IDs must be nonempty strings")
        if not isinstance(widgets, dict) or len(widgets) > 100:
            raise PlanError("Each input override must be an object of scalar widget values")
        result[node_id] = {}
        for name, scalar in widgets.items():
            _text(name, "Override widget name", 128)
            if not _scalar(scalar):
                raise PlanError("Overrides accept scalar widget values, never graph links or objects")
            if isinstance(scalar, str):
                _text(scalar, "Override value")
            result[node_id][name] = copy.deepcopy(scalar)
    return result


def _inherit(name, clip, scene, defaults, otherwise=None):
    for source in (clip, scene, defaults):
        if name in source and source[name] is not None:
            return source[name]
    return otherwise


def _direction(value, label):
    if value is None:
        return {}
    if not isinstance(value, dict) or any(key not in CINEMATIC_CHOICES for key in value):
        raise PlanError(f"{label} must contain only shot, camera, light and look")
    result = {}
    for key, selected in value.items():
        if selected in (None, ""):
            continue
        if not isinstance(selected, str) or selected not in CINEMATIC_CHOICES[key]:
            raise PlanError(f"{label} {key} must be one of the supported prompt choices")
        result[key] = selected
    return result


def _cinematic_prompt(scene_prompt, direction):
    return ". ".join(part for part in (
        direction.get("shot"), scene_prompt, direction.get("camera"),
        direction.get("light"), direction.get("look")) if part)


def compile_plan(plan, fallback=None) -> list[dict]:
    """Expand a frozen version-1 plan into deterministic, individually queued clips.

    Scene first_image starts that scene. Later clips carry their preceding actual
    frame unless a per-clip first_image explicitly replaces it. Seeds inherit
    literally, so callers can set per-clip seeds when variation is wanted.
    """
    if isinstance(plan, str):
        if len(plan.encode("utf-8")) > MAX_JSON_BYTES:
            raise PlanError("Plan JSON is too large")
        try:
            plan = json.loads(plan)
        except (ValueError, RecursionError) as exc:
            raise PlanError("Plan is not valid JSON") from exc
    if not isinstance(plan, dict) or plan.get("version") != 1 or isinstance(plan.get("version"), bool):
        raise PlanError("Plan must be an object with version: 1")
    fallback = fallback or {}
    if not isinstance(fallback, dict):
        raise PlanError("Widget fallback must be an object")
    _text(plan.get("project", "Untitled film"), "Project", 200)
    global_prompt = _text(plan.get("global_prompt", fallback.get("positive", "")), "Global prompt")
    global_negative = _text(plan.get("negative_prompt", fallback.get("negative", "")), "Negative prompt")
    defaults = {"frames": 81, "fps": 16, "seed": 1}
    defaults.update({key: fallback[key] for key in ("first_image", "last_image", "frames", "fps", "seed") if key in fallback})
    supplied_defaults = plan.get("defaults", {})
    if not isinstance(supplied_defaults, dict):
        raise PlanError("defaults must be an object")
    defaults.update(supplied_defaults)
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= MAX_SCENES:
        raise PlanError(f"Prepare between 1 and {MAX_SCENES} scenes")
    result, ids = [], set()
    for scene_index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            raise PlanError("Every scene must be an object")
        scene_id = _text(scene.get("id", f"s{scene_index:02}"), "Scene ID", 128)
        if not scene_id or scene_id in ids:
            raise PlanError("Scene IDs must be nonempty and unique")
        ids.add(scene_id)
        title = _text(scene.get("title", f"Scene {scene_index}"), "Scene title", 500)
        scene_prompt = _text(scene.get("prompt", ""), "Scene prompt")
        scene_direction = _direction(scene.get("direction"), "Scene direction")
        entries = scene.get("clips", 1)
        if isinstance(entries, int) and not isinstance(entries, bool):
            if not 1 <= entries <= MAX_CLIPS:
                raise PlanError(f"Scene clip count must be between 1 and {MAX_CLIPS}")
            entries = [{} for _ in range(entries)]
        if not isinstance(entries, list) or not entries or len(entries) + len(result) > MAX_CLIPS:
            raise PlanError(f"The expanded plan must contain between 1 and {MAX_CLIPS} clips")
        inherited_overrides = _overrides(scene.get("input_overrides"))
        for clip_index, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                raise PlanError("Each entry in a clips array must be an object")
            first = entry.get("first_image")
            if not first and clip_index == 1:
                first = scene.get("first_image")
            if not first:
                first = defaults.get("first_image") if not result else "@previous"
            last = next((source["last_image"] for source in (entry, scene, defaults)
                         if source.get("last_image")), None)
            first = _endpoint(first, "First image")
            last = _endpoint(last, "Last target image")
            if last == "@previous":
                raise PlanError("Every last target must be an explicit image; @previous is only a first image")
            if not result and "@previous" in (first, last):
                raise PlanError("The first clip requires explicit first and last target images")
            overrides = copy.deepcopy(inherited_overrides)
            for node_id, inputs in _overrides(entry.get("input_overrides")).items():
                overrides.setdefault(node_id, {}).update(inputs)
            direction = {**scene_direction, **_direction(entry.get("direction"), "Clip direction")}
            directed_scene_prompt = _cinematic_prompt(scene_prompt, direction)
            prompt = "\n\n".join(part for part in (global_prompt, directed_scene_prompt, _text(entry.get("prompt", ""), "Clip prompt")) if part)
            if len(prompt) > MAX_TEXT:
                raise PlanError(f"Combined clip prompt exceeds {MAX_TEXT} characters")
            if not prompt:
                raise PlanError("Every clip needs a global, scene or clip prompt")
            result.append({
                "id": f"s{scene_index:02}-c{clip_index:03}", "scene_id": scene_id, "scene_title": title,
                "prompt": prompt, "negative_prompt": _text(_inherit("negative_prompt", entry, scene, {}, global_negative), "Negative prompt"),
                "first_image": first, "last_image": last,
                "frames": _number(_inherit("frames", entry, scene, defaults), "frames", 2, MAX_FRAMES, True),
                "fps": _number(_inherit("fps", entry, scene, defaults), "fps", 0.01, MAX_FPS),
                "seed": _number(_inherit("seed", entry, scene, defaults), "seed", 0, MAX_SEED, True),
                "input_overrides": overrides,
            })
    return result


def _graph_links(workflow):
    if not isinstance(workflow, dict) or not 1 <= len(workflow) <= MAX_NODES:
        raise PlanError(f"workflow must be an API prompt object with at most {MAX_NODES} nodes")
    edges = {}
    for node_id, node in workflow.items():
        if not isinstance(node_id, str) or not node_id or len(node_id) > 128:
            raise PlanError("API workflow node IDs must be nonempty strings")
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise PlanError("Use the API prompt graph, not the browser workflow JSON")
        edges[node_id] = []
        for value in node["inputs"].values():
            if isinstance(value, list):
                if (len(value) != 2 or not isinstance(value[0], str) or value[0] not in workflow
                        or not isinstance(value[1], int) or isinstance(value[1], bool) or value[1] < 0):
                    raise PlanError(f"Node {node_id} has a malformed or missing graph connection")
                edges[node_id].append((value[0], value[1]))
    # Iterative DFS avoids a recursion failure on a legitimate long API graph.
    colors = {}
    for start in edges:
        stack = [(start, False)]
        while stack:
            current, leaving = stack.pop()
            if leaving:
                colors[current] = 2
                continue
            if colors.get(current) == 1:
                raise PlanError("Workflow connections contain a cycle")
            if colors.get(current) == 2:
                continue
            colors[current] = 1
            stack.append((current, True))
            for parent, _ in reversed(edges[current]):
                if colors.get(parent) == 1:
                    raise PlanError("Workflow connections contain a cycle")
                if colors.get(parent) != 2:
                    stack.append((parent, False))
    return edges


def _ancestors(edges, start):
    found, connections, stack = set(), set(), [start]
    while stack:
        current = stack.pop()
        if current in found:
            continue
        found.add(current)
        for parent, slot in edges[current]:
            connections.add((parent, slot))
            stack.append(parent)
    return found, connections


def validate_workflow(workflow, director_id, checkpoint_id, video_output_id) -> str:
    """Validate graph authority boundaries and return its timing mode.

    ``frames`` is the original image-batch workflow. ``duration`` is for native
    VIDEO generators such as H3, where the model owns the encoded frame rate.
    """
    edges = _graph_links(workflow)
    if len({director_id, checkpoint_id, video_output_id}) != 3:
        raise PlanError("Select three different Director, Checkpoint and video-output nodes")
    for node_id, expected in ((director_id, "AlphaSceneDirector"), (checkpoint_id, "AlphaSceneCheckpoint")):
        if node_id not in workflow or workflow[node_id]["class_type"] != expected:
            raise PlanError(f"Select an existing {expected} node")
    if video_output_id not in workflow or workflow[video_output_id]["class_type"] in CONTROL_TYPES:
        raise PlanError("Select the existing workflow's video-output node")
    director = workflow[director_id]["inputs"]
    for name in CONTROL_WIDGETS:
        if name not in director or not _scalar(director[name]):
            raise PlanError(f"Director {name} must remain a scalar widget so each clip can replace it")
    checkpoint = workflow[checkpoint_id]["inputs"]
    if checkpoint.get("run_id") != [director_id, 8] or checkpoint.get("clip_id") != [director_id, 9]:
        raise PlanError("Connect Director run_id and clip_id directly to the Checkpoint")
    frames_link = checkpoint.get("images")
    if not isinstance(frames_link, list) or len(frames_link) != 2:
        raise PlanError("Connect the decoded video IMAGE batch to the Checkpoint")
    _, image_connections = _ancestors(edges, frames_link[0])
    image_connections.add(tuple(frames_link))
    for slot, label in ((0, "first image"), (1, "last image"), (2, "positive prompt"), (4, "seed")):
        if (director_id, slot) not in image_connections:
            raise PlanError(f"The frame-generation graph must consume Director {label} output")
    consumes_frames = (director_id, 5) in image_connections
    consumes_duration = (director_id, 10) in image_connections
    if consumes_frames == consumes_duration:
        raise PlanError("The frame-generation graph must consume exactly one Director timing output: frame count or duration_seconds")
    timing_mode = "frames" if consumes_frames else "duration"
    video_ancestors, video_connections = _ancestors(edges, video_output_id)
    if checkpoint_id not in video_ancestors or (checkpoint_id, 0) not in video_connections:
        raise PlanError("The selected video saver must consume the Checkpoint IMAGE output")
    if (director_id, 7) not in video_connections:
        raise PlanError("The video-saving graph must consume Director filename_prefix output")
    if timing_mode == "frames":
        if (director_id, 6) not in video_connections:
            raise PlanError("The video-saving graph must consume Director fps output")
    else:
        director_fps = (director_id, 6) in video_connections
        native_fps = any(
            node_id in video_ancestors
            and workflow[node_id]["class_type"] == "GetVideoComponents"
            and (node_id, 2) in video_connections
            for node_id in video_ancestors
        )
        if not (director_fps or native_fps):
            raise PlanError("A duration-based workflow must use Director fps or preserve native fps from GetVideoComponents")
    return timing_mode


def build_prompt(workflow, clip, job_id, attempt_id, director_id, checkpoint_id, video_output_id, previous_frame=None) -> dict:
    validate_workflow(workflow, director_id, checkpoint_id, video_output_id)
    for value, label in ((job_id, "Job ID"), (attempt_id, "Attempt ID")):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
            raise PlanError(f"{label} is invalid")
    result = copy.deepcopy(workflow)
    for node_id, inputs in _overrides(clip.get("input_overrides")).items():
        if node_id not in result or result[node_id]["class_type"] in CONTROL_TYPES:
            raise PlanError("Overrides cannot change unknown nodes or Director/Checkpoint control nodes")
        for name, value in inputs.items():
            existing = result[node_id]["inputs"]
            if name not in existing or not _scalar(existing[name]):
                raise PlanError(f"Override {node_id}.{name} must name an existing scalar widget; links are protected")
            existing[name] = value
    def endpoint(name):
        value = clip[name]
        if value == "@previous":
            if previous_frame is None:
                raise PlanError("No actual preceding frame exists for @previous")
            value = previous_frame
        return _relative(value)
    result[director_id]["inputs"].update({
        "first_image": endpoint("first_image"), "last_image": endpoint("last_image"),
        "positive": clip["prompt"], "negative": clip["negative_prompt"],
        "seed": clip["seed"], "frames": clip["frames"], "fps": clip["fps"],
        "run_id": job_id, "clip_id": attempt_id,
    })
    return result


def atomic_json(path, data) -> None:
    path = Path(path)
    try:
        payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    except (TypeError, ValueError, RecursionError) as exc:
        raise PlanError("State must be finite JSON data") from exc
    if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
        raise PlanError("Saved state exceeds its size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".state-", suffix=".tmp", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path) -> dict:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise PlanError("Saved JSON exceeds its size limit")
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise PlanError("Saved JSON must be an object")
        return value
    except (OSError, ValueError, RecursionError) as exc:
        if isinstance(exc, PlanError):
            raise
        raise PlanError(f"Could not read JSON {path.name}: {exc}") from exc
