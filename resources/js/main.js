/**
 * main.js - Application Entry Point
 * 
 * This is the main orchestrator that imports all modules,
 * contains the main update loop, and initializes the application.
 * 
 * Level Top - Imports all modules
 */

// ========== MODULE IMPORTS ==========

// State (Level 0)
import {
    displayConfig,
    visualModeConfig,
    lastTrackInfo,
    lastLyrics,
    updateInterval,
    lastCheckTime,
    currentArtistImages,
    visualModeActive,
    queueDrawerOpen,
    manualStyleOverride,
    hasWordSync,
    wordSyncEnabled,
    anyProviderHasWordSync,
    debugTimingEnabled,
    setLastTrackInfo,
    setLastCheckTime,
    setCurrentArtistImages,
    setManualStyleOverride,
    setManualVisualModeOverride,
    setWordSyncEnabled,
    setDebugTimingEnabled
} from './modules/state.js';

// Utils (Level 1)
import { normalizeTrackId, sleep, areLyricsDifferent } from './modules/utils.js';

// API (Level 1)
import { getConfig, getCurrentTrack, getLyrics, fetchArtistImages } from './modules/api.js';

// DOM (Level 1)
import { setLyricsInDom, updateThemeColor } from './modules/dom.js';

// Settings (Level 2)
import { initializeDisplay } from './modules/settings.js';

// Controls (Level 2)
import {
    attachControlHandlers,
    updateControlState,
    updateProgress,
    updateTrackInfo,
    updateAlbumArt,
    setupQueueInteractions,
    toggleQueueDrawer,
    fetchAndRenderQueue,
    checkLikedStatus,
    toggleLike,
    setupTouchControls
} from './modules/controls.js';

// Background (Level 2)
import {
    updateBackground,
    getCurrentBackgroundStyle,
    applyBackgroundStyle,
    checkForVisualMode,
    enterVisualMode,
    exitVisualMode,
    resetVisualModeState,
    setSlideshowFunctions
} from './modules/background.js';

// Slideshow (Level 2)
import { startSlideshow, stopSlideshow } from './modules/slideshow.js';

// Provider (Level 3)
import { setupProviderUI, updateProviderDisplay, updateStyleButtonsInModal, updateInstrumentalButtonState } from './modules/provider.js';

// Audio Source (Level 3)
import audioSource from './modules/audioSource.js';

// Word Sync (Level 2)
import { startWordSyncAnimation, stopWordSyncAnimation, resetWordSyncState, updateDebugOverlay } from './modules/wordSync.js';

// Latency (Level 3)
import { setupLatencyControls, setupLatencyKeyboardShortcuts, updateLatencyDisplay } from './modules/latency.js';

// ========== CONNECT MODULES ==========

// Connect slideshow functions to background module
setSlideshowFunctions(startSlideshow, stopSlideshow);

// ========== WORD-SYNC TOGGLE UI HELPER ==========

/**
 * Update word-sync toggle button UI state
 * Called when hasWordSync or wordSyncEnabled changes
 */
function updateWordSyncToggleUI() {
    const toggleBtn = document.getElementById('btn-word-sync-toggle');
    if (!toggleBtn) return;
    
    const iconEl = toggleBtn.querySelector('i');
    
    // Update icon based on enabled state
    if (iconEl) {
        iconEl.className = wordSyncEnabled ? 'bi bi-mic-fill' : 'bi bi-mic-mute';
    }
    
    // Update active class (only active when enabled AND current provider has word-sync)
    toggleBtn.classList.toggle('active', wordSyncEnabled && hasWordSync);
    
    // Update unavailable class: toggle is available if ANY provider has word-sync
    // This allows user to enable word-sync even if current provider doesn't have it
    toggleBtn.classList.toggle('unavailable', !anyProviderHasWordSync);
    
    // Also sync the settings checkbox
    const checkbox = document.getElementById('opt-word-sync');
    if (checkbox) {
        checkbox.checked = wordSyncEnabled;
    }
}

// ========== ADAPTIVE POLLING CONSTANTS ==========
const IDLE_THRESHOLD = 20000; // 20 seconds before switching to slow polling
const IDLE_POLL_INTERVAL = 1000; // 1 second when in slow polling mode

// ========== MAIN UPDATE LOOP ==========

/**
 * Main polling loop - fetches track and lyrics data
 */
