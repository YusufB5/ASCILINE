"""Keep default grid budgets and allow an explicit resolution-only override."""
import pytest

from stream_server import calc_auto_dimensions


@pytest.mark.parametrize("cols,width,height,pixel,expected", [
    (750, 1280, 720, True, (750, 422)),
    (450, 1280, 720, True, (450, 253)),
    (450, 720, 1280, True, (450, 800)),
    (750, 1280, 720, False, (750, 211)),
    (200, 720, 1280, False, (200, 178)),
    (1, 1280, 720, False, (1, 1)),
])
def test_requested_grid_is_preserved_only_with_override(cols, width, height, pixel, expected):
    assert calc_auto_dimensions(
        cols, width, height, pixel, no_resolution_limit=True,
    ) == expected


@pytest.mark.parametrize("cols,width,height,pixel,expected", [
    (750, 1280, 720, True, (471, 265)),
    (450, 1280, 720, True, (450, 253)),
    (450, 720, 1280, True, (254, 452)),
    (750, 1280, 720, False, (207, 58)),
    (200, 720, 1280, False, (96, 85)),
    (1, 1280, 720, False, (1, 1)),
])
def test_default_keeps_original_grid_budgets(cols, width, height, pixel, expected):
    assert calc_auto_dimensions(cols, width, height, pixel) == expected
