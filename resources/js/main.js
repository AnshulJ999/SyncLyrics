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
    setLastTrackInfo,
    setLastLyrics,
    setLastCheckTime,
    setCurrentArtistImages
} from './modules/state.js';

// Utils (Level 1)
import { normalizeTrackId, sleep, areLyricsDifferent } from './modules/utils.js';

// API (Level 1)
import { getConfig, getCurrentTrack, getLyrics, fetchArtistImages } from './modules/api.js';

// DOM (Level 1)
import { setLyricsInDom, updateThemeColor, showToast } from './modules/dom.js';

// Settings (Level 2)
import { initializeDisplay, applyDisplayConfig } from './modules/settings.js';

// Controls (Level 2)
import {
    attachControlHandlers,
    updateControlState,
    updateProgress,
    updateTrackInfo,
    updateAlbumArt,
    setupQueueInteractions,
    checkLikedStatus,
    updateLikeButton,
    toggleLike,
    setupTouchControls
} from './modules/controls.js';

// Background (Level 2)
import {
    updateBackground,
    applySoftMode,
    applySharpMode,
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
import { setupProviderUI, updateProviderDisplay } from './modules/provider.js';

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

            // Update liked status for new track
            if (trackInfo.id) {
                checkLikedStatus(trackInfo.id);
            }

            // Fetch artist images for visual mode
            if (trackInfo.artist_id && visualModeConfig.enabled) {
                fetchArtistImages(trackInfo.artist_id).then(images => {
                    setCurrentArtistImages(images);
                });
            }
        }

        // Update track info
        setLastTrackInfo(trackInfo);
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

    // Apply initial background
    updateBackground();

    // Setup like button
    const likeBtn = document.getElementById('btn-like');
    if (likeBtn) {
        likeBtn.addEventListener('click', toggleLike);
    }

    // Setup queue button
    const queueBtn = document.getElementById('btn-queue');
    if (queueBtn) {
        queueBtn.addEventListener('click', async () => {
            const { toggleQueueDrawer } = await import('./modules/controls.js');
            toggleQueueDrawer();
        });
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
