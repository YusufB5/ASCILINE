/**
 * SPDX-License-Identifier: MIT
 * Part of ASCILINE — Licensed under MIT (see LICENSE-MIT)
 *
 * <ascf-player> Web Component
 * ===========================
 * A zero-config HTML custom element that wraps AsciiPlayer so you can
 * embed an ASCF clip with a single tag — no JavaScript needed:
 *
 *   <script type="module" src="https://cdn.jsdelivr.net/npm/asciline-player/src/ascf-element.js"></script>
 *
 *   <ascf-player src="demo.ascf" audio="demo.mp3" loop></ascf-player>
 *
 * Supported attributes
 * --------------------
 *   src        — URL of the .ascf file  (required)
 *   audio      — URL of a paired .mp3   (optional)
 *   ws         — WebSocket URL for live streaming (alternative to src)
 *   autoplay   — boolean, start playback immediately
 *   loop       — boolean, restart when finished (only with src)
 *   muted      — boolean, start with audio muted
 *
 * All AsciiPlayer events bubble out as DOM CustomEvents on the element:
 *   init, statechange, playing, paused, ended, error, timeupdate, fps
 */

import { AsciiPlayer } from './asciline-player.js';

export class AscfPlayerElement extends HTMLElement {
    static get observedAttributes() {
        return ['src', 'audio', 'ws', 'autoplay', 'loop', 'muted'];
    }

    constructor() {
        super();
        this._player  = null;
        this._canvas  = null;
        this._wrapper = null;
    }

    connectedCallback() {
        // Only initialise once
        if (this._player) return;

        // ── Shadow DOM wrapper so the element sizes itself naturally ──
        const wrapper = document.createElement('div');
        wrapper.style.cssText = 'position:relative;width:100%;height:100%;background:#000;overflow:hidden;user-select:none;-webkit-user-select:none;';
        this._wrapper = wrapper;

        const canvas = document.createElement('canvas');
        this._canvas = canvas;
        wrapper.appendChild(canvas);
        this.appendChild(wrapper);

        // Ensure the host element is positioned so overlays work
        if (getComputedStyle(this).position === 'static') {
            this.style.position = 'relative';
        }
        if (!this.style.display) {
            this.style.display = 'block';
        }

        const src      = this.getAttribute('src')      || null;
        const audioSrc = this.getAttribute('audio')    || null;
        const ws       = this.getAttribute('ws')       || null;
        const autoplay = this.hasAttribute('autoplay');
        const loop     = this.hasAttribute('loop');
        const muted    = this.hasAttribute('muted');

        this._player = new AsciiPlayer(canvas, {
            src:       src,
            audioSrc:  audioSrc,
            url:       ws,
            loop:      loop,
            audio:     true,
            autoplay:  autoplay,
            playOverlay:      true,
            muteButton:       true,
            clickToPlayPause: true,
            keyboardShortcuts: false, // let the page manage kb shortcuts
            container: wrapper,
        });

        // Mute if requested
        if (muted) this._player.mute();

        // Bubble AsciiPlayer events as DOM CustomEvents
        const EVENTS = ['init', 'statechange', 'playing', 'paused', 'ended',
                        'error', 'timeupdate', 'fps', 'buffering'];
        for (const ev of EVENTS) {
            this._player.on(ev, (...args) => {
                this.dispatchEvent(new CustomEvent(`ascf-${ev}`, {
                    detail: args.length === 1 ? args[0] : args,
                    bubbles: true, composed: true
                }));
            });
        }
    }

    disconnectedCallback() {
        if (this._player) {
            this._player.destroy();
            this._player = null;
        }
    }

    attributeChangedCallback(name, _old, val) {
        if (!this._player) return;
        if (name === 'muted') {
            val !== null ? this._player.mute() : this._player.unmute();
        }
    }

    // ── Public pass-through API ──

    /** Start or resume playback. Optionally supply ascfUrl and audioUrl. */
    play(src, audio) { this._player && this._player.play(src, audio); }

    /** Pause playback. */
    pause()          { this._player && this._player.pause(); }

    /** Toggle play / pause. */
    togglePlay()     { this._player && this._player.togglePlay(); }

    /** Mute audio. */
    mute()           { this._player && this._player.mute(); }

    /** Unmute audio. */
    unmute()         { this._player && this._player.unmute(); }

    /** Set volume (0–1). */
    setVolume(v)     { this._player && this._player.setVolume(v); }

    /** Get current playback time in seconds. */
    get currentTime()  { return this._player ? this._player.getMasterClock() : 0; }

    /** Get total duration in seconds (0 if unknown). */
    get duration()     { return this._player ? (this._player.duration || 0) : 0; }

    /** Get current player state string. */
    get playerState()  { return this._player ? this._player.getState() : 'IDLE'; }

    /** Direct access to the underlying AsciiPlayer instance. */
    get player()       { return this._player; }
}

// Register the custom element once (guard against duplicate imports)
if (!customElements.get('ascf-player')) {
    customElements.define('ascf-player', AscfPlayerElement);
}
