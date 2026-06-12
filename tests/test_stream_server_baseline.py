import json
from pathlib import Path
from types import SimpleNamespace

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


def test_calc_auto_rows_preserves_video_aspect_for_text_and_pixel():
    text_rows = stream_server.calc_auto_rows(240, 1920, 1080, pixel_mode=False)
    pixel_rows = stream_server.calc_auto_rows(450, 1920, 1080, pixel_mode=True)

    assert text_rows == round(240 / (1920 / 1080) / 2)
    assert pixel_rows == round(450 / (1920 / 1080))
    assert pixel_rows > text_rows


def test_load_folder_includes_only_supported_video_files(tmp_path):
    for name in ["intro.mp4", "clip.MOV", "notes.txt", "image.png", "scene.webm"]:
        (tmp_path / name).write_text("", encoding="utf-8")

    items = stream_server.load_folder(str(tmp_path), default_mode=3, default_vol=2)

    names = {Path(item["video"]).name for item in items}
    assert names == {"intro.mp4", "clip.MOV", "scene.webm"}
    assert {item["mode"] for item in items} == {3}
    assert {item["vol"] for item in items} == {2}


def test_build_queue_fills_playlist_defaults(tmp_path):
    playlist = tmp_path / "playlist.json"
    playlist.write_text(json.dumps([{"video": "clip.mp4"}]), encoding="utf-8")

    queue = stream_server.build_queue(
        make_args(playlist=str(playlist), mode=3, vol=2, pixel=False, cols=220)
    )

    assert queue == [
        {
            "video": "clip.mp4",
            "mode": 3,
            "vol": 2,
            "pixel": False,
            "cols": 220,
            "rows": 0,
        }
    ]
