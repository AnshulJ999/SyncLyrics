/**
 * videostream.js - REAPER Video Stream Module
 *
 * Embeds the REAPER MJPEG streamer directly via <img> tag (not iframe).
 * This gives full CSS control over sizing, transparency, and layout.
 *
 * Features:
 *  - Full-width auto-height overlay (no black bars, no modal backdrop)
 *  - Transparency mode: multiply / screen+invert blend modes
 *  - Per-filter sliders: contrast, brightness, saturation, opacity
 *  - Per-blend-mode filter persistence in localStorage
 *  - Fullscreen via native requestFullscreen() API
 *  - Auto-reconnect on stream drop (exponential backoff)
 *  - Controls extracted to sibling div (unaffected by blend modes)
 *
 * Level 2 - Imports: nothing (self-contained, no state/api dependencies)
 */

const STREAM_PORT = 9062;
const RECONNECT_BASE_MS = 2000;   // First retry after 2s
const RECONNECT_MAX_MS  = 10000;  // Cap at 10s between retries

const LS_BLEND_MODE        = 'reaper_video_blend_mode';
const LS_OPACITY           = 'reaper_video_opacity';
const LS_FILTERS_MULTIPLY  = 'reaper_video_filters_multiply';
const LS_FILTERS_SCREEN    = 'reaper_video_filters_screen';

// Default filter values (100% = no change)
const DEFAULT_FILTERS = { contrast: 100, brightness: 100, saturation: 100 };

