import py_compile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_entrypoints_compile():
    py_compile.compile(str(ROOT / "stream_server.py"), doraise=True)
    py_compile.compile(str(ROOT / "ascii_video_player2.py"), doraise=True)
