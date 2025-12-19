/**
 * state.js - Central State Management
 * 
 * This module contains all global state variables for the SyncLyrics frontend.
 * All mutable state is centralized here to make it clear where state lives.
 * 
 * Level 0 - No dependencies on other modules
 */

// ========== CORE STATE ==========
export let lastLyrics = null;
export let updateInProgress = false;
export let currentColors = ["#24273a", "#363b54"];
export let updateInterval = 100; // Default value, will be updated from config
export let lastCheckTime = 0;    // Track last check time

// ========== WORD-SYNC STATE ==========
export let wordSyncedLyrics = null;  // Current word-synced lyrics data from API
export let hasWordSync = false;       // Whether current song has word-sync available
export let wordSyncProvider = null;   // Which provider is serving word-sync data
export let wordSyncStyle = 'pop';    // 'fade' or 'pop' - animation style for word highlighting
export let wordSyncEnabled = true;    // Global toggle for word-sync (can be disabled via URL or button)

// Word-sync interpolation state (for smooth 60-144fps animation between 100ms polls)
export let wordSyncAnchorPosition = 0;    // Last known playback position (seconds)
export let wordSyncAnchorTimestamp = 0;   // performance.now() when position was received
export let wordSyncIsPlaying = true;      // Is playback currently active?
export let wordSyncAnimationId = null;    // requestAnimationFrame id for cleanup
export let wordSyncLatencyCompensation = 0; // Line-sync latency compensation from server (seconds)
export let wordSyncSpecificLatencyCompensation = 0; // Word-sync specific latency adjustment (seconds)
export let providerWordSyncOffset = 0; // Provider-specific word-sync offset (Musixmatch/NetEase)
export let songWordSyncOffset = 0; // Per-song word-sync offset (user adjustment)
export let anyProviderHasWordSync = false; // True if ANY cached provider has word-sync (for toggle availability)
export let instrumentalMarkers = [];       // Timestamps where ♪ appears in line-sync (for gap detection)

// ========== DEBUG OVERLAY STATE ==========
export let debugTimingEnabled = false;  // Whether debug overlay is visible
export let debugRtt = 0;                // Current RTT in ms
export let debugRttSmoothed = 0;        // EMA-smoothed RTT
export let debugRttJitter = 0;          // RTT variability (EMA of absolute deviation)
export let debugServerPosition = 0;    // Last server-reported position (with RTT correction)
export let debugPollTimestamp = 0;     // When last poll completed
export let debugLastPollTimestamp = 0; // Previous poll timestamp (for dt_poll calculation)
export let debugPollInterval = 0;      // Time between polls (dt_poll_ms)
export let debugSource = '';           // Current audio source
export let debugBadSamples = 0;        // Count of ignored bad samples

// ========== TRACK INFO ==========

export let lastTrackInfo = null;
export let pendingArtUrl = null;

// ========== DISPLAY CONFIGURATION ==========
export let displayConfig = {
    minimal: false,
    showAlbumArt: true,
    showTrackInfo: true,
    showControls: true,
    showProgress: true,
    showBottomNav: true,
    showProvider: true,
    showAudioSource: true,      // Audio source menu (top left)
    showVisualModeToggle: true, // Visual mode toggle button (bottom left)
    useAlbumColors: false,
    artBackground: false,
    softAlbumArt: false,  // Soft album art background (medium blur, no scaling, balanced)
    sharpAlbumArt: false  // Sharp album art background (no blur, no scaling, super sharp and clear)
};

// ========== VISUAL MODE STATE ==========
export let visualModeActive = false;
export let visualModeTimer = null;
export let visualModeDebounceTimer = null; // Prevents flickering status from resetting visual mode
export let manualVisualModeOverride = false; // Track if user manually enabled Visual Mode (prevents auto-exit)
export let visualModeTrackId = null; // Track ID that visual mode decision is based on (prevents stale timers)
export let visualModeTimerId = null;

// ========== SLIDESHOW STATE ==========
// SEPARATED DATA SOURCES to prevent collision between Visual Mode and Idle Mode
export let currentArtistImages = []; // For Visual Mode (Current Song's Artist)
export let dashboardImages = [];     // For Idle Mode (Global Random Shuffle)
export let slideshowInterval = null;
export let currentSlideIndex = 0;
export let slideshowEnabled = false;  // Separate from visual mode - for when no music is playing

