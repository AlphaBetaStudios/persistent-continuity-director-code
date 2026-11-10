"""Install this entire folder inside ComfyUI/custom_nodes."""
from .nodes import AlphaH3DurationFrames, AlphaSceneCheckpoint, AlphaSceneDirector

NODE_CLASS_MAPPINGS = {
    'AlphaSceneDirector': AlphaSceneDirector,
    'AlphaSceneCheckpoint': AlphaSceneCheckpoint,
    'AlphaH3DurationFrames': AlphaH3DurationFrames,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    'AlphaSceneDirector': 'Alpha Scene Director',
    'AlphaSceneCheckpoint': 'Alpha Scene Checkpoint',
    'AlphaH3DurationFrames': 'Alpha H3 Duration to Frames',
}
WEB_DIRECTORY = './web'
__all__ = ['NODE_CLASS_MAPPINGS', 'NODE_DISPLAY_NAME_MAPPINGS', 'WEB_DIRECTORY']

import folder_paths
from server import PromptServer
from .server_api import register_routes

_manager = register_routes(PromptServer.instance, folder_paths.get_input_directory(), folder_paths.get_output_directory())