async function updateLoop() {
    let lastTrackId = null;
    let isIdleState = false;
    let currentPollInterval = updateInterval;
    let idleStartTime = null;

    while (true) {
        const now = Date.now();
        const timeSinceLastCheck = now - lastCheckTime;

        // Update poll interval from config if not in idle mode
        if (currentPollInterval !== IDLE_POLL_INTERVAL) {
            currentPollInterval = updateInterval;
        }

        // Ensure minimum time between checks
        if (timeSinceLastCheck < currentPollInterval) {
            await sleep(currentPollInterval - timeSinceLastCheck);
            continue;
        }

        // Fetch track info and lyrics in parallel
        const [trackInfo, data] = await Promise.all([
            getCurrentTrack(),
            getLyrics(updateBackground, updateThemeColor, updateProviderDisplay)
        ]);

        setLastCheckTime(Date.now());

        // Fix 4.1: Update audio source button with current track source
        if (trackInfo && trackInfo.source) {
            const sourceBtn = document.getElementById('source-name');
            if (sourceBtn) {
                const sourceMap = {
                    'spotify': 'Spotify',
                    'spotify_hybrid': 'Hybrid',
                    'windows': 'Windows',
                    'windows_media': 'Windows',
                    'audio_recognition': 'Shazam',
                    'shazam': 'Shazam',
                    'reaper': 'Reaper'
                };
                sourceBtn.textContent = sourceMap[trackInfo.source] || 'Spotify';
            }
        }

        // Handle track info errors
        if (trackInfo.error || !trackInfo.title) {
            if (!isIdleState) {
                isIdleState = true;
                idleStartTime = Date.now();
            }

            if (isIdleState && idleStartTime && (Date.now() - idleStartTime > IDLE_THRESHOLD)) {
                currentPollInterval = IDLE_POLL_INTERVAL;
            }

            await sleep(currentPollInterval);
            continue;
        }

        // Reset idle state when we have valid track info
        if (isIdleState) {
            isIdleState = false;
            idleStartTime = null;
            currentPollInterval = updateInterval;
        }

        // Get track ID
        let trackId;
        if (trackInfo.track_id && trackInfo.track_id.trim()) {
            trackId = trackInfo.track_id.trim();
        } else {
            const artist = (trackInfo.artist || '').trim();
            const title = (trackInfo.title || '').trim();
            trackId = normalizeTrackId(artist, title);
        }

        // Detect track change
        const trackChanged = trackId !== lastTrackId;

        if (trackChanged) {
            console.log(`[Main] Track changed: ${lastTrackId} -> ${trackId}`);
            lastTrackId = trackId;

            // Reset visual mode on track change
            resetVisualModeState();

            // Reset word-sync state on track change (stops animation, clears logged flag)
            resetWordSyncState();

            // Reset manual overrides on track change
            setManualVisualModeOverride(false);
            setManualStyleOverride(false);

            // Update instrumental button state
            updateInstrumentalButtonState();

            // Clear current artist images
            setCurrentArtistImages([]);

            // Fetch artist images for visual mode (non-blocking)
            if (trackInfo.artist_id && visualModeConfig.enabled) {
                fetchArtistImages(trackInfo.artist_id).then(images => {
                    setCurrentArtistImages(images);
                });
            }

            // Update liked status for new track
            if (trackInfo.id) {
                checkLikedStatus(trackInfo.id);
            }

            // Reset style buttons in modal (show 'auto' when no saved preference)
            updateStyleButtonsInModal(trackInfo.background_style || 'auto');

            // Refresh queue if drawer is open
            if (queueDrawerOpen) {
                console.log('[Main] Track changed, refreshing queue...');
                fetchAndRenderQueue();
            }
        }

        // Update track info
        setLastTrackInfo(trackInfo);

        // Apply background style with priority: Saved Preference > URL Params > Default
        // Only apply saved style if user has opted-in to art background via URL or settings
        const hasArtBgEnabled = displayConfig.artBackground || displayConfig.softAlbumArt || displayConfig.sharpAlbumArt;
        if (trackInfo.background_style && !manualStyleOverride && !visualModeActive && hasArtBgEnabled) {
            const currentStyle = getCurrentBackgroundStyle();
            if (currentStyle !== trackInfo.background_style) {
                console.log(`[Main] Applying saved background style: ${trackInfo.background_style}`);
                applyBackgroundStyle(trackInfo.background_style);
            }
        } else if (!manualStyleOverride && !visualModeActive) {
            // Priority 2: URL parameters (fallback if no saved preference)
            const urlParams = new URLSearchParams(window.location.search);
            const currentStyle = getCurrentBackgroundStyle();
            let urlStyle = null;

            if (urlParams.has('sharpAlbumArt') && urlParams.get('sharpAlbumArt') === 'true') {
                urlStyle = 'sharp';
            } else if (urlParams.has('softAlbumArt') && urlParams.get('softAlbumArt') === 'true') {
                urlStyle = 'soft';
            } else if (urlParams.has('artBackground') && urlParams.get('artBackground') === 'true') {
                urlStyle = 'blur';
            }

            if (urlStyle && currentStyle !== urlStyle) {
                console.log(`[Main] Applying URL background style: ${urlStyle}`);
                applyBackgroundStyle(urlStyle);
            }
        }

        updateTrackInfo(trackInfo);
        updateAlbumArt(trackInfo, updateBackground);
        updateProgress(trackInfo);
        updateControlState(trackInfo);

        // Update lyrics
        if (data && data.lyrics && data.lyrics.length > 0) {
            if (areLyricsDifferent(lastLyrics, data.lyrics)) {
                setLyricsInDom(data.lyrics);
            }
        } else if (data && typeof data === 'object') {
            setLyricsInDom(data);
        }

        // Check for visual mode
        checkForVisualMode(data, trackId);

        // Start word-sync animation loop if word-sync is available
        // The rAF loop runs at display refresh rate (60-144fps) for smooth animation
        // Position is interpolated between polls using anchor + elapsed time
        startWordSyncAnimation();
        
        // Update word-sync toggle button UI state (icon, unavailable class)
        // This ensures button reflects current hasWordSync state after each poll
        updateWordSyncToggleUI();

        await sleep(currentPollInterval);
    }
}

