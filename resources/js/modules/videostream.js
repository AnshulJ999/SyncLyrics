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

const LS_BLEND_MODE      = 'reaper_video_blend_mode';
const LS_OPACITY         = 'reaper_video_opacity';
const LS_BOOST_MULTIPLY  = 'reaper_video_boost_multiply';
const LS_BOOST_SCREEN    = 'reaper_video_boost_screen';

// Boost presets — used by the cycle button; slider allows any value 0–200
const BOOST_PRESETS = [0, 50, 100, 150];
const BOOST_LABELS  = { 0: 'Off', 50: 'Low', 100: 'Medium', 150: 'High' };

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

    const FADE_DELAY_MS = 300000;

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

    // ── Boost state & helpers (declared before blend mode because
    //    applyBlendMode() calls restoreBoostForMode() during init) ────────
    //
    // Boost is a 0–200 integer ("percentage"). The slider maps this to
    // CSS filter values that differ per blend mode:
    //   Multiply (black tabs): contrast ↑, brightness slightly ↓
    //   Screen (white tabs):   contrast ↑, brightness slightly ↑
    // At 0 the image is unfiltered. Beyond 100 is "extreme" territory.
    // The cycle button snaps to presets (0/50/100/150).

    let currentBoost = 0; // 0–200

    function boostStorageKey(blendMode) {
        if (blendMode === 'multiply') return LS_BOOST_MULTIPLY;
        if (blendMode === 'screen')   return LS_BOOST_SCREEN;
        return null;
    }

    /** Build the CSS filter string for the current blend mode + boost level.
     *
     * Multiply (black tabs): white paper must stay pure white to vanish
     *   via multiply. contrast sharpens edges, brightness goes SLIGHTLY UP
     *   to push near-white paper toward pure white. Lowering brightness
     *   was wrong — it turned white paper gray, which multiply keeps visible.
     * Screen (white tabs): invert flips colours first, then contrast + brightness
     *   push the inverted paper (now black) blacker for cleaner screen removal.
     *
     * Edit the multipliers below to tune. t ranges 0.0 – 2.0 (slider 0–200).
     */
    function computeBoostFilter(pct) {
        const t = pct / 100; // 0.0 – 2.0
        if (currentBlendMode === 'screen') {
            // White tabs: invert + contrast up + brightness up
            const contrast   = 1 + t * 1.0;   // 1.0 → 3.0
            const brightness = 1 + t * 0.15;   // 1.0 → 1.3
            return `invert(1) contrast(${contrast.toFixed(2)}) brightness(${brightness.toFixed(2)})`;
        } else if (currentBlendMode === 'multiply') {
            // Black tabs: contrast sharpens edges, brightness slightly up
            // so near-white paper becomes pure white (fully transparent via multiply)
            const contrast   = 1 + t * 1.0;   // 1.0 → 3.0
            const brightness = 1 + t * 0.08;   // 1.0 → 1.16
            return `contrast(${contrast.toFixed(2)}) brightness(${brightness.toFixed(2)})`;
        }
        return ''; // no blend mode = no filter
    }

    function applyBoost(pct) {
        currentBoost = pct;
        // Apply filter inline (overrides CSS class-based presets)
        const filterStr = computeBoostFilter(pct);
        img.style.filter = filterStr || '';
        // Persist per blend mode
        const key = boostStorageKey(currentBlendMode);
        if (key) localStorage.setItem(key, String(pct));
        updateBoostBtn(pct);
        updateBoostSlider(pct);
    }

    function restoreBoostForMode() {
        const key = boostStorageKey(currentBlendMode);
        const raw = key ? localStorage.getItem(key) : null;
        const pct = raw !== null ? Math.max(0, Math.min(200, parseInt(raw, 10) || 0)) : 0;
        applyBoost(pct);
    }

    function updateBoostBtn(pct) {
        if (!boostBtn) return;
        boostBtn.classList.toggle('vs-boost-active', pct > 0);
        const label = BOOST_LABELS[pct] || `${pct}%`;
        boostBtn.title = `Boost: ${label} — tap to cycle`;
    }

    function updateBoostSlider(pct) {
        const slider = document.getElementById('vs-boost-slider');
        const valEl  = document.getElementById('vs-boost-value');
        if (slider) slider.value = pct;
        if (valEl)  valEl.textContent = pct === 0 ? 'Off' : `${pct}%`;
    }

    // ── Blend Mode ──────────────────────────────────────────────────────
    const BLEND_MODES = ['off', 'multiply', 'screen'];
    let currentBlendMode = 'off'; // updated by applyBlendMode

    function applyBlendMode(mode) {
        currentBlendMode = mode;
        overlay.classList.remove('vs-multiply', 'vs-screen');
        if (mode === 'multiply') overlay.classList.add('vs-multiply');
        if (mode === 'screen')   overlay.classList.add('vs-screen');
        localStorage.setItem(LS_BLEND_MODE, mode);
        updateBlendBtn(mode);
        // Restore the boost level saved for this blend mode
        restoreBoostForMode();
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
    // Continuous 10–100% via slider, with cycle presets on button tap.
    // Applied directly to img.style.opacity.

    const OPACITY_PRESETS = [100, 80, 60, 40];

    function applyOpacity(pct) {
        const val = Math.max(10, Math.min(100, pct));
        img.style.opacity = val / 100;
        localStorage.setItem(LS_OPACITY, String(val));
        if (opacityBtn) {
            opacityBtn.title = `Opacity: ${val}%`;
            opacityBtn.classList.toggle('active', val < 100);
        }
        updateOpacitySlider(val);
    }

    function updateOpacitySlider(pct) {
        const slider = document.getElementById('vs-opacity-slider');
        const valEl  = document.getElementById('vs-opacity-value');
        if (slider) slider.value = pct;
        if (valEl)  valEl.textContent = `${pct}%`;
    }

    // Restore saved opacity (default 100)
    const savedOpacity = parseInt(localStorage.getItem(LS_OPACITY), 10);
    applyOpacity(isNaN(savedOpacity) ? 100 : savedOpacity);

    // ── Boost init ──────────────────────────────────────────────────────
    // Initial restore (after blend mode has been set above)
    restoreBoostForMode();

    // ── Slider Popup ────────────────────────────────────────────────────
    //
    // Combined popup for boost + opacity sliders.
    // Open via: long-press (500ms hold) OR double-tap on boost/opacity button.
    // Single tap still cycles presets as before.
    // The popup is a sibling of the overlay so blend modes don't affect it.

    const sliderPopup   = document.getElementById('vs-slider-popup');
    const boostSlider   = document.getElementById('vs-boost-slider');
    const opacitySlider = document.getElementById('vs-opacity-slider');
    let sliderPopupOpen = false;

    function toggleSliderPopup(forceState) {
        if (!sliderPopup) return;
        sliderPopupOpen = forceState !== undefined ? forceState : !sliderPopupOpen;
        sliderPopup.classList.toggle('hidden', !sliderPopupOpen);
    }

    // ── Long-press helper ──
    // Returns a cleanup function. Attaches to both touch and mouse events.
    // If held for HOLD_MS without moving, calls onLongPress and suppresses
    // the subsequent click event so the cycle doesn't also fire.
    const HOLD_MS = 500;

    function addLongPress(el, onLongPress) {
        if (!el) return;
        let holdTimer = null;
        let didLongPress = false;

        function startHold(e) {
            didLongPress = false;
            holdTimer = setTimeout(() => {
                didLongPress = true;
                onLongPress();
            }, HOLD_MS);
        }

        function cancelHold() {
            clearTimeout(holdTimer);
            holdTimer = null;
        }

        // Suppress the click that follows a long-press release
        function onClick(e) {
            if (didLongPress) {
                e.preventDefault();
                e.stopImmediatePropagation();
                didLongPress = false;
            }
        }

        el.addEventListener('touchstart', startHold, { passive: true });
        el.addEventListener('touchend', cancelHold);
        el.addEventListener('touchmove', cancelHold);
        el.addEventListener('mousedown', startHold);
        el.addEventListener('mouseup', cancelHold);
        el.addEventListener('mouseleave', cancelHold);
        // Must be registered BEFORE the cycle click handler to suppress it
        el.addEventListener('click', onClick, { capture: true });
    }

    // Attach long-press to both buttons
    addLongPress(boostBtn,   () => toggleSliderPopup());
    addLongPress(opacityBtn, () => toggleSliderPopup());

    // ── Double-tap detection ──
    let lastBoostTap   = 0;
    let lastOpacityTap = 0;

    // Boost button: double-tap → slider, single tap → cycle presets
    if (boostBtn) {
        boostBtn.addEventListener('click', () => {
            const now = Date.now();
            if (now - lastBoostTap < 400) {
                toggleSliderPopup();
                lastBoostTap = 0;
                return;
            }
            lastBoostTap = now;
            // Single tap — cycle: 0 → 50 → 100 → 150 → 0
            let nextIdx = 0;
            for (let i = 0; i < BOOST_PRESETS.length; i++) {
                if (BOOST_PRESETS[i] === currentBoost) {
                    nextIdx = (i + 1) % BOOST_PRESETS.length;
                    break;
                }
                if (BOOST_PRESETS[i] > currentBoost) { nextIdx = i; break; }
                nextIdx = 0;
            }
            applyBoost(BOOST_PRESETS[nextIdx]);
        });
    }

    // Opacity button: double-tap → slider, single tap → cycle presets
    if (opacityBtn) {
        opacityBtn.addEventListener('click', () => {
            const now = Date.now();
            if (now - lastOpacityTap < 400) {
                toggleSliderPopup();
                lastOpacityTap = 0;
                return;
            }
            lastOpacityTap = now;
            const cur = Math.round(parseFloat(img.style.opacity) * 100) || 100;
            let nextIdx = 0;
            for (let i = 0; i < OPACITY_PRESETS.length; i++) {
                if (OPACITY_PRESETS[i] <= cur - 1) { nextIdx = i; break; }
                nextIdx = 0;
            }
            applyOpacity(OPACITY_PRESETS[nextIdx]);
        });
    }

    // ── Slider input handlers — real-time updates as user drags ──
    if (boostSlider) {
        boostSlider.addEventListener('input', () => {
            applyBoost(parseInt(boostSlider.value, 10));
        });
    }

    if (opacitySlider) {
        opacitySlider.addEventListener('input', () => {
            applyOpacity(parseInt(opacitySlider.value, 10));
        });
    }

    // Close popup on outside tap
    document.addEventListener('click', (e) => {
        if (sliderPopupOpen && sliderPopup &&
            !sliderPopup.contains(e.target) &&
            e.target !== boostBtn && !boostBtn?.contains(e.target) &&
            e.target !== opacityBtn && !opacityBtn?.contains(e.target)) {
            toggleSliderPopup(false);
        }
    });

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
