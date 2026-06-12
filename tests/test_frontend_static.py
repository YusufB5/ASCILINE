import subprocess
from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "app.js"


def test_app_js_has_idempotent_render_loop_guard():
    source = APP_JS.read_text(encoding="utf-8")

    assert "let renderLoopId = null;" in source
    assert "if (renderLoopId !== null) return;" in source
    assert "renderLoopId = requestAnimationFrame(renderFrame);" in source
    assert "cancelAnimationFrame(renderLoopId);" in source


def test_app_js_syntax_is_valid():
    subprocess.run(["node", "--check", str(APP_JS)], check=True)
