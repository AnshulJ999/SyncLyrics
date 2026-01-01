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
    slideshowImagePool,
    slideshowInterval,
    setLastTrackInfo,
    setLastCheckTime,
    setCurrentArtistImages,
    setCurrentArtistImageMetadata,
    setManualStyleOverride,
    setManualVisualModeOverride,
    setWordSyncEnabled,
    setDebugTimingEnabled
} from './modules/state.js';

// Utils (Level 1)
import { normalizeTrackId, sleep, areLyricsDifferent } from './modules/utils.js';

// API (Level 1)
import { getConfig, getCurrentTrack, getLyrics, fetchArtistImages, fetchQueue } from './modules/api.js';

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
    setupTouchControls,
    attachProgressBarSeek
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
import {
    startSlideshow,
    stopSlideshow,
    initSlideshow,
    setupSlideshowButton,
    handleArtistChange as handleSlideshowArtistChange,
    loadImagePoolForCurrentArtist,
    pauseSlideshow,
    showSlideshowModal,
    setupControlCenter,
    loadSettingsFromLocalStorage,
    advanceSlide,
    previousSlide,
    isSlideshowActive
} from './modules/slideshow.js';

// Provider (Level 3)
import { setupProviderUI, updateProviderDisplay, updateStyleButtonsInModal, updateInstrumentalButtonState } from './modules/provider.js';

// Audio Source (Level 3)
import audioSource from './modules/audioSource.js';

// Word Sync (Level 2)
import { startWordSyncAnimation, stopWordSyncAnimation, resetWordSyncState, updateDebugOverlay } from './modules/wordSync.js';

// Latency (Level 3)
import { setupLatencyControls, setupLatencyKeyboardShortcuts, updateLatencyDisplay } from './modules/latency.js';

// Waveform & Spectrum Visualizers (Level 2)
import { initWaveform, updateWaveform, hideWaveform, resetWaveform } from './modules/waveform.js';
import { initSpectrum, updateSpectrum, hideSpectrum, resetSpectrum } from './modules/spectrum.js';

// Art Zoom (Level 2)
import { resetArtZoom, resetImageIndex, resetManualImageFlag, setPauseSlideshowFn, setSlideshowCycleFns } from './modules/artZoom.js';

// Touch Gestures (Level 2)
import { initTouchGestures } from './modules/touchGestures.js';

// ========== CONNECT MODULES ==========

// Connect slideshow functions to background module
setSlideshowFunctions(startSlideshow, stopSlideshow);

// Connect pause function to artZoom for manual browsing
setPauseSlideshowFn(pauseSlideshow);

// Connect slideshow cycling functions to artZoom for edge tap
setSlideshowCycleFns(advanceSlide, previousSlide, isSlideshowActive);

// Initialize slideshow module
initSlideshow();
loadSettingsFromLocalStorage();  // Load saved settings from localStorage
setupSlideshowButton(showSlideshowModal);  // Pass modal function for long-press
setupControlCenter();  // Setup control center event handlers

// ========== WORD-SYNC TOGGLE UI HELPER ==========

/**
 * Update word-sync toggle button UI state
 * Called when hasWordSync or wordSyncEnabled changes
 */
