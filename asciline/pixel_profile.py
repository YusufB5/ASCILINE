"""Session-owned DCT encoding with the main tag-4 format and live timestamps."""
import struct

from codec import ProfileEncoder


def align_profile_grid(cols, rows):
    """YUV420 and 8x8 transforms require both coded dimensions divisible by 16."""
    return ((cols + 15) // 16 * 16, (rows + 15) // 16 * 16)


def validate_profile_quality(quality):
    if not isinstance(quality, int) or not 1 <= quality <= 100:
        raise ValueError("DCT quality must be an integer from 1 to 100")
    return quality


class PixelProfile:
    def __init__(self, engine, cols, rows, quality=70):
        validate_profile_quality(quality)
        self.cols, self.rows = cols, rows
        self.engine = engine
        self.quality = quality
        if engine.native and not hasattr(engine.native, "ProfileEncoder"):
            raise RuntimeError("Native DCT is unavailable; rebuild rust_core with this checkout")
        self.reset()

    def reset(self):
        # An independent predictor per WebSocket session, reset on every seek.
        encoder_type = self.engine.native.ProfileEncoder if self.engine.native else ProfileEncoder
        self.encoder = encoder_type(self.cols, self.rows, self.quality, 0.75, 256, 3)

    def encode(self, frame, frame_index):
        packet, _shown = self.encoder.encode(frame)
        # The encoder counter schedules keyframes; it is not the playback clock.
        return struct.pack(">I", frame_index) + packet[4:]
