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
