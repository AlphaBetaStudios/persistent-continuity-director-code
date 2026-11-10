"""Small isolated pose helper used by the optional full-body continuity gate.

Run this file with the dedicated pose environment, never ComfyUI's own Python.
It prints one JSON object to stdout and sends diagnostic output to stderr.
"""
from __future__ import annotations

import json
import sys


LANDMARKS = {"nose": 0, "left_heel": 29, "right_heel": 30,
             "left_foot": 31, "right_foot": 32}


def visible_full_body(result, *, margin: float = 0.02, minimum: float = 0.4) -> tuple[bool, dict]:
    if not result.pose_landmarks:
        return False, {"reason": "no_pose"}
    pose = result.pose_landmarks[0]
    observed = {}
    for name, index in LANDMARKS.items():
        point = pose[index]
        observed[name] = {"x": float(point.x), "y": float(point.y),
                          "visibility": float(point.visibility), "presence": float(point.presence)}
    def usable(point):
        return (margin <= point["x"] <= 1 - margin and margin <= point["y"] <= 1 - margin
                and point["visibility"] >= minimum and point["presence"] >= minimum)

    # Fast dance motion can briefly obscure one heel even while the full body
    # remains in frame. Keep a firm head signal and require three of the four
    # heel/foot landmarks instead of treating one occlusion as a close crop.
    head_present = usable(observed["nose"])
    lower_visible = sum(usable(observed[name]) for name in ("left_heel", "right_heel", "left_foot", "right_foot"))
    full_body = head_present and lower_visible >= 3
    return full_body, observed


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(json.dumps({"full_body_visible": False, "error": "usage"}))
        return 2
    model_path, image_path = argv[1:]
    import mediapipe as mp
    from mediapipe.tasks import python
    from mediapipe.tasks.python import vision
    options = vision.PoseLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.IMAGE,
        num_poses=1,
    )
    with vision.PoseLandmarker.create_from_options(options) as detector:
        result = detector.detect(mp.Image.create_from_file(image_path))
    full_body, landmarks = visible_full_body(result)
    print(json.dumps({"full_body_visible": full_body, "landmarks": landmarks}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
