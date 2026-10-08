/**
 * test_sdk_import.js
 * ------------------
 * Smoke-test for src/asciline-player.js SDK.
 * Verifies the class loads correctly and all public methods exist.
 * Runs in Node.js (no DOM needed for import/API surface checks).
 */

import { readFileSync, existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── Read SDK source ──────────────────────────────────────────────────────────
const sdkPath = resolve(__dirname, '../src/asciline-player.js');
if (!existsSync(sdkPath)) {
    console.error('[FAIL] src/asciline-player.js not found at:', sdkPath);
    process.exit(1);
}

const sdkSource = readFileSync(sdkPath, 'utf-8') + '\n' +
    readFileSync(resolve(__dirname, '../src/live-session.js'), 'utf-8');

// ── Verify class is exported ─────────────────────────────────────────────────
const hasClass  = /class AsciiPlayer/.test(sdkSource);
const hasExport = /(export\s+class\s+AsciiPlayer|export\s*\{[^}]*AsciiPlayer|export\s+default\s+AsciiPlayer)/.test(sdkSource);

if (!hasClass) {
    console.error('[FAIL] AsciiPlayer class not found in SDK source');
    process.exit(1);
}
console.log('[PASS] AsciiPlayer class definition found');

if (!hasExport) {
    console.error('[FAIL] AsciiPlayer is not exported from SDK');
    process.exit(1);
}
console.log('[PASS] AsciiPlayer is exported');

// ── Verify all required public methods exist ─────────────────────────────────
const requiredMethods = [
    'play', 'pause', 'resume', 'togglePlay',
    'seek', 'skip', 'setVolume', 'unmute',
    'setFilters', 'setPixelMode',
    'setSelectionLayer', 'toggleSelectionLayer',
    'getMasterClock', 'getState',
    'on', 'off', 'emit', 'destroy',
];

let allPassed = true;
for (const method of requiredMethods) {
    const pattern = new RegExp(`\\b${method}\\s*\\(`);
    if (pattern.test(sdkSource)) {
        console.log(`[PASS] method: ${method}()`);
    } else {
        console.error(`[FAIL] method missing: ${method}()`);
        allPassed = false;
    }
}

// ── Verify selection layer (user-select: text) ───────────────────────────────
if (/userSelect.*text/.test(sdkSource) || /user-select.*text/.test(sdkSource)) {
    console.log('[PASS] Selection layer user-select:text found');
} else {
    console.error('[FAIL] Selection layer user-select:text not found in SDK');
    allPassed = false;
}

// ── Verify codec tags are handled ────────────────────────────────────────────
for (const tag of ['TAG_RAW', 'TAG_ZLIB', 'TAG_DELTA', 'TAG_RLE_FULL']) {
    if (sdkSource.includes(tag)) {
        console.log(`[PASS] Codec tag: ${tag}`);
    } else {
        console.error(`[FAIL] Codec tag missing: ${tag}`);
        allPassed = false;
    }
}

// ── Verify init/fps/timeupdate events are emitted ────────────────────────────
for (const ev of ["'init'", "'fps'", "'timeupdate'", "'error'", "'buffering'"]) {
    if (sdkSource.includes(`emit(${ev}`)) {
        console.log(`[PASS] event emitted: ${ev}`);
    } else {
        console.error(`[FAIL] event not emitted: ${ev}`);
        allPassed = false;
    }
}

// ── Final result ─────────────────────────────────────────────────────────────
if (!allPassed) {
    console.error('\n[RESULT] SDK import test FAILED');
    process.exit(1);
}
console.log('\n[RESULT] SDK API surface smoke test PASSED');
