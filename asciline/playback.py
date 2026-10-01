"""Live frame-rate policy, independent of decoder implementation and grid size."""
import math


def resolve_fps_limit(engine_name, pixel_mode, requested_fps=None):
    if requested_fps is not None:
        value = float(requested_fps)
        if not math.isfinite(value) or value <= 0:
            raise ValueError("fps must be a finite number greater than zero")
        return value
    return 60.0 if pixel_mode and engine_name == "rust" else 30.0


def frame_sampling(source_fps, fps_limit):
    """Keep evenly spaced source frames; never interpolate or exceed the limit."""
    skip_n = max(1, math.ceil(source_fps / fps_limit))
    return skip_n, source_fps / skip_n
