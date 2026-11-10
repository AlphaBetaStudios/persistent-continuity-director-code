"""Explicit H3 deployment factory for the optional continuity gate.

Importing the custom node does not call this factory. A deployment must choose
its calibrated policy and paths, then pass the returned gate to ``register_routes``.
"""
from __future__ import annotations

from pathlib import Path

try:
    from .continuity_gate import GatePolicy
    from .measured_continuity_gate import MeasuredContinuityGate, PoseProbeEvidence
except ImportError:
    from continuity_gate import GatePolicy
    from measured_continuity_gate import MeasuredContinuityGate, PoseProbeEvidence


def create_h3_gate(*, input_root: Path, reference_image: str, policy: GatePolicy,
                   pose_python: Path, pose_model: Path, probe_path: Path):
    """Create a CPU-only face-and-pose gate without changing ComfyUI's venv."""
    from insightface.app import FaceAnalysis

    detector = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    detector.prepare(ctx_id=-1, det_size=(320, 320))
    body = PoseProbeEvidence(input_root, pose_python, pose_model, probe_path)
    return MeasuredContinuityGate(input_root, reference_image, policy, detector, body)