export function setupVideoStream() {
    const btn             = document.getElementById('btn-video-stream');
    const overlay         = document.getElementById('video-stream-overlay');
    const img             = document.getElementById('video-stream-img');
    const controlsBar     = document.getElementById('vs-controls-bar');
    const closeBtn        = document.getElementById('vs-close-btn');
    const refreshBtn      = document.getElementById('vs-refresh-btn');
    const transparencyBtn = document.getElementById('vs-transparency-btn');
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
        if (!controlsBar) return;
        controlsBar.classList.remove('faded');
        clearTimeout(fadeTimer);
        fadeTimer = setTimeout(() => controlsBar?.classList.add('faded'), FADE_DELAY_MS);
    }

    function hideControlsImmediate() {
        clearTimeout(fadeTimer);
        fadeTimer = null;
        controlsBar?.classList.remove('faded');
    }

    if (controlsBar) {
        controlsBar.addEventListener('mouseenter', showControls);
        controlsBar.addEventListener('touchstart', showControls, { passive: true });
    }

    // ── Centering ────────────────────────────────────────────────────────────
    //
    // We cannot use CSS transform: translateY(-50%) because transform creates
    // a GPU compositing layer that breaks mix-blend-mode: multiply on children.
    // Instead, compute top manually after the image has natural dimensions.

    function centerOverlay() {
        const overlayH  = overlay.offsetHeight;
        const viewportH = window.innerHeight;
        const top = Math.max(0, Math.round((viewportH - overlayH) / 2));
        overlay.style.top = top + 'px';
    }

    // Sync controls bar position to overlay's top-right corner
    function syncControlsPosition() {
        if (!controlsBar || !isOpen) return;
        const rect = overlay.getBoundingClientRect();
        controlsBar.style.top = (rect.top + 6) + 'px';
    }

    window.addEventListener('resize', () => {
        if (isOpen) {
            centerOverlay();
            syncControlsPosition();
        }
    });

    // ── Stream load/unload ───────────────────────────────────────────────────

    function loadStream() {
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

    img.addEventListener('error', () => {
        if (!isOpen) return;
        scheduleReconnect();
    });

    img.addEventListener('load', () => {
        reconnectDelay = RECONNECT_BASE_MS;
        centerOverlay();
        syncControlsPosition();
    });

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (isOpen) {
                loadStream();
                reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
            }
        }, reconnectDelay);
    }

    // ── Open / Close ─────────────────────────────────────────────────────────

    function open() {
        isOpen = true;
        overlay.classList.remove('hidden');
        controlsBar?.classList.remove('hidden');
        btn.classList.add('active');
        loadStream();
        showControls();
    }

    function close() {
        isOpen = false;
        overlay.classList.add('hidden');
        controlsBar?.classList.add('hidden');
        btn.classList.remove('active');
        stopStream();
        hideControlsImmediate();

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

    // ── Filter state ────────────────────────────────────────────────────────
    //
    // Per-blend-mode filter values (contrast, brightness, saturation).
    // Opacity is shared across all modes.
    // Stored as JSON in localStorage per mode.

    const filters = { contrast: 100, brightness: 100, saturation: 100 };
    let currentOpacity = 100;

    function filtersStorageKey(blendMode) {
        if (blendMode === 'multiply') return LS_FILTERS_MULTIPLY;
        if (blendMode === 'screen')   return LS_FILTERS_SCREEN;
        return null;
    }

    function saveFilters() {
        const key = filtersStorageKey(currentBlendMode);
        if (key) localStorage.setItem(key, JSON.stringify({ ...filters }));
    }

    function restoreFiltersForMode() {
        const key = filtersStorageKey(currentBlendMode);
        const raw = key ? localStorage.getItem(key) : null;
        if (raw) {
            try {
                const saved = JSON.parse(raw);
                filters.contrast   = clampVal(saved.contrast   ?? 100, 50, 400);
                filters.brightness = clampVal(saved.brightness ?? 100, 50, 200);
                filters.saturation = clampVal(saved.saturation ?? 100, 0, 200);
            } catch {
                Object.assign(filters, DEFAULT_FILTERS);
            }
        } else {
            Object.assign(filters, DEFAULT_FILTERS);
        }
        applyFilters();
    }

    function clampVal(v, min, max) {
        return Math.max(min, Math.min(max, v));
    }

    /** Build the CSS filter string for the current blend mode + filter values.
     *
     * Multiply (black tabs on white paper): white paper must stay pure white to vanish
     *   via multiply. saturate → contrast → brightness.
     * Screen (white tabs): saturate strips colour for cleaner invert, then invert(1)
     *   flips, then contrast → brightness push inverted paper blacker for screen removal.
     * No blend: no filter (returns empty string).
     */
    function computeFilter() {
        const c = (filters.contrast / 100).toFixed(2);
        const b = (filters.brightness / 100).toFixed(2);
        const s = (filters.saturation / 100).toFixed(2);

        if (currentBlendMode === 'screen') {
            return `saturate(${s}) invert(1) contrast(${c}) brightness(${b})`;
        } else if (currentBlendMode === 'multiply') {
            return `saturate(${s}) contrast(${c}) brightness(${b})`;
        }
        return ''; // no blend mode = no filter
    }

    function applyFilters() {
        const filterStr = computeFilter();
        img.style.filter = filterStr || '';
        updateSliders();
        updateBoostBtn();
    }

    function applyOpacity(pct) {
        currentOpacity = clampVal(pct, 10, 100);
        img.style.opacity = currentOpacity / 100;
        localStorage.setItem(LS_OPACITY, String(currentOpacity));
        updateOpacitySlider();
    }

    // ── Blend Mode ──────────────────────────────────────────────────────
    //
    // Cycles through 3 states on each click:
    //   off → multiply → screen (invert+screen) → off

    const BLEND_MODES = ['off', 'multiply', 'screen'];
    let currentBlendMode = 'off';

    function applyBlendMode(mode) {
        currentBlendMode = mode;
        overlay.classList.remove('vs-multiply', 'vs-screen');
        if (mode === 'multiply') overlay.classList.add('vs-multiply');
        if (mode === 'screen')   overlay.classList.add('vs-screen');
        localStorage.setItem(LS_BLEND_MODE, mode);
        updateBlendBtn(mode);
        // Restore the filter values saved for this blend mode
        restoreFiltersForMode();
    }

    function updateBlendBtn(mode) {
        if (!transparencyBtn) return;
        transparencyBtn.classList.remove('active');
        if (mode !== 'off') transparencyBtn.classList.add('active');
        if (mode === 'multiply') {
            transparencyBtn.title = 'Blend: Multiply — tap for Screen+Invert';
        } else if (mode === 'screen') {
            transparencyBtn.title = 'Blend: Screen+Invert — tap to disable';
        } else {
            transparencyBtn.title = 'Blend: Off — tap for Multiply';
        }
    }

    // Restore saved blend mode (+ migrate old boolean 'true' → multiply)
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

    // Restore saved opacity (default 100)
    const savedOpacity = parseInt(localStorage.getItem(LS_OPACITY), 10);
    applyOpacity(isNaN(savedOpacity) ? 100 : savedOpacity);

    // ── Slider Popup ────────────────────────────────────────────────────
    //
    // 4-slider popup: contrast, brightness, saturation, opacity.
    // Single tap on boost button opens/closes.

    const sliderPopup      = document.getElementById('vs-slider-popup');
    const contrastSlider   = document.getElementById('vs-contrast-slider');
    const brightnessSlider = document.getElementById('vs-brightness-slider');
    const saturationSlider = document.getElementById('vs-saturation-slider');
    const opacitySlider    = document.getElementById('vs-opacity-slider');
    let sliderPopupOpen    = false;

    function toggleSliderPopup(forceState) {
        if (!sliderPopup) return;
        sliderPopupOpen = forceState !== undefined ? forceState : !sliderPopupOpen;
        sliderPopup.classList.toggle('hidden', !sliderPopupOpen);
    }

    // Boost button — single tap opens/closes slider popup
    if (boostBtn) {
        boostBtn.addEventListener('click', () => {
            toggleSliderPopup();
        });
    }

    function updateBoostBtn() {
        if (!boostBtn) return;
        const hasFilters = currentBlendMode !== 'off' &&
            (filters.contrast !== 100 || filters.brightness !== 100 || filters.saturation !== 100);
        boostBtn.classList.toggle('active', hasFilters);
        boostBtn.title = hasFilters ? 'Filters (active)' : 'Filters';
    }

    function updateSliders() {
        if (contrastSlider) contrastSlider.value = filters.contrast;
        if (brightnessSlider) brightnessSlider.value = filters.brightness;
        if (saturationSlider) saturationSlider.value = filters.saturation;

        const cv = document.getElementById('vs-contrast-value');
        const bv = document.getElementById('vs-brightness-value');
        const sv = document.getElementById('vs-saturation-value');
        if (cv) cv.textContent = `${filters.contrast}%`;
        if (bv) bv.textContent = `${filters.brightness}%`;
        if (sv) sv.textContent = `${filters.saturation}%`;
    }

    function updateOpacitySlider() {
        if (opacitySlider) opacitySlider.value = currentOpacity;
        const ov = document.getElementById('vs-opacity-value');
        if (ov) ov.textContent = `${currentOpacity}%`;
    }

    // ── Slider input handlers — real-time updates as user drags ──
    if (contrastSlider) {
        contrastSlider.addEventListener('input', () => {
            filters.contrast = parseInt(contrastSlider.value, 10);
            applyFilters();
            saveFilters();
        });
    }

    if (brightnessSlider) {
        brightnessSlider.addEventListener('input', () => {
            filters.brightness = parseInt(brightnessSlider.value, 10);
            applyFilters();
            saveFilters();
        });
    }

    if (saturationSlider) {
        saturationSlider.addEventListener('input', () => {
            filters.saturation = parseInt(saturationSlider.value, 10);
            applyFilters();
            saveFilters();
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
            e.target !== boostBtn && !boostBtn?.contains(e.target)) {
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
                overlay.requestFullscreen({ navigationUI: 'hide' }).catch((err) => {
                    console.warn('[VideoStream] Fullscreen request failed:', err);
                });
            }
        });
    }

    document.addEventListener('fullscreenchange', updateFullscreenBtn);

    // ── Keyboard ─────────────────────────────────────────────────────────────

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isOpen && document.fullscreenElement !== overlay) {
            close();
        }
    });
}
