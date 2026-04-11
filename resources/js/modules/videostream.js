/**
 * videostream.js - REAPER Video Stream Module
 *
 * Embeds the REAPER MJPEG streamer directly via <img> tag (not iframe).
 * This gives full CSS control over sizing, transparency, and layout.
 *
 * Features:
 *  - Full-width auto-height overlay (no black bars, no modal backdrop)
 *  - Transparency mode: multiply / screen+invert blend modes
 *  - Per-filter sliders: contrast, brightness, saturation, hue, opacity
 *  - Per-blend-mode filter + opacity persistence in localStorage
 *  - Drag (touch + mouse) to reposition, pinch-to-resize (aspect ratio preserved)
 *  - Lock button: freezes position/size + enables passthrough; long-press = snap to center
 *  - Z-index stepper (configurable via popup)
 *  - Fullscreen via native requestFullscreen() API
 *  - Auto-reconnect on stream drop (exponential backoff)
 *  - Controls extracted to sibling divs (unaffected by blend modes)
 *
 * Level 2 - Imports: nothing (self-contained, no state/api dependencies)
 */

const STREAM_PORT       = 9062;
const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS  = 10000;
// Hold-to-drag: how long finger must be down before drag activates.
// Taps shorter than this pass through to underlying elements.
// Increase to 250 if accidental drags occur; decrease to 150 for snappier dragging.
const TAP_THRESHOLD_MS  = 400;

// localStorage keys
const LS_BLEND_MODE         = 'reaper_video_blend_mode';
const LS_OPACITY_OFF        = 'reaper_video_opacity_off';
const LS_OPACITY_BLEND      = 'reaper_video_opacity_blend';
const LS_FILTERS_MULTIPLY   = 'reaper_video_filters_multiply';
const LS_FILTERS_SCREEN     = 'reaper_video_filters_screen';
const LS_POS_BOTTOM_OFFSET  = 'reaper_video_bottom_offset';
const LS_POS_LEFT           = 'reaper_video_left';
const LS_WIDTH_PCT          = 'reaper_video_width_pct';
const LS_ZINDEX             = 'reaper_video_zindex';
const LS_LOCKED             = 'reaper_video_locked';
const LS_CROP_TOP           = 'reaper_video_crop_top_pct';
const LS_CROP_BOTTOM        = 'reaper_video_crop_bottom_pct';
const LS_LYRICS_MODE        = 'reaper_video_lyrics_mode';
const LS_BG_BLUR            = 'reaper_video_bg_blur';

// Defaults
const DEFAULT_FILTERS = { contrast: 100, brightness: 100, saturation: 100, hue: 0 };
const DEFAULT_ZINDEX  = 950;
const ZINDEX_STEP     = 50;

