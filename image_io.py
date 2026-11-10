"""Small, CPU-only image operations shared by nodes and verification."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import tempfile

import numpy as np
from PIL import Image, ImageOps

if __package__:
    from .director_core import atomic_json, safe_path
else:
    from director_core import atomic_json, safe_path


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError('Job and clip IDs must contain only letters, numbers, underscores or hyphens.')
    return value


def load_image_array(input_root: Path, filename: str) -> np.ndarray:
    path = safe_path(input_root, filename)
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert('RGB')
        return np.asarray(image, dtype=np.float32)[None, ...] / 255.0


def image_fingerprint(input_root: Path, filename: str) -> str:
    path = safe_path(input_root, filename)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _pixels(frame: np.ndarray) -> np.ndarray:
    frame = np.asarray(frame)
    if frame.ndim != 3 or frame.shape[-1] not in (3, 4) or min(frame.shape[:2]) < 1:
        raise ValueError('A checkpoint frame must have shape [height, width, 3 or 4].')
    if not np.isfinite(frame).all():
        raise ValueError('A checkpoint frame contains non-finite pixel values.')
    return np.rint(np.clip(frame[..., :3], 0, 1) * 255).astype(np.uint8)


def _atomic_png(path: Path, pixels: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            Image.fromarray(pixels).save(stream, format='PNG')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if hasattr(os, 'O_DIRECTORY'):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_endpoints(input_root: Path, output_root: Path, job_id: str, attempt_id: str,
                   first: np.ndarray, last: np.ndarray, frame_count: int) -> dict:
    """Save two PNGs before the receipt; successful video history commits a clip later."""
    job_id, attempt_id = _identifier(job_id), _identifier(attempt_id)
    if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count < 1:
        raise ValueError('The decoded frame count must be a positive integer.')
    first_pixels, last_pixels = _pixels(first), _pixels(last)
    if first_pixels.shape != last_pixels.shape:
        raise ValueError('The first and last decoded frames must have equal dimensions.')
    first_name = f'alpha_scene_director/{job_id}/{attempt_id}_first.png'
    last_name = f'alpha_scene_director/{job_id}/{attempt_id}_last.png'
    first_path, last_path = safe_path(input_root, first_name), safe_path(input_root, last_name)
    receipt_path = safe_path(output_root, f'{job_id}/{attempt_id}.receipt.json')
    _atomic_png(first_path, first_pixels)
    _atomic_png(last_path, last_pixels)
    receipt = {
        'job_id': job_id, 'attempt_id': attempt_id,
        'first_frame': first_name, 'last_frame': last_name,
        'frame_count': frame_count, 'width': int(last_pixels.shape[1]), 'height': int(last_pixels.shape[0]),
    }
    atomic_json(receipt_path, receipt)
    return receipt
