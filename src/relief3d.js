/**
 * relief3d.js — 3D relief view for ASCILINE frames (WebGL2, no dependencies).
 *
 * Every grid cell becomes a block whose height follows its brightness. The
 * decoded frame bytes are uploaded unchanged as a cols×rows texture, so colour,
 * luminance and height are computed on the GPU: the per-frame CPU cost is one
 * texture upload and one instanced draw call, with no per-cell JavaScript loop.
 *
 * Frame layouts (the same bytes the 2D renderers draw):
 *   'bgr'  — pixel mode, 3 bytes per cell: B, G, R
 *   'cell' — ASCII colour modes 2–6, 4 bytes per cell: char code, R, G, B
 *   'text' — text-only mode 1, a string of rows separated by '
'; each
 *            character's ink density sets its grey level and height
 *
 * Styles: 'columns' extrude each cell from the base plane into a solid block;
 * 'floating' draws a thin tile at the same height, so cells hover in space.
 *
 * In 'cell' and 'text' layouts the block tops show the cell's character, so
 * the 3D view keeps the ASCII text instead of only its colour.
 *
 * Camera: drag to orbit, Shift/right/middle-drag to pan, wheel or pinch to
 * zoom; resetView() restores the default angle. A plain click is left to the
 * host page (the root player uses it for pause); a drag never reaches it.
 *
 * Touch modes react to the pointer: 'hole' is a black hole that spirals cells
 * in and sinks them, 'bump' raises them, 'repel' pushes blocks sideways out of the way, and
 * 'ripple' sends a wave across the grid from every click. In 'hole' mode a
 * click collapses the whole grid into the black hole and blasts it back out.
 * (Clicks used by these modes do not reach the host page.)
 */

const DEFAULTS = {
    depth: 1.0,        // block height scale (0 = flat)
    style: 'columns',  // 'columns' rise from the base; 'floating' thin tiles hover at their height
    gap: 0.06,         // gap between blocks, as a fraction of a cell
    glyphs: true,      // draw characters on block tops in 'cell' and 'text' layouts
    invert: false,     // dark cells stand out instead of bright ones
    autoOrbit: false,  // gentle automatic camera sway
    touch: 'off',      // 'off' | 'hole' | 'bump' | 'repel' | 'ripple'
    touchRadius: 0.22, // pointer influence radius (grid height = 2)
    touchStrength: 1.0, // overall power of the touch effects (0.2 subtle .. 3 extreme)
    renderScale: 1.0,  // backing-store scale (lower = cheaper)
    maxPixelRatio: 2,
    background: [0.02, 0.02, 0.02],
};

const DEPTH_UNIT = 0.25;      // world height of a white block at depth 1 (grid height = 2)
const MIN_HEIGHT = 0.004;     // keeps black cells as visible tiles
const FOV = 35 * Math.PI / 180;
const DEFAULT_YAW = -0.38;
const DEFAULT_PITCH = 0.30;
const TOUCH_MODES = { off: 0, hole: 1, bump: 2, repel: 3, ripple: 4 };
const MAX_RIPPLES = 8;
const RIPPLE_LIFE = 3.2;       // seconds a wave stays visible
// Black-hole collapse started by a click in HOLE mode (seconds): the grid is
// eaten from the click outward, hangs as an ember for a beat, then bursts back out.
const BANG_SUCK = 2.4, BANG_VOID = 0.4, BANG_BLAST = 1.6;
const BANG_TOTAL = BANG_SUCK + BANG_VOID + BANG_BLAST;
const EMBER_RADIUS = 0.21;     // world radius of the ball the picture is squeezed into

