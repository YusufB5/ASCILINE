"""Audio must not accidentally use an incompatible FFmpeg from PATH."""
import json
import os

import pytest

from asciline import media_runtime


def runtime(tmp_path):
    executable = tmp_path / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    executable.parent.mkdir()
    executable.touch()
    return executable


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    monkeypatch.delenv("ASCILINE_FFMPEG_DIR", raising=False)
    monkeypatch.delenv("FFMPEG_DIR", raising=False)


def test_native_audio_uses_build_runtime_instead_of_path(tmp_path, monkeypatch):
    executable = runtime(tmp_path)
    config = tmp_path / "runtime.json"
    config.write_text(json.dumps({"ffmpeg_dir": str(tmp_path)}))
    monkeypatch.setattr(media_runtime, "_RUNTIME_CONFIG", config)
    monkeypatch.setattr(media_runtime.shutil, "which", lambda _: "obsolete-ffmpeg")
    assert media_runtime.ffmpeg_executable(native=True) == str(executable.resolve())
    assert media_runtime.ffmpeg_executable(native=False) == "obsolete-ffmpeg"


def test_explicit_runtime_overrides_path(tmp_path, monkeypatch):
    executable = runtime(tmp_path)
    monkeypatch.setenv("ASCILINE_FFMPEG_DIR", str(tmp_path))
    assert media_runtime.ffmpeg_executable() == str(executable.resolve())


def test_missing_explicit_executable_is_not_silently_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("ASCILINE_FFMPEG_DIR", str(tmp_path))
    monkeypatch.setattr(media_runtime.shutil, "which", lambda _: "obsolete-ffmpeg")
    with pytest.raises(FileNotFoundError, match="configured runtime"):
        media_runtime.ffmpeg_executable()
