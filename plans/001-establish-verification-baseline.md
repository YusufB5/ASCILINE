# Plan 001: Establish Reproducible Verification Baseline

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row for this plan in `plans/README.md`
> unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `rtk git diff --stat 312d5d6..HEAD -- README.md stream_server.py ascii_video_player2.py app.js .gitignore`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests, dx
- **Planned at**: commit `312d5d6`, 2026-06-12

## Why This Matters

ASCILINE currently has no tracked dependency manifest, test suite, or CI entry
point. The README gives an install command, but future agents cannot run one
stable command to prove the code still imports, the JavaScript still parses, or
core queue helpers still behave. This plan creates the smallest useful
verification baseline so later fixes can be made with confidence.

## Current State

- `README.md` documents manual installation only:

````markdown
README.md:45
### 2. Install dependencies
```bash
pip install fastapi uvicorn opencv-python numpy websockets
```
````

- `stream_server.py` imports runtime dependencies directly and documents a
  shorter dependency list than README:

```python
stream_server.py:13
import asyncio
import subprocess
import json
import numpy as np
import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
```

```python
stream_server.py:5
Dependencies: pip install fastapi uvicorn websockets
```

- `stream_server.py` has pure helpers worth testing before touching playback
  internals:

```python
stream_server.py:42
def calc_auto_rows(cols: int, vid_w: int, vid_h: int, pixel_mode: bool) -> int:
    ratio = vid_w / max(vid_h, 1)
    if pixel_mode:
        return max(1, round(cols / ratio))
    else:
        return max(1, round(cols / ratio / 2))
```

```python
stream_server.py:63
def resolve_video_path(video: str) -> str:
    candidates = [
        video,
        os.path.join(BASE_DIR, video),
        os.path.join(BASE_DIR, "videos", os.path.basename(video)),
    ]
```

- Existing local verification run during audit:
  - `rtk python3 -c "from pathlib import Path; [compile(Path(f).read_text(encoding='utf-8'), f, 'exec') for f in ['stream_server.py','ascii_video_player2.py']]; print('python syntax ok')"` exited 0.
  - `rtk node --check app.js` exited 0.
  - `rtk find . -maxdepth 2 -type f -name '*test*'` found no tests.
  - No tracked `requirements.txt`, `pyproject.toml`, test config, or CI files exist.

Repo conventions to preserve:

- Small flat repo, not a package directory tree.
- Python files use straightforward functions/classes and argparse entry points.
- Browser client is plain JavaScript in `app.js`; do not introduce a bundler.
- Commands in this Codex environment should be prefixed with `rtk`; if `rtk`
  is unavailable, run the raw command.

## Commands You Will Need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Check current Python syntax | `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` | exit 0 |
| Check current JS syntax | `rtk node --check app.js` | exit 0 |
| Install after manifest exists | `rtk python3 -m pip install -e ".[dev]"` | exit 0 |
| Test after tests exist | `rtk python3 -m pytest -q` | exit 0, all tests pass |
| no-mistakes gate | `rtk git push no-mistakes HEAD && rtk no-mistakes` | gate run opens/passes or reports scoped findings |

## Scope

**In scope**:

- `pyproject.toml` (create)
- `tests/` (create)
- `.github/workflows/ci.yml` (create)
- `README.md` (only update install/verification instructions if needed)
- `plans/README.md` (status update only)

**Out of scope**:

- Do not change runtime behavior in `stream_server.py`, `ascii_video_player2.py`,
  `app.js`, `index.html`, or `style.css`.
- Do not add a JavaScript build system.
- Do not pin exact dependency versions unless a test requires it.

## Git Workflow

- Branch: `codex/001-verification-baseline`
- Commit message style in this repo is mostly conventional commits, for example
  `feat: smart cols resolution` and `fix: remove copied architecture notes...`.
  Use `chore: add verification baseline`.
- Use the no-mistakes workflow from `plans/README.md` after local checks pass.
- Do not push to `origin` unless the operator explicitly asks.

## Steps

### Step 1: Add Python Project Metadata

Create `pyproject.toml` for a flat-module project. Use setuptools with explicit
modules so editable installs work without moving files:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "asciline"
version = "0.1.0"
description = "Real-time ASCII and pixel video streaming engine"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115,<1",
  "uvicorn[standard]>=0.30,<1",
  "opencv-python>=4.10,<5",
  "numpy>=2,<3",
  "websockets>=13,<16",
]

