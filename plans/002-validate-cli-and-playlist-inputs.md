# Plan 002: Validate CLI And Playlist Playback Inputs

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row for this plan in `plans/README.md`
> unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `rtk git diff --stat 312d5d6..HEAD -- stream_server.py tests plans/README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/001-establish-verification-baseline.md`
- **Category**: bug
- **Planned at**: commit `312d5d6`, 2026-06-12

## Why This Matters

Playback dimensions and playlist values flow directly into OpenCV resizing,
NumPy buffer allocation, FFmpeg volume filters, and client protocol metadata.
Today, CLI `--cols`, `--rows`, and `--vol` are plain integers with no bounds,
and playlist entries can override `mode`, `pixel`, `vol`, `cols`, and `rows`
without schema validation. A typo or huge value can crash playback or allocate
very large buffers before the user sees a helpful error.

## Current State

- Playlist values are accepted and defaulted without validating type/range:

```python
stream_server.py:114
if args.playlist:
    print(f"[PLAYLIST] Loading: {args.playlist}")
    items = load_playlist(args.playlist)
    # Fill missing fields with global defaults
    for item in items:
        item.setdefault("mode", args.mode)
        item.setdefault("vol",  args.vol)
        item.setdefault("pixel", args.pixel)

        is_pixel = item.get("pixel", False)
        default_cols = args.cols if args.cols is not None else (450 if is_pixel else 200)
        item.setdefault("cols", default_cols)
        item.setdefault("rows", args.rows)
```

- CLI dimensions and volume are unbounded:

```python
stream_server.py:522
render.add_argument("--cols", type=int, default=None, help="Grid columns (default: 200 for text, 450 for pixel)")
render.add_argument("--rows", type=int, default=0,   help="Grid rows    (default: auto from video aspect ratio)")
```

```python
stream_server.py:527
playback.add_argument(
    "--vol",
    type=int, default=1,
    help="Volume 0-5  (0=muted, 1=normal, 5=double)"
)
```

- Playback allocates based on unchecked `rows * cols`:

```python
stream_server.py:306
frame_buf = np.empty((rows, cols, 4), dtype=np.uint8) if render_mode > 1 else None
```

```python
stream_server.py:313
if pixel_mode:
    pixel_send_buf = bytearray(4 + rows * cols * 3)
elif render_mode > 1:
    ascii_send_buf = bytearray(4 + rows * cols * 4)
```

Repo conventions to preserve:

- Keep `build_queue(args)` as the central queue construction path.
- Keep user-facing errors as concise `[ERROR] ...` CLI output plus exit code 1.
- Do not introduce Pydantic for this small local CLI unless it remains clearly
  simpler than local helper functions.

## Commands You Will Need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Install dev deps | `rtk python3 -m pip install -e ".[dev]"` | exit 0 |
| Python syntax | `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` | exit 0 |
| JS syntax | `rtk node --check app.js` | exit 0 |
| Tests | `rtk python3 -m pytest -q` | exit 0, all tests pass |
| no-mistakes gate | `rtk git push no-mistakes HEAD && rtk no-mistakes` | gate run opens/passes or reports scoped findings |

## Scope

**In scope**:

- `stream_server.py`
- `tests/test_stream_server_validation.py` (create or extend tests from plan 001)
- `README.md` only if validation limits must be documented
- `plans/README.md` status update only

**Out of scope**:

- Do not rewrite the WebSocket frame protocol.
- Do not change rendering algorithms in `ascii_video_player2.py`.
- Do not change the static root / host binding finding; the operator deferred it.
- Do not remove playlist, folder, or single-video modes.

## Git Workflow

- Branch: `codex/002-validate-playback-inputs`
- Commit message: `fix: validate playback inputs`
- Use the no-mistakes workflow from `plans/README.md` after local checks pass.
- Do not push to `origin` unless the operator explicitly asks.

## Steps

### Step 1: Add Validation Constants And Helpers

In `stream_server.py`, near the top-level helper functions, add named constants
and a small validation layer. Suggested starting values:

```python
MIN_COLS = 1
MAX_COLS = 1200
MIN_ROWS = 0
MAX_ROWS = 800
MAX_CELLS = 500_000
MIN_VOL = 0
MAX_VOL = 5
VALID_MODES = {1, 2, 3, 4, 5}
```

Add helpers with explicit names, for example:

- `validate_int_range(name: str, value: object, min_value: int, max_value: int) -> int`
- `validate_bool(name: str, value: object) -> bool`
- `validate_queue_entry(entry: dict, index: int) -> dict`
- `validate_dimensions(cols: int, rows: int) -> None`

Rules:

