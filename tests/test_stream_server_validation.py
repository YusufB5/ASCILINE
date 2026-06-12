import json
from types import SimpleNamespace

import pytest

import stream_server


def make_args(**overrides):
    values = {
        "video": "video.mp4",
        "playlist": None,
        "folder": None,
        "mode": 1,
        "vol": 1,
        "pixel": False,
        "cols": None,
        "rows": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def assert_rejected(args, message_part):
    with pytest.raises(ValueError, match=message_part):
        stream_server.build_queue(args)


def test_valid_single_video_args_produce_normalized_queue_entry():
    queue = stream_server.build_queue(
        make_args(video="movie.mp4", mode=5, vol=3, pixel=True, cols=520, rows=240)
    )

    assert queue == [
        {
            "video": "movie.mp4",
            "mode": 5,
            "vol": 3,
            "pixel": True,
            "cols": 520,
            "rows": 240,
        }
    ]


@pytest.mark.parametrize("vol", [-1, 6])
def test_rejects_volume_outside_supported_range(vol):
    assert_rejected(make_args(vol=vol), "vol must be between 0 and 5")


@pytest.mark.parametrize("cols", [0, -1, stream_server.MAX_COLS + 1])
def test_rejects_invalid_columns(cols):
    assert_rejected(make_args(cols=cols), "cols must be between")


@pytest.mark.parametrize("rows", [-1, stream_server.MAX_ROWS + 1])
def test_rejects_invalid_rows(rows):
    assert_rejected(make_args(rows=rows), "rows must be between")


def test_rejects_playlist_entry_missing_video(tmp_path):
    playlist = tmp_path / "playlist.json"
    playlist.write_text(json.dumps([{"mode": 3}]), encoding="utf-8")

    assert_rejected(make_args(playlist=str(playlist)), "playlist entry 1: video")


def test_rejects_pixel_playlist_entry_in_text_mode(tmp_path):
    playlist = tmp_path / "playlist.json"
    playlist.write_text(
        json.dumps([{"video": "clip.mp4", "mode": 1, "pixel": True}]),
        encoding="utf-8",
    )

    assert_rejected(make_args(playlist=str(playlist)), "pixel mode requires color mode 2-5")


def test_rejects_explicit_grid_larger_than_cell_limit():
    assert_rejected(
        make_args(mode=5, pixel=True, cols=1200, rows=800),
        "grid size 1200x800 exceeds",
    )


def test_accepts_zero_rows_for_auto_scaling():
    queue = stream_server.build_queue(make_args(cols=200, rows=0))

    assert queue[0]["rows"] == 0
