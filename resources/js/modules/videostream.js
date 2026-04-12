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

const STREAM_PORT           = 9062;
const STREAM_STATUS_POLL_MS = 2000;
const STANDBY_DELAY_MS      = 18000; // Matches Python configuration timeout for idle/black before fading UI
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
const LS_POS_Y_RATIO        = 'reaper_video_y_ratio';
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
    // --- GHOST CLICK SUPPRESSOR FOR TAP PASSTHROUGH ---
    // When we synthesize a click via tap passthrough, the browser's native touch 
    // sequence still generates a physical `click` ~300ms later at the exact same geometry.
    // That native click hits the overlay (or newly opened backdrops like Queue) and triggers 
    // "close on outside click" listeners, instantly shutting the menus.
    // We suppress ANY trusted (native) clicks for 500ms following a synthetic passthrough.
    window.addEventListener('click', (e) => {
        if (e.isTrusted && window.__vs_suppress_click && (Date.now() - window.__vs_suppress_click < 500)) {
            e.stopPropagation();
            e.preventDefault();
        }
    }, true); // Capture phase explicitly intercepts before ANY other document listeners

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

    const iframe          = document.getElementById('vs-native-iframe');

    if (!btn || !overlay || !img) return;

    // ── Runtime state ─────────────────────────────────────────────────────────
    let isOpen          = false;
    let sliderPopupOpen = false;
    let statusTimer     = null;
    let streamOk        = false;
    let isConnecting    = false;
    let standbyTimer    = null;
    let fadeTimer       = null;
    let isLocked        = false;
    let currentZIndex   = DEFAULT_ZINDEX;

    // ── URL helper ───────────────────────────────────────────────────────────
    const getStreamUrl = () => `http://${window.location.hostname}:${STREAM_PORT}/stream`;
    const getStatusUrl = () => `http://${window.location.hostname}:${STREAM_PORT}/status`;
    const getViewerUrl = () => `http://${window.location.hostname}:${STREAM_PORT}/`;

    // ── Control auto-fade ────────────────────────────────────────────────────
    const FADE_DELAY_MS = 5000; //

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

    // Position-preserving — only recalculates top from saved proportional y-ratio.
    // Does NOT touch width or left. Used by resize handler and restorePosition.
    // Falls back to vertical centering when no saved offset exists.
    function recalcTopFromRatio() {
        const overlayH = getExpectedOverlayHeight();
        const vh       = window.innerHeight;
        const savedY   = localStorage.getItem(LS_POS_Y_RATIO);
        if (savedY !== null) {
            const parsedY = parseFloat(savedY);
            const yRatio = isNaN(parsedY) ? 0.5 : parsedY;
            
            const cropTopPx    = overlayH * (cropTopPct / 100);
            const cropBottomPx = overlayH * (cropBottomPct / 100);
            const visibleH     = overlayH - cropTopPx - cropBottomPx;
            
            const naturalMinTop = -cropTopPx;
            const naturalMaxTop = vh - cropTopPx - visibleH;
            
            const rawTop = Math.round(naturalMinTop + yRatio * (naturalMaxTop - naturalMinTop));
            const { top } = clampPosition(rawTop, getOverlayLeft());
            overlay.style.top = top + 'px';
        } else {
            overlay.style.top = Math.max(0, Math.round((vh - overlayH) / 2)) + 'px';
        }
    }

    function snapToCenter() {
        centerOverlayFull();
        localStorage.removeItem(LS_POS_Y_RATIO);
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
        // Recalc top from saved proportional offset — preserves user's pinch width and left position
        recalcTopFromRatio();
        // Re-clamp left to new viewport bounds (width unchanged)
        const { left } = clampPosition(getOverlayTop(), getOverlayLeft());
        overlay.style.left = left + 'px';
        syncBars();
    });

    const overlaySizeObserver = new ResizeObserver(() => {
        if (!isOpen || isDragging) return;
        // Automatically fires when MJPEG stream aspect ratio changes mid-connection, 
        // or during pinch zooms. Guarantees the overlay re-anchors geometrically.
        recalcTopFromRatio();
        const { left } = clampPosition(getOverlayTop(), getOverlayLeft());
        overlay.style.left = left + 'px';
        syncBars();
    });
    overlaySizeObserver.observe(overlay);

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
        if (!isOpen) return;
        isConnecting = true;
        // The cache-buster forces the browser to open a fresh TCP socket to Python.
        img.src = getStreamUrl() + '?t=' + Date.now(); 
        
        // 8-second safety release in case the connection ghosts silently
        setTimeout(() => { isConnecting = false; }, 8000); 
    }

    function stopStream() {
        img.src = '';
        streamOk = false;
        cancelStandby();
        if (statusTimer) {
            clearInterval(statusTimer);
            statusTimer = null;
        }
    }

    // ── Auto-reconnect & Standby ─────────────────────────────────────────────

    function enterStandby() {
        overlay.classList.add('vs-standby');
        if (controlsBar) controlsBar.classList.add('vs-standby');
        if (editBar)     editBar.classList.add('vs-standby');
        if (iframe)      iframe.classList.add('vs-standby');
    }

    function exitStandby() {
        overlay.classList.remove('vs-standby');
        if (controlsBar) controlsBar.classList.remove('vs-standby');
        if (editBar)     editBar.classList.remove('vs-standby');
        if (iframe)      iframe.classList.remove('vs-standby');
    }

    function queueStandby() {
        if (!isOpen || standbyTimer) return;
        standbyTimer = setTimeout(() => {
            standbyTimer = null;
            enterStandby();
        }, STANDBY_DELAY_MS);
    }

    function cancelStandby() {
        if (standbyTimer) {
            clearTimeout(standbyTimer);
            standbyTimer = null;
        }
    }

    // Forcefully marks the connection dead if the native OS stack catches a TCP drop.
    function handleSocketDeath() {
        if (!isOpen) return;
        
        isConnecting = false; // Release the loading lock on abort
        streamOk = false;     // Flag for the heartbeat to reconnect safely
        queueStandby();       // Start the fade-out grace period
    }

    // Immediate fallback: only triggers if OS sends a clean TCP abort before heartbeat polls
    img.addEventListener('error', () => {
        if (!isOpen) return;
        handleSocketDeath();
    });

    // Validates a successful connection
    img.addEventListener('load', () => {
        if (!isOpen) return;
        isConnecting = false; // Successfully downloaded the first frame
        streamOk = true;
        cancelStandby();      // Rip away the timeout execution
        exitStandby();
        restorePosition();
        showControls();
        syncBars();
    });

    // Master JSON Heartbeat: Bypasses Chrome's silent handling of graceful disconnects
    function startStatusHeartbeat() {
        if (statusTimer) clearInterval(statusTimer);
        statusTimer = setInterval(() => {
            if (!isOpen) return;

            fetch(getStatusUrl())
                .then(r => {
                    if (!r.ok) {
                        handleSocketDeath();
                        return null; // Stop propagation
                    }
                    return r.json();
                })
                .then(data => {
                    if (!data) return;

                    if (data.stream_state === 'active') {
                        cancelStandby();
                        if (!streamOk) {
                            if (!isConnecting) loadStream(); // Dial a new cache-busting connection
                        } else {
                            exitStandby(); // Guarantee UI fades back in natively if the OS socket survived the Idle timeout
                        }
                    } else if (data.stream_state === 'idle' || data.stream_state === 'black') {
                        // Project is "black" or "idle". We queue the 15-second grace period.
                        // We strictly DO NOT mark streamOk=false because Python is keeping the socket alive during this period!
                        queueStandby();
                    } else {
                        // "starting" states aggressively kill the connection
                        handleSocketDeath();
                    }
                })
                .catch(() => {
                    // Network error (Server aggressively dead)
                    handleSocketDeath();
                });
        }, STREAM_STATUS_POLL_MS);
    }

    // ── Open / Close ─────────────────────────────────────────────────────────

    function open() {
        isOpen = true;
        overlay.classList.remove('hidden');
        controlsBar?.classList.remove('hidden');
        editBar?.classList.remove('hidden');
        btn.classList.add('active');
        
        startStatusHeartbeat();
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
        exitStandby(); // Always clean state naturally on violent close
        hideControlsImmediate();
        toggleSliderPopup(false);
        if (isCropMode) exitCropMode();
        resetLyricsOffset();
        resetLyricsMode();
        resetBgBlur();             // remove body class — background-overlay reverts to current mode

        if (document.fullscreenElement === iframe) {
            document.exitFullscreen().catch(() => {});
        }
    }

    let lastBtnClick = 0;
    btn.addEventListener('click', (e) => {
        // Prevent double-clicks (e.g. native touch click arriving after synthetic click wrapper)
        if (e.timeStamp - lastBtnClick < 400) return;
        lastBtnClick = e.timeStamp;
        
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
        const defaults = (currentBlendMode === 'off') ? 100 : 100;
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
        const cropTopPx    = overlayH * (cropTopPct    / 100);
        const cropBottomPx = overlayH * (cropBottomPct / 100);
        const visibleH     = overlayH - cropTopPx - cropBottomPx;

        // Visible region top in viewport = overlay.top + cropTopPx
        const naturalMinTop = -cropTopPx;
        const naturalMaxTop = vh - cropTopPx - visibleH;
        
        // By min/maxing, we allow negative bounds when the video is taller than the screen,
        // so you can still drag it around (bottom edge to bottom vs top edge to top)
        const minTop = Math.min(naturalMinTop, naturalMaxTop);
        const maxTop = Math.max(naturalMinTop, naturalMaxTop);

        const clampedTop  = clampVal(top, minTop, maxTop);
        const clampedLeft = getOverlayWidthPct() >= 99.5
            ? 0
            : clampVal(left, 0, Math.max(0, vw - overlayW));
        return { top: clampedTop, left: clampedLeft };
    }

    function savePosition() {
        const top      = parseInt(overlay.style.top, 10) || 0;
        const vh       = window.innerHeight;
        const overlayH = Math.max(1, overlay.offsetHeight);
        
        const cropTopPx    = overlayH * (cropTopPct / 100);
        const cropBottomPx = overlayH * (cropBottomPct / 100);
        const visibleH     = overlayH - cropTopPx - cropBottomPx;

        const naturalMinTop = -cropTopPx;
        const naturalMaxTop = vh - cropTopPx - visibleH;
        
        const range = naturalMaxTop - naturalMinTop;
        let yRatio = 0.5;
        if (range !== 0) {
            yRatio = (top - naturalMinTop) / range;
        }
        const clampedYRatio = Math.max(0, Math.min(1, yRatio));
        
        localStorage.setItem(LS_POS_Y_RATIO, String(clampedYRatio));
        localStorage.setItem(LS_POS_LEFT,    String(parseInt(overlay.style.left, 10) || 0));
        localStorage.setItem(LS_WIDTH_PCT,   String(getOverlayWidthPct()));
    }

    function getExpectedOverlayHeight() {
        if (img && img.naturalWidth && img.naturalHeight) {
            // Bypass CSS layout delay by predicting the box model height mathematically
            return Math.max(1, Math.round(overlay.offsetWidth * (img.naturalHeight / img.naturalWidth)));
        }
        return Math.max(1, overlay.offsetHeight);
    }

    function restorePosition() {
        const savedY    = localStorage.getItem(LS_POS_Y_RATIO);
        const savedLeft = localStorage.getItem(LS_POS_LEFT);
        const savedW    = localStorage.getItem(LS_WIDTH_PCT);

        // Restore width first — new height will strictly follow this width via aspect ratio
        if (savedW !== null) {
            overlay.style.width = parseFloat(savedW) + '%';
        }

        if (savedY !== null) {
            const parsedY = parseFloat(savedY);
            const yRatio   = isNaN(parsedY) ? 0.5 : parsedY;
            
            const overlayH = getExpectedOverlayHeight();
            const vh       = window.innerHeight;
            
            const cropTopPx    = overlayH * (cropTopPct / 100);
            const cropBottomPx = overlayH * (cropBottomPct / 100);
            const visibleH     = overlayH - cropTopPx - cropBottomPx;

            const naturalMinTop = -cropTopPx;
            const naturalMaxTop = vh - cropTopPx - visibleH;
            
            const rawTop   = Math.round(naturalMinTop + yRatio * (naturalMaxTop - naturalMinTop));
            const left     = savedLeft !== null ? (parseInt(savedLeft, 10) || 0) : 0;
            const clamped  = clampPosition(rawTop, left);
            overlay.style.top  = clamped.top  + 'px';
            overlay.style.left = clamped.left + 'px';
        } else {
            // No saved state — center vertically, left=0
            const vh = window.innerHeight;
            const overlayH = getExpectedOverlayHeight();
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
        showControls(); // Unconditionally wake up controls on any tap
        
        if (isLocked) return;
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
            // Released before threshold: was a tap
            clearTimeout(tapTimer);
            tapTimer = null;
            if (dragActive) overlay.releasePointerCapture(e.pointerId);
            isDragging = false;
            dragActive = false;
            
            // Synthesize click pass-through to underlying elements
            const prevPE = overlay.style.pointerEvents;
            overlay.style.pointerEvents = 'none';
            const target = document.elementFromPoint(e.clientX, e.clientY);
            overlay.style.pointerEvents = prevPE;
            
            if (target && target !== overlay) {
                // Activate global ghost-click suppressor
                window.__vs_suppress_click = Date.now();
                
                const clickEvent = new MouseEvent('click', {
                    view: window,
                    bubbles: true,
                    cancelable: true,
                    clientX: e.clientX,
                    clientY: e.clientY,
                    button: 0
                });
                target.dispatchEvent(clickEvent);
            }
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

    // ── Fullscreen (Iframe Handoff) ──────────────────────────────────────────

    function updateFullscreenBtn() {
        if (!fullscreenBtn) return;
        const isFs = document.fullscreenElement === iframe;
        fullscreenBtn.innerHTML = isFs
            ? '<i class="bi bi-fullscreen-exit"></i>'
            : '<i class="bi bi-fullscreen"></i>';
        fullscreenBtn.title = isFs ? 'Exit fullscreen' : 'Fullscreen';
    }

    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', () => {
            if (document.fullscreenElement === iframe) {
                document.exitFullscreen().catch(() => {});
            } else {
                if (!iframe) return;
                
                if (isCropMode) exitCropMode();
                
                // 1) Handoff: stop img stream, start iframe
                img.src = '';
                iframe.src = getViewerUrl();
                iframe.classList.remove('hidden');
                
                iframe.requestFullscreen({ navigationUI: 'hide' }).catch((err) => {
                    console.warn('[VideoStream] Fullscreen request failed:', err);
                    // Revert handoff on failure
                    iframe.src = '';
                    iframe.classList.add('hidden');
                    if (isOpen) loadStream(); // Use strict cache-busting entry point 
                });
            }
        });
    }

    document.addEventListener('fullscreenchange', () => {
        updateFullscreenBtn();
        
        // Handoff Return: when exiting fullscreen, swap streams back
        if (document.fullscreenElement === null) {
            if (iframe && !iframe.classList.contains('hidden')) {
                iframe.src = '';
                iframe.classList.add('hidden');
                if (isOpen) loadStream(); // Restore cache-busting heartbeat safely
            }
        }
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
    const LS_BG_MODE = 'vs_bgBlurMode';
    let currentBgBlur = 15; // px; default: comfortable mid-level frosted glass
    let currentBgMode = 'auto'; // 'auto' | 'override'
    
    const btnBgAuto     = document.getElementById('vs-bg-mode-auto');
    const btnBgOverride = document.getElementById('vs-bg-mode-override');

    function updateBgModeButtons() {
        if (!btnBgAuto || !btnBgOverride) return;
        btnBgAuto.classList.toggle('active', currentBgMode === 'auto');
        btnBgOverride.classList.toggle('active', currentBgMode === 'override');
    }

    function applyBgBlur(px, mode = currentBgMode) {
        currentBgBlur = Math.max(0, Math.min(40, Math.round(px)));
        currentBgMode = mode;
        
        document.documentElement.style.setProperty('--vs-blur-override', currentBgBlur + 'px');
        
        // Compute Sandbox/Override math interpolations
        if (currentBgMode === 'override') {
            // Slider (0~40px)
            // Saturation: 130% -> 180%  ( +1.25 per px )
            const computedSat = 130 + (currentBgBlur * 1.25);
            // Opacity of mask: 0.03 -> 0.30  ( +0.00675 per px )
            const computedOp = 0.03 + (currentBgBlur * 0.00675);
            
            document.documentElement.style.setProperty('--vs-computed-saturate', `${computedSat}%`);
            // Format to 3 decimal places to avoid floating point CSS parsing issues
            document.documentElement.style.setProperty('--vs-computed-opacity', computedOp.toFixed(3));
            
            document.body.classList.add('vs-override-mode');
        } else {
            document.body.classList.remove('vs-override-mode');
        }
        
        document.body.classList.add('vs-overlay-active');
        
        localStorage.setItem(LS_BG_BLUR, String(currentBgBlur));
        localStorage.setItem(LS_BG_MODE, currentBgMode);
        
        updateBgBlurSlider();
        updateBgModeButtons();
    }

    function resetBgBlur() {
        // Remove classes so the existing background mode (sharp/soft/blur) takes over again.
        document.body.classList.remove('vs-overlay-active');
        document.body.classList.remove('vs-override-mode');
    }

    function updateBgBlurSlider() {
        if (!bgBlurSlider) return;
        bgBlurSlider.value = currentBgBlur;
        const valEl = document.getElementById('vs-bg-blur-value');
        if (valEl) valEl.textContent = currentBgBlur + 'px';
    }

    if (bgBlurSlider) {
        bgBlurSlider.addEventListener('input', () => applyBgBlur(parseInt(bgBlurSlider.value, 10)));
    }
    
    if (btnBgAuto) {
        btnBgAuto.addEventListener('click', () => applyBgBlur(currentBgBlur, 'auto'));
    }
    
    if (btnBgOverride) {
        btnBgOverride.addEventListener('click', () => applyBgBlur(currentBgBlur, 'override'));
    }

    // Restore saved blur mode & amount on init (don't apply class — overlay is closed at this point)
    const _savedBgBlur = localStorage.getItem(LS_BG_BLUR);
    const _savedBgMode = localStorage.getItem(LS_BG_MODE);
    if (_savedBgMode === 'auto' || _savedBgMode === 'override') {
        currentBgMode = _savedBgMode;
    }
    if (_savedBgBlur !== null) {
        currentBgBlur = Math.max(0, Math.min(40, parseInt(_savedBgBlur, 10) || 15));
        document.documentElement.style.setProperty('--vs-blur-override', currentBgBlur + 'px');
        updateBgBlurSlider();
        updateBgModeButtons();
    }

    // ── Keyboard ─────────────────────────────────────────────────────────────

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isOpen && document.fullscreenElement !== iframe) {
            close();
        }
    });
}