export function setupVideoStream() {
    // ── DOM refs — ALL declared at top to avoid TDZ errors during init ────────
    const btn             = document.getElementById('btn-video-stream');
    const overlay         = document.getElementById('video-stream-overlay');
    const img             = document.getElementById('video-stream-img');
    const controlsBar     = document.getElementById('vs-controls-bar');
    const editBar         = document.getElementById('vs-edit-bar');
    const closeBtn        = document.getElementById('vs-close-btn');
    const refreshBtn      = document.getElementById('vs-refresh-btn');
    const transparencyBtn = document.getElementById('vs-transparency-btn');
    const boostBtn        = document.getElementById('vs-boost-btn');
    const fullscreenBtn   = document.getElementById('vs-fullscreen-btn');
    const lockBtn         = document.getElementById('vs-lock-btn');
    const cropBtn         = document.getElementById('vs-crop-btn');

    // Slider popup elements (must be declared before any function that reads them)
    const sliderPopup      = document.getElementById('vs-slider-popup');
    const contrastSlider   = document.getElementById('vs-contrast-slider');
    const brightnessSlider = document.getElementById('vs-brightness-slider');
    const saturationSlider = document.getElementById('vs-saturation-slider');
    const hueSlider        = document.getElementById('vs-hue-slider');
    const opacitySlider    = document.getElementById('vs-opacity-slider');
    const zindexMinusBtn    = document.getElementById('vs-zindex-minus');
    const zindexPlusBtn     = document.getElementById('vs-zindex-plus');
    const lyricsOffsetSlider = document.getElementById('vs-lyrics-offset-slider');
    const bgBlurSlider      = document.getElementById('vs-bg-blur-slider');

    if (!btn || !overlay || !img) return;

    // ── Runtime state ─────────────────────────────────────────────────────────
    let isOpen          = false;
    let sliderPopupOpen = false;
    let reconnectTimer  = null;
    let reconnectDelay  = RECONNECT_BASE_MS;
    let fadeTimer       = null;
    let isLocked        = false;
    let currentZIndex   = DEFAULT_ZINDEX;

    // ── URL helper ───────────────────────────────────────────────────────────
    const getStreamUrl = () => `http://${window.location.hostname}:${STREAM_PORT}/stream`;

    // ── Control auto-fade ────────────────────────────────────────────────────
    const FADE_DELAY_MS = 300000; // 5 min

    function showControls() {
        controlsBar?.classList.remove('faded');
        editBar?.classList.remove('faded');
        clearTimeout(fadeTimer);
        fadeTimer = setTimeout(() => {
            controlsBar?.classList.add('faded');
            editBar?.classList.add('faded');
        }, FADE_DELAY_MS);
    }

    function hideControlsImmediate() {
        clearTimeout(fadeTimer);
        fadeTimer = null;
        controlsBar?.classList.remove('faded');
        editBar?.classList.remove('faded');
    }

    if (controlsBar) {
        controlsBar.addEventListener('mouseenter', showControls);
        controlsBar.addEventListener('touchstart', showControls, { passive: true });
    }
    if (editBar) {
        editBar.addEventListener('mouseenter', showControls);
        editBar.addEventListener('touchstart', showControls, { passive: true });
    }

    // ── Centering ────────────────────────────────────────────────────────────
    // NO transform: transform creates a GPU compositing layer that breaks mix-blend-mode.
    // We compute top manually in JS.

    // Full reset — used ONLY by snapToCenter (resets width=100%, left=0, centers top)
    function centerOverlayFull() {
        const overlayH  = overlay.offsetHeight;
        const viewportH = window.innerHeight;
        const top = Math.max(0, Math.round((viewportH - overlayH) / 2));
        overlay.style.top   = top + 'px';
        overlay.style.left  = '0px';
        overlay.style.width = '100%';
    }

    // Position-preserving — only recalculates top from saved bottom offset.
    // Does NOT touch width or left. Used by resize handler and restorePosition.
    // Falls back to vertical centering when no saved offset exists.
    function recalcTopFromBottom() {
        const overlayH = overlay.offsetHeight;
        const vh       = window.innerHeight;
        const savedB   = localStorage.getItem(LS_POS_BOTTOM_OFFSET);
        if (savedB !== null) {
            const bottomOffset = parseInt(savedB, 10) || 0;
            const rawTop = vh - overlayH - bottomOffset;
            const { top } = clampPosition(rawTop, getOverlayLeft());
            overlay.style.top = top + 'px';
        } else {
            overlay.style.top = Math.max(0, Math.round((vh - overlayH) / 2)) + 'px';
        }
    }

    function snapToCenter() {
        centerOverlayFull();
        localStorage.removeItem(LS_POS_BOTTOM_OFFSET);
        localStorage.removeItem(LS_POS_LEFT);
        localStorage.removeItem(LS_WIDTH_PCT);
        syncBars();
    }

    // Sync both sibling bars' top to match overlay top-right / top-left corners
    function syncBars() {
        if (!isOpen) return;
        const rect = overlay.getBoundingClientRect();
        const topPx = (rect.top + 6) + 'px';
        if (controlsBar) controlsBar.style.top = topPx;
        if (editBar)     editBar.style.top     = topPx;
        // Keep crop handles aligned when overlay moves/resizes
        applyCrop();
    }

    window.addEventListener('resize', () => {
        if (!isOpen) return;
        // Recalc top from saved bottom offset — preserves user's pinch width and left position
        recalcTopFromBottom();
        // Re-clamp left to new viewport bounds (width unchanged)
        const { left } = clampPosition(getOverlayTop(), getOverlayLeft());
        overlay.style.left = left + 'px';
        syncBars();
    });

    // ── Z-Index management ───────────────────────────────────────────────────

    function applyZIndex(z) {
        currentZIndex = z;
        overlay.style.zIndex      = String(z);
        if (controlsBar) controlsBar.style.zIndex = String(z + 10);
        if (editBar)     editBar.style.zIndex     = String(z + 10);
        if (sliderPopup) sliderPopup.style.zIndex = String(z + 60);
        localStorage.setItem(LS_ZINDEX, String(z));
        const el = document.getElementById('vs-zindex-value');
        if (el) el.textContent = String(z);
    }

    if (zindexMinusBtn) {
        zindexMinusBtn.addEventListener('click', () => applyZIndex(Math.max(100, currentZIndex - ZINDEX_STEP)));
    }
    if (zindexPlusBtn) {
        zindexPlusBtn.addEventListener('click', () => applyZIndex(Math.min(1900, currentZIndex + ZINDEX_STEP)));
    }

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
        restorePosition();
        syncBars();
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
        editBar?.classList.remove('hidden');
        btn.classList.add('active');
        loadStream();
        showControls();
    }

    function close() {
        isOpen = false;
        overlay.classList.add('hidden');
        controlsBar?.classList.add('hidden');
        editBar?.classList.add('hidden');
        btn.classList.remove('active');
        stopStream();
        hideControlsImmediate();
        toggleSliderPopup(false);
        if (isCropMode) exitCropMode();
        resetLyricsOffset();
        resetLyricsMode();
        resetBgBlur();             // remove body class — background-overlay reverts to current mode

        if (document.fullscreenElement === overlay) {
            document.exitFullscreen().catch(() => {});
        }
    }

    btn.addEventListener('click', () => {
        if (overlay.classList.contains('hidden')) open();
        else close();
    });

    if (closeBtn) closeBtn.addEventListener('click', close);

    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
            reconnectDelay = RECONNECT_BASE_MS;
            loadStream();
        });
    }

    // ── Filter state ─────────────────────────────────────────────────────────
    // Per-blend-mode: contrast, brightness, saturation, hue
    // Opacity: two keys — off mode vs blend mode

    const filters = { contrast: 100, brightness: 100, saturation: 100, hue: 0 };
    let currentOpacity = 100;

    function filtersStorageKey(mode) {
        if (mode === 'multiply') return LS_FILTERS_MULTIPLY;
        if (mode === 'screen')   return LS_FILTERS_SCREEN;
        return null;
    }

    function opacityStorageKey(mode) {
        return (mode === 'multiply' || mode === 'screen') ? LS_OPACITY_BLEND : LS_OPACITY_OFF;
    }

    function clampVal(v, min, max) {
        return Math.max(min, Math.min(max, v));
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
                filters.hue        = clampVal(saved.hue        ?? 0, -180, 180);
            } catch {
                Object.assign(filters, DEFAULT_FILTERS);
            }
        } else {
            Object.assign(filters, DEFAULT_FILTERS);
        }
        applyFilters();
    }

    function restoreOpacityForMode() {
        const key = opacityStorageKey(currentBlendMode);
        const raw = localStorage.getItem(key);
        const defaults = (currentBlendMode === 'off') ? 40 : 100;
        const pct = raw !== null ? clampVal(parseInt(raw, 10) || defaults, 10, 100) : defaults;
        applyOpacity(pct);
    }

    /** Build CSS filter string for current blend mode + filter values.
     * Order: saturate → hue-rotate → [invert] → contrast → brightness
     * Saturation and hue-rotate go BEFORE invert so they apply to the original colours.
     */
    function computeFilter() {
        const c = (filters.contrast   / 100).toFixed(2);
        const b = (filters.brightness / 100).toFixed(2);
        const s = (filters.saturation / 100).toFixed(2);
        const h = filters.hue;

        if (currentBlendMode === 'screen') {
            return `saturate(${s}) hue-rotate(${h}deg) invert(1) contrast(${c}) brightness(${b})`;
        } else if (currentBlendMode === 'multiply') {
            return `saturate(${s}) hue-rotate(${h}deg) contrast(${c}) brightness(${b})`;
        }
        return '';
    }

    function applyFilters() {
        img.style.filter = computeFilter() || '';
        updateSliders();
        updateBoostBtn();
    }

    function applyOpacity(pct) {
        currentOpacity = clampVal(pct, 10, 100);
        img.style.opacity = currentOpacity / 100;
        localStorage.setItem(opacityStorageKey(currentBlendMode), String(currentOpacity));
        updateOpacitySlider();
    }

    // ── Blend Mode ───────────────────────────────────────────────────────────

    const BLEND_MODES = ['off', 'multiply', 'screen'];
    let currentBlendMode = 'off';

    function applyBlendMode(mode) {
        currentBlendMode = mode;
        overlay.classList.remove('vs-multiply', 'vs-screen');
        if (mode === 'multiply') overlay.classList.add('vs-multiply');
        if (mode === 'screen')   overlay.classList.add('vs-screen');
        localStorage.setItem(LS_BLEND_MODE, mode);
        updateBlendBtn(mode);
        restoreFiltersForMode();
        restoreOpacityForMode();
    }

    function updateBlendBtn(mode) {
        if (!transparencyBtn) return;
        transparencyBtn.classList.remove('active');
        if (mode !== 'off') transparencyBtn.classList.add('active');
        if (mode === 'multiply')  transparencyBtn.title = 'Blend: Multiply — tap for Screen+Invert';
        else if (mode === 'screen') transparencyBtn.title = 'Blend: Screen+Invert — tap to disable';
        else                      transparencyBtn.title = 'Blend: Off — tap for Multiply';
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

    // ── Slider Popup ─────────────────────────────────────────────────────────
    // Single tap on boost button opens/closes

    function toggleSliderPopup(forceState) {
        if (!sliderPopup) return;
        sliderPopupOpen = forceState !== undefined ? forceState : !sliderPopupOpen;
        sliderPopup.classList.toggle('hidden', !sliderPopupOpen);
    }

    if (boostBtn) {
        boostBtn.addEventListener('click', () => toggleSliderPopup());
    }

    function updateBoostBtn() {
        if (!boostBtn) return;
        const hasFilters = currentBlendMode !== 'off' &&
            (filters.contrast !== 100 || filters.brightness !== 100 ||
             filters.saturation !== 100 || filters.hue !== 0);
        boostBtn.classList.toggle('active', hasFilters);
        boostBtn.title = hasFilters ? 'Filters (active)' : 'Filters';
    }

    function updateSliders() {
        if (contrastSlider)   contrastSlider.value   = filters.contrast;
        if (brightnessSlider) brightnessSlider.value = filters.brightness;
        if (saturationSlider) saturationSlider.value = filters.saturation;
        if (hueSlider)        hueSlider.value        = filters.hue;

        const cv = document.getElementById('vs-contrast-value');
        const bv = document.getElementById('vs-brightness-value');
        const sv = document.getElementById('vs-saturation-value');
        const hv = document.getElementById('vs-hue-value');
        if (cv) cv.textContent = `${filters.contrast}%`;
        if (bv) bv.textContent = `${filters.brightness}%`;
        if (sv) sv.textContent = `${filters.saturation}%`;
        if (hv) hv.textContent = `${filters.hue}°`;
    }

    function updateOpacitySlider() {
        if (opacitySlider) opacitySlider.value = currentOpacity;
        const ov = document.getElementById('vs-opacity-value');
        if (ov) ov.textContent = `${currentOpacity}%`;
    }

    // Slider input handlers
    if (contrastSlider) {
        contrastSlider.addEventListener('input', () => {
            filters.contrast = parseInt(contrastSlider.value, 10);
            applyFilters(); saveFilters();
        });
    }
    if (brightnessSlider) {
        brightnessSlider.addEventListener('input', () => {
            filters.brightness = parseInt(brightnessSlider.value, 10);
            applyFilters(); saveFilters();
        });
    }
    if (saturationSlider) {
        saturationSlider.addEventListener('input', () => {
            filters.saturation = parseInt(saturationSlider.value, 10);
            applyFilters(); saveFilters();
        });
    }
    if (hueSlider) {
        hueSlider.addEventListener('input', () => {
            filters.hue = parseInt(hueSlider.value, 10);
            applyFilters(); saveFilters();
        });
    }
    if (opacitySlider) {
        opacitySlider.addEventListener('input', () => {
            applyOpacity(parseInt(opacitySlider.value, 10));
        });
    }

    // Close popup on outside click
    document.addEventListener('click', (e) => {
        if (sliderPopupOpen && sliderPopup &&
            !sliderPopup.contains(e.target) &&
            e.target !== boostBtn && !boostBtn?.contains(e.target)) {
            toggleSliderPopup(false);
        }
    });

    // ── Lock & Passthrough ───────────────────────────────────────────────────
    // Lock   = freeze position/size + enable passthrough clicks
    // Unlock = overlay is a drag/pinch target
    // Long-press (1200ms) on lock btn = snap to center + unlock

    let lockHoldTimer  = null;
    let lockPressTime  = 0;
    const LOCK_HOLD_MS = 1200;

    function setLocked(locked) {
        isLocked = locked;
        localStorage.setItem(LS_LOCKED, locked ? '1' : '0');
        if (lockBtn) {
            lockBtn.innerHTML = locked
                ? '<i class="bi bi-lock"></i>'
                : '<i class="bi bi-unlock"></i>';
            lockBtn.classList.toggle('vs-locked', locked);
            lockBtn.title = locked
                ? 'Locked — long-press to snap to center'
                : 'Unlocked — tap to lock';
        }
        // Passthrough: locked = pointer-events none, unlocked = auto (draggable)
        overlay.style.pointerEvents = locked ? 'none' : 'auto';
    }

    if (lockBtn) {
        // Touch start — start hold timer
        lockBtn.addEventListener('pointerdown', (e) => {
            lockPressTime = Date.now();
            lockHoldTimer = setTimeout(() => {
                // Long-press: snap to center
                snapToCenter();
                setLocked(false);
                lockHoldTimer = null;
            }, LOCK_HOLD_MS);
        });

        // Touch end — if not a long-press, toggle lock
        lockBtn.addEventListener('pointerup', (e) => {
            if (lockHoldTimer) {
                clearTimeout(lockHoldTimer);
                lockHoldTimer = null;
                setLocked(!isLocked);
            }
        });

        lockBtn.addEventListener('pointerleave', () => {
            if (lockHoldTimer) { clearTimeout(lockHoldTimer); lockHoldTimer = null; }
        });
    }


    function clampPosition(top, left) {
        const overlayW = overlay.offsetWidth;
        const overlayH = overlay.offsetHeight;
        const vw = window.innerWidth;
        const vh = window.innerHeight;

        // Crop-aware bounds: clip-path changes the *visible* region but not the layout box.
        // offsetHeight still returns the full uncropped height, so we derive the visible
        // region's top offset and height from the crop percentages.
        const cropTopPx    = overlayH * (cropTopPct    / 100);
        const cropBottomPx = overlayH * (cropBottomPct / 100);
        const visibleH     = overlayH - cropTopPx - cropBottomPx;

        // Visible region top in viewport = overlay.top + cropTopPx
        // Constraints: visibleTop >= 0 and visibleTop + visibleH <= vh
        const minTop = -cropTopPx;                               // allows visible top to reach 0
        const maxTop = Math.max(minTop, vh - cropTopPx - visibleH); // visible bottom at vh

        const clampedTop  = clampVal(top, minTop, maxTop);
        const clampedLeft = getOverlayWidthPct() >= 99.5
            ? 0
            : clampVal(left, 0, Math.max(0, vw - overlayW));
        return { top: clampedTop, left: clampedLeft };
    }

    function savePosition() {
        const top      = parseInt(overlay.style.top, 10) || 0;
        const vh       = window.innerHeight;
        const overlayH = overlay.offsetHeight;
        // Save as distance from element bottom to viewport bottom.
        // This way, different-height videos (height:auto) always restore
        // to the same bottom edge position regardless of stream aspect ratio.
        const bottomOffset = vh - top - overlayH;
        localStorage.setItem(LS_POS_BOTTOM_OFFSET, String(bottomOffset));
        localStorage.setItem(LS_POS_LEFT,           String(parseInt(overlay.style.left, 10) || 0));
        localStorage.setItem(LS_WIDTH_PCT,          String(getOverlayWidthPct()));
    }

    function restorePosition() {
        const savedB    = localStorage.getItem(LS_POS_BOTTOM_OFFSET);
        const savedLeft = localStorage.getItem(LS_POS_LEFT);
        const savedW    = localStorage.getItem(LS_WIDTH_PCT);

        // Restore width first — offsetHeight depends on width when height is auto
        if (savedW !== null) {
            overlay.style.width = parseFloat(savedW) + '%';
        }

        if (savedB !== null) {
            const bottomOffset = parseInt(savedB, 10) || 0;
            const overlayH = overlay.offsetHeight;
            const vh       = window.innerHeight;
            const rawTop   = vh - overlayH - bottomOffset;
            const left     = savedLeft !== null ? (parseInt(savedLeft, 10) || 0) : 0;
            const clamped  = clampPosition(rawTop, left);
            overlay.style.top  = clamped.top  + 'px';
            overlay.style.left = clamped.left + 'px';
        } else {
            // No saved state — center vertically, left=0
            const vh = window.innerHeight;
            const overlayH = overlay.offsetHeight;
            overlay.style.top  = Math.max(0, Math.round((vh - overlayH) / 2)) + 'px';
            overlay.style.left = '0px';
        }
        syncBars();
    }

    // ── Drag (touch + mouse) ─────────────────────────────────────────────────
    // Hold-to-drag: tap shorter than TAP_THRESHOLD_MS passes through to underlying elements.
    // Drag activates after TAP_THRESHOLD_MS hold OR if dead zone is exceeded while holding.
    // Only active when unlocked (overlay.style.pointerEvents = 'auto')

    let isDragging       = false;
    let dragActive       = false;
    let tapTimer         = null;
    let dragStartX       = 0;
    let dragStartY       = 0;
    let overlayStartTop  = 0;
    let overlayStartLeft = 0;
    let activePointers   = new Map();
    let dragStartPointerId = 0;
    const DRAG_DEAD_ZONE = 10;

    function getOverlayLeft() { return parseInt(overlay.style.left, 10) || 0; }
    function getOverlayTop()  { return parseInt(overlay.style.top,  10) || 0; }
    function getOverlayWidthPct() { return parseFloat(overlay.style.width) || 100; }

    function activateDrag() {
        if (dragActive) return;
        dragActive = true;
        overlay.setPointerCapture(dragStartPointerId);
        document.body.classList.add('vs-dragging');
    }

    overlay.addEventListener('pointerdown', (e) => {
        activePointers.set(e.pointerId, true);
        if (isLocked) { showControls(); return; }
        if (activePointers.size > 1) { isDragging = false; dragActive = false; return; }

        isDragging        = true;
        dragActive        = false;
        dragStartX        = e.clientX;
        dragStartY        = e.clientY;
        overlayStartTop   = getOverlayTop();
        overlayStartLeft  = getOverlayLeft();
        dragStartPointerId = e.pointerId;

        // Start hold timer — if it fires, drag is activated
        tapTimer = setTimeout(() => {
            tapTimer = null;
            if (isDragging) activateDrag();
        }, TAP_THRESHOLD_MS);
    });

    overlay.addEventListener('pointermove', (e) => {
        if (!isDragging || isLocked) return;
        e.preventDefault();
        const dx = e.clientX - dragStartX;
        const dy = e.clientY - dragStartY;

        // Exceeded dead zone during hold — activate drag immediately
        if (!dragActive && (Math.abs(dx) > DRAG_DEAD_ZONE || Math.abs(dy) > DRAG_DEAD_ZONE)) {
            activateDrag();
        }

        if (!dragActive) return;

        const newTop  = overlayStartTop  + dy;
        const newLeft = overlayStartLeft + dx;
        const clamped = clampPosition(newTop, newLeft);
        overlay.style.top  = clamped.top  + 'px';
        overlay.style.left = clamped.left + 'px';
        syncBars();
    });

    overlay.addEventListener('pointerup', (e) => {
        activePointers.delete(e.pointerId);
        if (tapTimer) {
            // Released before threshold: was a tap — release capture so event reaches underlying elements
            clearTimeout(tapTimer);
            tapTimer = null;
            if (dragActive) overlay.releasePointerCapture(e.pointerId);
            isDragging = false;
            dragActive = false;
            return;
        }
        if (!isDragging) return;
        isDragging = false;
        dragActive = false;
        document.body.classList.remove('vs-dragging');
        savePosition();
    });

    overlay.addEventListener('pointercancel', (e) => {
        activePointers.delete(e.pointerId);
        if (tapTimer) { clearTimeout(tapTimer); tapTimer = null; }
        isDragging = false;
        dragActive = false;
        document.body.classList.remove('vs-dragging');
    });

    // ── Pinch-to-resize ──────────────────────────────────────────────────────
    // Track two touch pointers; scale overlay width based on pinch distance ratio.
    // Clamps to [30%, 100%]. Height adjusts automatically (img height: auto).

    let pinchPointers    = new Map(); // pointerId → {x, y}
    let pinchStartDist   = 0;
    let pinchStartWidthPct = 100;

    function getPinchDistance(map) {
        const pts = [...map.values()];
        if (pts.length < 2) return 0;
        const dx = pts[0].x - pts[1].x;
        const dy = pts[0].y - pts[1].y;
        return Math.hypot(dx, dy);
    }

    overlay.addEventListener('touchstart', (e) => {
        if (isLocked) return;
        for (const t of e.changedTouches) {
            pinchPointers.set(t.identifier, { x: t.clientX, y: t.clientY });
        }
        if (pinchPointers.size === 2) {
            // Starting a pinch — cancel any ongoing drag
            isDragging = false;
            document.body.classList.remove('vs-dragging');
            pinchStartDist      = getPinchDistance(pinchPointers);
            pinchStartWidthPct  = getOverlayWidthPct();
            e.preventDefault();
        }
    }, { passive: false });

    overlay.addEventListener('touchmove', (e) => {
        if (isLocked || pinchPointers.size < 2) return;

        for (const t of e.changedTouches) {
            if (pinchPointers.has(t.identifier)) {
                pinchPointers.set(t.identifier, { x: t.clientX, y: t.clientY });
            }
        }

        const currentDist = getPinchDistance(pinchPointers);
        if (pinchStartDist === 0) return;
        const scale = currentDist / pinchStartDist;
        const newPct = clampVal(pinchStartWidthPct * scale, 30, 100);
        overlay.style.width = newPct + '%';
        // Re-center left if back to full width
        if (newPct >= 99.5) overlay.style.left = '0px';
        syncBars();
        e.preventDefault();
    }, { passive: false });

    overlay.addEventListener('touchend', (e) => {
        for (const t of e.changedTouches) {
            pinchPointers.delete(t.identifier);
        }
        if (pinchPointers.size < 2) {
            if (pinchStartDist > 0) {
                // Pinch just ended — save
                savePosition();
                pinchStartDist = 0;
            }
        }
    });

    // ── Position restore (on first load) ─────────────────────────────────────
    // Called after img.onload to ensure offsetHeight is correct

    // ── Lock restore ─────────────────────────────────────────────────────────
    const savedLocked = localStorage.getItem(LS_LOCKED);
    setLocked(savedLocked === '1');

    // ── Z-Index restore ──────────────────────────────────────────────────────
    const savedZ = parseInt(localStorage.getItem(LS_ZINDEX), 10);
    applyZIndex(isNaN(savedZ) ? DEFAULT_ZINDEX : savedZ);

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

    document.addEventListener('fullscreenchange', () => {
        updateFullscreenBtn();
        // Exit crop mode when entering fullscreen — handles would overlap fullscreen content
        if (document.fullscreenElement === overlay && isCropMode) exitCropMode();
    });

    // ── Crop ─────────────────────────────────────────────────────────────────
    // Implemented via CSS clip-path: inset(topPct% 0 bottomPct% 0).
    // topPct / bottomPct are percentages of the overlay's own height.
    // The visible indicator divs are positioned in viewport coords at the crop edge.

    const cropTopHandle    = document.getElementById('vs-crop-top-handle');
    const cropBottomHandle = document.getElementById('vs-crop-bottom-handle');

    let cropTopPct    = 0;  // 0–50
    let cropBottomPct = 0;  // 0–50
    let isCropMode    = false;

    // Apply clip-path to overlay and reposition handle divs in viewport coords
    function applyCrop() {
        overlay.style.clipPath = (cropTopPct === 0 && cropBottomPct === 0)
            ? ''
            : `inset(${cropTopPct.toFixed(2)}% 0 ${cropBottomPct.toFixed(2)}% 0)`;

        // Reposition handles to match the crop lines in viewport coords
        if (!isCropMode) return;
        const rect = overlay.getBoundingClientRect();
        const topEdgePx    = rect.top  + rect.height * (cropTopPct    / 100);
        const bottomEdgePx = rect.top  + rect.height * (1 - cropBottomPct / 100);

        if (cropTopHandle) {
            cropTopHandle.style.top = (topEdgePx - 22) + 'px'; // center on crop line
        }
        if (cropBottomHandle) {
            cropBottomHandle.style.top = (bottomEdgePx - 22) + 'px';
        }
    }

    function saveCrop() {
        localStorage.setItem(LS_CROP_TOP,    String(cropTopPct));
        localStorage.setItem(LS_CROP_BOTTOM, String(cropBottomPct));
    }

    function restoreCrop() {
        const rawT = localStorage.getItem(LS_CROP_TOP);
        const rawB = localStorage.getItem(LS_CROP_BOTTOM);
        cropTopPct    = rawT !== null ? clampVal(parseFloat(rawT)    || 0, 0, 50) : 0;
        cropBottomPct = rawB !== null ? clampVal(parseFloat(rawB) || 0, 0, 50) : 0;
        applyCrop();
    }

    function enterCropMode() {
        isCropMode = true;
        if (cropBtn) {
            cropBtn.classList.add('active');
            cropBtn.title = 'Exit crop mode';
        }
        cropTopHandle?.classList.remove('hidden');
        cropBottomHandle?.classList.remove('hidden');
        applyCrop(); // position handles
    }

    function exitCropMode() {
        isCropMode = false;
        if (cropBtn) {
            cropBtn.classList.remove('active');
            cropBtn.title = 'Edit / Crop';
        }
        cropTopHandle?.classList.add('hidden');
        cropBottomHandle?.classList.add('hidden');
        saveCrop();
    }

    // Generic handle drag logic — works for both top and bottom.
    // Events attach to the pill (first child) since the container wrapper has pointer-events:none.
    // setPointerCapture on the pill, .dragging class on the outer container (CSS selector target).
    function setupCropHandleDrag(handleEl, isTop) {
        if (!handleEl) return;
        const pill = handleEl.firstElementChild; // .vs-crop-handle-indicator
        if (!pill) return;

        let dragging = false;
        let startY   = 0;
        let startPct = 0;

        function onMove(clientY) {
            if (!dragging) return;
            const rect = overlay.getBoundingClientRect();
            if (rect.height === 0) return;

            const deltaY   = clientY - startY;
            const deltaPct = (deltaY / rect.height) * 100;

            if (isTop) {
                const maxTop = 50 - cropBottomPct;
                cropTopPct = clampVal(startPct + deltaPct, 0, maxTop);
            } else {
                const maxBottom = 50 - cropTopPct;
                cropBottomPct = clampVal(startPct - deltaPct, 0, maxBottom);
            }
            applyCrop();
        }

        pill.addEventListener('pointerdown', (e) => {
            if (!isCropMode) return;
            dragging = true;
            startY   = e.clientY;
            startPct = isTop ? cropTopPct : cropBottomPct;
            pill.setPointerCapture(e.pointerId);
            handleEl.classList.add('dragging');  // CSS target is the outer container
            e.stopPropagation();
        });

        pill.addEventListener('pointermove', (e) => {
            if (!dragging) return;
            onMove(e.clientY);
            e.stopPropagation();
        });

        pill.addEventListener('pointerup', (e) => {
            if (!dragging) return;
            dragging = false;
            handleEl.classList.remove('dragging');
            saveCrop();
            e.stopPropagation();
        });

        pill.addEventListener('pointercancel', () => {
            dragging = false;
            handleEl.classList.remove('dragging');
        });
    }

    setupCropHandleDrag(cropTopHandle,    true);
    setupCropHandleDrag(cropBottomHandle, false);

    // Crop button toggle
    if (cropBtn) {
        cropBtn.addEventListener('click', () => {
            if (isCropMode) exitCropMode();
            else            enterCropMode();
        });
    }

    // Restore saved crop on load
    restoreCrop();

    // ── Lyrics Offset ─────────────────────────────────────────────────────────
    // Shifts the lyrics container up or down via margin-top.
    // Uses margin-top (not transform) to avoid conflicting with the container's
    // existing transform: translateY() used by visual mode animations.
    // Resets to 0 on close — offset is only meaningful while the overlay is open.

    const LS_LYRICS_OFFSET = 'reaper_video_lyrics_offset';
    const lyricsEl = document.getElementById('lyrics');
    let currentLyricsOffset = 0; // px, range -400 to +200

    function applyLyricsOffset(px) {
        currentLyricsOffset = clampVal(px, -400, 200);
        if (lyricsEl) lyricsEl.style.marginTop = currentLyricsOffset === 0 ? '' : currentLyricsOffset + 'px';
        localStorage.setItem(LS_LYRICS_OFFSET, String(currentLyricsOffset));
        updateLyricsOffsetSlider();
    }

    function resetLyricsOffset() {
        // Clear the DOM only — do NOT touch currentLyricsOffset.
        // Keeping the in-memory value intact means the next open() re-applies it correctly.
        if (lyricsEl) lyricsEl.style.marginTop = '';
        updateLyricsOffsetSlider();
    }

    function updateLyricsOffsetSlider() {
        if (!lyricsOffsetSlider) return;
        lyricsOffsetSlider.value = currentLyricsOffset;
        const valEl = document.getElementById('vs-lyrics-offset-value');
        if (valEl) {
            const sign = currentLyricsOffset > 0 ? '+' : '';
            valEl.textContent = `${sign}${currentLyricsOffset}px`;
        }
    }

    if (lyricsOffsetSlider) {
        lyricsOffsetSlider.addEventListener('input', () => {
            applyLyricsOffset(parseInt(lyricsOffsetSlider.value, 10));
        });
    }

    // Restore saved offset and sync slider display on init
    // (Don't apply margin to DOM here — overlay is closed at this point)
    const _savedLyricsOffset = localStorage.getItem(LS_LYRICS_OFFSET);
    if (_savedLyricsOffset !== null) {
        currentLyricsOffset = clampVal(parseInt(_savedLyricsOffset, 10) || 0, -400, 200);
        updateLyricsOffsetSlider();
    }

    // Apply restored offset + mode + blur when overlay opens.
    // Secondary click listener runs after open() (first listener) has set isOpen = true.
    btn.addEventListener('click', () => {
        if (isOpen) {
            if (currentLyricsOffset !== 0 && lyricsEl) {
                lyricsEl.style.marginTop = currentLyricsOffset + 'px';
            }
            if (currentLyricsMode !== 'full') {
                applyLyricsMode(currentLyricsMode);
            }
            applyBgBlur(currentBgBlur);  // always re-apply blur class on open
        }
    });

    // ── Lyrics Focus Mode ─────────────────────────────────────────────────────
    // Three states: full (default) | focused (3 lines) | solo (1 line).
    // Applies CSS classes to the lyrics container. Matches lyrics offset lifecycle:
    // resets to 'full' on close, restores saved mode on open.

    let currentLyricsMode = 'full'; // 'full' | 'focused' | 'solo'

    function applyLyricsMode(mode) {
        currentLyricsMode = mode;
        if (lyricsEl) {
            lyricsEl.classList.remove('vs-lyric-focused', 'vs-lyric-solo');
            if (mode === 'focused') lyricsEl.classList.add('vs-lyric-focused');
            else if (mode === 'solo') lyricsEl.classList.add('vs-lyric-solo');
        }
        localStorage.setItem(LS_LYRICS_MODE, mode);
        updateLyricsModeButtons();
    }

    function resetLyricsMode() {
        // Clear DOM only — preserve currentLyricsMode for next open (same as offset pattern)
        if (lyricsEl) lyricsEl.classList.remove('vs-lyric-focused', 'vs-lyric-solo');
        updateLyricsModeButtons();
    }

    function updateLyricsModeButtons() {
        ['full', 'focused', 'solo'].forEach(m => {
            const el = document.getElementById(`vs-lyrics-mode-${m}`);
            if (el) el.classList.toggle('active', currentLyricsMode === m && isOpen);
        });
    }

    document.getElementById('vs-lyrics-mode-full')   ?.addEventListener('click', () => applyLyricsMode('full'));
    document.getElementById('vs-lyrics-mode-focused') ?.addEventListener('click', () => applyLyricsMode('focused'));
    document.getElementById('vs-lyrics-mode-solo')    ?.addEventListener('click', () => applyLyricsMode('solo'));

    // Restore saved mode from localStorage on init (don't apply to DOM — overlay is closed)
    const _savedLyricsMode = localStorage.getItem(LS_LYRICS_MODE);
    if (_savedLyricsMode && ['full', 'focused', 'solo'].includes(_savedLyricsMode)) {
        currentLyricsMode = _savedLyricsMode;
        updateLyricsModeButtons();
    }

    // ── Background Blur Override ─────────────────────────────────────────────────
    // When the video overlay is open, a CSS class on <body> combined with a CSS
    // custom property forces the background-overlay's backdrop-filter to the
    // user-chosen blur value — overriding sharp/soft/blur mode and visual mode.
    // On overlay close, the class is removed and the existing mode resumes exactly.

    let currentBgBlur = 15; // px; default: comfortable mid-level frosted glass

    function applyBgBlur(px) {
        currentBgBlur = Math.max(0, Math.min(40, Math.round(px)));
        document.documentElement.style.setProperty('--vs-blur-override', currentBgBlur + 'px');
        document.body.classList.add('vs-overlay-active');
        localStorage.setItem(LS_BG_BLUR, String(currentBgBlur));
        updateBgBlurSlider();
    }

    function resetBgBlur() {
        // Remove class so the existing background mode (sharp/soft/blur) takes over again.
        // Do NOT clear --vs-blur-override or currentBgBlur — preserve for next open.
        document.body.classList.remove('vs-overlay-active');
    }

    function updateBgBlurSlider() {
        if (!bgBlurSlider) return;
        bgBlurSlider.value = currentBgBlur;
        const valEl = document.getElementById('vs-bg-blur-value');
        if (valEl) valEl.textContent = currentBgBlur + 'px';
    }

    if (bgBlurSlider) {
        bgBlurSlider.addEventListener('input', () => {
            applyBgBlur(parseInt(bgBlurSlider.value, 10));
        });
    }

    // Restore saved blur on init (don't apply class — overlay is closed at this point)
    const _savedBgBlur = localStorage.getItem(LS_BG_BLUR);
    if (_savedBgBlur !== null) {
        currentBgBlur = Math.max(0, Math.min(40, parseInt(_savedBgBlur, 10) || 15));
        document.documentElement.style.setProperty('--vs-blur-override', currentBgBlur + 'px');
        updateBgBlurSlider();
    }

    // ── Keyboard ─────────────────────────────────────────────────────────────

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isOpen && document.fullscreenElement !== overlay) {
            close();
        }
    });
}
