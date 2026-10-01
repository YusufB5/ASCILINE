"""Backend selection and fallback behavior independent of native installation."""
from unittest.mock import patch
import pytest
from asciline.engines import get_engine


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
