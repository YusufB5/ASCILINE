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
 *
 * In 'cell' layout the block tops can show the cell's character (glyphs), so
 * the 3D view keeps the ASCII text instead of only its colour.
 *
 * Camera: drag to orbit, Shift/right/middle-drag to pan, wheel or pinch to
 * zoom; resetView() restores the default angle. A plain click is left to the
 * host page (the root player uses it for pause); a drag never reaches it.
 */

const DEFAULTS = {
    depth: 1.0,        // block height scale (0 = flat)
    gap: 0.06,         // gap between blocks, as a fraction of a cell
    glyphs: true,      // draw characters on block tops in 'cell' layout
    invert: false,     // dark cells stand out instead of bright ones
    autoOrbit: false,  // gentle automatic camera sway
    renderScale: 1.0,  // backing-store scale (lower = cheaper)
    maxPixelRatio: 2,
    background: [0.02, 0.02, 0.02],
};

const DEPTH_UNIT = 0.25;      // world height of a white block at depth 1 (grid height = 2)
const MIN_HEIGHT = 0.004;     // keeps black cells as visible tiles
const FOV = 35 * Math.PI / 180;
const DEFAULT_YAW = -0.38;
const DEFAULT_PITCH = 0.30;

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
uniform mat4 uViewProj;

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
    vec2 inner = uGap * 0.5 + aPos.xy * (1.0 - uGap);
    vec3 world = vec3(
        -half_.x + (float(col) + inner.x) * uCell.x,
         half_.y - (float(row) + 1.0 - inner.y) * uCell.y,
         aPos.z * h);

    vColor = color;
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

function multiply(out, a, b) {
    for (let c = 0; c < 4; c++) {
        for (let r = 0; r < 4; r++) {
            out[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1]
                + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
        }
    }
    return out;
}

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

    /** Set grid size and byte layout ('bgr' | 'cell'); cellAspect = cell width / height. */
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

    /** Upload one decoded frame (Uint8Array in the current layout). */
    update(frame) {
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
            this._render(t);
            if (this.opts.autoOrbit) this.requestRender();
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

    _bpp() { return this.layout === 'cell' ? 4 : 3; }

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
            'uViewProj', 'uAtlas', 'uGlyphs', 'uLight']) {
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
        const cell = this.layout === 'cell';
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
            this.layout === 'cell' ? gl.RGBA : gl.RGB, gl.UNSIGNED_BYTE, this._frame);
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

    _render(now) {
        const gl = this.gl;
        if (!gl || this._lost || this._disposed) return;
        const [r, g, b] = this.opts.background;
        gl.viewport(0, 0, this.canvas.width, this.canvas.height);
        gl.clearColor(r, g, b, 1);
        gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
        if (!this.cols || !this._hasFrame) return;

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

        const { cellW, cellH } = this._gridSize();
        const u = this.u;
        gl.useProgram(this.program);
        gl.activeTexture(gl.TEXTURE0);
        gl.bindTexture(gl.TEXTURE_2D, this.frameTex);
        gl.uniform1i(u.uFrame, 0);
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, this.atlasTex);
        gl.uniform1i(u.uAtlas, 1);
        gl.uniform1i(u.uLayout, this.layout === 'cell' ? 1 : 0);
        gl.uniform1i(u.uGlyphs, this.layout === 'cell' && this.opts.glyphs ? 1 : 0);
        gl.uniform2i(u.uGrid, this.cols, this.rows);
        gl.uniform2f(u.uCell, cellW, cellH);
        gl.uniform1f(u.uDepth, this.opts.depth);
        gl.uniform1f(u.uGap, Math.min(0.5, Math.max(0, this.opts.gap)));
        gl.uniform1f(u.uInvert, this.opts.invert ? 1 : 0);
        gl.uniform3f(u.uLight, -0.55, 0.62, 0.56);
        gl.uniformMatrix4fv(u.uViewProj, false, this._viewProj);
        gl.bindVertexArray(this.vao);
        gl.drawArraysInstanced(gl.TRIANGLES, 0, this.vertexCount, this.cols * this.rows);
        gl.bindVertexArray(null);
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
        // A drag must not reach the host page as a click (the root player pauses on click).
        const onClick = (e) => { if (moved > 4) { e.stopPropagation(); e.preventDefault(); } moved = 0; };
        const onWheel = (e) => { e.preventDefault(); this._zoom(Math.exp(e.deltaY * 0.0012)); };
        const onCtx = (e) => e.preventDefault();
        const onLost = (e) => { e.preventDefault(); this._lost = true; };
        const onRestored = () => { this._lost = false; this._initGL(); this.requestRender(); };

        el.addEventListener('pointerdown', onDown);
        el.addEventListener('pointermove', onMove);
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
            el.removeEventListener('pointerup', onUp);
            el.removeEventListener('pointercancel', onUp);
            el.removeEventListener('click', onClick, true);
            el.removeEventListener('wheel', onWheel);
            el.removeEventListener('contextmenu', onCtx);
            el.removeEventListener('webglcontextlost', onLost);
            el.removeEventListener('webglcontextrestored', onRestored);
        };
    }

    _zoom(factor) {
        const fit = this._fitDistance();
        this.distance = Math.max(fit * 0.15, Math.min(fit * 4, this.distance * factor));
        this.requestRender();
    }
}