function updateWordSyncToggleUI() {
    const toggleBtn = document.getElementById('btn-word-sync-toggle');
    if (!toggleBtn) return;
    
    // SVG icon doesn't need class changes - use button's active/inactive classes instead
    // The SVG inherits color from the button via currentColor
    
    // Update active class (only active when enabled AND current provider has word-sync)
    toggleBtn.classList.toggle('active', wordSyncEnabled && hasWordSync);
    
    // When disabled but available, add inactive class for visual feedback
    toggleBtn.classList.toggle('inactive', !wordSyncEnabled && anyProviderHasWordSync);
    
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

// ========== NEXT-UP CARD CONSTANTS ==========
const NEXT_UP_SHOW_THRESHOLD_MS = 30000; // Show card in last 30 seconds
const NEXT_UP_RETRY_DELAY_MS = 4000; // 4 seconds between retries on error
let nextUpCardVisible = false;
let nextUpTrackId = null; // Cache to avoid redundant fetches
let nextUpLastFetchAttempt = 0; // Timestamp to throttle retries on error

/**
 * Update next-up preview card based on track position
 * Shows card in the last 30 seconds of a song
 * 
 * @param {Object} trackInfo - Current track information
 */
async function updateNextUpCard(trackInfo) {
    const card = document.getElementById('next-up-card');
    if (!card) return;

    const positionMs = (trackInfo.position || 0) * 1000;
    const durationMs = trackInfo.duration_ms || 0;
    const remainingMs = durationMs - positionMs;

    // Check if we're in the last 30 seconds
    if (durationMs > 0 && remainingMs <= NEXT_UP_SHOW_THRESHOLD_MS && remainingMs > 0) {
        // Fetch queue if: (1) new song, OR (2) not visible AND retry delay has passed
        // The retry delay prevents rapid API calls when fetch fails (error case)
        const now = Date.now();
        const isNewSong = nextUpTrackId !== trackInfo.track_id;
        const canRetry = (now - nextUpLastFetchAttempt) > NEXT_UP_RETRY_DELAY_MS;
        
        if (isNewSong || (!nextUpCardVisible && canRetry)) {
            nextUpLastFetchAttempt = now;
            try {
                const queueData = await fetchQueue();
                if (queueData && queueData.queue && queueData.queue.length > 0) {
                    const nextTrack = queueData.queue[0];
                    
                    // Populate card
                    const artEl = document.getElementById('next-up-art');
                    const titleEl = document.getElementById('next-up-title');
                    const artistEl = document.getElementById('next-up-artist');
                    
                    if (artEl && nextTrack.album && nextTrack.album.images) {
                        const artUrl = nextTrack.album.images[1]?.url || nextTrack.album.images[0]?.url || '';
                        artEl.src = artUrl;
                    }
                    if (titleEl) titleEl.textContent = nextTrack.name || 'Unknown';
                    if (artistEl) artistEl.textContent = nextTrack.artists?.[0]?.name || 'Unknown';
                    
                    card.classList.remove('hidden');
                    nextUpCardVisible = true;
                    nextUpTrackId = trackInfo.track_id;
                    console.log('[NextUp] Showing card for next track:', nextTrack.name);
                } else {
                    // No next track in queue - only log once
                    if (nextUpCardVisible) {
                        console.log('[NextUp] Queue empty, hiding card');
                    }
                    card.classList.add('hidden');
                    nextUpCardVisible = false;
                }
            } catch (e) {
                // Only log error once per song to avoid spam
                if (!nextUpCardVisible && nextUpTrackId !== trackInfo.track_id) {
                    console.error('[NextUp] Failed to fetch queue:', e);
                    nextUpTrackId = trackInfo.track_id; // Prevent repeated logs
                }
            }
        }
    } else {
        // Hide card when not in the last 30 seconds
        if (nextUpCardVisible) {
            card.classList.add('hidden');
            nextUpCardVisible = false;
            nextUpTrackId = null;
        }
    }
}

// ========== LINE-SYNC OUTRO VISUAL MODE ==========
const OUTRO_VISUAL_MODE_DELAY_SEC = 6.0; // Configurable delay before entering visual mode
let outroVisualModeTriggered = false;
let outroVisualModeTimer = null;

/**
 * Check for line-sync outro and trigger visual mode after delay
 * Called when lyrics.prev-1 shows "End"
 * 
 * @param {Object|Array} lyricsData - Lyrics data from API
 */
function checkForLineSyncOutro(lyricsData) {
    if (!visualModeConfig.enabled) return;
    if (visualModeActive) return; // Already in visual mode
    
    // Check if lyrics show "End" (outro state)
    const lyrics = lyricsData?.lyrics || lyricsData;
    const isOutro = Array.isArray(lyrics) && 
                    lyrics.length >= 2 && 
                    lyrics[1] === 'End';
    
    if (isOutro && !outroVisualModeTriggered) {
        console.log(`[Main] Line-sync outro detected, scheduling visual mode in ${OUTRO_VISUAL_MODE_DELAY_SEC}s`);
        outroVisualModeTriggered = true;
        
        outroVisualModeTimer = setTimeout(() => {
            if (!visualModeActive) {
                console.log('[Main] Line-sync outro delay elapsed, entering visual mode');
                enterVisualMode();
            }
        }, OUTRO_VISUAL_MODE_DELAY_SEC * 1000);
    } else if (!isOutro && outroVisualModeTriggered) {
        // Reset if no longer in outro (e.g., seeked back)
        outroVisualModeTriggered = false;
        if (outroVisualModeTimer) {
            clearTimeout(outroVisualModeTimer);
            outroVisualModeTimer = null;
        }
    }
}

// Reset outro state on track change (called from updateLoop when track changes)
function resetOutroState() {
    outroVisualModeTriggered = false;
    if (outroVisualModeTimer) {
        clearTimeout(outroVisualModeTimer);
        outroVisualModeTimer = null;
    }
    nextUpCardVisible = false;
    nextUpTrackId = null;
    const card = document.getElementById('next-up-card');
    if (card) card.classList.add('hidden');
}

// ========== MAIN UPDATE LOOP ==========

/**
 * Main polling loop - fetches track and lyrics data
 */
async function updateLoop() {
    let lastTrackId = null;
    let lastSource = null;  // Track audio source for reset on source change
    let lastAlbumArtUrl = null;  // Track album art for smart zoom reset
    let lastArtistId = null;  // Track artist for smart image index reset
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
                // Special handling for audio_recognition - show actual provider (Shazam or ACRCloud)
                if (trackInfo.source === 'audio_recognition') {
                    const provider = trackInfo.recognition_provider;
                    if (provider === 'acrcloud') {
                        sourceBtn.textContent = 'ACRCloud';
                    } else if (provider === 'shazam') {
                        sourceBtn.textContent = 'Shazam';
                    } else {
                        // Fallback: Shazam is the primary recognizer
                        sourceBtn.textContent = 'Audio';
                    }
                } else {
                    // Standard source mapping
                    const sourceMap = {
                        'spotify': 'Spotify',
                        'spotify_hybrid': 'Hybrid',
                        'spicetify': 'Spicetify',
                        'windows': 'Windows',
                        'windows_media': 'Windows',
                        'reaper': 'Reaper'
                    };
                    sourceBtn.textContent = sourceMap[trackInfo.source] || 'Spotify';
                }
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

            // Reset outro state (line-sync visual mode timer + next-up card)
            resetOutroState();

            // Reset word-sync state on track change (stops animation, clears logged flag)
            resetWordSyncState();

            // Reset waveform and spectrum on track change
            resetWaveform();
            resetSpectrum();

            // Reset manual overrides on track change
            setManualVisualModeOverride(false);
            setManualStyleOverride(false);

            // Update instrumental button state
            updateInstrumentalButtonState();

            // Smart art reset: only reset zoom if album art URL changes
            const newAlbumArt = trackInfo.album_art_url || trackInfo.album_art_path || '';
            if (newAlbumArt !== lastAlbumArtUrl) {
                resetArtZoom();
                lastAlbumArtUrl = newAlbumArt;
            }

            // Smart artist reset: only reset if artist changes
            const newArtistId = trackInfo.artist_id || '';
            const sameArtist = newArtistId === lastArtistId;
            if (!sameArtist) {
                resetImageIndex();
                resetManualImageFlag();  // Clear manual image selection when artist changes
                lastArtistId = newArtistId;
                
                // Only clear and refetch images when artist changes
                setCurrentArtistImages([]);
                setCurrentArtistImageMetadata([]);
                if (trackInfo.artist_id) {
                    // Capture artist ID at fetch time to detect stale responses
                    const artistIdAtFetch = trackInfo.artist_id;
                    // Fetch with metadata so modal has resolution data available
                    fetchArtistImages(trackInfo.artist_id, true).then(data => {
                        // Guard: Discard stale response if artist changed during fetch
                        if (lastTrackInfo?.artist_id !== artistIdAtFetch) {
                            console.log('[Main] Artist changed during image fetch, discarding stale data');
                            return;
                        }
                        // Don't prepend album art - slideshow handles it separately
                        // Art mode will access album art from lastTrackInfo when needed
                        setCurrentArtistImages(data.images || []);
                        setCurrentArtistImageMetadata(data.metadata || []);
                        
                        // Update slideshow image pool and restart if enabled
                        loadImagePoolForCurrentArtist();
                        // Slideshow should continue/restart with new images
                        startSlideshow();
                    });
                }
            }
            
            // Notify slideshow of artist change (handles same artist vs different artist logic)
            handleSlideshowArtistChange(trackInfo.artist || '', sameArtist);
            
            // Safety net: Only restart slideshow if it stopped unexpectedly
            // If slideshow is already running (!slideshowInterval = false), leave it alone
            // This prevents unnecessary re-shuffle and image change on track skip
            if (sameArtist && slideshowImagePool.length > 0 && !slideshowInterval) {
                console.log('[Main] Slideshow stopped unexpectedly, restarting for same artist');
                startSlideshow();
            }
            // If same artist, keep existing artist images and selected index

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

        // Detect source change (e.g., Spicetify -> MusicBee)
        // Reset waveform/spectrum since audio analysis data changes
        const currentSource = trackInfo?.source;
        if (currentSource && currentSource !== lastSource) {
            console.log(`[Main] Source changed: ${lastSource} -> ${currentSource}`);
            lastSource = currentSource;
            
            // Reset waveform and spectrum on source change
            // (audio analysis data is source-specific)
            resetWaveform();
            resetSpectrum();
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

        // Next-up preview card - show in last 30 seconds of song
        updateNextUpCard(trackInfo);

        // Update waveform seekbar (if enabled)
        if (displayConfig.showWaveform) {
            updateWaveform(trackInfo);
        } else {
            hideWaveform();
        }

        // Update spectrum visualizer (if enabled)
        if (displayConfig.showSpectrum) {
            updateSpectrum(trackInfo);
        } else {
            hideSpectrum();
        }

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

        // Check for line-sync outro (triggers visual mode after 6s delay)
        checkForLineSyncOutro(data);

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
    attachProgressBarSeek();  // Enable click-to-seek on progress bar
    setupProviderUI();
    setupQueueInteractions();
    setupTouchControls();

    // Initialize multi-finger touch gestures (3-finger tap for play/pause)
    initTouchGestures();

    // Initialize audio source module
    audioSource.init();

    // Initialize waveform and spectrum visualizers
    initWaveform();
    initSpectrum();

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

    // Setup next-up card tap-to-dismiss
    const nextUpCard = document.getElementById('next-up-card');
    if (nextUpCard) {
        nextUpCard.addEventListener('click', () => {
            nextUpCard.classList.add('hidden');
            nextUpCardVisible = false;
        });
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
    
    // Listen for word-sync outro event to trigger visual mode
    // When lyrics finish, auto-enter visual mode for songs with long instrumental outros
    // Gated by visualModeConfig.enabled to respect user settings
    window.addEventListener('wordSyncOutro', () => {
        if (!visualModeConfig.enabled) {
            console.log('[Main] Word-sync outro detected, but visual mode disabled in settings');
            return;
        }
        console.log('[Main] Word-sync outro detected, entering visual mode');
        enterVisualMode();  // Already has internal guard for visualModeActive
    });

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
