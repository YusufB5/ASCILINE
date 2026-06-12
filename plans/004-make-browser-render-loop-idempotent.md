# Plan 004: Make Browser Render Loop Idempotent

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report. When done, update the status row for this plan in `plans/README.md`
> unless a reviewer told you they maintain the index.
>
> **Drift check (run first)**:
> `rtk git diff --stat 312d5d6..HEAD -- app.js tests plans/README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: `plans/001-establish-verification-baseline.md`
- **Category**: bug, perf
- **Planned at**: commit `312d5d6`, 2026-06-12

## Why This Matters

The client starts rendering when audio is ready, but it also has a 500ms
fallback for muted or failed audio. If the fallback fires and the audio later
emits `playing`, `beginRendering()` can schedule a second `requestAnimationFrame`
loop. Multiple render loops compete for the same frame buffer, which can cause
jitter, extra CPU use, and hard-to-debug playback timing behavior.

## Current State

- `beginRendering()` schedules RAF unconditionally:

```javascript
app.js:174
const beginRendering = () => {
    readyToRender = true;
    streamStartTime = performance.now();
    lastRenderTime = performance.now();
    lastFpsUpdate = lastRenderTime;
    requestAnimationFrame(renderFrame);
};
```

- Both `playing` and the fallback can call it:

```javascript
app.js:193
audioEl.addEventListener('playing', beginRendering, { once: true });
// Fallback: if audio fails to load (vol=0 / 204), start after 500ms
setTimeout(() => {
    if (!readyToRender) beginRendering();
}, 500);
```

- `renderFrame` also schedules the next frame unconditionally while playing:

```javascript
app.js:248
function renderFrame(now) {
    if (state !== 'PLAYING' || !readyToRender) return;
    requestAnimationFrame(renderFrame);
```

- Cleanup does not cancel any outstanding RAF id:

```javascript
app.js:341
function finishStream() {
    state = 'IDLE';
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
    if (audioEl) { audioEl.pause(); audioEl.src = ''; }
```

Repo conventions to preserve:

- Plain JavaScript; no bundler, framework, or npm dependency.
- Keep the audio ready gate behavior: wait for audio when possible, fallback for
  muted/204 audio.

## Commands You Will Need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| Install dev deps | `rtk python3 -m pip install -e ".[dev]"` | exit 0 |
| JS syntax | `rtk node --check app.js` | exit 0 |
| Full tests | `rtk python3 -m pytest -q` | exit 0, all tests pass |
| Python syntax | `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` | exit 0 |
| no-mistakes gate | `rtk git push no-mistakes HEAD && rtk no-mistakes` | gate run opens/passes or reports scoped findings |

## Scope

**In scope**:

- `app.js`
- `tests/test_frontend_static.py` (create or extend)
- `plans/README.md` status update only

**Out of scope**:

- Do not redesign the player UI.
- Do not change the WebSocket protocol.
- Do not add Playwright, jsdom, npm, or a frontend build system.
- Do not modify server files except if a syntax/test command requires no
  behavioral change; report first if that happens.

## Git Workflow

- Branch: `codex/004-idempotent-render-loop`
- Commit message: `fix: make render loop idempotent`
- Use the no-mistakes workflow from `plans/README.md` after local checks pass.
- Do not push to `origin` unless the operator explicitly asks.

## Steps

### Step 1: Track The Active RAF

In the state section near the timing variables, add:

```javascript
let renderLoopId = null;
```

**Verify**: `rtk node --check app.js` -> exits 0.

### Step 2: Add A Single Render-Loop Starter

Replace the inline INIT `beginRendering` logic with a helper function at module
scope, for example:

```javascript
function beginRendering() {
    if (renderLoopId !== null) return;
    readyToRender = true;
    streamStartTime = performance.now();
    lastRenderTime = performance.now();
    lastFpsUpdate = lastRenderTime;
    renderLoopId = requestAnimationFrame(renderFrame);
}
```

Then in the INIT handler, call this shared helper from:

- the immediate `audioEl.readyState >= 3` path;
- the `playing` event listener;
- the 500ms fallback;
- the no-audio-element branch.

Keep the existing fallback guard `if (!readyToRender) beginRendering();`, but the
new helper must also guard on `renderLoopId !== null`.

**Verify**: `rtk node --check app.js` -> exits 0.

### Step 3: Keep RAF Id Updated And Clear It On Stop

Update `renderFrame(now)` so it owns the active RAF id:

```javascript
function renderFrame(now) {
    renderLoopId = null;
    if (state !== 'PLAYING' || !readyToRender) return;
    renderLoopId = requestAnimationFrame(renderFrame);
    ...
}
```

Update `finishStream()` to cancel any pending frame:

```javascript
if (renderLoopId !== null) {
    cancelAnimationFrame(renderLoopId);
    renderLoopId = null;
}
```

Do this before clearing buffers and showing the overlay.

**Verify**: `rtk node --check app.js` -> exits 0.

### Step 4: Add A Lightweight Static Regression Test

Create `tests/test_frontend_static.py`. Since this repo has no frontend test
runner and this plan must not add one, use a focused static guard:

- read `app.js`;
- assert it contains `let renderLoopId = null`;
- assert `beginRendering` checks `renderLoopId !== null`;
- assert `renderFrame` assigns `renderLoopId = requestAnimationFrame(renderFrame)`;
- assert `finishStream` calls `cancelAnimationFrame(renderLoopId)`;
- run `node --check app.js` with `subprocess.run(..., check=True)`.

This is not a full behavior test, but it preserves the no-dependency frontend
setup while catching regressions to the exact bug fixed here.

**Verify**: `rtk python3 -m pytest -q` -> exits 0.

### Step 5: Run no-mistakes Gate

Use the workflow in `plans/README.md`.

**Verify**: `rtk git push no-mistakes HEAD && rtk no-mistakes` -> the gate run
opens/passes or reports findings limited to this plan scope.

## Test Plan

- `node --check app.js` for JS parseability.
- `tests/test_frontend_static.py` for the render-loop guard and cleanup shape.
- Full pytest suite from plan 001.

## Done Criteria

- [ ] `beginRendering()` is idempotent.
- [ ] `renderFrame()` stores the active RAF id.
- [ ] `finishStream()` cancels and clears any active RAF id.
- [ ] No new frontend dependencies are added.
- [ ] `rtk node --check app.js` exits 0.
- [ ] `rtk python3 -m pytest -q` exits 0.
- [ ] `rtk python3 -m py_compile stream_server.py ascii_video_player2.py` exits 0.
- [ ] `rtk git status --short` shows only in-scope files changed.
- [ ] no-mistakes gate has run.
- [ ] `plans/README.md` status row updated.

## STOP Conditions

Stop and report back if:

- The app has already gained a frontend test runner or module system; this plan
  should be rewritten to use that instead of static tests.
- The render loop has been substantially refactored since the excerpts above.
- Fixing duplicate RAF loops requires changing server timing or the WebSocket
  protocol.

## Maintenance Notes

If future client work adds pause/resume, seeking, or playlist controls, route
all render-loop starts through the same idempotent starter. Reviewers should
look for accidental extra `requestAnimationFrame(renderFrame)` calls outside the
helper.