- `mode` must be an integer in `1..5`.
- `pixel` must be boolean after defaults are applied.
- `vol` must be integer in `0..5`.
- `cols` must be integer in `1..1200`.
- `rows` must be integer in `0..800`; `0` means auto.
- If `pixel` is true, `mode` must be `2..5`.
- If `rows > 0`, `cols * rows` must be `<= MAX_CELLS`.
- Playlist `video` must exist and be a non-empty string before resolving.

Raise `ValueError` with messages that include the playlist entry index when
applicable, for example `playlist entry 2: vol must be between 0 and 5`.

**Verify**: `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` -> exits 0.

### Step 2: Use Validation In Queue Construction

Update `load_playlist` and/or `build_queue` so every returned queue item has
validated `video`, `mode`, `vol`, `pixel`, `cols`, and `rows` fields.

Important details:

- Preserve default behavior: text mode defaults to `cols=200`; pixel mode
  defaults to `cols=450`; `rows=0` means auto.
- Validate single-video and folder-generated entries too, not just playlists.
- Keep path resolution behavior unchanged after the `video` field passes the
  non-empty string check.
- Wrap `queue = build_queue(args)` in the `__main__` block with a `try/except
  ValueError` that prints `[ERROR] {message}` and exits 1.

**Verify**:

- `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` -> exits 0.
- `rtk python3 stream_server.py --help >/tmp/asciline-help.txt` -> exits 0 and
  does not start the server.

### Step 3: Validate Auto-Calculated Rows Before Allocation

In `websocket_endpoint`, after auto rows are calculated or explicit rows are
selected, call `validate_dimensions(cols, rows)` before `VideoDecoder(...)` and
before any NumPy/bytearray allocation.

If validation fails at this stage:

- Send a WebSocket text message starting with `Error:`.
- Advance to the next queue item using the same existing queue-advance behavior
  used for missing files.
- Do not crash the server task.

**Verify**: `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` -> exits 0.

### Step 4: Add Regression Tests

Create `tests/test_stream_server_validation.py` or extend an existing test file.
Cover:

- valid single-video args produce one normalized queue entry;
- `vol=-1` and `vol=6` are rejected;
- `cols=0`, `cols=-1`, and `cols > MAX_COLS` are rejected;
- `rows=-1` and `rows > MAX_ROWS` are rejected;
- playlist entry missing `video` is rejected with entry index in the message;
- playlist entry with `"pixel": true, "mode": 1` is rejected;
- explicit `cols * rows > MAX_CELLS` is rejected;
- `rows=0` is accepted so auto-scaling still works.

Use `types.SimpleNamespace` to construct `args` objects instead of invoking the
server. Use temporary playlist JSON files with `tmp_path`; do not require real
video files.

**Verify**: `rtk python3 -m pytest -q` -> exits 0 and includes the new tests.

### Step 5: Update README If Limits Are User-Facing

If you added hard maximums, document them near the resolution guidance so users
know why extreme values are rejected.

Keep wording practical:

```markdown
ASCILINE rejects extreme grid sizes before playback to avoid accidental memory
spikes. Keep `--cols` at or below 1200 and explicit `--rows` at or below 800.
```

**Verify**: `rtk rg -n "1200|800|memory" README.md` -> shows the new note, if
README was updated.

### Step 6: Run no-mistakes Gate

Use the workflow in `plans/README.md`.

**Verify**: `rtk git push no-mistakes HEAD && rtk no-mistakes` -> the gate run
opens/passes or reports findings limited to this plan scope.

## Test Plan

- Python unit tests for queue normalization and validation edge cases.
- Existing syntax checks remain green.
- No real video files or FFmpeg process required.

## Done Criteria

- [ ] Invalid CLI/playlist `mode`, `pixel`, `vol`, `cols`, and `rows` fail with clear errors.
- [ ] `--pixel` with mode 1 is rejected for playlist entries as well as global CLI args.
- [ ] Auto-calculated dimensions are checked before frame buffer allocation.
- [ ] `rtk python3 -m pytest -q` exits 0.
- [ ] `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` exits 0.
- [ ] `rtk node --check app.js` exits 0.
- [ ] `rtk git status --short` shows only in-scope files changed.
- [ ] no-mistakes gate has run.
- [ ] `plans/README.md` status row updated.

## STOP Conditions

Stop and report back if:

- The desired validation requires changing the WebSocket protocol.
- A test cannot be written without checking in or downloading a video file.
- Existing plan 001 verification baseline is missing.
- Validation changes require touching `ascii_video_player2.py` beyond syntax or
  import compatibility.

## Maintenance Notes

If future features add higher-quality pixel modes, revisit `MAX_COLS`,
`MAX_ROWS`, and `MAX_CELLS` together. Reviewers should scrutinize error
messages and default preservation: valid existing README commands should still
build the same queue.
