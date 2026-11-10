"""Local measurement adapter for the Scene Director continuity gate.

This module deliberately has no automatic body-framing claim. A project must
provide a real full-body evidence callback before a clip can be auto-accepted;
without one, the policy fails closed to manual review.
"""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
from typing import Protocol

import numpy as np
from PIL import Image, ImageOps

try:
    from .continuity_gate import GatePolicy, evaluate
    from .continuity_scorer import ScoringUnavailable, score_face
    from .director_core import safe_path
except ImportError:
    from continuity_gate import GatePolicy, evaluate
    from continuity_scorer import ScoringUnavailable, score_face
    from director_core import safe_path


class FullBodyEvidence(Protocol):
    def __call__(self, relative_path: str) -> bool: ...


def load_bgr(input_root: Path, relative_path: str) -> np.ndarray:
    """Load a trusted ComfyUI input image as BGR for InsightFace."""
    path = safe_path(input_root, relative_path)
    with Image.open(path) as source:
        rgb = np.asarray(ImageOps.exif_transpose(source).convert("RGB"))
    return np.ascontiguousarray(rgb[..., ::-1])


class MeasuredContinuityGate:
    """Attach actual local face measurements to a deterministic gate policy."""

    def __init__(self, input_root: Path, reference_image: str, policy: GatePolicy,
                 detector, full_body_evidence: FullBodyEvidence | None = None):
        self.input_root = Path(input_root)
        self.reference_image = reference_image
        self.policy = policy
        self.detector = detector
        self.full_body_evidence = full_body_evidence

    def evaluate(self, job: dict, clip: dict, receipt: dict, videos: list[dict]) -> dict:
        measurements: dict[str, object] = {
            "artifact_ok": bool(receipt and videos),
            "face_detected": False,
            "full_body_visible": False,
        }
        try:
            reference = load_bgr(self.input_root, self.reference_image)
            first = load_bgr(self.input_root, receipt["first_frame"])
            last = load_bgr(self.input_root, receipt["last_frame"])
            measurements["face_similarity"] = score_face(self.detector, reference, last).similarity_to_reference
            measurements["boundary_similarity"] = score_face(self.detector, first, last).similarity_to_reference
            measurements["face_detected"] = True
            if self.full_body_evidence is not None:
                measurements["full_body_visible"] = bool(self.full_body_evidence(receipt["last_frame"]))
        except (KeyError, OSError, ValueError, ScoringUnavailable):
            # Missing evidence must not turn into a silent automatic pass.
            pass
        return evaluate(measurements, self.policy, len(clip.get("attempts", [])) + 1)


class PoseProbeEvidence:
    """Run the isolated local pose helper against a safe ComfyUI input image."""

    def __init__(self, input_root: Path, python_path: Path, model_path: Path, probe_path: Path):
        self.input_root = Path(input_root)
        self.python_path = Path(python_path)
        self.model_path = Path(model_path)
        self.probe_path = Path(probe_path)

    def __call__(self, relative_path: str) -> bool:
        image_path = safe_path(self.input_root, relative_path)
        completed = subprocess.run(
            [str(self.python_path), str(self.probe_path), str(self.model_path), str(image_path)],
            check=False, capture_output=True, text=True, timeout=45,
        )
        if completed.returncode != 0:
            return False
        try:
            payload = json.loads(completed.stdout)
        except (TypeError, ValueError):
            return False
        return isinstance(payload, dict) and payload.get("full_body_visible") is True
