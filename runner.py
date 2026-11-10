"""Durable sequential orchestration outside ComfyUI's node execution worker.

The adapter supplied by server_api owns all HTTP behavior. This manager stores
only the frozen plan, graph and render identifiers; it never stores credentials.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path, PurePosixPath
import re
import time
import uuid

from PIL import Image

if __package__:
    from .director_core import (MAX_CLIPS, MAX_JSON_BYTES, PlanError, atomic_json,
                                build_prompt, compile_plan, read_json, safe_path,
                                validate_workflow)
else:
    from director_core import (MAX_CLIPS, MAX_JSON_BYTES, PlanError, atomic_json,
                               build_prompt, compile_plan, read_json, safe_path,
                               validate_workflow)


VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".mpeg", ".mpg", ".ogv"}
MAX_ATTEMPTS = 100
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_JOB_COUNT = 10_000
JOB_ID_PATTERN = re.compile(r"[a-f0-9]{32}")
ATTEMPT_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")


class _Interrupted(RuntimeError):
    """The current render cannot be confirmed without another explicit Resume."""


def _json_copy(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
            raise PlanError("The scene request is too large")
        return json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, PlanError):
            raise
        raise PlanError("Scene requests must contain only finite JSON data") from exc


def _checked_image(path, *, require_png=False, expected_size=None):
    """Decode the image, rather than trusting its extension or a receipt alone."""
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES:
            raise PlanError(f"Image is missing, empty or larger than 64 MiB: {path.name}")
        with Image.open(path) as image:
            if require_png and image.format != "PNG":
                raise PlanError(f"Checkpoint must contain a real PNG: {path.name}")
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise PlanError(f"Image exceeds the 40-million-pixel limit: {path.name}")
            if expected_size is not None and image.size != expected_size:
                raise PlanError(f"Checkpoint image size disagrees with its receipt: {path.name}")
            image.load()
            return width, height
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, PlanError):
            raise
        raise PlanError(f"Could not decode image {path.name}: {exc}") from exc


class JobManager:
    """A single-server manager whose public methods run on its asyncio thread.

    create/resume capture the current request's adapter synchronously. The task
    uses that one adapter for the whole job, so another browser request cannot
    replace credentials underneath an active run. ComfyUI startup creates one
    manager; saved active jobs remain interrupted until the user resumes them.
    """

    def __init__(self, root, input_root, client_factory, continuity_gate=None):
        self.root = Path(root).resolve()
        self.input_root = Path(input_root).resolve()
        self.output_root = self.root.parent
        self.client_factory = client_factory
        self.continuity_gate = continuity_gate
        self.poll_interval = 0.5
        self.max_clip_seconds = 24 * 60 * 60
        self._session_id = uuid.uuid4().hex
        self._jobs = {}
        self._tasks = {}
        self.root.mkdir(parents=True, exist_ok=True)
        self._load_jobs()

    def _job_path(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID_PATTERN.fullmatch(job_id):
            raise PlanError("Invalid job ID")
        return safe_path(self.root, f"{job_id}/job.json")

    def _load_jobs(self):
        directories = sorted(self.root.iterdir(), key=lambda item: item.name)
        if len(directories) > MAX_JOB_COUNT:
            raise PlanError(f"Archive old jobs before exceeding {MAX_JOB_COUNT} job directories")
        for directory in directories:
            if not JOB_ID_PATTERN.fullmatch(directory.name) or not directory.is_dir():
                continue
            manifest_path = self._job_path(directory.name)
            if not manifest_path.is_file():
                continue
            job = read_json(manifest_path)
            if (job.get("version") != 1 or job.get("job_id") != directory.name
                    or not isinstance(job.get("clips"), list) or not 1 <= len(job["clips"]) <= MAX_CLIPS):
                raise PlanError(f"Saved job {directory.name} has an invalid manifest")
            self._jobs[job["job_id"]] = job
            if job.get("status") in ("running", "pausing"):
                job["status"] = "interrupted"
                job["error"] = "ComfyUI restarted. Resume to reconcile the outstanding prompt before continuing."
                self._save(job)

    def _save(self, job):
        job["updated_at"] = time.time()
        atomic_json(self._job_path(job["job_id"]), job)

    def _require_job(self, job_id):
        self._job_path(job_id)
        if job_id not in self._jobs:
            raise PlanError("Scene Director job was not found")
        return self._jobs[job_id]

    def _assert_available(self, same_job=None):
        for job_id, job in self._jobs.items():
            if job_id == same_job:
                continue
            task = self._tasks.get(job_id)
            outstanding = any(clip.get("prompt_id") and clip.get("status") not in ("completed", "failed")
                              for clip in job["clips"])
            if (task and not task.done()) or job["status"] in ("running", "pausing") or outstanding:
                raise PlanError(f"Job {job_id} already has active or unconfirmed work. Pause or Resume that job first.")

    def _prepare(self, request):
        if not isinstance(request, dict):
            raise PlanError("A scene request must be an object")
        required = ("plan", "workflow", "director_id", "checkpoint_id", "video_output_id")
        if any(key not in request for key in required):
            raise PlanError("Provide plan, API workflow, director_id, checkpoint_id and video_output_id")
        frozen = _json_copy({key: request[key] for key in required})
        if isinstance(frozen["plan"], str):
            try:
                frozen["plan"] = json.loads(frozen["plan"])
            except (ValueError, RecursionError) as exc:
                raise PlanError("Plan is not valid JSON") from exc
        for key in ("director_id", "checkpoint_id", "video_output_id"):
            if not isinstance(frozen[key], str) or not frozen[key]:
                raise PlanError(f"{key} must be an existing string node ID")
        graph = frozen["workflow"]
        frozen["timing_mode"] = validate_workflow(
            graph, frozen["director_id"], frozen["checkpoint_id"], frozen["video_output_id"])
        fallback = graph[frozen["director_id"]]["inputs"]
        clips = compile_plan(frozen["plan"], fallback)
        self._validate_static_images(clips)
        # Every override is checked now, including overrides on later clips.
        for clip in clips:
            build_prompt(graph, clip, "validation", "validation", frozen["director_id"],
                         frozen["checkpoint_id"], frozen["video_output_id"], "validation/previous.png")
        return frozen, clips

    def _validate_static_images(self, clips):
        references = {clip[key] for clip in clips for key in ("first_image", "last_image")
                      if clip[key] != "@previous"}
        for relative in sorted(references):
            _checked_image(safe_path(self.input_root, relative))

    def validate(self, request) -> dict:
        frozen, clips = self._prepare(request)
        return {"valid": True, "project": frozen["plan"].get("project", "Untitled film"),
                "timing_mode": frozen["timing_mode"],
                "total": len(clips), "clips": copy.deepcopy(clips),
                "video_seconds": sum(clip["frames"] / clip["fps"] for clip in clips)}

    def create(self, request) -> dict:
        self._assert_available()
        frozen, compiled = self._prepare(request)
        if len(self._jobs) >= MAX_JOB_COUNT:
            raise PlanError("Archive old jobs before starting another project")
        loop = asyncio.get_running_loop()
        client = self.client_factory()
        job_id = uuid.uuid4().hex
        now = time.time()
        job = {"version": 1, "job_id": job_id, "created_at": now, "updated_at": now,
               "status": "running", "project": frozen["plan"].get("project", "Untitled film"),
               "pause_requested": False, "error": None, **frozen,
               "clips": [{**clip, "status": "pending", "attempt_id": None, "prompt_id": None,
                          "attempts": [], "receipt": None, "video_files": [], "gate_report": None, "error": None,
                          "elapsed_seconds": None} for clip in compiled]}
        # Identifiers and immutable request exist on disk before any task can POST.
        self._save(job)
        self._jobs[job_id] = job
        self._launch(loop, job, client, resuming=False)
        return self._public(job)

    def _launch(self, loop, job, client, *, resuming):
        job_id = job["job_id"]
        task = loop.create_task(self._run(job_id, client, resuming=resuming), name=f"AlphaSceneDirector:{job_id}")
        self._tasks[job_id] = task
        def finished(done):
            if self._tasks.get(job_id) is done:
                self._tasks.pop(job_id, None)
        task.add_done_callback(finished)

    def get(self, job_id) -> dict:
        return self._public(self._require_job(job_id))

    def list_jobs(self) -> list[dict]:
        return [self._public(job) for job in sorted(self._jobs.values(),
                key=lambda item: item.get("created_at", 0), reverse=True)]

    def _public(self, job):
        completed = [clip for clip in job["clips"] if clip["status"] == "completed"]
        unfinished = [clip for clip in job["clips"] if clip["status"] != "completed"]
        measured_frames = sum(clip["frames"] for clip in completed if clip.get("elapsed_seconds") is not None)
        measured_seconds = sum(clip.get("elapsed_seconds") or 0 for clip in completed)
        estimate = (round(measured_seconds / measured_frames * sum(clip["frames"] for clip in unfinished), 1)
                    if measured_frames else None)
        if not unfinished:
            estimate = 0
        visible = ("id", "scene_id", "scene_title", "status", "attempt_id", "prompt_id", "receipt",
                   "video_files", "gate_report", "error", "elapsed_seconds", "frames", "fps")
        return {"job_id": job["job_id"], "project": job.get("project", "Untitled film"),
                "timing_mode": job.get("timing_mode", "frames"),
                "status": job["status"], "completed": len(completed), "total": len(job["clips"]),
                "current": unfinished[0]["id"] if unfinished else None, "error": job.get("error"),
                "estimated_seconds": estimate, "created_at": job.get("created_at"), "updated_at": job.get("updated_at"),
                "clips": [{key: copy.deepcopy(clip.get(key)) for key in visible} for clip in job["clips"]]}

    def pause(self, job_id) -> dict:
        job = self._require_job(job_id)
        if job["status"] in ("running", "pausing"):
            job["pause_requested"] = True
            job["status"] = "pausing"
            self._save(job)
        return self._public(job)

    def resume(self, job_id) -> dict:
        job = self._require_job(job_id)
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            return self._public(job)
        self._assert_available(same_job=job_id)
        # Confirmed artifacts are never silently skipped when they have disappeared.
        for clip in job["clips"]:
            if clip["status"] == "completed":
                self._verify_completed(job, clip)
        if all(clip["status"] == "completed" for clip in job["clips"]):
            job["status"] = "completed"
            self._save(job)
            return self._public(job)
        validate_workflow(job["workflow"], job["director_id"], job["checkpoint_id"], job["video_output_id"])
        self._validate_static_images([clip for clip in job["clips"] if clip["status"] != "completed"])
        loop = asyncio.get_running_loop()
        client = self.client_factory()
        job["pause_requested"] = False
        job["status"] = "running"
        job["error"] = None
        self._save(job)
        self._launch(loop, job, client, resuming=True)
        return self._public(job)

    def _new_attempt(self, job, clip):
        attempts = clip.setdefault("attempts", [])
        if clip.get("attempt_id"):
            attempts.append({key: copy.deepcopy(clip.get(key)) for key in
                             ("attempt_id", "prompt_id", "status", "error", "started_at", "finished_at")})
        if len(attempts) >= MAX_ATTEMPTS:
            raise PlanError(f"This clip exceeded {MAX_ATTEMPTS} explicitly requested attempts")
        clip.update(attempt_id=f"{clip['id']}-{uuid.uuid4().hex}", prompt_id=str(uuid.uuid4()),
                    status="submitting", submission_session=self._session_id, reconcile_required=True,
                    receipt=None, video_files=[], error=None, elapsed_seconds=None,
                    started_at=time.time(), finished_at=None)
        self._save(job)

    async def _run(self, job_id, client, *, resuming):
        job = self._jobs[job_id]
        current = None
        try:
            previous = None
            index = 0
            while index < len(job["clips"]):
                clip = job["clips"][index]
                current = clip
                if clip["status"] == "completed":
                    previous = clip["receipt"]["last_frame"]
                    index += 1
                    continue
                existing = bool(clip.get("prompt_id")) and clip.get("status") != "failed"
                if job.get("pause_requested") and not existing:
                    job["status"] = "paused"
                    self._save(job)
                    return
                history = None
                if existing:
                    history, found = await self._locate(client, clip["prompt_id"])
                    if not found:
                        # Absence after a timeout may mean /prompt is still validating.
                        # A fresh manager is created only by ComfyUI server startup.
                        if not resuming or clip.get("submission_session") == self._session_id:
                            raise _Interrupted("The outstanding prompt is absent from queue and history. It may still be validating. "
                                               "Wait and Resume to reconcile again, or restart ComfyUI before retrying this unconfirmed clip.")
                        existing = False
                if not existing:
                    self._new_attempt(job, clip)
                    prompt = build_prompt(job["workflow"], clip, job_id, clip["attempt_id"], job["director_id"],
                                          job["checkpoint_id"], job["video_output_id"], previous)
                    # Revalidate the current carried endpoint immediately before queueing.
                    _checked_image(safe_path(self.input_root, prompt[job["director_id"]]["inputs"]["first_image"]))
                    try:
                        response = await client.submit(prompt, clip["prompt_id"])
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        if getattr(exc, "definitive", False):
                            clip["reconcile_required"] = False
                            raise PlanError(f"ComfyUI rejected the prompt: {exc}") from exc
                        clip["error"] = f"Submission response was uncertain: {str(exc)[:2000]}"
                        self._save(job)
                        try:
                            history, found = await self._locate(client, clip["prompt_id"])
                        except Exception as locate_error:
                            raise _Interrupted(f"Submission was uncertain and reconciliation failed: {locate_error}") from locate_error
                        if not found:
                            raise _Interrupted("Submission was uncertain and the prompt is not yet visible. "
                                               "No retry was sent. Wait and Resume, or restart ComfyUI before retrying.") from exc
                    else:
                        accepted_id = response if isinstance(response, str) else response.get("prompt_id") if isinstance(response, dict) else None
                        if not isinstance(accepted_id, str) or not 1 <= len(accepted_id) <= 128:
                            raise _Interrupted("ComfyUI did not return a usable prompt ID. Resume will reconcile the saved ID before continuing.")
                        # Older servers may return a different ID than the requested UUID.
                        clip["prompt_id"] = accepted_id
                        self._save(job)
                clip["status"] = "running"
                clip["error"] = None
                self._save(job)
                history = await self._wait_for_history(client, clip["prompt_id"], history)
                outcome = self._complete(job, clip, history)
                if outcome == "retry":
                    continue
                if not outcome:
                    return
                previous = clip["receipt"]["last_frame"]
                if job.get("pause_requested") and any(c["status"] != "completed" for c in job["clips"]):
                    job["status"] = "paused"
                    self._save(job)
                    return
                index += 1
            job["status"] = "completed"
            job["error"] = None
            self._save(job)
        except asyncio.CancelledError:
            self._stop(job, current, "interrupted", "ComfyUI stopped before the current clip was confirmed. Resume to reconcile its saved prompt ID.")
            raise
        except _Interrupted as exc:
            self._stop(job, current, "interrupted", str(exc))
        except Exception as exc:
            # API reads can fail independently of the GPU work. Preserve the ID.
            uncertain = current is not None and current.get("reconcile_required", False)
            status = "interrupted" if uncertain and not isinstance(exc, PlanError) else "failed"
            self._stop(job, current, status, str(exc))

    def _stop(self, job, clip, status, error):
        job["status"] = status
        job["error"] = error[:4000]
        if clip is not None and clip["status"] != "completed":
            clip["status"] = status
            clip["error"] = job["error"]
            if status == "failed":
                clip["reconcile_required"] = False
        self._save(job)

    @staticmethod
    def _history_entry(payload, prompt_id):
        if not isinstance(payload, dict):
            raise _Interrupted("ComfyUI returned malformed history; the prompt has not been retried")
        if prompt_id in payload:
            entry = payload[prompt_id]
            if not isinstance(entry, dict):
                raise _Interrupted("ComfyUI returned a malformed history entry")
            return entry
        if "status" in payload and "outputs" in payload:
            return payload
        return None

    async def _locate(self, client, prompt_id):
        history = self._history_entry(await client.history(prompt_id), prompt_id)
        if history is not None:
            return history, True
        queued = await client.queue_ids()
        if not isinstance(queued, (list, tuple, set, frozenset)) or any(not isinstance(item, str) for item in queued):
            raise _Interrupted("ComfyUI returned malformed queue IDs; no prompt was retried")
        if prompt_id in queued:
            return None, True
        # Close the race where the prompt finishes between the first history read
        # and the queue read. A second history read then sees its completed entry.
        history = self._history_entry(await client.history(prompt_id), prompt_id)
        return history, history is not None

    @staticmethod
    def _history_result(history):
        if history is None:
            return False
        status = history.get("status")
        if not isinstance(status, dict):
            raise _Interrupted("History has no recognized status; no prompt was retried")
        state = status.get("status_str")
        if state == "error":
            detail = "ComfyUI execution failed"
            for message in status.get("messages", []):
                if isinstance(message, (list, tuple)) and len(message) == 2 and message[0] == "execution_error" and isinstance(message[1], dict):
                    detail = str(message[1].get("exception_message") or message[1].get("exception_type") or detail)
            raise PlanError(detail[:4000])
        if status.get("completed") is True:
            if state != "success":
                raise PlanError(f"ComfyUI finished with unsuccessful status: {state}")
            return True
        return False

    async def _wait_for_history(self, client, prompt_id, history=None):
        deadline = time.monotonic() + self.max_clip_seconds
        while True:
            if self._history_result(history):
                return history
            if time.monotonic() >= deadline:
                raise _Interrupted("The clip exceeded the 24-hour observation limit. Its prompt ID is preserved; Resume to reconcile it.")
            history, found = await self._locate(client, prompt_id)
            if not found:
                raise _Interrupted("The accepted prompt disappeared from queue and history. No retry was sent; Resume after reconnecting or restarting ComfyUI.")
            if self._history_result(history):
                return history
            await asyncio.sleep(max(0.001, self.poll_interval))

    def _receipt(self, job, clip):
        attempt = clip.get("attempt_id")
        if not isinstance(attempt, str) or not ATTEMPT_PATTERN.fullmatch(attempt):
            raise PlanError("Saved clip has an invalid attempt ID")
        receipt = read_json(safe_path(self.root, f"{job['job_id']}/{attempt}.receipt.json"))
        if receipt.get("job_id") != job["job_id"] or receipt.get("attempt_id") != attempt:
            raise PlanError("Checkpoint receipt belongs to another job or attempt")
        frame_count = receipt.get("frame_count")
        if not isinstance(frame_count, int) or isinstance(frame_count, bool) or frame_count < 1:
            raise PlanError("Checkpoint frame count is invalid")
        if job.get("timing_mode", "frames") == "frames" and frame_count != clip["frames"]:
            raise PlanError("Checkpoint frame count differs from the planned frames; verify the Director frame-count wiring")
        width, height = receipt.get("width"), receipt.get("height")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in (width, height)):
            raise PlanError("Checkpoint receipt has invalid dimensions")
        for key, suffix in (("first_frame", "first"), ("last_frame", "last")):
            expected = f"alpha_scene_director/{job['job_id']}/{attempt}_{suffix}.png"
            if receipt.get(key) != expected:
                raise PlanError("Checkpoint frame path does not match its job and attempt")
            _checked_image(safe_path(self.input_root, expected), require_png=True, expected_size=(width, height))
        return receipt

    def _video_artifacts(self, history, video_output_id, job_id, attempt_id):
        outputs = history.get("outputs")
        selected = outputs.get(video_output_id) if isinstance(outputs, dict) else None
        if not isinstance(selected, dict):
            raise PlanError("Successful history contains no output from the selected video saver")
        files, seen = [], set()
        for bucket in ("images", "gifs", "videos"):
            entries = selected.get(bucket, [])
            if not isinstance(entries, list):
                raise PlanError("Video saver history contains a malformed artifact list")
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("filename"), str):
                    continue
                filename = entry["filename"]
                if PurePosixPath(filename).suffix.lower() not in VIDEO_SUFFIXES:
                    continue
                if "/" in filename or "\\" in filename or entry.get("type", "output") != "output":
                    raise PlanError("Video artifact must be a regular file in ComfyUI output")
                subfolder = entry.get("subfolder", "")
                if not isinstance(subfolder, str):
                    raise PlanError("Video artifact subfolder is invalid")
                relative = f"{subfolder}/{filename}" if subfolder else filename
                path = safe_path(self.output_root, relative)
                expected_directory = safe_path(self.root, job_id)
                try:
                    path.relative_to(expected_directory)
                except ValueError as exc:
                    raise PlanError("Video artifact belongs to another job; connect the Director filename_prefix") from exc
                if not filename.startswith(attempt_id):
                    raise PlanError("Video artifact belongs to another attempt; connect the Director filename_prefix")
                if not path.is_file() or path.stat().st_size <= 0:
                    raise PlanError(f"Video saver reported a missing or empty file: {relative}")
                if relative not in seen:
                    seen.add(relative)
                    files.append({"filename": filename, "subfolder": subfolder, "type": "output", "relative_path": relative})
        if not files:
            raise PlanError("The selected output saved no supported video file (.mp4, .webm, .mov, .mkv, etc.)")
        return files

    def _complete(self, job, clip, history):
        # A successful history is authoritative about execution, but the receipt
        # alone can predate a failed video encoder. Both sets of files must exist.
        self._history_result(history)
        receipt = self._receipt(job, clip)
        videos = self._video_artifacts(history, job["video_output_id"], job["job_id"], clip["attempt_id"])
        report = None
        if self.continuity_gate is not None:
            report = self.continuity_gate.evaluate(job, clip, receipt, videos)
            if not isinstance(report, dict) or report.get("decision") not in {"accepted", "repair_required", "manual_review"}:
                raise PlanError("Continuity gate returned an invalid report")
            report = _json_copy(report)
            if report["decision"] == "repair_required":
                clip.update(status="repair_required", prompt_id=None, receipt=receipt, video_files=videos, gate_report=report,
                            error="Continuity gate requested a bounded repair retry", reconcile_required=False,
                            finished_at=time.time(), elapsed_seconds=max(0, time.time() - clip.get("started_at", time.time())))
                self._save(job)
                return "retry"
            if report["decision"] != "accepted":
                clip.update(status="manual_review", receipt=receipt, video_files=videos, gate_report=report,
                            error="Continuity gate requires review", reconcile_required=False,
                            finished_at=time.time(), elapsed_seconds=max(0, time.time() - clip.get("started_at", time.time())))
                job["status"] = "paused"
                job["error"] = "Continuity gate requires review before this clip can become the next checkpoint."
                self._save(job)
                return False
        clip.update(status="completed", receipt=receipt, video_files=videos, error=None,
                    gate_report=report, reconcile_required=False, finished_at=time.time(),
                    elapsed_seconds=max(0, time.time() - clip.get("started_at", time.time())))
        self._save(job)
        return True

    def _verify_completed(self, job, clip):
        receipt = self._receipt(job, clip)
        if receipt != clip.get("receipt"):
            raise PlanError(f"Completed clip {clip['id']} has a changed checkpoint receipt")
        videos = clip.get("video_files")
        if not isinstance(videos, list) or not videos:
            raise PlanError(f"Completed clip {clip['id']} has no saved video records")
        # Use the same traversal, suffix and actual-file checks as fresh history.
        self._video_artifacts({"outputs": {job["video_output_id"]: {"videos": videos}}},
                              job["video_output_id"], job["job_id"], clip["attempt_id"])