// ========== VISUAL MODE CONFIGURATION ==========
export let visualModeConfig = {
    enabled: true,
    delaySeconds: 10,
    autoSharp: true,
    slideshowEnabled: true,
    slideshowIntervalSeconds: 8
};

// ========== BACKGROUND STATE ==========
export let savedBackgroundState = null;
export let manualStyleOverride = false; // Phase 2: Track if user manually overrode style

// ========== QUEUE & LIKE STATE ==========
export let queueDrawerOpen = false;
export let queuePollInterval = null; // Track the polling interval for queue updates
export let isLiked = false;

// ========== CONSTANTS ==========
// Provider Display Names Mapping
export const providerDisplayNames = {
    "lrclib": "LRCLib",
    "spotify": "Spotify",
    "netease": "NetEase",
    "qq": "QQ",
    "musixmatch": "Musixmatch"
};

// ========== STATE SETTERS ==========
// These functions allow other modules to update state

export function setLastLyrics(value) { lastLyrics = value; }
export function setUpdateInProgress(value) { updateInProgress = value; }
export function setCurrentColors(value) { currentColors = value; }
export function setUpdateInterval(value) { updateInterval = value; }
export function setLastCheckTime(value) { lastCheckTime = value; }
export function setLastTrackInfo(value) { lastTrackInfo = value; }
export function setPendingArtUrl(value) { pendingArtUrl = value; }
export function setVisualModeActive(value) { visualModeActive = value; }
export function setVisualModeTimer(value) { visualModeTimer = value; }
export function setVisualModeDebounceTimer(value) { visualModeDebounceTimer = value; }
export function setManualVisualModeOverride(value) { manualVisualModeOverride = value; }
export function setVisualModeTrackId(value) { visualModeTrackId = value; }
export function setVisualModeTimerId(value) { visualModeTimerId = value; }
export function setCurrentArtistImages(value) { currentArtistImages = value; }
export function setDashboardImages(value) { dashboardImages = value; }
export function setSlideshowInterval(value) { slideshowInterval = value; }
export function setCurrentSlideIndex(value) { currentSlideIndex = value; }
export function setSlideshowEnabled(value) { slideshowEnabled = value; }
export function setSavedBackgroundState(value) { savedBackgroundState = value; }
export function setManualStyleOverride(value) { manualStyleOverride = value; }
export function setQueueDrawerOpen(value) { queueDrawerOpen = value; }
export function setQueuePollInterval(value) { queuePollInterval = value; }
export function setIsLiked(value) { isLiked = value; }
export function setWordSyncedLyrics(value) { wordSyncedLyrics = value; }
export function setHasWordSync(value) { hasWordSync = value; }
export function setWordSyncProvider(value) { wordSyncProvider = value; }
export function setWordSyncStyle(value) { wordSyncStyle = value; }
export function setWordSyncEnabled(value) { wordSyncEnabled = value; }

// Word-sync interpolation setters
export function setWordSyncAnchorPosition(value) { wordSyncAnchorPosition = value; }
export function setWordSyncAnchorTimestamp(value) { wordSyncAnchorTimestamp = value; }
export function setWordSyncIsPlaying(value) { wordSyncIsPlaying = value; }
export function setWordSyncAnimationId(value) { wordSyncAnimationId = value; }
export function setWordSyncLatencyCompensation(value) { wordSyncLatencyCompensation = value; }
export function setWordSyncSpecificLatencyCompensation(value) { wordSyncSpecificLatencyCompensation = value; }
export function setProviderWordSyncOffset(value) { providerWordSyncOffset = value; }
export function setSongWordSyncOffset(value) { songWordSyncOffset = value; }
export function setAnyProviderHasWordSync(value) { anyProviderHasWordSync = value; }
export function setInstrumentalMarkers(value) { instrumentalMarkers = value || []; }

// Debug overlay setters
export function setDebugTimingEnabled(value) { debugTimingEnabled = value; }
export function setDebugRtt(value) { debugRtt = value; }
export function setDebugRttSmoothed(value) { debugRttSmoothed = value; }
export function setDebugRttJitter(value) { debugRttJitter = value; }
export function setDebugServerPosition(value) { debugServerPosition = value; }
export function setDebugPollTimestamp(value) { debugPollTimestamp = value; }
export function setDebugLastPollTimestamp(value) { debugLastPollTimestamp = value; }
export function setDebugPollInterval(value) { debugPollInterval = value; }
export function setDebugSource(value) { debugSource = value; }
export function setDebugBadSamples(value) { debugBadSamples = value; }