// ========== INITIALIZATION ==========

/**
 * Main initialization function
 */
async function main() {
    // Mark document as JS-ready immediately to reveal content (FOUC prevention)
    document.documentElement.classList.add('js-ready');
    
    console.log('[Main] Initializing SyncLyrics...');

    // Load config first
    await getConfig();

    // Initialize display from URL params
    initializeDisplay();

    // Setup UI components
    attachControlHandlers(enterVisualMode, exitVisualMode);
    setupProviderUI();
    setupQueueInteractions();
    setupTouchControls();

    // Initialize audio source module
    audioSource.init();

    // Apply initial background
    updateBackground();

    // Setup like button
    const likeBtn = document.getElementById('btn-like');
    if (likeBtn) {
        likeBtn.addEventListener('click', toggleLike);
    }

    // Setup queue buttons
    const queueBtn = document.getElementById('btn-queue');
    if (queueBtn) {
        queueBtn.addEventListener('click', toggleQueueDrawer);
    }

    const queueCloseBtn = document.getElementById('queue-close');
    if (queueCloseBtn) {
        queueCloseBtn.addEventListener('click', toggleQueueDrawer);
    }

    // Setup word-sync toggle button
    const wordSyncToggleBtn = document.getElementById('btn-word-sync-toggle');
    if (wordSyncToggleBtn) {
        // Initialize button state
        updateWordSyncToggleUI();
        
        wordSyncToggleBtn.addEventListener('click', () => {
            const newState = !wordSyncEnabled;
            setWordSyncEnabled(newState);
            
            // Update toggle button AND settings checkbox
            updateWordSyncToggleUI();
            
            // Save to localStorage for persistence
            localStorage.setItem('wordSyncEnabled', newState);
            
            // Start/stop word-sync animation based on new state
            if (newState && hasWordSync) {
                startWordSyncAnimation();
                console.log('[WordSync] Enabled via toggle');
            } else {
                stopWordSyncAnimation();
                console.log('[WordSync] Disabled via toggle');
            }
            
            // Update URL without page reload
            const url = new URL(window.location.href);
            if (!newState) {
                url.searchParams.set('wordSync', 'false');
            } else {
                url.searchParams.delete('wordSync');
            }
            history.replaceState(null, '', url.toString());
        });
        
        // Load from localStorage (URL param takes precedence via initializeDisplay)
        const savedState = localStorage.getItem('wordSyncEnabled');
        if (savedState !== null && !new URLSearchParams(window.location.search).has('wordSync')) {
            const enabled = savedState === 'true';
            setWordSyncEnabled(enabled);
            updateWordSyncToggleUI();
        }
    }

    // Initialize latency controls
    setupLatencyControls();
    setupLatencyKeyboardShortcuts();

    console.log('[Main] Initialization complete. Starting update loop...');

    // Start the main loop
    updateLoop();
    
    // Mark initialization as fully complete (for watchdog)
    // This is separate from js-ready which is set early for FOUC prevention
    document.documentElement.classList.add('js-init-complete');
}

