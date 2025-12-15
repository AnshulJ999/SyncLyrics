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
    setLastTrackInfo,
    setLastCheckTime,
    setCurrentArtistImages,
    setManualStyleOverride,
    setManualVisualModeOverride
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
import { startWordSyncAnimation, stopWordSyncAnimation, resetWordSyncState } from './modules/wordSync.js';

// ========== CONNECT MODULES ==========

// Connect slideshow functions to background module
setSlideshowFunctions(startSlideshow, stopSlideshow);

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

        await sleep(currentPollInterval);
    }
}

// ========== INITIALIZATION ==========

/**
 * Main initialization function
 */
async function main() {
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

    console.log('[Main] Initialization complete. Starting update loop...');

    // Start the main loop
    updateLoop();
}

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
