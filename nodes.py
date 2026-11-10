"""ComfyUI graph nodes: scene inputs and decoded-frame checkpoint boundary."""
from pathlib import Path

import folder_paths

if __package__:
    from .image_io import image_fingerprint, load_image_array, save_endpoints, _identifier
else:
    from image_io import image_fingerprint, load_image_array, save_endpoints, _identifier


class AlphaSceneDirector:
    CATEGORY = 'AlphaBeta Studios/Scene Director'
    FUNCTION = 'prepare'
    RETURN_TYPES = ('IMAGE', 'IMAGE', 'STRING', 'STRING', 'INT', 'INT', 'FLOAT', 'STRING', 'STRING', 'STRING', 'FLOAT')
    RETURN_NAMES = ('first_image', 'last_image', 'positive', 'negative', 'seed', 'frames', 'fps', 'filename_prefix', 'run_id', 'clip_id', 'duration_seconds')
    DESCRIPTION = 'Open Scene Director to prepare prompts, first/last frames and a resumable sequence. Connect these outputs to your working video workflow.'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'scene_plan': ('STRING', {'multiline': True, 'default': ''}),
            'first_image': ('STRING', {'default': '', 'tooltip': 'Filename relative to ComfyUI input; upload in Scene Director.'}),
            'last_image': ('STRING', {'default': '', 'tooltip': 'Filename relative to ComfyUI input; upload in Scene Director.'}),
            'positive': ('STRING', {'multiline': True, 'default': ''}),
            'negative': ('STRING', {'multiline': True, 'default': ''}),
            'seed': ('INT', {'default': 1, 'min': 0, 'max': 9007199254740991}),
            'frames': ('INT', {'default': 81, 'min': 2, 'max': 4097}),
            'fps': ('FLOAT', {'default': 16.0, 'min': 0.01, 'max': 240.0, 'step': 0.01}),
            'run_id': ('STRING', {'default': 'preview'}),
            'clip_id': ('STRING', {'default': 'clip-001'}),
        }}

    @classmethod
    def IS_CHANGED(cls, first_image, last_image, **kwargs):
        root = Path(folder_paths.get_input_directory())
        return (image_fingerprint(root, first_image), image_fingerprint(root, last_image))

    def prepare(self, scene_plan, first_image, last_image, positive, negative, seed, frames, fps, run_id, clip_id):
        import torch
        root = Path(folder_paths.get_input_directory())
        run_id, clip_id = _identifier(run_id), _identifier(clip_id)
        first = torch.from_numpy(load_image_array(root, first_image))
        last = torch.from_numpy(load_image_array(root, last_image))
        prefix = f'alpha_scene_director/{run_id}/{clip_id}'
        duration_seconds = frames / fps
        return first, last, positive, negative, seed, frames, fps, prefix, run_id, clip_id, duration_seconds


class AlphaH3DurationFrames:
    CATEGORY = 'AlphaBeta Studios/Scene Director'
    FUNCTION = 'convert'
    RETURN_TYPES = ('INT',)
    RETURN_NAMES = ('h3_frame_count',)
    DESCRIPTION = 'Convert duration seconds to MiniMax H3 frame count, snapped to its 17k+5 grid.'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'duration_seconds': ('FLOAT', {'forceInput': True}),
            'fps': ('INT', {'default': 24, 'min': 1, 'max': 120}),
            'block_size': ('INT', {'default': 17, 'min': 1, 'max': 1000}),
            'offset': ('INT', {'default': 5, 'min': 0, 'max': 999}),
            'min_frames': ('INT', {'default': 5, 'min': 1, 'max': 3600}),
            'max_frames': ('INT', {'default': 3600, 'min': 1, 'max': 100000}),
        }}

    def convert(self, duration_seconds, fps, block_size, offset, min_frames, max_frames):
        if duration_seconds <= 0:
            raise ValueError('duration_seconds must be positive')
        if min_frames > max_frames:
            raise ValueError('min_frames cannot exceed max_frames')
        raw = max(min_frames, round(duration_seconds * fps))
        snapped = raw + (offset - (raw % block_size)) % block_size
        if snapped > max_frames:
            raise ValueError(f'H3 frame count {snapped} exceeds configured maximum {max_frames}')
        return (snapped,)


class AlphaSceneCheckpoint:
    CATEGORY = 'AlphaBeta Studios/Scene Director'
    FUNCTION = 'checkpoint'
    RETURN_TYPES = ('IMAGE',)
    RETURN_NAMES = ('images',)
    OUTPUT_NODE = True
    DESCRIPTION = 'Place between decoded video frames and the video saver. Saves actual first/last PNGs; successful video completion commits the sequence checkpoint.'

    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'images': ('IMAGE',),
            'run_id': ('STRING', {'forceInput': True}),
            'clip_id': ('STRING', {'forceInput': True}),
        }}

    def checkpoint(self, images, run_id, clip_id):
        if images.ndim != 4 or images.shape[0] < 1:
            raise ValueError('Connect a decoded IMAGE batch [frames, height, width, channels] to Scene Checkpoint.')
        # Transfer only two frames to the CPU, not the entire video batch.
        first = images[0].detach().float().cpu().numpy()
        last = images[-1].detach().float().cpu().numpy()
        save_endpoints(Path(folder_paths.get_input_directory()),
                       Path(folder_paths.get_output_directory()) / 'alpha_scene_director',
                       run_id, clip_id, first, last, int(images.shape[0]))
        return (images,)