// ========== JS INIT WATCHDOG ==========
// Auto-reload if JS fails to initialize (fixes HA WebView silent module failures)
(function initWatchdog() {
    const MAX_RETRIES = 3;
    const TIMEOUT_MS = 10000; // 10 seconds - generous for slow networks
    const retries = parseInt(sessionStorage.getItem('js-init-retries') || '0', 10);
    
    setTimeout(() => {
        // Check js-init-complete (set at END of main), not js-ready (set at START)
        // This catches: module load failures, init crashes, stuck async operations
        if (!document.documentElement.classList.contains('js-init-complete')) {
            console.error('[Init] JS failed to fully initialize — forcing reload');
            if (retries < MAX_RETRIES) {
                sessionStorage.setItem('js-init-retries', String(retries + 1));
                location.reload();
            } else {
                console.error('[Init] Max retries reached — showing content anyway');
                // Show content as last resort to avoid permanent blank screen
                document.documentElement.classList.add('js-ready');
                document.documentElement.classList.add('js-init-complete');
            }
        } else {
            // Success — reset retry counter
            sessionStorage.setItem('js-init-retries', '0');
        }
    }, TIMEOUT_MS);
})();

// ========== EVENT LISTENERS ==========

document.addEventListener('DOMContentLoaded', () => {
    main();
});

// ========== EXPORTS FOR HTML INLINE HANDLERS (if any) ==========
// If there are any onclick handlers in HTML that reference functions,
// we need to expose them on window. Currently there are none.

// Export for debugging
window.SyncLyrics = {
    state: () => ({ lastTrackInfo, displayConfig, visualModeActive, currentArtistImages }),
    enterVisualMode,
    exitVisualMode,
    updateBackground
};

// ========== DEBUG TIMING OVERLAY ==========

/**
 * Initialize debug timing overlay
 * Activated via URL param ?debug=timing or triple-tap on lyrics
 */
function initDebugOverlay() {
    // Check URL param
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('debug') === 'timing') {
        enableDebugOverlay();
    }
    
    // Setup triple-tap gesture on lyrics container
    const lyricsContainer = document.querySelector('.lyrics-container');
    if (lyricsContainer) {
        let tapCount = 0;
        let lastTapTime = 0;
        const TAP_THRESHOLD = 500; // 500ms window for triple-tap
        
        lyricsContainer.addEventListener('click', (e) => {
            // Don't trigger on control buttons
            if (e.target.closest('button') || e.target.closest('.control')) return;
            
            const now = Date.now();
            if (now - lastTapTime > TAP_THRESHOLD) {
                tapCount = 1;
            } else {
                tapCount++;
            }
            lastTapTime = now;
            
            if (tapCount === 3) {
                toggleDebugOverlay();
                tapCount = 0;
            }
        });
    }
}

/**
 * Enable debug overlay
 */
function enableDebugOverlay() {
    setDebugTimingEnabled(true);
    
    // Create overlay element if doesn't exist
    if (!document.getElementById('debug-timing-overlay')) {
        const overlay = document.createElement('div');
        overlay.id = 'debug-timing-overlay';
        overlay.className = 'debug-timing-overlay';
        overlay.innerHTML = '<div class="debug-row">Loading...</div>';
        document.body.appendChild(overlay);
    }
    
    document.getElementById('debug-timing-overlay').style.display = 'block';
    console.log('[Debug] Timing overlay enabled');
    
    // Start update loop for when word-sync is not active
    startDebugUpdateLoop();
}

/**
 * Disable debug overlay
 */
function disableDebugOverlay() {
    setDebugTimingEnabled(false);
    const overlay = document.getElementById('debug-timing-overlay');
    if (overlay) {
        overlay.style.display = 'none';
    }
    console.log('[Debug] Timing overlay disabled');
}

/**
 * Toggle debug overlay
 */
function toggleDebugOverlay() {
    if (debugTimingEnabled) {
        disableDebugOverlay();
    } else {
        enableDebugOverlay();
    }
}

/**
 * Update loop for debug overlay when word-sync animation isn't running
 */
function startDebugUpdateLoop() {
    function updateLoop() {
        if (!debugTimingEnabled) return;
        
        // Only update if word-sync animation isn't handling it
        if (!wordSyncEnabled || !hasWordSync) {
            updateDebugOverlay();
        }
        
        requestAnimationFrame(updateLoop);
    }
    requestAnimationFrame(updateLoop);
}

// Initialize debug overlay after DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Small delay to ensure main() has run
    setTimeout(initDebugOverlay, 100);
});