const VERT = `#version 300 es
precision highp float;
precision highp int;
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec3 aNormal;

uniform sampler2D uFrame;
uniform int uLayout;          // 0 = bgr, 1 = cell
uniform ivec2 uGrid;          // cols, rows
uniform vec2 uCell;           // world size of one cell
uniform float uDepth;
uniform float uGap;
uniform float uInvert;
uniform int uStyle;           // 0 = columns, 1 = floating tiles
uniform mat4 uViewProj;
uniform int uTouchMode;       // 1 hole, 2 bump, 3 repel (0/4: none)
uniform vec3 uTouch;          // pointer x, y (world) and strength 0..1
uniform float uTouchRadius;
uniform float uTouchStrength;
uniform float uTime;          // seconds, same clock as the ripple start times
uniform vec4 uRipples[${MAX_RIPPLES}];  // x, y, start time, amplitude (0 = unused)
uniform vec4 uBang;           // x, y, start time, max radius (0 = idle)

out vec3 vColor;
out vec3 vNormal;
out vec2 vUV;
flat out int vChar;

void main() {
    int col = gl_InstanceID % uGrid.x;
    int row = gl_InstanceID / uGrid.x;
    vec4 t = texelFetch(uFrame, ivec2(col, row), 0);
    vec3 color = uLayout == 0 ? t.bgr : t.gba;
    vChar = uLayout == 1 ? int(t.r * 255.0 + 0.5) : 0;

    float lum = dot(color, vec3(0.299, 0.587, 0.114));
    lum = mix(lum, 1.0 - lum, uInvert);
    float h = max(lum * uDepth * ${DEPTH_UNIT.toFixed(4)}, ${MIN_HEIGHT.toFixed(4)});

    vec2 half_ = vec2(uGrid) * uCell * 0.5;
    vec2 center = vec2(-half_.x + (float(col) + 0.5) * uCell.x, half_.y - (float(row) + 0.5) * uCell.y);
    vec2 shift = vec2(0.0);
    float lift = ${DEPTH_UNIT.toFixed(4)} * max(uDepth, 0.6);

    // Pointer: a soft Gaussian brush around the cursor.
    float dark = 0.0;   // 0..1, how much the hole swallows a cell's colour
    float rim = 0.0;    // 0..1, accretion-ring glow
    float zOff = 0.0;   // lifts a whole block off the floor (flying debris)
    if (uTouchMode >= 1 && uTouchMode <= 3 && uTouch.z > 0.0) {
        vec2 away = center - uTouch.xy;
        float s = uTouchStrength;
        if (uTouchMode == 1) {
            // Black hole: cells spiral into the pointer, the middle sinks and
            // goes dark, and an orange rim glows at the event horizon.
            float R = uTouchRadius * 1.8;
            float dist = length(away);
            float f = uTouch.z * exp(-(dist * dist) / (R * R));
            float pull = clamp(f * s * 0.97, 0.0, 0.985);
            float ang = f * s * 2.6;
            float ca = cos(ang), sa = sin(ang);
            vec2 spiral = vec2(ca * away.x - sa * away.y, sa * away.x + ca * away.y) * (1.0 - pull);
            shift = uTouch.xy + spiral - center;
            h *= 1.0 - clamp(f * s * 1.15, 0.0, 1.0);
            // The glow band sits just outside the dark core, whatever the strength.
            dark = smoothstep(0.62, 0.95, pull);
            rim = smoothstep(0.2, 0.55, pull) * (1.0 - smoothstep(0.62, 0.92, pull));
            h += rim * lift * 0.35;
        } else {
            float d = length(away) / uTouchRadius;
            float f = uTouch.z * exp(-d * d);
            if (uTouchMode == 2) h += f * lift * 1.4 * s;
            else {
                shift = (length(away) > 1e-5 ? normalize(away) : vec2(0.0)) * f * uTouchRadius * 0.85 * s;
                h *= 1.0 - clamp(0.45 * f * s, 0.0, 0.95);
            }
        }
    }

    // Click in HOLE mode: collapse, ember, blast, regrow. Every cell follows the
    // same timeline, offset by its distance from the click.
    float bangAge = uBang.w > 0.0 ? uTime - uBang.z : -1.0;
    if (bangAge >= 0.0 && bangAge < ${BANG_TOTAL.toFixed(3)}) {
        vec2 bc = uBang.xy;
        vec2 away = center - bc;
        float dist = length(away);
        vec2 dir = dist > 1e-5 ? away / dist : vec2(1.0, 0.0);
        float dn = clamp(dist / uBang.w, 0.0, 1.0);
        float baseH = h;
        // The swallowed picture ends up as a small glowing ember ball: cells keep
        // their order (inner ones near the middle) and form a dome.
        float emberR = ${EMBER_RADIUS.toFixed(3)};
        vec2 ember = dir * emberR * dn;
        float emberH = ${MIN_HEIGHT.toFixed(4)} + lift * 0.9 * (1.0 - dn);
        float pulse = 0.78 + 0.22 * sin(uTime * 16.0);
        if (bangAge < ${BANG_SUCK.toFixed(3)}) {
            // The cells nearest the click go first and the hunger spreads outward;
            // each one spirals in and joins the ember.
            float tn = bangAge / ${BANG_SUCK.toFixed(3)};
            float u = clamp((tn - dn * 0.3) / 0.7, 0.0, 1.0);
            if (u > 0.0) {
                float ue = u * u * (3.0 - 2.0 * u);
                float ang = ue * 5.5;
                float ca = cos(ang), sa = sin(ang);
                float rad = mix(dist, emberR * dn, pow(u, 1.5));
                vec2 p = vec2(ca * dir.x - sa * dir.y, sa * dir.x + ca * dir.y) * rad;
                shift = bc + p - center;
                h = mix(baseH, emberH, ue);
                dark = max(dark, 0.4 * smoothstep(0.4, 1.0, u));
                rim = max(rim, mix(smoothstep(0.1, 0.45, u) * (1.0 - smoothstep(0.55, 0.9, u)), pulse, smoothstep(0.85, 1.0, u)));
            }
        } else {
            float te = (bangAge - ${(BANG_SUCK + BANG_VOID).toFixed(3)}) / ${BANG_BLAST.toFixed(3)};
            float v = te < 0.0 ? 0.0 : clamp((te - dn * 0.45) / 0.55, 0.0, 1.0);
            if (v <= 0.0) {
                shift = bc + ember - center;
                h = emberH;
                dark = 0.4;
                rim = pulse;
            } else {
                // Blast: cells are flung back to their places with a little overshoot.
                float q = v - 1.0;
                float e = 1.0 + 2.70158 * q * q * q + 1.70158 * q * q;
                shift = (bc + ember - center) * (1.0 - e);
                float rnd = fract(sin(dot(vec2(float(col), float(row)), vec2(12.9898, 78.233))) * 43758.5453);
                zOff = sin(3.14159 * v) * (0.35 + 0.65 * rnd) * ${DEPTH_UNIT.toFixed(4)} * 2.4;
                h = mix(emberH, baseH, 1.0 - pow(1.0 - v, 3.0));
                dark = max(dark, 0.4 * (1.0 - smoothstep(0.0, 0.45, v)));
                rim = max(rim, 1.0 - smoothstep(0.0, 0.4, v));
            }
        }
    }

    // Ripples: expanding rings that lift the cells they pass over.
    for (int i = 0; i < ${MAX_RIPPLES}; i++) {
        vec4 r = uRipples[i];
        if (r.w <= 0.0) continue;
        float age = uTime - r.z;
        if (age < 0.0 || age > ${RIPPLE_LIFE.toFixed(2)}) continue;
        float ring = (length(center - r.xy) - age * 1.1) / 0.09;
        float fade = (1.0 - age / ${RIPPLE_LIFE.toFixed(2)});
        h += r.w * uTouchStrength * lift * 0.9 * exp(-ring * ring) * fade * fade;
    }
    h = max(h, ${MIN_HEIGHT.toFixed(4)});

    // Columns span 0..h; floating tiles are a thin slab whose top sits at h.
    float thick = uCell.y * 0.12;
    float z = (uStyle == 1 ? h + (aPos.z - 1.0) * thick : aPos.z * h) + zOff;

    vec2 inner = uGap * 0.5 + aPos.xy * (1.0 - uGap);
    vec3 world = vec3(
        -half_.x + (float(col) + inner.x) * uCell.x + shift.x,
         half_.y - (float(row) + 1.0 - inner.y) * uCell.y + shift.y,
         z);

    vColor = mix(color, color * vec3(1.0, 0.62, 0.3) * 1.7 + vec3(0.12, 0.05, 0.0), clamp(rim, 0.0, 1.0)) * (1.0 - 0.96 * dark);
    vNormal = aNormal;
    vUV = vec2(aPos.x, 1.0 - aPos.y);
    gl_Position = uViewProj * vec4(world, 1.0);
}`;

