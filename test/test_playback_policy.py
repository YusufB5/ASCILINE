"""Frame-rate defaults and overrides are independent of resolution limits."""
import pytest

from asciline import AsciiStreamServer
from asciline.playback import frame_sampling, resolve_fps_limit


@pytest.mark.parametrize("engine,pixel,requested,source,expected", [
    ("rust", False, None, 60, 30),
    ("rust", True, None, 60, 60),
    ("python", False, None, 60, 30),
    ("python", True, None, 60, 30),
    ("rust", False, 60, 60, 60),
    ("python", False, 60, 60, 60),
    ("rust", True, 30, 60, 30),
    ("rust", False, 60, 24, 24),
    ("rust", False, None, 60000 / 1001, 30000 / 1001),
    ("rust", False, None, 50, 25),
])
def test_output_rate(engine, pixel, requested, source, expected):
    limit = resolve_fps_limit(engine, pixel, requested)
    skip_n, fps = frame_sampling(source, limit)
    assert fps == pytest.approx(expected)
    assert skip_n >= 1
    assert fps <= limit


@pytest.mark.parametrize("fps", [0, -1, float("nan"), float("inf")])
def test_invalid_rate_rejected_by_library(fps):
    with pytest.raises(ValueError, match="fps"):
        AsciiStreamServer(fps=fps)
