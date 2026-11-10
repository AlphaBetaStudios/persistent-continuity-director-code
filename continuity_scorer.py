"""Face-embedding measurements for the continuity gate.

The scorer is intentionally separate from the gate policy: it reports evidence,
while ``continuity_gate`` decides whether that evidence is good enough to pass,
repair, or pause for review.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Protocol, Sequence


class ScoringUnavailable(RuntimeError):
    """Raised when a required reference or detected face is unavailable."""


class FaceDetector(Protocol):
    def get(self, image) -> Sequence[object]: ...


@dataclass(frozen=True)
class FaceMeasurement:
    similarity_to_reference: float


def largest_face_embedding(detector: FaceDetector, image) -> Sequence[float]:
    """Return the largest detected face embedding or fail closed.

    InsightFace results expose ``bbox`` and ``embedding``. Keeping this adapter
    tiny lets the policy remain testable without loading an ML model.
    """
    faces = list(detector.get(image) or [])
    if not faces:
        raise ScoringUnavailable("no face detected")

    def area(face: object) -> float:
        bbox = getattr(face, "bbox", None)
        if bbox is None or len(bbox) != 4:
            return -1.0
        return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))

    face = max(faces, key=area)
    embedding = getattr(face, "embedding", None)
    if embedding is None:
        raise ScoringUnavailable("detected face has no embedding")
    values = [float(value) for value in embedding]
    if not values:
        raise ScoringUnavailable("detected face embedding is empty")
    return values


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Compute a bounded cosine score and reject malformed embeddings."""
    if len(left) != len(right) or not left:
        raise ScoringUnavailable("face embeddings are incompatible")
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = sqrt(sum(float(a) ** 2 for a in left))
    right_norm = sqrt(sum(float(b) ** 2 for b in right))
    if left_norm == 0 or right_norm == 0:
        raise ScoringUnavailable("face embedding has zero magnitude")
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def score_face(detector: FaceDetector, reference_image, candidate_image) -> FaceMeasurement:
    reference = largest_face_embedding(detector, reference_image)
    candidate = largest_face_embedding(detector, candidate_image)
    return FaceMeasurement(similarity_to_reference=cosine_similarity(reference, candidate))