const FRAG = `#version 300 es
precision mediump float;
in vec3 vColor;
in vec3 vNormal;
in vec2 vUV;
flat in int vChar;

uniform sampler2D uAtlas;
uniform int uGlyphs;
uniform vec3 uLight;

out vec4 outColor;

void main() {
    vec3 c = vColor;
    if (vNormal.z > 0.5) {
        if (uGlyphs == 1 && vChar > 32 && vChar < 128) {
            vec2 cellUV = vec2(float(vChar % 16), float(vChar / 16));
            vec2 uv = (cellUV + mix(vec2(0.04), vec2(0.96), vUV)) / vec2(16.0, 8.0);
            float a = texture(uAtlas, uv).a;
            c = mix(c * 0.16, c, a);
        } else if (uGlyphs == 1) {
            c *= 0.16;
        }
    } else {
        c *= 0.32 + 0.58 * max(dot(vNormal, uLight), 0.0);
    }
    outColor = vec4(c, 1.0);
}`;

// Unit block without a bottom face: top + 4 sides, CCW seen from outside.
function blockGeometry() {
    const faces = [
        [[0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 0, 1], [1, 1, 1], [0, 1, 1], [0, 0, 1]],
        [[0, 0, 0], [1, 0, 0], [1, 0, 1], [0, 0, 0], [1, 0, 1], [0, 0, 1], [0, -1, 0]],
        [[1, 1, 0], [0, 1, 0], [0, 1, 1], [1, 1, 0], [0, 1, 1], [1, 1, 1], [0, 1, 0]],
        [[0, 1, 0], [0, 0, 0], [0, 0, 1], [0, 1, 0], [0, 0, 1], [0, 1, 1], [-1, 0, 0]],
        [[1, 0, 0], [1, 1, 0], [1, 1, 1], [1, 0, 0], [1, 1, 1], [1, 0, 1], [1, 0, 0]],
    ];
    const out = new Float32Array(faces.length * 6 * 6);
    let o = 0;
    for (const f of faces) {
        const n = f[6];
        for (let v = 0; v < 6; v++) {
            out[o++] = f[v][0]; out[o++] = f[v][1]; out[o++] = f[v][2];
            out[o++] = n[0]; out[o++] = n[1]; out[o++] = n[2];
        }
    }
    return out;
}

// ── Small column-major mat4 helpers ─────────────────────────────────────────
function perspective(out, fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    out.fill(0);
    out[0] = f / aspect; out[5] = f;
    out[10] = (far + near) * nf; out[11] = -1;
    out[14] = 2 * far * near * nf;
    return out;
}

function lookAt(out, eye, target, up) {
    let zx = eye[0] - target[0], zy = eye[1] - target[1], zz = eye[2] - target[2];
    let len = Math.hypot(zx, zy, zz) || 1; zx /= len; zy /= len; zz /= len;
    let xx = up[1] * zz - up[2] * zy, xy = up[2] * zx - up[0] * zz, xz = up[0] * zy - up[1] * zx;
    len = Math.hypot(xx, xy, xz) || 1; xx /= len; xy /= len; xz /= len;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    out[0] = xx; out[1] = yx; out[2] = zx; out[3] = 0;
    out[4] = xy; out[5] = yy; out[6] = zy; out[7] = 0;
    out[8] = xz; out[9] = yz; out[10] = zz; out[11] = 0;
    out[12] = -(xx * eye[0] + xy * eye[1] + xz * eye[2]);
    out[13] = -(yx * eye[0] + yy * eye[1] + yz * eye[2]);
    out[14] = -(zx * eye[0] + zy * eye[1] + zz * eye[2]);
    out[15] = 1;
    return out;
}

