"""Deterministic policy for accepting or rejecting generated continuity clips.

Image and face models belong behind a scorer adapter.  This module deliberately
does not invent a visual score; it validates recorded measurements and makes a
persistable decision from explicitly configured thresholds.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


DECISIONS = frozenset({"accepted", "repair_required", "manual_review"})


@dataclass(frozen=True)
class GatePolicy:
    min_face_similarity: float
    min_boundary_similarity: float
    max_blur: float | None = None
    max_attempts: int = 2
    version: str = "v1"

    def __post_init__(self):
        for name in ("min_face_similarity", "min_boundary_similarity"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.max_blur is not None and (not isinstance(self.max_blur, (int, float)) or self.max_blur < 0):
            raise ValueError("max_blur must be non-negative or None")
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version is required")


def evaluate(measurements: Mapping[str, object], policy: GatePolicy, attempt_number: int) -> dict:
    """Return a JSON-safe continuity report without mutating a job or artifacts.

    Required measurements are `artifact_ok`, `face_detected`,
    `face_similarity`, `boundary_similarity`, and `full_body_visible`.
    A scorer may also provide `blur`. Missing/invalid measurements result in
    manual review instead of an unsupported automatic repair decision.
    """
    if not isinstance(attempt_number, int) or attempt_number < 1:
        raise ValueError("attempt_number must be a positive integer")
    reasons: list[str] = []
    artifact_ok = measurements.get("artifact_ok")
    face_detected = measurements.get("face_detected")
    face_similarity = measurements.get("face_similarity")
    boundary_similarity = measurements.get("boundary_similarity")
    full_body_visible = measurements.get("full_body_visible")
    blur = measurements.get("blur")

    if artifact_ok is not True:
        reasons.append("artifact_integrity_failed")
    if face_detected is not True:
        reasons.append("face_not_detected")
    if not _score(face_similarity):
        reasons.append("face_similarity_unavailable")
    elif face_similarity < policy.min_face_similarity:
        reasons.append("face_similarity_below_threshold")
    if not _score(boundary_similarity):
        reasons.append("boundary_similarity_unavailable")
    elif boundary_similarity < policy.min_boundary_similarity:
        reasons.append("boundary_similarity_below_threshold")
    if full_body_visible is not True:
        reasons.append("full_body_framing_failed")
    if policy.max_blur is not None:
        if not _nonnegative(blur):
            reasons.append("blur_measurement_unavailable")
        elif blur > policy.max_blur:
            reasons.append("blur_above_threshold")

    unknown = any(
        reason.endswith("_unavailable") or reason in {"face_not_detected", "full_body_framing_failed"}
        for reason in reasons
    )
    if not reasons:
        decision = "accepted"
    elif unknown or attempt_number >= policy.max_attempts:
        decision = "manual_review"
    else:
        decision = "repair_required"
    return {
        "policy": asdict(policy),
        "attempt_number": attempt_number,
        "measurements": dict(measurements),
        "reasons": reasons,
        "decision": decision,
    }


def _score(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 <= value <= 1.0


def _nonnegative(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
