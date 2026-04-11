/**
 * videostream.js - REAPER Video Stream Module
 *
 * Embeds the REAPER MJPEG streamer directly via <img> tag (not iframe).
 * This gives full CSS control over sizing, transparency, and layout.
 *
 * Features:
 *  - Full-width auto-height overlay (no black bars, no modal backdrop)
 *  - Transparency mode: mix-blend-mode:multiply (Guitar Pro white bg → transparent)
 *  - Fullscreen via native requestFullscreen() API
 *  - Auto-reconnect on stream drop (exponential backoff)
 *  - All prefs persisted in localStorage
 *
 * Level 2 - Imports: nothing (self-contained, no state/api dependencies)
 */

const STREAM_PORT = 9062;
const RECONNECT_BASE_MS = 2000;   // First retry after 2s
const RECONNECT_MAX_MS  = 10000;  // Cap at 10s between retries

const LS_BLEND_MODE  = 'reaper_video_blend_mode';
const LS_OPACITY     = 'reaper_video_opacity';
const LS_BOOST       = 'reaper_video_boost';

export function setupVideoStream() {
    const btn             = document.getElementById('btn-video-stream');
    const overlay         = document.getElementById('video-stream-overlay');
    const img             = document.getElementById('video-stream-img');
    const controls        = overlay?.querySelector('.vs-controls');
    const closeBtn        = document.getElementById('vs-close-btn');
    const refreshBtn      = document.getElementById('vs-refresh-btn');
    const transparencyBtn = document.getElementById('vs-transparency-btn');
    const opacityBtn      = document.getElementById('vs-opacity-btn');
    const boostBtn        = document.getElementById('vs-boost-btn');
    const fullscreenBtn   = document.getElementById('vs-fullscreen-btn');

    if (!btn || !overlay || !img) return;

    let isOpen         = false;
    let reconnectTimer = null;
    let reconnectDelay = RECONNECT_BASE_MS;
    let fadeTimer      = null;

    // ── URL helpers ─────────────────────────────────────────────────────────

    const getStreamUrl = () => `http://${window.location.hostname}:${STREAM_PORT}/stream`;

    // ── Control strip auto-fade ───────────────────────────────────────────────────
    //
    // Controls fade to near-invisible after FADE_DELAY_MS of no interaction.
    // Any hover (desktop) or touch on the controls strip resets the timer.

    const FADE_DELAY_MS = 3000;

    function showControls() {
        if (!controls) return;
        controls.classList.remove('faded');
        clearTimeout(fadeTimer);
        fadeTimer = setTimeout(() => controls?.classList.add('faded'), FADE_DELAY_MS);
    }

    function hideControlsImmediate() {
        clearTimeout(fadeTimer);
        fadeTimer = null;
        controls?.classList.remove('faded');
    }

    if (controls) {
        // Reveal on hover (desktop mouse)
        controls.addEventListener('mouseenter', showControls);
        // Reveal on any touch of the strip (tablet)
        controls.addEventListener('touchstart', showControls, { passive: true });
    }

    // ── Centering ────────────────────────────────────────────────────────────
    //
    // We cannot use CSS transform: translateY(-50%) because transform creates
    // a GPU compositing layer that breaks mix-blend-mode: multiply on children.
    // Instead, compute top manually after the image has natural dimensions.
    //
    // NOTE: overlay.style.top is later overridden by the drag system (Phase 2).
    // centerOverlay() acts as the "snap to center" default.

    function centerOverlay() {
        const overlayH  = overlay.offsetHeight;
        const viewportH = window.innerHeight;
        // Clamp: never go above viewport top, always stay within viewport
        const top = Math.max(0, Math.round((viewportH - overlayH) / 2));
        overlay.style.top = top + 'px';
    }

    window.addEventListener('resize', () => {
        if (isOpen) centerOverlay();
    });

    // ── Stream load/unload ───────────────────────────────────────────────────

    function loadStream() {
        // Setting src to '' first forces the browser to drop the old MJPEG connection
        img.src = '';
        img.src = getStreamUrl();
    }

    function stopStream() {
        img.src = '';
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        reconnectDelay = RECONNECT_BASE_MS;
    }

    // ── Auto-reconnect ───────────────────────────────────────────────────────

    // img.onerror fires when the MJPEG connection is lost or server goes down
    img.addEventListener('error', () => {
        if (!isOpen) return;
        scheduleReconnect();
    });

    // img.onload fires on the first successful frame — reset backoff + recenter
    img.addEventListener('load', () => {
        reconnectDelay = RECONNECT_BASE_MS;
        centerOverlay();
    });

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (isOpen) {
                loadStream();
                // Exponential backoff: double delay each retry, capped at max
                reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
            }
        }, reconnectDelay);
    }

    // ── Open / Close ─────────────────────────────────────────────────────────

    function open() {
        isOpen = true;
        overlay.classList.remove('hidden');
        btn.classList.add('active');
        loadStream();
        showControls(); // start fade cycle
    }

    function close() {
        isOpen = false;
        overlay.classList.add('hidden');
        btn.classList.remove('active');
        stopStream();
        hideControlsImmediate(); // reset for next open

        // Exit fullscreen if we own it
        if (document.fullscreenElement === overlay) {
            document.exitFullscreen().catch(() => {});
        }
    }

    btn.addEventListener('click', () => {
        if (overlay.classList.contains('hidden')) {
            open();
        } else {
            close();
        }
    });

    if (closeBtn) {
        closeBtn.addEventListener('click', close);
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            reconnectDelay = RECONNECT_BASE_MS;
            loadStream();
        });
    }

    // ── Blend Mode ──────────────────────────────────────────────────────
    //
    // Cycles through 3 states on each click:
    //   off → multiply → screen (invert+screen) → off
    //
    // Multiply:       white paper → transparent, black notation stays.
    //                 Best on light album art backgrounds.
    // Screen+Invert:  filter:invert(1) flips image, then screen blend removes
    //                 the black (ex-white paper). Notation becomes white,
    //                 visible on any background colour.
    // All CSS-only — /stream JPEG is always used, zero server overhead.

    const BLEND_MODES = ['off', 'multiply', 'screen'];
    let currentBlendMode = 'off'; // updated by applyBlendMode

    function applyBlendMode(mode) {
        currentBlendMode = mode;
        overlay.classList.remove('vs-multiply', 'vs-screen');
        if (mode === 'multiply') overlay.classList.add('vs-multiply');
        if (mode === 'screen')   overlay.classList.add('vs-screen');
        localStorage.setItem(LS_BLEND_MODE, mode);
        updateBlendBtn(mode);
    }

    function updateBlendBtn(mode) {
        if (!transparencyBtn) return;
        transparencyBtn.classList.remove('active', 'vs-blend-multiply', 'vs-blend-screen');
        if (mode === 'multiply') {
            transparencyBtn.classList.add('active', 'vs-blend-multiply');
            transparencyBtn.title = 'Blend: Multiply — tap for Screen+Invert';
        } else if (mode === 'screen') {
            transparencyBtn.classList.add('active', 'vs-blend-screen');
            transparencyBtn.title = 'Blend: Screen+Invert — tap to disable';
        } else {
            transparencyBtn.title = 'Blend: Off — tap for Multiply';
        }
    }

    // Restore saved blend mode.
    // Migrate old boolean value ('true') from the previous LS_TRANSPARENT key.
    const _savedBlend = localStorage.getItem(LS_BLEND_MODE);
    const initBlend   = BLEND_MODES.includes(_savedBlend)
        ? _savedBlend
        : (_savedBlend === 'true' ? 'multiply' : 'off');
    applyBlendMode(initBlend);

    if (transparencyBtn) {
        transparencyBtn.addEventListener('click', () => {
            const idx = BLEND_MODES.indexOf(currentBlendMode);
            applyBlendMode(BLEND_MODES[(idx + 1) % BLEND_MODES.length]);
        });
    }

    // ── Opacity ──────────────────────────────────────────────────────
    //
    // Cycles through 4 opacity presets (100 → 80 → 60 → 40 → 100%).
    // Applied directly to img.style.opacity so it persists across open/close
    // without needing any extra init on open().

    const OPACITY_LEVELS = [1.0, 0.8, 0.6, 0.4];

    function applyOpacity(opacity) {
        img.style.opacity = opacity;
        localStorage.setItem(LS_OPACITY, String(opacity));
        if (!opacityBtn) return;
        const pct = Math.round(opacity * 100);
        opacityBtn.title = `Opacity: ${pct}%`;
        opacityBtn.classList.toggle('active', opacity < 1.0);
    }

    // Restore saved opacity; sanitize to valid preset (default 1.0)
    const savedOpacity = parseFloat(localStorage.getItem(LS_OPACITY));
    const initOpacity  = OPACITY_LEVELS.includes(savedOpacity) ? savedOpacity : 1.0;
    applyOpacity(initOpacity);

    if (opacityBtn) {
        opacityBtn.addEventListener('click', () => {
            const idx     = OPACITY_LEVELS.indexOf(parseFloat(img.style.opacity) || 1.0);
            const nextIdx = (idx === -1 ? 0 : idx + 1) % OPACITY_LEVELS.length;
            applyOpacity(OPACITY_LEVELS[nextIdx]);
        });
    }

    // ── Boost ────────────────────────────────────────────────────────────
    //
    // Cycles through 4 filter presets that sharpen notation visibility:
    //   off → low → medium → high → off
    //
    // Filters (contrast + brightness) are applied to .vs-img via CSS classes
    // on the overlay. They pre-process pixels before the blend calculation,
    // so notation lines become bolder and paper becomes cleaner.

    const BOOST_LEVELS = ['off', 'low', 'med', 'high'];
    const BOOST_LABELS = { off: 'Off', low: 'Low', med: 'Medium', high: 'High' };
    let currentBoost = 'off';

    function applyBoost(level) {
        currentBoost = level;
        overlay.classList.remove('vs-boost-low', 'vs-boost-med', 'vs-boost-high');
        if (level === 'low')  overlay.classList.add('vs-boost-low');
        if (level === 'med')  overlay.classList.add('vs-boost-med');
        if (level === 'high') overlay.classList.add('vs-boost-high');
        localStorage.setItem(LS_BOOST, level);
        updateBoostBtn(level);
    }

    function updateBoostBtn(level) {
        if (!boostBtn) return;
        boostBtn.classList.remove('vs-boost-active');
        if (level !== 'off') boostBtn.classList.add('vs-boost-active');
        boostBtn.title = `Boost: ${BOOST_LABELS[level]} — tap to cycle`;
    }

    // Restore saved boost level
    const savedBoost = localStorage.getItem(LS_BOOST);
    applyBoost(BOOST_LEVELS.includes(savedBoost) ? savedBoost : 'off');

    if (boostBtn) {
        boostBtn.addEventListener('click', () => {
            const idx = BOOST_LEVELS.indexOf(currentBoost);
            applyBoost(BOOST_LEVELS[(idx + 1) % BOOST_LEVELS.length]);
        });
    }

    // ── Fullscreen ───────────────────────────────────────────────────────────

    function updateFullscreenBtn() {
        if (!fullscreenBtn) return;
        const isFs = document.fullscreenElement === overlay;
        fullscreenBtn.innerHTML = isFs
            ? '<i class="bi bi-fullscreen-exit"></i>'
            : '<i class="bi bi-fullscreen"></i>';
        fullscreenBtn.title = isFs ? 'Exit fullscreen' : 'Fullscreen';
    }

    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', () => {
            if (document.fullscreenElement === overlay) {
                document.exitFullscreen().catch(() => {});
            } else {
                // navigationUI: 'hide' suppresses browser's fullscreen UI controls
                overlay.requestFullscreen({ navigationUI: 'hide' }).catch((err) => {
                    console.warn('[VideoStream] Fullscreen request failed:', err);
                });
            }
        });
    }

    document.addEventListener('fullscreenchange', updateFullscreenBtn);

    // ── Keyboard ─────────────────────────────────────────────────────────────

    document.addEventListener('keydown', (e) => {
        // Escape closes overlay (unless we're in OS fullscreen — browser handles that)
        if (e.key === 'Escape' && isOpen && document.fullscreenElement !== overlay) {
            close();
        }
    });
}