function invert(out, m) {
    const [a00, a01, a02, a03, a10, a11, a12, a13, a20, a21, a22, a23, a30, a31, a32, a33] = m;
    const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10;
    const b03 = a01 * a12 - a02 * a11, b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12;
    const b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30, b08 = a20 * a33 - a23 * a30;
    const b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
    const det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
    if (!det) return null;
    const id = 1 / det;
    out[0] = (a11 * b11 - a12 * b10 + a13 * b09) * id;
    out[1] = (a02 * b10 - a01 * b11 - a03 * b09) * id;
    out[2] = (a31 * b05 - a32 * b04 + a33 * b03) * id;
    out[3] = (a22 * b04 - a21 * b05 - a23 * b03) * id;
    out[4] = (a12 * b08 - a10 * b11 - a13 * b07) * id;
    out[5] = (a00 * b11 - a02 * b08 + a03 * b07) * id;
    out[6] = (a32 * b02 - a30 * b05 - a33 * b01) * id;
    out[7] = (a20 * b05 - a22 * b02 + a23 * b01) * id;
    out[8] = (a10 * b10 - a11 * b08 + a13 * b06) * id;
    out[9] = (a01 * b08 - a00 * b10 - a03 * b06) * id;
    out[10] = (a30 * b04 - a31 * b02 + a33 * b00) * id;
    out[11] = (a21 * b02 - a20 * b04 - a23 * b00) * id;
    out[12] = (a11 * b07 - a10 * b09 - a12 * b06) * id;
    out[13] = (a00 * b09 - a01 * b07 + a02 * b06) * id;
    out[14] = (a31 * b01 - a30 * b03 - a32 * b00) * id;
    out[15] = (a20 * b03 - a21 * b01 + a22 * b00) * id;
    return out;
}

function multiply(out, a, b) {
    for (let c = 0; c < 4; c++) {
        for (let r = 0; r < 4; r++) {
            out[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1]
                + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
        }
    }
    return out;
}

// Ink coverage of each printable ASCII glyph (0..1, '@' ≈ 1, space = 0),
// measured from the atlas; gives text-only frames a brightness per cell.
let glyphDensity = null;

function buildAtlas() {
    const size = 32, atlas = document.createElement('canvas');
    atlas.width = 16 * size; atlas.height = 8 * size;
    const g = atlas.getContext('2d');
    g.fillStyle = '#fff';
    g.font = `bold ${Math.round(size * 0.86)}px "Courier New", monospace`;
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    for (let code = 33; code < 127; code++) {
        g.fillText(String.fromCharCode(code), (code % 16 + 0.5) * size, (Math.floor(code / 16) + 0.54) * size);
    }
    if (!glyphDensity) {
        const px = g.getImageData(0, 0, atlas.width, atlas.height).data;
        const ink = new Float32Array(128);
        let max = 0;
        for (let code = 33; code < 127; code++) {
            const x0 = (code % 16) * size, y0 = Math.floor(code / 16) * size;
            let sum = 0;
            for (let y = 0; y < size; y++) {
                for (let x = 0; x < size; x++) sum += px[((y0 + y) * atlas.width + x0 + x) * 4 + 3];
            }
            ink[code] = sum;
            if (sum > max) max = sum;
        }
        for (let code = 0; code < 128; code++) ink[code] = max ? ink[code] / max : 0;
        glyphDensity = ink;
    }
    return atlas;
}

function compile(gl, type, src) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
        const log = gl.getShaderInfoLog(s);
        gl.deleteShader(s);
        throw new Error('relief3d shader: ' + log);
    }
    return s;
}

export function isRelief3DSupported() {
    try {
        return !!document.createElement('canvas').getContext('webgl2');
    } catch (_) {
        return false;
    }
}

export class Relief3D {
    /**
     * @param {HTMLElement} container element the 3D canvas is placed in (positioned)
     * @param {object} [options] see DEFAULTS
     */
    constructor(container, options = {}) {
        this.container = container;
        this.opts = { ...DEFAULTS, ...options };
        this.cols = 0;
        this.rows = 0;
        this.layout = 'bgr';
        this.cellAspect = 1;
        this._frame = null;           // last frame copy (re-upload after context loss)
        this._hasFrame = false;
        this._raf = null;
        this._lost = false;
        this._viewProj = new Float32Array(16);
        this._proj = new Float32Array(16);
        this._view = new Float32Array(16);
        this._invViewProj = new Float32Array(16);
        this._hasInverse = false;
        this._pointer = null;         // pointer in NDC while over the canvas
        this._touchAmount = 0;        // eased 0..1 brush strength
        this._lastFrameT = 0;
        this._ripples = new Float32Array(MAX_RIPPLES * 4);
        this._rippleNext = 0;
        this._bang = null;            // { x, y, t0, r } while the collapse plays
        this._clockT0 = performance.now();

        this.canvas = document.createElement('canvas');
        this.canvas.className = 'asciline-relief3d';
        Object.assign(this.canvas.style, {
            position: 'absolute', inset: '0', width: '100%', height: '100%',
            zIndex: '2', display: 'block', touchAction: 'none', cursor: 'grab',
        });
        container.appendChild(this.canvas);

        this._initGL();
        this.resetView();
        this._bindControls();
        this._resizeObserver = new ResizeObserver(() => this._resize());
        this._resizeObserver.observe(container);
        this._resize();
    }

