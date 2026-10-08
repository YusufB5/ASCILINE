"""Backend selection and fallback behavior independent of native installation."""
from unittest.mock import patch
import pytest
from asciline.engines import get_engine
from asciline.engines import Engine
from types import SimpleNamespace
from unittest.mock import Mock


def test_python_does_not_load_native():
    with patch("asciline.engines._load_native", side_effect=AssertionError("unexpected load")):
        assert get_engine("python").name == "python"


def test_auto_preserves_failure_reason(caplog):
    with patch("asciline.engines._load_native", side_effect=ImportError("missing DLL")):
        engine = get_engine("auto")
    assert engine.name == "python"
    assert "missing DLL" in engine.fallback_reason
    assert "missing DLL" in caplog.text


def test_explicit_rust_does_not_silently_fallback():
    with patch("asciline.engines._load_native", side_effect=ImportError("missing DLL")):
        with pytest.raises(RuntimeError, match="missing DLL"):
            get_engine("rust")


def test_invalid_selection():
    with pytest.raises(ValueError):
        get_engine("unknown")


def test_decode_threads_optional_and_native_capability():
    implementation = Mock()
    native = SimpleNamespace(VideoDecoder=implementation)
    engine = Engine("rust", native)
    engine.decoder("clip.mp4", 16, 16, decode_threads=None)
    implementation.assert_called_once_with("clip.mp4", 16, 16)
    with pytest.raises(RuntimeError, match="rebuild"):
        engine.decoder("clip.mp4", 16, 16, decode_threads=2)
    native.DECODE_THREADS_SUPPORTED = True
    engine.decoder("clip.mp4", 16, 16, decode_threads=0)
    implementation.assert_called_with("clip.mp4", 16, 16, decode_threads=0)


@pytest.mark.parametrize("value", [-1, 65, True, 1.5, "2"])
def test_decode_threads_rejects_invalid_values(value):
    with pytest.raises(ValueError, match="between 0 and 64"):
        get_engine("python").decoder("unused", 16, 16, decode_threads=value)


def test_decode_threads_rejects_python_and_webcam():
    with pytest.raises(ValueError, match="Rust file"):
        get_engine("python").decoder("unused", 16, 16, decode_threads=2)
    with pytest.raises(ValueError, match="Rust file"):
        Engine("rust", SimpleNamespace()).decoder(0, 16, 16, decode_threads=2)