[project.optional-dependencies]
dev = [
  "pytest>=8,<9",
  "httpx>=0.27,<1",
]

[tool.setuptools]
py-modules = ["stream_server", "ascii_video_player2"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

**Verify**: `rtk python3 -m pip install -e ".[dev]"` -> exits 0.

### Step 2: Add Baseline Python Tests

Create `tests/test_stream_server_baseline.py`.

Test these cases:

- `calc_auto_rows(240, 1920, 1080, False)` returns `68` or the live exact value
  from current code. If the exact rounding differs on the target Python version,
  compute the expected value using the formula in the current state excerpt and
  assert that.
- `calc_auto_rows(450, 1920, 1080, True)` returns a positive integer larger
  than the ASCII-mode result for the same video.
- `load_folder(tmp_path, default_mode=3, default_vol=2)` includes only files
  ending in `.mp4`, `.mkv`, `.avi`, `.mov`, or `.webm`.
- `build_queue(SimpleNamespace(...))` fills missing playlist fields with global
  defaults. Use a temporary playlist file and monkeypatch
  `stream_server.BASE_DIR` if needed.

Keep tests focused on helper behavior; do not require real video decoding.

**Verify**: `rtk python3 -m pytest -q` -> exits 0 and reports the new tests pass.

### Step 3: Add Syntax Verification To Tests Or CI Script

Add either:

- a Python test file `tests/test_syntax_baseline.py` that invokes
  `python -m py_compile stream_server.py ascii_video_player2.py` and
  `node --check app.js` with `subprocess.run(..., check=True)`, or
- equivalent explicit CI steps in `.github/workflows/ci.yml`.

Prefer both if cheap: CI steps are clearer in logs, and a Python test keeps
local `pytest` meaningful.

**Verify**:

- `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` -> exits 0.
- `rtk node --check app.js` -> exits 0.
- `rtk python3 -m pytest -q` -> exits 0.

### Step 4: Add CI

Create `.github/workflows/ci.yml`:

- Trigger on `push` and `pull_request`.
- Use `actions/checkout`.
- Use `actions/setup-python` with Python 3.11.
- Use `actions/setup-node` with a current LTS node version only for
  `node --check app.js`; do not introduce npm.
- Install with `python -m pip install -e ".[dev]"`.
- Run:
  - `python -m py_compile stream_server.py ascii_video_player2.py`
  - `node --check app.js`
  - `python -m pytest -q`

**Verify**: `rtk python3 -m pytest -q && rtk node --check app.js` -> exits 0.

### Step 5: Update README Verification Notes

Update the install section to prefer:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
node --check app.js
```

Keep the simple manual `pip install ...` command only if you label it as a quick
runtime-only path.

**Verify**: `rtk rg -n "pytest|pyproject|pip install -e" README.md` -> shows the
new instructions.

### Step 6: Run no-mistakes Gate

Use the workflow in `plans/README.md`.

**Verify**: `rtk git push no-mistakes HEAD && rtk no-mistakes` -> the gate run
opens/passes or reports findings limited to this plan scope.

## Test Plan

- `tests/test_stream_server_baseline.py` covers helper behavior without real
  video files.
- Optional `tests/test_syntax_baseline.py` covers Python and JS parseability.
- CI repeats the same commands.

## Done Criteria

- [ ] `pyproject.toml` exists and `rtk python3 -m pip install -e ".[dev]"` exits 0.
- [ ] `rtk python3 -m pytest -q` exits 0.
- [ ] `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` exits 0.
- [ ] `rtk node --check app.js` exits 0.
- [ ] `.github/workflows/ci.yml` exists and runs install, Python syntax, JS syntax, and pytest.
- [ ] README documents the verification path.
- [ ] `rtk git status --short` shows only in-scope files changed.
- [ ] no-mistakes gate has run.
- [ ] `plans/README.md` status row updated.

## STOP Conditions

Stop and report back if:

- Editable install cannot work without reorganizing source files into a package
  directory.
- Installing OpenCV fails in the target environment for reasons unrelated to
  the repo.
- Any test requires checking in a real video file.
- CI requires secrets, external services, or anything beyond GitHub-hosted
  runner capabilities.

## Maintenance Notes

Future plans should depend on this baseline and add regression tests beside the
helper or behavior they change. Reviewers should keep this baseline small:
avoid expanding it into broad linting/formatting until the runtime fixes have
landed.