    // ── Public API ───────────────────────────────────────────────────────────

    /** Set grid size and layout ('bgr' | 'cell' | 'text'); cellAspect = cell width / height. */
    setGrid(cols, rows, layout, cellAspect = 1) {
        const changed = cols !== this.cols || rows !== this.rows || layout !== this.layout;
        this.cols = cols;
        this.rows = rows;
        this.layout = layout;
        this.cellAspect = cellAspect > 0 ? cellAspect : 1;
        if (changed) {
            this._frame = new Uint8Array(cols * rows * this._bpp());
            this._hasFrame = false;
            this._allocFrameTexture();
            this.resetView();
        }
        this.requestRender();
    }

    /** Upload one decoded frame (Uint8Array, or a string in 'text' layout). */
    update(frame) {
        if (this.layout === 'text') {
            if (typeof frame !== 'string' || !this.cols) return false;
            this._fillFromText(frame);
            this._hasFrame = true;
            if (!this._lost) this._uploadFrame();
            this.requestRender();
            return true;
        }
        const expected = this.cols * this.rows * this._bpp();
        if (!this.cols || !frame || frame.length !== expected) return false;
        this._frame.set(frame);
        this._hasFrame = true;
        if (!this._lost) this._uploadFrame();
        this.requestRender();
        return true;
    }

    /** Change any option from DEFAULTS. */
    set(options) {
        const resize = 'renderScale' in options || 'maxPixelRatio' in options;
        Object.assign(this.opts, options);
        if (resize) this._resize();
        this.requestRender();
    }

    resetView() {
        this.yaw = DEFAULT_YAW;
        this.pitch = DEFAULT_PITCH;
        this.target = [0, 0, 0];
        this.distance = this._fitDistance();
        this._orbitT0 = performance.now();
        this.requestRender();
    }

    requestRender() {
        if (this._raf !== null || this._disposed) return;
        this._raf = requestAnimationFrame((t) => {
            this._raf = null;
            const busy = this._render(t);
            if (busy || this.opts.autoOrbit) this.requestRender();
        });
    }

    dispose() {
        this._disposed = true;
        if (this._raf !== null) cancelAnimationFrame(this._raf);
        this._resizeObserver.disconnect();
        this._unbind();
        const gl = this.gl;
        if (gl && !this._lost) {
            gl.deleteProgram(this.program);
            gl.deleteBuffer(this.vbo);
            gl.deleteVertexArray(this.vao);
            gl.deleteTexture(this.frameTex);
            gl.deleteTexture(this.atlasTex);
        }
        this.canvas.remove();
    }

    // ── GL setup ─────────────────────────────────────────────────────────────

    _bpp() { return this.layout === 'bgr' ? 3 : 4; }

    // Text rows -> [char, grey, grey, grey] cells; short rows are padded with spaces.
    _fillFromText(text) {
        const out = this._frame, cols = this.cols, rows = this.rows, ink = glyphDensity;
        let i = 0, col = 0, row = 0;
        out.fill(0);
        for (let k = 0; k < text.length && row < rows; k++) {
            const c = text.charCodeAt(k);
            if (c === 10) { row++; col = 0; continue; }
            if (c === 13 || col >= cols) continue;
            const code = c < 128 ? c : 64;
            const grey = code > 32 ? Math.round(48 + 207 * (ink ? ink[code] : 1)) : 0;
            i = (row * cols + col) * 4;
            out[i] = code; out[i + 1] = grey; out[i + 2] = grey; out[i + 3] = grey;
            col++;
        }
    }

    _initGL() {
        const gl = this.canvas.getContext('webgl2', { antialias: true, alpha: false, powerPreference: 'high-performance' });
        if (!gl) throw new Error('WebGL2 is not available');
        this.gl = gl;

        const prog = gl.createProgram();
        gl.attachShader(prog, compile(gl, gl.VERTEX_SHADER, VERT));
        gl.attachShader(prog, compile(gl, gl.FRAGMENT_SHADER, FRAG));
        gl.linkProgram(prog);
        if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
            throw new Error('relief3d link: ' + gl.getProgramInfoLog(prog));
        }
        this.program = prog;
        this.u = {};
        for (const name of ['uFrame', 'uLayout', 'uGrid', 'uCell', 'uDepth', 'uGap', 'uInvert',
            'uViewProj', 'uAtlas', 'uGlyphs', 'uLight', 'uStyle',
            'uTouchMode', 'uTouch', 'uTouchRadius', 'uTouchStrength', 'uTime', 'uRipples', 'uBang']) {
            this.u[name] = gl.getUniformLocation(prog, name);
        }

