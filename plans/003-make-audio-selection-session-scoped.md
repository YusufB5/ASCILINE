# Plan 003: Make Audio Selection Session-Scoped

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row for this plan in `plans/README.md`
> unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `rtk git diff --stat 312d5d6..HEAD -- stream_server.py app.js tests plans/README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: MED
- **Depends on**: `plans/001-establish-verification-baseline.md`
- **Category**: bug
- **Planned at**: commit `312d5d6`, 2026-06-12

## Why This Matters

The WebSocket endpoint has a local `queue_index` per connected browser, but the
audio endpoint reads a global `app.state.current_index`. With two browser
sessions, one client's video loop can update the global index right before the
other client requests `/audio`, so the second client can receive the wrong
track. Audio selection should be derived from the client/session's queue item,
not from mutable global playback state.

## Current State

- `/audio` reads a global index:

```python
stream_server.py:156
@app.get("/audio")
async def audio_stream():
    queue = getattr(app.state, "queue", [])
    idx   = getattr(app.state, "current_index", 0)
    entry = queue[idx] if queue else {}
```

- The WebSocket endpoint owns a local `queue_index`, but writes it to global
  state before INIT:

```python
stream_server.py:235
queue_index = 0  # local index; advances through the queue
```

```python
stream_server.py:246
# IMPORTANT: Update current_index BEFORE sending INIT so that
# when the client reloads /audio in response to INIT, the endpoint
# already serves the correct video's audio.
app.state.current_index = queue_index
```

- The client always requests global `/audio` after INIT:

```javascript
app.js:182
if (audioEl) {
    audioEl.pause();
    audioEl.src = '/audio?' + Date.now();
    audioEl.volume = volumeSlider ? volumeSlider.value : 1.0;
    audioEl.load();
    audioEl.play().catch(() => {});
```

- INIT currently contains six fields:

```python
stream_server.py:302
await websocket.send_text(f"INIT:{effective_fps}:{render_mode}:{cols}:{rows}:{int(pixel_mode)}")
```

Repo conventions to preserve:

- Keep the compact colon-delimited `INIT` handshake; do not introduce a JSON
  protocol unless absolutely necessary.
- Keep `/audio` no-auth and local-app simple.
- Keep server-side volume behavior: `vol <= 0` returns 204 without launching
  FFmpeg.

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
- `app.js`
- `tests/test_audio_selection.py` (create or extend tests from plan 001)
- `plans/README.md` status update only

**Out of scope**:

- Do not add authentication, session cookies, or CSRF protection.
- Do not change static file serving or host binding.
- Do not change frame binary format except adding one optional field to the INIT
  text message.
- Do not modify video decoding internals.

## Git Workflow

- Branch: `codex/003-session-audio-selection`
- Commit message: `fix: make audio selection per client`
- Use the no-mistakes workflow from `plans/README.md` after local checks pass.
- Do not push to `origin` unless the operator explicitly asks.

## Steps

### Step 1: Add Indexed Audio Endpoint

In `stream_server.py`, add a new route:

```python
@app.get("/audio/{queue_index}")
async def audio_stream_for_index(queue_index: int):
    ...
```

Move the existing audio implementation into a helper such as:

```python
def get_queue_entry(queue_index: int) -> dict:
    queue = getattr(app.state, "queue", [])
    if queue_index < 0 or queue_index >= len(queue):
        raise HTTPException(status_code=404, detail="Audio entry not found")
    return queue[queue_index]
```

Important behavior:

- `/audio/{queue_index}` must select from the path parameter, not
  `app.state.current_index`.
- Invalid indices return 404.
- `vol <= 0` still returns 204 and never starts FFmpeg.
- For compatibility, you may keep `/audio` as a fallback route that uses
  `app.state.current_index`, but new client code must not use it.

**Verify**: `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` -> exits 0.

### Step 2: Send Queue Index In INIT

Update the INIT message to append the local `queue_index`:

```python
await websocket.send_text(
    f"INIT:{effective_fps}:{render_mode}:{cols}:{rows}:{int(pixel_mode)}:{queue_index}"
)
```

Remove the comment that says the global `current_index` is needed for `/audio`.
You may keep `app.state.current_index = queue_index` only for `/status` display
and backwards-compatible `/audio`, but it must no longer be required for normal
client playback.

**Verify**: `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` -> exits 0.

### Step 3: Update Client Audio URL

In `app.js`, parse the optional seventh INIT field:

```javascript
const audioIndex = p.length > 6 ? parseInt(p[6], 10) : 0;
```

Use it for audio:

```javascript
audioEl.src = `/audio/${audioIndex}?` + Date.now();
```

Keep compatibility with older INIT messages by defaulting to `0` if the field is
missing.

**Verify**: `rtk node --check app.js` -> exits 0.

### Step 4: Add Server Regression Tests

Create `tests/test_audio_selection.py`.

Use FastAPI `TestClient` after plan 001 has added `httpx`:

- Set `stream_server.app.state.queue` to two fake entries:
  - entry 0: `{"video": "missing-a.mp4", "mode": 1, "vol": 0, "pixel": False, "cols": 200, "rows": 0}`
  - entry 1: `{"video": "missing-b.mp4", "mode": 1, "vol": 0, "pixel": False, "cols": 200, "rows": 0}`
- Set `app.state.current_index = 1`.
- `GET /audio/0` returns 204 because entry 0 is muted. This proves the route did
  not use global `current_index`.
- `GET /audio/1` returns 204.
- `GET /audio/99` returns 404.

Do not test unmuted audio by launching FFmpeg.

**Verify**: `rtk python3 -m pytest -q` -> exits 0 and includes these tests.

### Step 5: Run no-mistakes Gate

Use the workflow in `plans/README.md`.

**Verify**: `rtk git push no-mistakes HEAD && rtk no-mistakes` -> the gate run
opens/passes or reports findings limited to this plan scope.

## Test Plan

- FastAPI tests prove indexed audio does not depend on global state.
- `node --check app.js` proves the client remains parseable.
- Existing baseline tests remain green.

## Done Criteria

- [ ] Client requests `/audio/<queue_index>` after INIT.
- [ ] Normal client playback no longer depends on `app.state.current_index` for audio selection.
- [ ] Invalid audio indices return 404.
- [ ] Muted indexed audio returns 204 without FFmpeg.
- [ ] `rtk python3 -m pytest -q` exits 0.
- [ ] `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` exits 0.
- [ ] `rtk node --check app.js` exits 0.
- [ ] `rtk git status --short` shows only in-scope files changed.
- [ ] no-mistakes gate has run.
- [ ] `plans/README.md` status row updated.

## STOP Conditions

Stop and report back if:

- The client has already been changed away from the colon-delimited INIT format.
- Tests require launching FFmpeg or reading a real media file.
- The fix appears to require adding authentication/session infrastructure.
- Plan 001 verification baseline is not present.

## Maintenance Notes

If future work adds seeking, playlists with duplicate entries, or per-client
volume, use an explicit playback/session identifier rather than returning to
global mutable state. Reviewers should focus on backwards compatibility and
whether `/audio/{queue_index}` is the only path used by current `app.js`.