        const geo = blockGeometry();
        this.vertexCount = geo.length / 6;
        this.vao = gl.createVertexArray();
        gl.bindVertexArray(this.vao);
        this.vbo = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
        gl.bufferData(gl.ARRAY_BUFFER, geo, gl.STATIC_DRAW);
        gl.enableVertexAttribArray(0);
        gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 24, 0);
        gl.enableVertexAttribArray(1);
        gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 24, 12);
        gl.bindVertexArray(null);

        this.frameTex = gl.createTexture();
        this.atlasTex = gl.createTexture();
        gl.bindTexture(gl.TEXTURE_2D, this.atlasTex);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, buildAtlas());
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

        gl.enable(gl.DEPTH_TEST);
        gl.enable(gl.CULL_FACE);
        gl.cullFace(gl.BACK);

        if (this.cols) {
            this._allocFrameTexture();
            if (this._hasFrame) this._uploadFrame();
        }
    }

    _allocFrameTexture() {
        const gl = this.gl;
        if (!gl || this._lost) return;
        const cell = this.layout !== 'bgr';
        gl.bindTexture(gl.TEXTURE_2D, this.frameTex);
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        gl.texImage2D(gl.TEXTURE_2D, 0, cell ? gl.RGBA8 : gl.RGB8, this.cols, this.rows, 0,
            cell ? gl.RGBA : gl.RGB, gl.UNSIGNED_BYTE, null);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    }

    _uploadFrame() {
        const gl = this.gl;
        gl.bindTexture(gl.TEXTURE_2D, this.frameTex);
        gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
        gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, 0, this.cols, this.rows,
            this.layout === 'bgr' ? gl.RGB : gl.RGBA, gl.UNSIGNED_BYTE, this._frame);
    }

    _resize() {
        const w = this.container.clientWidth, h = this.container.clientHeight;
        const ratio = Math.min(window.devicePixelRatio || 1, this.opts.maxPixelRatio) * this.opts.renderScale;
        const bw = Math.max(1, Math.round(w * ratio)), bh = Math.max(1, Math.round(h * ratio));
        if (this.canvas.width !== bw || this.canvas.height !== bh) {
            this.canvas.width = bw;
            this.canvas.height = bh;
        }
        this.requestRender();
    }

    _gridSize() {
        const cellH = this.rows ? 2 / this.rows : 0.02;
        return { cellW: cellH * this.cellAspect, cellH, width: (this.cols || 1) * cellH * this.cellAspect, height: 2 };
    }

    // Smallest distance at which the whole grid (incl. full-height blocks)
    // fits the viewport from the default angle.
    _fitDistance() {
        const { width, height } = this._gridSize();
        const aspect = (this.container.clientWidth || 1) / (this.container.clientHeight || 1);
        const t = Math.tan(FOV / 2);
        let d = Math.max(height / 2 / t, width / 2 / (t * aspect));
        const zMax = this.opts.depth * DEPTH_UNIT;
        const tz = zMax * 0.35;
        const corners = [];
        for (const x of [-width / 2, width / 2])
            for (const y of [-height / 2, height / 2])
                for (const z of [0, zMax]) corners.push([x, y, z]);
        const proj = new Float32Array(16), view = new Float32Array(16), vp = new Float32Array(16);
        const cp = Math.cos(DEFAULT_PITCH);
        for (let i = 0; i < 40; i++) {
            const eye = [d * Math.sin(DEFAULT_YAW) * cp, d * Math.sin(DEFAULT_PITCH), tz + d * Math.cos(DEFAULT_YAW) * cp];
            perspective(proj, FOV, aspect, d * 0.02, d * 6 + 10);
            lookAt(view, eye, [0, 0, tz], [0, 1, 0]);
            multiply(vp, proj, view);
            const fits = corners.every(([x, y, z]) => {
                const w = vp[3] * x + vp[7] * y + vp[11] * z + vp[15];
                const nx = (vp[0] * x + vp[4] * y + vp[8] * z + vp[12]) / w;
                const ny = (vp[1] * x + vp[5] * y + vp[9] * z + vp[13]) / w;
                return w > 0 && Math.abs(nx) < 0.94 && Math.abs(ny) < 0.94;
            });
            if (fits) break;
            d *= 1.06;
        }
        return d;
    }

    // Returns true while an animation (brush easing, ripples) needs more frames.
    _render(now) {
        const gl = this.gl;
        if (!gl || this._lost || this._disposed) return false;
        const [r, g, b] = this.opts.background;
        gl.viewport(0, 0, this.canvas.width, this.canvas.height);
        gl.clearColor(r, g, b, 1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        if (!this.cols || !this._hasFrame) return false;

        let yaw = this.yaw, pitch = this.pitch;
        if (this.opts.autoOrbit) {
            const t = (now - this._orbitT0) / 1000;
            yaw += Math.sin(t * 0.35) * 0.45;
            pitch += Math.sin(t * 0.23) * 0.08;
        }
        const d = this.distance, cp = Math.cos(pitch);
        const tz = this.opts.depth * DEPTH_UNIT * 0.35;
        const tgt = [this.target[0], this.target[1], this.target[2] + tz];
        const eye = [tgt[0] + d * Math.sin(yaw) * cp, tgt[1] + d * Math.sin(pitch), tgt[2] + d * Math.cos(yaw) * cp];
        perspective(this._proj, FOV, this.canvas.width / this.canvas.height, d * 0.02, d * 6 + 10);
        lookAt(this._view, eye, tgt, [0, 1, 0]);
        multiply(this._viewProj, this._proj, this._view);
        this._hasInverse = invert(this._invViewProj, this._viewProj) !== null;
        this._touchPlaneZ = tz;

        // Ease the brush in while the pointer is over the grid, out when it leaves.
        const dt = this._lastFrameT ? Math.min(0.1, (now - this._lastFrameT) / 1000) : 0;
        this._lastFrameT = now;
        const mode = TOUCH_MODES[this.opts.touch] || 0;
        const time0 = (now - this._clockT0) / 1000;
        if (this._bang && time0 - this._bang.t0 > BANG_TOTAL) this._bang = null;
        const brush = mode >= 1 && mode <= 3 && !this._bang ? this._pointerWorld(this._pointer) : null;
        const goal = brush ? 1 : 0;
        this._touchAmount += (goal - this._touchAmount) * Math.min(1, dt * 9);
        if (brush) this._touchXY = brush;
        const time = (now - this._clockT0) / 1000;
        let ripplesAlive = false;
        for (let i = 0; i < MAX_RIPPLES; i++) {
            const r = this._ripples;
            if (r[i * 4 + 3] > 0 && time - r[i * 4 + 2] > RIPPLE_LIFE) r[i * 4 + 3] = 0;
            if (r[i * 4 + 3] > 0) ripplesAlive = true;
        }

        const { cellW, cellH } = this._gridSize();
        const u = this.u;
        gl.useProgram(this.program);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, this.frameTex);
        gl.uniform1i(u.uFrame, 0);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, this.atlasTex);
        gl.uniform1i(u.uAtlas, 1);
        gl.uniform1i(u.uLayout, this.layout === 'bgr' ? 0 : 1);
        gl.uniform1i(u.uGlyphs, this.layout !== 'bgr' && this.opts.glyphs ? 1 : 0);
        gl.uniform2i(u.uGrid, this.cols, this.rows);
        gl.uniform2f(u.uCell, cellW, cellH);
        gl.uniform1f(u.uDepth, this.opts.depth);
        gl.uniform1f(u.uGap, Math.min(0.5, Math.max(0, this.opts.gap)));
        gl.uniform1f(u.uInvert, this.opts.invert ? 1 : 0);
        gl.uniform1i(u.uStyle, this.opts.style === 'floating' ? 1 : 0);
        gl.uniform3f(u.uLight, -0.55, 0.62, 0.56);
        gl.uniformMatrix4fv(u.uViewProj, false, this._viewProj);
        const xy = this._touchXY || [0, 0];
        gl.uniform1i(u.uTouchMode, mode);
        gl.uniform3f(u.uTouch, xy[0], xy[1], this._touchAmount > 0.002 ? this._touchAmount : 0);
        gl.uniform1f(u.uTouchRadius, this.opts.touchRadius);
        gl.uniform1f(u.uTouchStrength, Math.min(3, Math.max(0, this.opts.touchStrength)));
        gl.uniform1f(u.uTime, time);
        gl.uniform4fv(u.uRipples, this._ripples);
        const bang = this._bang;
        gl.uniform4f(u.uBang, bang ? bang.x : 0, bang ? bang.y : 0, bang ? bang.t0 : 0, bang ? bang.r : 0);
        gl.bindVertexArray(this.vao);
        gl.drawArraysInstanced(gl.TRIANGLES, 0, this.vertexCount, this.cols * this.rows);
        gl.bindVertexArray(null);
        return ripplesAlive || this._bang !== null || Math.abs(goal - this._touchAmount) > 0.002;
    }

    // Where the pointer ray meets the grid's mid-height plane, in world units.
    _pointerWorld(ndc) {
        if (!ndc || !this._hasInverse) return null;
        const m = this._invViewProj;
        const unproject = (z) => {
            const x = ndc[0], y = ndc[1];
            const w = m[3] * x + m[7] * y + m[11] * z + m[15];
            return [(m[0] * x + m[4] * y + m[8] * z + m[12]) / w,
                (m[1] * x + m[5] * y + m[9] * z + m[13]) / w,
                (m[2] * x + m[6] * y + m[10] * z + m[14]) / w];
        };
        const a = unproject(-1), b = unproject(1);
        const dz = b[2] - a[2];
        if (Math.abs(dz) < 1e-6) return null;
        const s = (this._touchPlaneZ - a[2]) / dz;
        if (s < 0) return null;
        return [a[0] + (b[0] - a[0]) * s, a[1] + (b[1] - a[1]) * s];
    }

    // Start the collapse at the pointer (ignored while one is already playing).
    _startBang(ndc) {
        if (this._bang) return false;
        const at = this._pointerWorld(ndc);
        if (!at) return false;
        const { width, height } = this._gridSize();
        const r = Math.max(Math.hypot(at[0] - width / 2, at[1] - height / 2), Math.hypot(at[0] + width / 2, at[1] - height / 2),
            Math.hypot(at[0] - width / 2, at[1] + height / 2), Math.hypot(at[0] + width / 2, at[1] + height / 2));
        this._bang = { x: at[0], y: at[1], t0: (performance.now() - this._clockT0) / 1000, r: Math.max(r, 0.1) };
        this.requestRender();
        return true;
    }

    _spawnRipple(ndc) {
        const at = this._pointerWorld(ndc);
        if (!at) return;
        const i = this._rippleNext;
        this._rippleNext = (i + 1) % MAX_RIPPLES;
        this._ripples.set([at[0], at[1], (performance.now() - this._clockT0) / 1000, 1], i * 4);
        this.requestRender();
    }

    // ── Camera controls ──────────────────────────────────────────────────────

    _bindControls() {
        const el = this.canvas;
        const pointers = new Map();
        let mode = null, lastX = 0, lastY = 0, moved = 0, pinchDist = 0;

        const onDown = (e) => {
            el.setPointerCapture(e.pointerId);
            pointers.set(e.pointerId, [e.clientX, e.clientY]);
            if (pointers.size === 2) {
                const [a, b] = [...pointers.values()];
                pinchDist = Math.hypot(a[0] - b[0], a[1] - b[1]);
                mode = 'pinch';
            } else {
                mode = (e.button === 1 || e.button === 2 || e.shiftKey) ? 'pan' : 'orbit';
                moved = 0;
            }
            lastX = e.clientX; lastY = e.clientY;
            el.style.cursor = 'grabbing';
        };
        const onMove = (e) => {
            if (!pointers.has(e.pointerId)) return;
            pointers.set(e.pointerId, [e.clientX, e.clientY]);
            if (mode === 'pinch' && pointers.size === 2) {
                const [a, b] = [...pointers.values()];
                const dist = Math.hypot(a[0] - b[0], a[1] - b[1]);
                if (pinchDist > 0) this._zoom(pinchDist / dist);
                pinchDist = dist;
                moved += 10;
                return;
            }
            const dx = e.clientX - lastX, dy = e.clientY - lastY;
            lastX = e.clientX; lastY = e.clientY;
            moved += Math.abs(dx) + Math.abs(dy);
            if (mode === 'orbit') {
                this.yaw -= dx * 0.006;
                this.pitch = Math.max(-1.45, Math.min(1.45, this.pitch + dy * 0.006));
            } else if (mode === 'pan') {
                const s = this.distance * 0.0016;
                const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
                this.target[0] -= dx * s * cy;
                this.target[2] += dx * s * sy;
                this.target[1] += dy * s;
            }
            this.requestRender();
        };
        const onUp = (e) => {
            pointers.delete(e.pointerId);
            if (pointers.size === 0) { mode = null; el.style.cursor = 'grab'; }
        };
        // A drag must not reach the host page as a click; neither does a click that makes a ripple.
        const onClick = (e) => {
            if (moved > 4) { e.stopPropagation(); e.preventDefault(); }
            else if (this.opts.touch === 'ripple') {
                this._spawnRipple(this._ndc(e));
                e.stopPropagation(); e.preventDefault();
            } else if (this.opts.touch === 'hole') {
                this._startBang(this._ndc(e));
                e.stopPropagation(); e.preventDefault();
            }
            moved = 0;
        };
        const onHover = (e) => { this._pointer = this._ndc(e); if (this.opts.touch !== 'off') this.requestRender(); };
        const onLeave = () => { this._pointer = null; this.requestRender(); };
        const onWheel = (e) => { e.preventDefault(); this._zoom(Math.exp(e.deltaY * 0.0012)); };
        const onCtx = (e) => e.preventDefault();
        const onLost = (e) => { e.preventDefault(); this._lost = true; };
        const onRestored = () => { this._lost = false; this._initGL(); this.requestRender(); };

        el.addEventListener('pointerdown', onDown);
        el.addEventListener('pointermove', onMove);
        el.addEventListener('pointermove', onHover);
        el.addEventListener('pointerleave', onLeave);
        el.addEventListener('pointerup', onUp);
        el.addEventListener('pointercancel', onUp);
        el.addEventListener('click', onClick, true);
        el.addEventListener('wheel', onWheel, { passive: false });
        el.addEventListener('contextmenu', onCtx);
        el.addEventListener('webglcontextlost', onLost);
        el.addEventListener('webglcontextrestored', onRestored);
        this._unbind = () => {
            el.removeEventListener('pointerdown', onDown);
            el.removeEventListener('pointermove', onMove);
            el.removeEventListener('pointermove', onHover);
            el.removeEventListener('pointerleave', onLeave);
            el.removeEventListener('pointerup', onUp);
            el.removeEventListener('pointercancel', onUp);
            el.removeEventListener('click', onClick, true);
            el.removeEventListener('wheel', onWheel);
            el.removeEventListener('contextmenu', onCtx);
            el.removeEventListener('webglcontextlost', onLost);
            el.removeEventListener('webglcontextrestored', onRestored);
        };
    }

    _ndc(e) {
        const r = this.canvas.getBoundingClientRect();
        return [((e.clientX - r.left) / r.width) * 2 - 1, 1 - ((e.clientY - r.top) / r.height) * 2];
    }

    _zoom(factor) {
        const fit = this._fitDistance();
        this.distance = Math.max(fit * 0.15, Math.min(fit * 4, this.distance * factor));
        this.requestRender();
    }
}
