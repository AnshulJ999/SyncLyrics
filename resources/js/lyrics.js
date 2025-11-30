let lastLyrics = null;
let updateInProgress = false;
let currentColors = ["#24273a", "#363b54"];
let updateInterval = 100; // Default value, will be updated from config
let lastCheckTime = 0;    // Track last check time

// Display configuration
let displayConfig = {
    minimal: false,
    showAlbumArt: true,
    showTrackInfo: true,
    showControls: true,
    showProgress: true,
    showBottomNav: true,
    showProvider: true,  // NEW
    useAlbumColors: false,
    artBackground: false,
    softAlbumArt: false,  // Soft album art background (medium blur, no scaling, balanced)
    sharpAlbumArt: false  // Sharp album art background (no blur, no scaling, super sharp and clear)
};

let lastTrackInfo = null;

// Global variable at the top of the file
let pendingArtUrl = null;

// Visual Mode State Management
let visualModeActive = false;
let visualModeTimer = null;
let artistImages = [];
let slideshowInterval = null;
let currentSlideIndex = 0;
let slideshowEnabled = false;  // Separate from visual mode - for when no music is playing
let visualModeConfig = {
    enabled: true,
    delaySeconds: 10,
    autoSharp: true,
    slideshowEnabled: true,
    slideshowIntervalSeconds: 8
};
// ADD THIS: Global variable to store state
let savedBackgroundState = null;
// Phase 2: Track if user manually overrode style (to prevent auto-applying saved style)
let manualStyleOverride = false;

// Global variable
let visualModeTimerId = null;

// Global state variables
let queueDrawerOpen = false;
let isLiked = false;

// --- Helper: Robust Clipboard Copy ---
async function copyToClipboard(text) {
    // Try modern API first (Works on HTTPS / Localhost)
    if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text);
    }

    // Fallback for HTTP (Mobile LAN)
    return new Promise((resolve, reject) => {
        try {
            const textArea = document.createElement("textarea");
            textArea.value = text;

            // Ensure it's not visible but part of DOM
            textArea.style.position = "fixed";
            textArea.style.left = "-9999px";
            textArea.style.top = "0";
            document.body.appendChild(textArea);

            textArea.focus();
            textArea.select();

            // Mobile specific selection
            textArea.setSelectionRange(0, 99999);

            const successful = document.execCommand('copy');
            document.body.removeChild(textArea);

            if (successful) resolve();
            else reject(new Error("execCommand failed"));
        } catch (err) {
            reject(err);
        }
    });
}

async function getConfig() {
    try {
        const response = await fetch('/config');
        const config = await response.json();
        updateInterval = config.updateInterval;
        console.log(`Update interval set to: ${updateInterval}ms`);  // Debug log

        if (config.overlayOpacity !== undefined) {
            document.documentElement.style.setProperty('--overlay-opacity', config.overlayOpacity);
        }
        if (config.blurStrength !== undefined) {
            document.documentElement.style.setProperty('--blur-strength', config.blurStrength + 'px');
        }
        // Set soft album art mode from config only if URL didn't explicitly set it
        // URL parameters take precedence over server config
        const urlParams = new URLSearchParams(window.location.search);
        if (config.softAlbumArt !== undefined && !urlParams.has('softAlbumArt')) {
            displayConfig.softAlbumArt = config.softAlbumArt;
            // Enforce mutual exclusivity when setting from server config
            if (displayConfig.softAlbumArt) {
                displayConfig.artBackground = false;
                displayConfig.sharpAlbumArt = false;
            }
            applySoftMode();
        }
        // Set sharp album art mode from config only if URL didn't explicitly set it
        // URL parameters take precedence over server config
        if (config.sharpAlbumArt !== undefined && !urlParams.has('sharpAlbumArt')) {
            displayConfig.sharpAlbumArt = config.sharpAlbumArt;
            // Enforce mutual exclusivity when setting from server config
            if (displayConfig.sharpAlbumArt) {
                displayConfig.artBackground = false;
                displayConfig.softAlbumArt = false;
            }
            applySharpMode();
        }

        // Load visual mode settings from server
        if (config.visualModeEnabled !== undefined) {
            visualModeConfig.enabled = config.visualModeEnabled;
        }
        if (config.visualModeDelaySeconds !== undefined) {
            visualModeConfig.delaySeconds = config.visualModeDelaySeconds;
        }
        if (config.visualModeAutoSharp !== undefined) {
            visualModeConfig.autoSharp = config.visualModeAutoSharp;
        }
        if (config.slideshowEnabled !== undefined) {
            visualModeConfig.slideshowEnabled = config.slideshowEnabled;
        }
        if (config.slideshowIntervalSeconds !== undefined) {
            visualModeConfig.slideshowIntervalSeconds = config.slideshowIntervalSeconds;
        }

        console.log(`Config loaded: Interval=${updateInterval}ms, Blur=${config.blurStrength}px, Opacity=${config.overlayOpacity}, Soft=${config.softAlbumArt}, Sharp=${config.sharpAlbumArt}`);

    } catch (error) {
        console.error('Error fetching config:', error);
    }
}

async function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function getCurrentTrack() {
    try {
        const response = await fetch('/current-track');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('Error fetching current track:', error);
        return { error: error.message };
    }
}

async function getLyrics() {
    try {
        let response = await fetch('/lyrics');
        let data = await response.json();

        // Update background if colors are present
        if (data.colors && (data.colors[0] !== currentColors[0] || data.colors[1] !== currentColors[1])) {
            currentColors = data.colors;
            // We call updateBackground here to ensure colors are applied if art background is off
            updateBackground();
            // Update PWA theme-color meta tag with the first color (for Android status bar)
            updateThemeColor(data.colors[0]);
        }

        // Update provider info
        if (data.provider) {
            updateProviderDisplay(data.provider);
        } else {
            // Ensure provider button is visible even if no lyrics found (for Album Art selection)
            updateProviderDisplay("None");
        }

        return data || data.lyrics;
    } catch (error) {
        console.error('Error fetching lyrics:', error);
        return null;
    }
}

function areLyricsDifferent(oldLyrics, newLyrics) {
    if (!oldLyrics || !newLyrics) return true;
    if (!Array.isArray(oldLyrics) || !Array.isArray(newLyrics)) return true;
    return JSON.stringify(oldLyrics) !== JSON.stringify(newLyrics);
}

/**
 * Update the theme-color meta tag dynamically when album colors change.
 * This updates the Android status bar and task switcher preview color.
 * @param {string} color - The color to set (hex format, e.g., "#1db954")
 */
function updateThemeColor(color) {
    const metaThemeColor = document.querySelector('meta[name="theme-color"]');
    if (metaThemeColor && color) {
        metaThemeColor.setAttribute('content', color);
    }
}

function updateBackground() {
    const bgLayer = document.getElementById('background-layer');
    const bgOverlay = document.getElementById('background-overlay');

    // Check for album art backgrounds in priority order: Sharp > Soft > Blur
    if (displayConfig.sharpAlbumArt && lastTrackInfo && lastTrackInfo.album_art_url) {
        // Fix: Encode URI to handle spaces/symbols in local paths
        // We use encodeURI to allow the full URL structure but escape spaces
        const safeUrl = encodeURI(lastTrackInfo.album_art_url);
        bgLayer.style.backgroundImage = `url("${safeUrl}")`;

        bgLayer.classList.add('visible');
        bgOverlay.classList.add('visible');
        // Remove gradient from body when art background is active
        document.body.style.background = 'transparent';
    }
    else if (displayConfig.softAlbumArt && lastTrackInfo && lastTrackInfo.album_art_url) {
        // Fix: Encode URI to handle spaces/symbols in local paths
        // We use encodeURI to allow the full URL structure but escape spaces
        const safeUrl = encodeURI(lastTrackInfo.album_art_url);
        bgLayer.style.backgroundImage = `url("${safeUrl}")`;

        bgLayer.classList.add('visible');
        bgOverlay.classList.add('visible');
        // Remove gradient from body when art background is active
        document.body.style.background = 'transparent';
    }
    else if (displayConfig.artBackground && lastTrackInfo && lastTrackInfo.album_art_url) {
        // Fix: Encode URI to handle spaces/symbols in local paths
        // We use encodeURI to allow the full URL structure but escape spaces
        const safeUrl = encodeURI(lastTrackInfo.album_art_url);
        bgLayer.style.backgroundImage = `url("${safeUrl}")`;

        bgLayer.classList.add('visible');
        bgOverlay.classList.add('visible');
        // Remove gradient from body when art background is active
        document.body.style.background = 'transparent';
    }
    else if (displayConfig.useAlbumColors && currentColors) {
        bgLayer.classList.remove('visible');
        bgOverlay.classList.remove('visible');
        document.body.style.background = `linear-gradient(135deg, ${currentColors[0]} 0%, ${currentColors[1]} 100%)`;
    }
    else {
        bgLayer.classList.remove('visible');
        bgOverlay.classList.remove('visible');
        document.body.style.background = `linear-gradient(135deg, #1e2030 0%, #2f354d 100%)`;
    }

    // Apply mode styling
    applySoftMode();
    applySharpMode();

    // Add subtle animation
    document.body.style.transition = 'background 1s ease-in-out';
}

/**
 * Apply soft mode styling for medium blur album art background
 * This function toggles the 'soft-mode' class on the body element
 * which is used by CSS to apply medium blur effects
 */
function applySoftMode() {
    // Toggle soft-mode class on body based on softAlbumArt setting
    if (displayConfig.softAlbumArt) {
        document.body.classList.add('soft-mode');
    } else {
        document.body.classList.remove('soft-mode');
    }
}

/**
 * Apply sharp mode styling to remove blur and scaling effects
 * This function toggles the 'sharp-mode' class on the body element
 * which is used by CSS to disable blur filters and scaling transforms
 */
function applySharpMode() {
    // Toggle sharp-mode class on body based on sharpAlbumArt setting
    if (displayConfig.sharpAlbumArt) {
        document.body.classList.add('sharp-mode');
    } else {
        document.body.classList.remove('sharp-mode');
    }
}

function updateLyricElement(element, text) {
    if (element && element.textContent !== text) {
        element.textContent = text;
    }
}

function setLyricsInDom(lyrics) {
    if (updateInProgress) return;
    if (!Array.isArray(lyrics)) {
        lyrics = ['', '', lyrics.msg || '', '', '', ''];
    }

    // Only update if lyrics have changed
    if (!areLyricsDifferent(lastLyrics, lyrics)) {
        return;
    }

    updateInProgress = true;
    lastLyrics = [...lyrics];

    // Update all elements simultaneously
    updateLyricElement(document.getElementById('prev-2'), lyrics[0]);
    updateLyricElement(document.getElementById('prev-1'), lyrics[1]);
    updateLyricElement(document.getElementById('current'), lyrics[2]);
    updateLyricElement(document.getElementById('next-1'), lyrics[3]);
    updateLyricElement(document.getElementById('next-2'), lyrics[4]);
    updateLyricElement(document.getElementById('next-3'), lyrics[5]);

    setTimeout(() => {
        updateInProgress = false;
    }, 800);
}

// Parse URL parameters and initialize display
function initializeDisplay() {
    const params = new URLSearchParams(window.location.search);

    // Parse parameters - only override defaults if explicitly set in URL
    displayConfig.minimal = params.get('minimal') === 'true';

    if (params.has('showAlbumArt')) {
        displayConfig.showAlbumArt = params.get('showAlbumArt') === 'true';
    }
    if (params.has('showTrackInfo')) {
        displayConfig.showTrackInfo = params.get('showTrackInfo') === 'true';
    }
    if (params.has('showControls')) {
        displayConfig.showControls = params.get('showControls') === 'true';
    }
    if (params.has('showProgress')) {
        displayConfig.showProgress = params.get('showProgress') === 'true';
    }
    if (params.has('showBottomNav')) {
        displayConfig.showBottomNav = params.get('showBottomNav') === 'true';
    }
    if (params.has('useAlbumColors')) {
        displayConfig.useAlbumColors = params.get('useAlbumColors') === 'true';
    }
    if (params.has('artBackground')) {
        displayConfig.artBackground = params.get('artBackground') === 'true';
    }
    if (params.has('softAlbumArt')) {
        displayConfig.softAlbumArt = params.get('softAlbumArt') === 'true';
    }
    if (params.has('sharpAlbumArt')) {
        displayConfig.sharpAlbumArt = params.get('sharpAlbumArt') === 'true';
    }
    // Enforce mutual exclusivity: Sharp > Soft > Blur (priority order)
    if (displayConfig.sharpAlbumArt) {
        displayConfig.artBackground = false;
        displayConfig.softAlbumArt = false;
    } else if (displayConfig.softAlbumArt) {
        displayConfig.artBackground = false;
        displayConfig.sharpAlbumArt = false;
    } else if (displayConfig.artBackground) {
        displayConfig.softAlbumArt = false;
        displayConfig.sharpAlbumArt = false;
    }
    if (params.has('showProvider')) {
        displayConfig.showProvider = params.get('showProvider') === 'true';
    }

    // Minimal mode overrides all
    if (displayConfig.minimal) {
        displayConfig.showAlbumArt = false;
        displayConfig.showTrackInfo = false;
        displayConfig.showControls = false;
        displayConfig.showProgress = false;
        displayConfig.showBottomNav = false;
        displayConfig.showProvider = false;
    }

    // Apply visibility
    applyDisplayConfig();
    // Apply mode styling
    applySoftMode();
    applySharpMode();

    // Setup settings panel (if not minimal)
    if (!displayConfig.minimal) {
        setupSettingsPanel();
    }
}

function applyDisplayConfig() {
    const trackHeader = document.getElementById('track-header');
    const progressContainer = document.getElementById('progress-container');
    const playbackControls = document.getElementById('playback-controls');
    const settingsToggle = document.getElementById('settings-toggle');
    const bottomNav = document.getElementById('bottom-nav');

    // Toggle bottom nav visibility and body class for dynamic positioning
    if (bottomNav) {
        if (displayConfig.showBottomNav) {
            bottomNav.classList.remove('hidden');
            document.body.classList.remove('hide-nav');
        } else {
            bottomNav.classList.add('hidden');
            document.body.classList.add('hide-nav');
        }
    }

    if (trackHeader) {
        trackHeader.style.display = (displayConfig.showAlbumArt || displayConfig.showTrackInfo) ? 'flex' : 'none';
    }

    if (progressContainer) {
        progressContainer.style.display = displayConfig.showProgress ? 'block' : 'none';
    }

    if (playbackControls) {
        playbackControls.style.display = displayConfig.showControls ? 'flex' : 'none';
    }

    if (settingsToggle) {
        settingsToggle.style.display = displayConfig.minimal ? 'none' : 'block';
    }

    const providerInfo = document.getElementById('provider-info');
    if (providerInfo) {
        providerInfo.style.display = displayConfig.showProvider ? 'flex' : 'none';
    }

    // Ensure background is correct
    updateBackground();
}

// Helper to save background style to server (Per-Album Persistence)
async function saveBackgroundStyle(style) {
    try {
        await fetch('/api/album-art/background-style', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ style: style })
        });
        console.log(`Saved background style: ${style}`);
    } catch (e) {
        console.error("Failed to save background style:", e);
    }
}

// Helper to save global settings (if needed for defaults)
async function saveGlobalSetting(key, value) {
    try {
        await fetch(`/api/settings/${key}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value: value })
        });
    } catch (e) {
        console.error("Failed to save global setting:", key, e);
    }
}

function setupSettingsPanel() {
    const settingsToggle = document.getElementById('settings-toggle');
    const settingsPanel = document.getElementById('settings-panel');
    const copyUrlBtn = document.getElementById('copy-url-btn');

    if (!settingsToggle || !settingsPanel) return;

    // Toggle panel
    settingsToggle.addEventListener('click', (e) => {
        e.stopPropagation(); // Prevent click from bubbling to document
        const isVisible = settingsPanel.style.display !== 'none';
        settingsPanel.style.display = isVisible ? 'none' : 'block';
    });

    // Close panel when clicking outside of it
    document.addEventListener('click', (e) => {
        // Check if click is outside the settings panel and toggle button
        if (settingsPanel.style.display !== 'none' &&
            !settingsPanel.contains(e.target) &&
            !settingsToggle.contains(e.target)) {
            settingsPanel.style.display = 'none';
        }
    });

    // Prevent panel from closing when clicking inside it
    settingsPanel.addEventListener('click', (e) => {
        e.stopPropagation(); // Prevent click from bubbling to document
    });

    // Sync checkboxes with current config
    document.getElementById('opt-album-art').checked = displayConfig.showAlbumArt;
    document.getElementById('opt-track-info').checked = displayConfig.showTrackInfo;
    document.getElementById('opt-controls').checked = displayConfig.showControls;
    document.getElementById('opt-progress').checked = displayConfig.showProgress;
    document.getElementById('opt-bottom-nav').checked = displayConfig.showBottomNav;
    document.getElementById('opt-colors').checked = displayConfig.useAlbumColors;
    document.getElementById('opt-art-bg').checked = displayConfig.artBackground;
    document.getElementById('opt-soft-art-bg').checked = displayConfig.softAlbumArt;
    document.getElementById('opt-sharp-art-bg').checked = displayConfig.sharpAlbumArt;
    document.getElementById('opt-show-provider').checked = displayConfig.showProvider;

    // Handle checkbox changes
    const checkboxes = ['opt-album-art', 'opt-track-info', 'opt-controls', 'opt-progress', 'opt-bottom-nav', 'opt-colors', 'opt-art-bg', 'opt-soft-art-bg', 'opt-sharp-art-bg', 'opt-show-provider'];

    checkboxes.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', (e) => {
                // Map checkbox ID to config key
                if (id === 'opt-album-art') displayConfig.showAlbumArt = e.target.checked;
                if (id === 'opt-track-info') displayConfig.showTrackInfo = e.target.checked;
                if (id === 'opt-controls') displayConfig.showControls = e.target.checked;
                if (id === 'opt-progress') displayConfig.showProgress = e.target.checked;
                if (id === 'opt-bottom-nav') displayConfig.showBottomNav = e.target.checked;
                if (id === 'opt-colors') displayConfig.useAlbumColors = e.target.checked;
                if (id === 'opt-art-bg') {
                    displayConfig.artBackground = e.target.checked;
                    // Make mutually exclusive with soft and sharp album art
                    if (e.target.checked) {
                        if (displayConfig.softAlbumArt) {
                            displayConfig.softAlbumArt = false;
                            document.getElementById('opt-soft-art-bg').checked = false;
                        }
                        if (displayConfig.sharpAlbumArt) {
                            displayConfig.sharpAlbumArt = false;
                            document.getElementById('opt-sharp-art-bg').checked = false;
                        }
                        // SAVE PER-ALBUM PREFERENCE
                        saveBackgroundStyle('blur');
                    } else {
                        // EXPLICITLY SAVE 'NONE' IF UNCHECKED BY USER
                        saveBackgroundStyle('none');
                    }
                }
                if (id === 'opt-soft-art-bg') {
                    displayConfig.softAlbumArt = e.target.checked;
                    // Make mutually exclusive with blurred and sharp album art
                    if (e.target.checked) {
                        if (displayConfig.artBackground) {
                            displayConfig.artBackground = false;
                            document.getElementById('opt-art-bg').checked = false;
                        }
                        if (displayConfig.sharpAlbumArt) {
                            displayConfig.sharpAlbumArt = false;
                            document.getElementById('opt-sharp-art-bg').checked = false;
                        }
                        // SAVE PER-ALBUM PREFERENCE
                        saveBackgroundStyle('soft');
                    } else {
                        // EXPLICITLY SAVE 'NONE' IF UNCHECKED BY USER
                        saveBackgroundStyle('none');
                    }
                }
                if (id === 'opt-sharp-art-bg') {
                    displayConfig.sharpAlbumArt = e.target.checked;
                    // Make mutually exclusive with blurred and soft album art
                    if (e.target.checked) {
                        if (displayConfig.artBackground) {
                            displayConfig.artBackground = false;
                            document.getElementById('opt-art-bg').checked = false;
                        }
                        if (displayConfig.softAlbumArt) {
                            displayConfig.softAlbumArt = false;
                            document.getElementById('opt-soft-art-bg').checked = false;
                        }
                        // SAVE PER-ALBUM PREFERENCE
                        saveBackgroundStyle('sharp');
                    } else {
                        // EXPLICITLY SAVE 'NONE' IF UNCHECKED BY USER
                        saveBackgroundStyle('none');
                    }
                }
                if (id === 'opt-show-provider') displayConfig.showProvider = e.target.checked;

                applyDisplayConfig();
                applySoftMode();
                applySharpMode();
                updateUrlDisplay();

                // Update browser URL without page reload (enables refresh persistence & direct URL copy)
                history.replaceState(null, '', generateCurrentUrl());

                manualStyleOverride = true; // User manually changed style
            });
        }
    });

    // Copy URL button
    if (copyUrlBtn) {
        copyUrlBtn.addEventListener('click', () => {
            const url = generateCurrentUrl();
            copyToClipboard(url).then(() => {
                copyUrlBtn.textContent = '✓ Copied!';
                setTimeout(() => {
                    copyUrlBtn.textContent = 'Copy Current URL';
                }, 2000);
            }).catch(() => {
                copyUrlBtn.textContent = '✗ Failed';
                setTimeout(() => {
                    copyUrlBtn.textContent = 'Copy Current URL';
                }, 2000);
            });
        });
    }

    updateUrlDisplay();
}

function updateUrlDisplay() {
    const urlDisplay = document.getElementById('url-display');
    if (urlDisplay) {
        urlDisplay.textContent = generateCurrentUrl();
    }
}

function generateCurrentUrl() {
    const base = window.location.origin + window.location.pathname;
    const params = new URLSearchParams();

    if (!displayConfig.showAlbumArt) params.set('showAlbumArt', 'false');
    if (!displayConfig.showTrackInfo) params.set('showTrackInfo', 'false');
    if (!displayConfig.showControls) params.set('showControls', 'false');
    if (!displayConfig.showProgress) params.set('showProgress', 'false');
    if (!displayConfig.showBottomNav) params.set('showBottomNav', 'false');
    if (!displayConfig.showProvider) params.set('showProvider', 'false');
    if (displayConfig.useAlbumColors) params.set('useAlbumColors', 'true');
    // Enforce mutual exclusivity: only add one of artBackground, softAlbumArt, or sharpAlbumArt
    // Priority: Sharp > Soft > Blur
    if (displayConfig.sharpAlbumArt) {
        params.set('sharpAlbumArt', 'true');
    } else if (displayConfig.softAlbumArt) {
        params.set('softAlbumArt', 'true');
    } else if (displayConfig.artBackground) {
        params.set('artBackground', 'true');
    }

    return params.toString() ? `${base}?${params.toString()}` : base;
}

function updateAlbumArt(trackInfo) {
    const albumArt = document.getElementById('album-art');
    const trackHeader = document.getElementById('track-header');

    if (!albumArt || !trackHeader) return;

    if (trackInfo.album_art_url) {
        // Create absolute URL for reliable comparison
        // This handles cases where one is relative (/cover-art) and one is absolute (http://...)
        const targetUrl = new URL(trackInfo.album_art_url, window.location.href).href;

        // SIMPLIFIED CHECK: Just compare the full absolute URLs.
        // If the timestamp or ID changed, the URL will be different.
        // We removed the unreliable endsWith() check which failed with query params.
        if (albumArt.src !== targetUrl) {

            // Check if we are already loading this exact URL to avoid duplicate work
            if (pendingArtUrl !== targetUrl) {
                pendingArtUrl = targetUrl;

                const img = new Image();

                img.onload = () => {
                    // CRITICAL: Check if this is STILL the URL we want to show
                    // If the user skipped to another song while this was loading, pendingArtUrl will be different
                    if (pendingArtUrl === targetUrl) {
                        albumArt.src = targetUrl;

                        // Update background only when image is ready
                        if (displayConfig.artBackground || displayConfig.softAlbumArt || displayConfig.sharpAlbumArt) {
                            updateBackground();
                        }

                        albumArt.style.display = displayConfig.showAlbumArt ? 'block' : 'none';
                        pendingArtUrl = null; // Reset
                    }
                };

                img.onerror = () => {
                    if (pendingArtUrl === targetUrl) pendingArtUrl = null;
                };

                img.src = targetUrl;
            }
        } else {
            // URL matches current, just ensure it's visible
            albumArt.style.display = displayConfig.showAlbumArt ? 'block' : 'none';
        }
    } else {
        albumArt.style.display = 'none';
        pendingArtUrl = null;
    }

    // Show/hide header based on whether we have art or track info
    const hasContent = (trackInfo.album_art_url && displayConfig.showAlbumArt) || displayConfig.showTrackInfo;
    trackHeader.style.display = hasContent ? 'flex' : 'none';

    // Note: updateBackground() is called when src changes (line 569)
    // No need for forced call here since lastTrackInfo is now updated before this function is called
}

function updateTrackInfo(trackInfo) {
    if (!displayConfig.showTrackInfo) return;

    const titleEl = document.getElementById('track-title');
    const artistEl = document.getElementById('track-artist');

    if (titleEl) titleEl.textContent = trackInfo.title || 'Unknown Track';
    if (artistEl) artistEl.textContent = trackInfo.artist || 'Unknown Artist';
}

function updateProgress(trackInfo) {
    if (!displayConfig.showProgress) return;

    const fill = document.getElementById('progress-fill');
    const currentTime = document.getElementById('current-time');
    const totalTime = document.getElementById('total-time');
    const progressContainer = document.getElementById('progress-container');

    // Handle null duration gracefully (Linux)
    if (!trackInfo.duration_ms || trackInfo.position === undefined) {
        if (progressContainer) progressContainer.style.display = 'none';
        return;
    }

    if (progressContainer) progressContainer.style.display = 'block';

    const percent = Math.min(100, (trackInfo.position * 1000 / trackInfo.duration_ms) * 100);
    if (fill) fill.style.width = `${percent}%`;

    if (currentTime) currentTime.textContent = formatTime(trackInfo.position);
    if (totalTime) totalTime.textContent = formatTime(trackInfo.duration_ms / 1000);
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}

function attachControlHandlers() {
    if (!displayConfig.showControls) return;

    const prevBtn = document.getElementById('btn-previous');
    const playPauseBtn = document.getElementById('btn-play-pause');
    const nextBtn = document.getElementById('btn-next');

    if (prevBtn) {
        prevBtn.addEventListener('click', async () => {
            try {
                await fetch('/api/playback/previous', { method: 'POST' });
            } catch (error) {
                console.error('Previous track error:', error);
                showToast('Failed to skip previous', 'error');
            }
        });
    }

    if (playPauseBtn) {
        playPauseBtn.addEventListener('click', async () => {
            try {
                await fetch('/api/playback/play-pause', { method: 'POST' });
                // Force immediate update of track info to reflect new play/pause state
                // This ensures the button icon updates immediately
                setTimeout(async () => {
                    const trackInfo = await getCurrentTrack();
                    if (trackInfo && !trackInfo.error) {
                        updateControlState(trackInfo);
                    }
                }, 200); // Small delay to allow server to process the state change
            } catch (error) {
                console.error('Play/Pause error:', error);
                showToast('Failed to toggle playback', 'error');
            }
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener('click', async () => {
            try {
                await fetch('/api/playback/next', { method: 'POST' });
            } catch (error) {
                console.error('Next track error:', error);
                showToast('Failed to skip next', 'error');
            }
        });
    }

    // UPDATED: New Lyric/Visual Mode Toggle Button
    const visualModeBtn = document.getElementById('btn-lyrics-toggle');
    if (visualModeBtn) {
        visualModeBtn.addEventListener('click', () => {
            if (visualModeActive) {
                exitVisualMode();
            } else {
                enterVisualMode();
            }
        });
    }
}

function updateControlState(trackInfo) {
    if (!displayConfig.showControls) return;

    const prevBtn = document.getElementById('btn-previous');
    const playPauseBtn = document.getElementById('btn-play-pause');
    const nextBtn = document.getElementById('btn-next');

    // Enable controls for Spotify or Spotify Hybrid (Windows Media enriched with Spotify)
    const canControl = trackInfo.source === 'spotify' || trackInfo.source === 'spotify_hybrid';

    if (prevBtn) prevBtn.disabled = !canControl;
    if (nextBtn) nextBtn.disabled = !canControl;
    if (playPauseBtn) {
        playPauseBtn.disabled = !canControl;
        // Update play/pause icon via CSS classes
        // Ensure we always update the state, even if it hasn't changed
        // Default to paused if is_playing is undefined or false
        const isPlaying = trackInfo.is_playing === true;

        if (isPlaying) {
            // Remove paused class and add playing class
            playPauseBtn.classList.remove('paused');
            playPauseBtn.classList.add('playing');
        } else {
            // Remove playing class and add paused class
            playPauseBtn.classList.remove('playing');
            playPauseBtn.classList.add('paused');
        }
    }
}

// Provider Display Names Mapping
const providerDisplayNames = {
    "lrclib": "LRCLib",
    "spotify": "Spotify",
    "netease": "NetEase",
    "qq": "QQ",
    "musicxmatch": "Musixmatch"
};

function updateProviderDisplay(providerName) {
    if (!displayConfig.showProvider) return;

    const providerInfo = document.getElementById('provider-info');
    const providerNameEl = document.getElementById('provider-name');

    if (providerInfo && providerNameEl) {
        const displayName = providerDisplayNames[providerName] ||
            providerName.charAt(0).toUpperCase() + providerName.slice(1);
        providerNameEl.textContent = displayName;
        providerInfo.classList.remove('hidden');
    }
}

async function showProviderModal() {
    try {
        // Load lyrics providers
        const response = await fetch('/api/providers/available');
        const data = await response.json();

        if (data.error) {
            console.error('Cannot show providers:', data.error);
            return;
        }

        const modal = document.getElementById('provider-modal');
        const providerList = document.getElementById('provider-list');

        // Clear existing content
        providerList.innerHTML = '';

        // Build provider list
        data.providers.forEach(provider => {
            const providerItem = document.createElement('div');
            providerItem.className = 'provider-item';
            if (provider.is_current) {
                providerItem.classList.add('current-provider');
            }

            const displayName = providerDisplayNames[provider.name] ||
                provider.name.charAt(0).toUpperCase() + provider.name.slice(1);

            const providerInfo = `
                <div class="provider-item-content">
                    <div class="provider-item-header">
                        <span class="provider-item-name">${displayName}</span>
                        ${provider.is_current ? '<span class="current-badge">Current</span>' : ''}
                        ${provider.cached ? '<span class="cached-badge">Cached</span>' : ''}
                    </div>
                    <div class="provider-item-meta">
                        Priority: ${provider.priority}
                    </div>
                </div>
                <button class="provider-select-btn" data-provider="${provider.name}">
                    ${provider.is_current ? 'Selected' : 'Use This'}
                </button>
            `;

            providerItem.innerHTML = providerInfo;
            providerList.appendChild(providerItem);
        });

        // ALWAYS load album art, not just if tab is active
        // This ensures desktop view (which shows both) is populated
        loadAlbumArtTab(); // Remove await so it runs in parallel with UI showing

        // Show modal (modal already declared on line 738)
        modal.classList.remove('hidden');

    } catch (error) {
        console.error('Error loading providers:', error);
    }
}

async function loadAlbumArtTab() {
    try {
        const response = await fetch('/api/album-art/options');
        const data = await response.json();

        if (data.error) {
            // No album art database entry - that's okay, just show empty state
            const grid = document.getElementById('album-art-grid');
            if (grid) {
                grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: rgba(255, 255, 255, 0.5); padding: 40px;">No album art options available yet. They will be populated in the background.</div>';
            }
            return;
        }

        const grid = document.getElementById('album-art-grid');
        if (!grid) return;

        grid.innerHTML = '';

        // REMOVED: JS Injection of Style Controls (Now Static in HTML)
        
        // UPDATE: Correctly target buttons (document scope) and ONLY update visual state
        // Event listeners are now handled in DOMContentLoaded to prevent duplication
        const styleBtns = document.querySelectorAll('.style-btn');
        const currentStyle = getCurrentBackgroundStyle();

        styleBtns.forEach(btn => {
            // Reset state
            btn.classList.remove('active');
            btn.style.background = '';
            btn.style.borderColor = '';

            // Highlight current style
            if (btn.dataset.style === currentStyle) {
                btn.classList.add('active');
                // Keep inline styles for specific highlight if needed, or rely on CSS .active class
                // adhering to the previous logic:
                btn.style.background = 'rgba(29, 185, 84, 0.3)';
                btn.style.borderColor = 'rgba(29, 185, 84, 0.6)';
            }

            // Hover effects
            btn.addEventListener('mouseenter', () => {
                if (btn.dataset.style !== currentStyle) {
                    btn.style.background = 'rgba(255,255,255,0.15)';
                }
            });
            btn.addEventListener('mouseleave', () => {
                if (btn.dataset.style !== currentStyle) {
                    btn.style.background = 'rgba(255,255,255,0.1)';
                }
            });

            btn.addEventListener('click', async (e) => {
                const style = e.target.dataset.style;

                // Apply locally immediately
                applyBackgroundStyle(style);
                manualStyleOverride = true; // User manually changed it

                // Update UI - reset all buttons, highlight selected
                styleBtns.forEach(b => {
                    b.style.background = 'rgba(255,255,255,0.1)';
                    b.style.borderColor = 'rgba(255,255,255,0.2)';
                });
                e.target.style.background = 'rgba(29, 185, 84, 0.3)';
                e.target.style.borderColor = 'rgba(29, 185, 84, 0.6)';

                // Save to server
                try {
                    const response = await fetch('/api/album-art/background-style', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ style: style })
                    });
                    const res = await response.json();
                    if (res.status === 'success') {
                        showToast(`Saved preference: ${style}`);
                    } else {
                        showToast(`Error: ${res.error || 'Failed to save'}`, 'error');
                    }
                } catch (err) {
                    console.error('Error saving style:', err);
                    showToast('Failed to save style preference', 'error');
                }
            });
        });

        // Build art grid
        data.options.forEach(option => {
            const card = document.createElement('div');
            card.className = 'art-card';
            if (option.is_preferred) {
                card.classList.add('selected');
            }
            card.dataset.provider = option.provider;

            card.innerHTML = `
                <img src="${option.image_url}" alt="${option.provider}" class="art-card-image" loading="lazy" onerror="this.parentElement.classList.add('loading')">
                <div class="art-card-overlay">
                    <div class="art-card-provider">${option.provider}</div>
                    <div class="art-card-resolution">${option.resolution}</div>
                </div>
                ${option.is_preferred ? '<div class="art-card-badge">Selected</div>' : ''}
            `;

            // Add click handler
            card.addEventListener('click', () => selectAlbumArt(option.provider));

            grid.appendChild(card);
        });

        // NEW: Fetch and Append Artist Images to the SAME grid
        if (lastTrackInfo && lastTrackInfo.artist_id) {
            // Add a header/separator with correct grid positioning
            //const separator = document.createElement('div');
            //separator.className = 'artist-images-header';
            //separator.textContent = 'Artist Images';
            //grid.appendChild(separator);

            // Fetch images (reuse existing function logic or global variable if already fetched)
            // We'll use the global artistImages array if populated, or fetch if empty
            let images = artistImages;
            if (!images || images.length === 0) {
                images = await fetchArtistImages(lastTrackInfo.artist_id);
            }

            if (images && images.length > 0) {
                images.forEach((url, index) => {
                    const card = document.createElement('div');
                    card.className = 'art-card';
                    card.innerHTML = `
                        <img src="${url}" class="art-card-image" loading="lazy">
                        <div class="art-card-overlay">
                            <div class="art-card-provider">Artist Image</div>
                        </div>
                    `;
                    // Optional: Click to view/set as background logic could go here
                    grid.appendChild(card);
                });
            } else {
                const msg = document.createElement('div');
                msg.style.gridColumn = '1 / -1';
                msg.style.opacity = '0.5';
                msg.style.fontSize = '0.85rem';
                msg.textContent = 'No artist images found';
                grid.appendChild(msg);
            }
        }

    } catch (error) {
        console.error('Error loading album art options:', error);
        const grid = document.getElementById('album-art-grid');
        if (grid) {
            grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: rgba(255, 255, 255, 0.5); padding: 40px;">Error loading album art options.</div>';
        }
    }
}

// NEW FUNCTION: Load Artist Images Tab
function loadArtistImagesTab() {
    const grid = document.getElementById('artist-images-grid');
    if (!grid) return;
    
    grid.innerHTML = '';
    
    if (!artistImages || artistImages.length === 0) {
        grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: rgba(255, 255, 255, 0.5); padding: 40px;">No artist images available.</div>';
        return;
    }
    
    artistImages.forEach((url, index) => {
        const card = document.createElement('div');
        card.className = 'art-card';
        // Non-interactive for now, just visual
        
        card.innerHTML = `
            <img src="${url}" class="art-card-image" loading="lazy">
            <div class="art-card-overlay">
                <div class="art-card-provider">Image ${index + 1}</div>
            </div>
        `;
        
        grid.appendChild(card);
    });
}

async function selectAlbumArt(providerName) {
    try {
        const response = await fetch('/api/album-art/preference', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: providerName })
        });

        const result = await response.json();

        if (result.status === 'success') {
            // Update UI to show selected state
            const cards = document.querySelectorAll('.art-card');
            cards.forEach(card => {
                card.classList.remove('selected');
                const badge = card.querySelector('.art-card-badge');
                if (badge) badge.remove();
            });

            const selectedCard = document.querySelector(`.art-card[data-provider="${providerName}"]`);
            if (selectedCard) {
                selectedCard.classList.add('selected');
                // Add badge if it doesn't exist
                if (!selectedCard.querySelector('.art-card-badge')) {
                    selectedCard.insertAdjacentHTML('afterbegin', '<div class="art-card-badge">Selected</div>');
                }
            }

            // Force immediate art refresh without full page reload
            const albumArt = document.getElementById('album-art');
            if (albumArt && result.cache_bust) {
                // Update album art with cache buster
                const currentSrc = albumArt.src;
                const baseUrl = currentSrc.split('?')[0];
                albumArt.src = `${baseUrl}?t=${result.cache_bust}`;
                
                // Also update background if using art background
                if (displayConfig.artBackground || displayConfig.softAlbumArt || displayConfig.sharpAlbumArt) {
                    updateBackground();
                }
            }

            showToast(`Switched to ${providerName} album art`);
            
            // Close modal after brief delay
            setTimeout(() => {
                hideProviderModal();
            }, 1000);
        } else {
            showToast(`Error: ${result.error || result.message}`, 'error');
        }
    } catch (error) {
        console.error('Error selecting album art:', error);
        showToast('Failed to switch album art', 'error');
    }
}

function hideProviderModal() {
    const modal = document.getElementById('provider-modal');
    if (modal) {
        modal.classList.add('hidden');
    }
}

async function selectProvider(providerName) {
    try {
        const response = await fetch('/api/providers/preference', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: providerName })
        });

        const result = await response.json();

        if (result.status === 'success') {
            // Update UI immediately with new lyrics if provided
            if (result.lyrics) {
                setLyricsInDom(result.lyrics);
            }
            updateProviderDisplay(result.provider);
            hideProviderModal();

            // Show brief success message
            const displayName = providerDisplayNames[result.provider] || result.provider;
            showToast(`Switched to ${displayName}`);
        } else {
            showToast(`Error: ${result.message}`, 'error');
        }
    } catch (error) {
        console.error('Error selecting provider:', error);
        showToast('Failed to switch provider', 'error');
    }
}

async function clearProviderPreference() {
    try {
        const response = await fetch('/api/providers/preference', {
            method: 'DELETE'
        });

        const result = await response.json();

        if (result.status === 'success') {
            hideProviderModal();
            showToast('Reset to automatic provider selection');
        } else {
            showToast('Failed to reset preference', 'error');
        }
    } catch (error) {
        console.error('Error clearing preference:', error);
        showToast('Failed to reset preference', 'error');
    }
}

async function deleteCachedLyrics() {
    // Confirm before deleting
    if (!confirm('Delete all cached lyrics for this song?\n\nThis will remove lyrics from all providers and re-fetch them fresh.')) {
        return;
    }

    try {
        const response = await fetch('/api/lyrics/delete', {
            method: 'DELETE'
        });

        const result = await response.json();

        if (result.status === 'success') {
            hideProviderModal();
            showToast('Cached lyrics deleted. Re-fetching...');
            // Force a refresh of lyrics display
            lastLyrics = null;
        } else {
            showToast(result.message || 'Failed to delete lyrics', 'error');
        }
    } catch (error) {
        console.error('Error deleting cached lyrics:', error);
        showToast('Failed to delete cached lyrics', 'error');
    }
}

function showToast(message, type = 'success') {
    // Simple toast notification
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('show');
    }, 10);

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// ========== VISUAL MODE FUNCTIONS ==========

/**
 * Fetch artist images from Spotify API
 * @param {string} artistId - Spotify artist ID
 * @returns {Promise<Array<string>>} Array of image URLs
 */
async function fetchArtistImages(artistId) {
    if (!artistId) {
        console.warn('No artist ID provided for image fetch');
        return [];
    }

    try {
        const response = await fetch(`/api/artist/images?artist_id=${encodeURIComponent(artistId)}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        if (data.images && data.images.length > 0) {
            artistImages = data.images;
            console.log(`Loaded ${artistImages.length} artist images`);
            return artistImages;
        }
    } catch (error) {
        console.error('Error fetching artist images:', error);
    }
    return [];
}

/**
 * Check if we should enter visual mode based on lyrics availability
 * @param {boolean} lyricsAvailable - Whether lyrics are available
 * @param {boolean} isInstrumental - Whether the track is instrumental
 */
function checkForVisualMode(data, trackId) {
    // Don't check if visual mode is disabled
    if (!visualModeConfig.enabled) return;
    
    // Use flags from the backend response
    const lyricsAvailable = data && data.has_lyrics;
    const isInstrumental = data && data.is_instrumental;

    // Condition to enter visual mode: No lyrics OR Instrumental
    const shouldEnterVisualMode = !lyricsAvailable || isInstrumental;

    if (shouldEnterVisualMode) {
        // If already active, nothing to do
        if (visualModeActive) return;

        // If timer is already running, let it run (DON'T RESET IT)
        // This fixes the issue where frequent updates kept resetting the timer
        if (visualModeTimer) return;

        // Start timer to enter visual mode
        // Fast entry for confirmed instrumental (2s), otherwise configured delay
        const delayMs = isInstrumental ? 6000 : (visualModeConfig.delaySeconds * 1000);

        console.log(`[Visual Mode] Starting timer: ${delayMs}ms for ${trackId}`);

        // Generate unique ID for this specific timer instance
        const currentTimerId = Date.now();
        visualModeTimerId = currentTimerId;

        visualModeTimer = setTimeout(() => {
            visualModeTimer = null; // Clear timer reference

            // Verify THIS specific timer is still valid matches the global ID
            if (visualModeTimerId !== currentTimerId) {
                console.log('[Visual Mode] Timer invalidated (new timer started), aborting');
                return;
            }

            if (lastTrackInfo) {
                // Re-verify track ID match
                let currentId;
                if (lastTrackInfo.track_id && lastTrackInfo.track_id.trim()) {
                    currentId = lastTrackInfo.track_id.trim();
                } else {
                    const artist = (lastTrackInfo.artist || '').trim();
                    const title = (lastTrackInfo.title || '').trim();
                    if (artist && title) {
                        currentId = `${artist} - ${title}`;
                    } else if (title) {
                        currentId = title;
                    } else if (artist) {
                        currentId = artist;
                    } else {
                        currentId = 'unknown';
                    }
                }

                if (currentId === trackId) {
                    console.log('[Visual Mode] Activation conditions met, entering...');
                    enterVisualMode();
                } else {
                    console.log(`[Visual Mode] Track changed (${trackId} vs ${currentId}), aborting`);
                }
            }
        }, delayMs);
    } else {
        // Condition NOT met (we have lyrics and it's not instrumental)

        // If active, exit
        if (visualModeActive) {
            exitVisualMode();
        }

        // If timer is running, cancel it
        if (visualModeTimer) {
            console.log('[Visual Mode] Conditions no longer met, cancelling timer');
            clearTimeout(visualModeTimer);
            visualModeTimer = null;
            visualModeTimerId = null; // Clear ID
        }
    }
}

function enterVisualMode() {
    if (visualModeActive) return;

    console.log('Entering Visual Mode');
    visualModeActive = true;

    // Hide lyrics container with fade
    const lyricsContainer = document.querySelector('.lyrics-container') || document.getElementById('lyrics');
    if (lyricsContainer) {
        lyricsContainer.classList.add('visual-mode-hidden');
    }

    // SAVE current state before changing
    savedBackgroundState = getCurrentBackgroundStyle();

    // Auto-switch to sharp mode if configured
    if (visualModeConfig.autoSharp) {
        // Only apply if not already sharp to avoid unnecessary updates
        if (savedBackgroundState !== 'sharp') {
            applyBackgroundStyle('sharp');
        }
    }

    // Start slideshow if we have artist images
    if (artistImages.length > 0) {
        startSlideshow();
    }
}

/**
 * Exit visual mode - show lyrics again
 */
function exitVisualMode() {
    if (!visualModeActive) return;

    console.log('Exiting Visual Mode');
    visualModeActive = false;

    // Stop slideshow
    stopSlideshow();

    // Show lyrics container
    const lyricsContainer = document.querySelector('.lyrics-container') || document.getElementById('lyrics');
    if (lyricsContainer) {
        lyricsContainer.classList.remove('visual-mode-hidden');
    }

    // RESTORE previous background style
    if (savedBackgroundState) {
        applyBackgroundStyle(savedBackgroundState);
        savedBackgroundState = null;
    }
}

/**
 * Get current background style
 * @returns {string} Current background style ('sharp', 'soft', 'blur', or 'none')
 */
function getCurrentBackgroundStyle() {
    if (displayConfig.sharpAlbumArt) return 'sharp';
    if (displayConfig.softAlbumArt) return 'soft';
    if (displayConfig.artBackground) return 'blur';
    return 'none';
}

/**
 * Apply background style programmatically
 * @param {string} style - Style to apply ('sharp', 'soft', 'blur', or 'none')
 */
function applyBackgroundStyle(style) {
    // Reset all styles
    displayConfig.sharpAlbumArt = false;
    displayConfig.softAlbumArt = false;
    displayConfig.artBackground = false;

    // Apply selected style
    if (style === 'sharp') {
        displayConfig.sharpAlbumArt = true;
        applySharpMode();
    } else if (style === 'soft') {
        displayConfig.softAlbumArt = true;
        applySoftMode();
    } else if (style === 'blur') {
        displayConfig.artBackground = true;
    }

    updateBackground();
}

/**
 * Start slideshow - cycle through artist images and album art
 */
function startSlideshow() {
    if (slideshowInterval) {
        clearInterval(slideshowInterval);
    }
    
    // FIX: Check if we have EITHER artist images OR album art
    const hasAlbumArt = lastTrackInfo && lastTrackInfo.album_art_url;
    const totalSlides = artistImages.length + (hasAlbumArt ? 1 : 0);
    
    if (totalSlides === 0) {
        console.log("Slideshow: No images available to show (no artist images, no album art).");
        return;
    }

    currentSlideIndex = 0;
    
    // Show first image immediately
    showSlide(currentSlideIndex);
    
    // Then cycle through images
    const intervalMs = visualModeConfig.slideshowIntervalSeconds * 1000;
    slideshowInterval = setInterval(() => {
        // Re-calculate total slides in case art loaded/changed
        const currentTotal = artistImages.length + (lastTrackInfo && lastTrackInfo.album_art_url ? 1 : 0);
        if (currentTotal > 0) {
            currentSlideIndex = (currentSlideIndex + 1) % currentTotal;
            showSlide(currentSlideIndex);
        }
    }, intervalMs);
}

/**
 * Stop slideshow
 */
function stopSlideshow() {
    if (slideshowInterval) {
        clearInterval(slideshowInterval);
        slideshowInterval = null;
    }
    
    // Clear slideshow images
    const bgContainer = document.getElementById('background-layer');
    if (bgContainer) {
        const slideshowImages = bgContainer.querySelectorAll('.slideshow-image');
        slideshowImages.forEach(img => img.remove());
    }
}

/**
 * Show a specific slide in the slideshow
 * @param {number} index - Index of the slide to show
 */
function showSlide(index) {
    const bgContainer = document.getElementById('background-layer');
    if (!bgContainer) return;
    
    let imageUrl;
    
    // Last index is album art, rest are artist images
    if (index >= artistImages.length) {
        // Show album art
        if (lastTrackInfo && lastTrackInfo.album_art_url) {
            imageUrl = lastTrackInfo.album_art_url;
        } else {
            return; // No album art available
        }
    } else {
        // Show artist image
        imageUrl = artistImages[index];
    }
    
    // Create new image element for crossfade
    const newImg = document.createElement('div');
    newImg.className = 'slideshow-image';
    newImg.style.backgroundImage = `url(${imageUrl})`;
    
    // Add Ken Burns animation class
    newImg.classList.add('ken-burns-effect');
    
    bgContainer.appendChild(newImg);
    
    // Fade in new image
    setTimeout(() => {
        newImg.classList.add('active');
    }, 50);
    
    // DOM CLEANUP: Remove old images after transition
    setTimeout(() => {
        const oldImages = bgContainer.querySelectorAll('.slideshow-image:not(.active)');
        oldImages.forEach(img => {
            // Safety check: ensure we don't remove the image we just added
            if (img !== newImg) {
                img.remove();
            }
        });
    }, 2000); // Match CSS transition duration
}

function setupProviderUI() {
    // Provider badge click handler
    const providerBadge = document.getElementById('provider-badge');
    if (providerBadge) {
        providerBadge.addEventListener('click', showProviderModal);
    }

    // Modal close handlers
    const modalClose = document.getElementById('provider-modal-close');
    if (modalClose) {
        modalClose.addEventListener('click', hideProviderModal);
    }

    // Tab switching
    const tabs = document.querySelectorAll('.provider-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const tabName = tab.dataset.tab;

            // Update tab active state
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Update content visibility
            const contents = document.querySelectorAll('.provider-tab-content');
            contents.forEach(content => {
                content.classList.remove('active');
            });

            const activeContent = document.getElementById(`provider-tab-content-${tabName}`);
            if (activeContent) {
                activeContent.classList.add('active');
            }

            // Load album art tab if switching to it
            if (tabName === 'album-art') {
                loadAlbumArtTab();
            } else if (tabName === 'artist-images') {
                // Load artist images if switching to that tab
                loadArtistImagesTab();
            }
        });
    });

    const modal = document.getElementById('provider-modal');
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                hideProviderModal();
            }
        });
    }

    // Clear preference button
    const clearBtn = document.getElementById('provider-clear-preference');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearProviderPreference);
    }

    // Delete cached lyrics button
    const deleteBtn = document.getElementById('lyrics-delete-cache');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', deleteCachedLyrics);
    }

    // Provider selection (event delegation)
    const providerList = document.getElementById('provider-list');
    if (providerList) {
        providerList.addEventListener('click', (e) => {
            if (e.target.classList.contains('provider-select-btn')) {
                const providerName = e.target.getAttribute('data-provider');
                selectProvider(providerName);
            }
        });
    }
}

/**
 * Fetch random images from the global database for idle slideshow
 */
async function fetchRandomSlideshowImages() {
    try {
        const response = await fetch('/api/slideshow/random-images?limit=50');
        if (!response.ok) throw new Error('Failed to fetch random images');
        
        const data = await response.json();
        if (data.images && data.images.length > 0) {
            console.log(`Loaded ${data.images.length} random images for global slideshow`);
            return data.images;
        }
    } catch (error) {
        console.error('Error fetching random slideshow images:', error);
    }
    return [];
}

async function updateLoop() {
    let lastTrackId = null;
    let isIdleState = false; // Track idle state to prevent repeated fetches

    while (true) {
        const now = Date.now();
        const timeSinceLastCheck = now - lastCheckTime;

        // Ensure minimum time between checks
        if (timeSinceLastCheck < updateInterval) {
            await sleep(updateInterval - timeSinceLastCheck);
            continue;
        }

        // Get track info first
        const trackInfo = await getCurrentTrack();

        // Only get lyrics if we have track info
        if (trackInfo && !trackInfo.error) {
            isIdleState = false; // Reset idle state
            
            // ROBUST TRACK ID GENERATION
            // 1. Prefer track_id if available (Spotify provides this)
            // 2. Fall back to "Artist - Title" for Windows Media and other sources
            // 3. Handle edge cases where artist/title might be missing
            let currentTrackId;
            if (trackInfo.track_id && trackInfo.track_id.trim()) {
                // Use the backend-provided track_id (most reliable for Spotify)
                currentTrackId = trackInfo.track_id.trim();
            } else {
                // Fallback: construct from artist and title
                const artist = (trackInfo.artist || '').trim();
                const title = (trackInfo.title || '').trim();
                if (artist && title) {
                    currentTrackId = `${artist} - ${title}`;
                } else if (title) {
                    currentTrackId = title; // At least use title if available
                } else if (artist) {
                    currentTrackId = artist; // Or artist if that's all we have
                } else {
                    currentTrackId = 'unknown'; // Last resort
                }
            }

            const trackChanged = lastTrackId !== currentTrackId;

            // FIX: Check like status on first load even if track hasn't "changed" (e.g. refresh)
            if (trackChanged || (currentTrackId && !lastTrackId)) {
                // Track changed - fetch artist images and reset visual mode
                lastTrackId = currentTrackId;
                visualModeActive = false; // Reset visual mode state
                manualStyleOverride = false; // Reset manual override on track change (allow saved style to apply)
                if (visualModeTimer) {
                    clearTimeout(visualModeTimer);
                    visualModeTimer = null;
                }
                stopSlideshow();

                // Fetch artist images for potential visual mode
                if (trackInfo.artist_id) {
                    await fetchArtistImages(trackInfo.artist_id);
                }

                // Check like status for new track (Moved inside trackChanged)
                if (trackInfo.id) {
                    checkLikedStatus(trackInfo.id);
                }
                
                // Reset style buttons in modal (Moved inside trackChanged)
                updateStyleButtonsInModal(trackInfo.background_style || 'blur');

                // FIX: Refresh queue if drawer is open
                if (queueDrawerOpen) {
                    console.log("Track changed, refreshing queue...");
                    fetchAndRenderQueue();
                }
            }

            // Update lastTrackInfo FIRST so updateBackground() has current data
            // This fixes the stale data issue without needing forced updateBackground() calls
            lastTrackInfo = trackInfo;

            // Phase 2: Apply saved background style if available (and not manually overridden)
            if (trackInfo.background_style && !manualStyleOverride && !visualModeActive) {
                const currentStyle = getCurrentBackgroundStyle();
                if (currentStyle !== trackInfo.background_style) {
                    console.log(`Applying saved background style: ${trackInfo.background_style}`);
                    applyBackgroundStyle(trackInfo.background_style);
                }
            }

            // Update all UI components
            updateAlbumArt(trackInfo);
            updateTrackInfo(trackInfo);
            updateProgress(trackInfo);
            updateControlState(trackInfo);

            // Update lyrics
            const data = await getLyrics();

            // Consolidate instrumental flag (prefer trackInfo as it comes from a fresher source or cache)
            const isInstrumental = trackInfo.is_instrumental || (data && data.is_instrumental);

            if (data) {
                // 1. Update DOM
                // If lyrics exist, show them. 
                // If not, pass the WHOLE data object (which contains data.msg = "Instrumental") 
                // so setLyricsInDom can display the status message properly.
                const lyricsToDisplay = (data.lyrics && data.lyrics.length > 0) ? data.lyrics : data;
                setLyricsInDom(lyricsToDisplay);

                // 2. Check for Visual Mode using the backend flags
                // Pass consolidated flags
                data.is_instrumental = isInstrumental;
                checkForVisualMode(data, currentTrackId);
            } else {
                // Fallback if no data (e.g. API error)
                // Pass a dummy object saying no lyrics
                checkForVisualMode({ has_lyrics: false, is_instrumental: isInstrumental }, currentTrackId);
            }
        } else {
            // No track playing - handle global slideshow
            if (visualModeConfig.slideshowEnabled) {
                // If we just entered idle state, fetch fresh random images
                if (!isIdleState) {
                    isIdleState = true;
                    console.log("Player is idle, initializing global dashboard slideshow...");
                    
                    // Fetch random images from the entire DB
                    const randomImages = await fetchRandomSlideshowImages();
                    
                    if (randomImages.length > 0) {
                        artistImages = randomImages; // Replace current artist images with global mix
                        
                        // Start slideshow immediately
                        if (!slideshowInterval) {
                            startSlideshow();
                        } else {
                            // Restart to pick up new images
                            stopSlideshow();
                            startSlideshow();
                        }
                    }
                }
                
                // Ensure slideshow is running
                if (!slideshowInterval && artistImages.length > 0) {
                    startSlideshow();
                }
            } else if (!visualModeConfig.slideshowEnabled) {
                stopSlideshow();
            }
        }

        lastCheckTime = Date.now();
        await sleep(updateInterval);
    }
}

async function main() {
    // Initialize display configuration
    initializeDisplay();

    // Get configuration from server
    await getConfig();

    // Set initial background
    document.body.style.background = `linear-gradient(135deg, ${currentColors[0]} 0%, ${currentColors[1]} 100%)`;

    // Attach control handlers
    attachControlHandlers();

    // NEW: Setup Queue Interactions (Click outside, Swipe)
    setupQueueInteractions();

    // Setup provider UI
    setupProviderUI();

    // Start the update loop
    updateLoop();
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Start the main app loop
    main();
    
    // UI Event Listeners
    document.getElementById('btn-queue')?.addEventListener('click', toggleQueueDrawer);
    document.getElementById('queue-close')?.addEventListener('click', toggleQueueDrawer);
    document.getElementById('btn-like')?.addEventListener('click', toggleLike);
    
    // Background Style Buttons
    document.querySelectorAll('.style-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const style = e.target.dataset.style;
            saveBackgroundStyle(style);
            
            // Update active state immediately
            document.querySelectorAll('.style-btn').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
        });
    });

    // Touch/Swipe Controls
    setupTouchControls();
});

// --- QUEUE FUNCTIONS ---

// NEW: Queue Interactions
function setupQueueInteractions() {
    // 1. Click Outside to Close
    // Create backdrop if it doesn't exist
    let backdrop = document.querySelector('.queue-backdrop');
    if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.className = 'queue-backdrop';
        document.body.appendChild(backdrop);
        
        // TO DISABLE CLICK-OUTSIDE: Comment out these 3 lines below
        backdrop.addEventListener('click', () => {
            if (queueDrawerOpen) toggleQueueDrawer();
        });
    }

    // REMOVED: Duplicate swipe logic. 
    // Swipe handling is now centralized in setupTouchControls() -> handleSwipe()
}

// UPDATE: Toggle Queue to handle Backdrop
async function toggleQueueDrawer() {
    const drawer = document.getElementById('queue-drawer');
    const backdrop = document.querySelector('.queue-backdrop');
    
    queueDrawerOpen = !queueDrawerOpen;
    
    if (queueDrawerOpen) {
        drawer.classList.add('open');
        // Ensure backdrop is visible and clickable
        if (backdrop) {
            backdrop.classList.add('visible');
            backdrop.style.pointerEvents = 'auto'; // Force clickable
        }
        await fetchAndRenderQueue();
    } else {
        drawer.classList.remove('open');
        if (backdrop) {
            backdrop.classList.remove('visible');
            backdrop.style.pointerEvents = 'none'; // Pass through clicks when hidden
        }
    }
}

async function fetchAndRenderQueue() {
    try {
        const response = await fetch('/api/playback/queue');
        if (!response.ok) return;
        
        const data = await response.json();
        const list = document.getElementById('queue-list');
        list.innerHTML = '';
        
        if (data.queue && data.queue.length > 0) {
            data.queue.forEach(track => {
                const item = document.createElement('div');
                item.className = 'queue-item';
                
                // Use placeholder if no art
                const artUrl = track.album.images[2]?.url || track.album.images[0]?.url || 'resources/images/icon.png';
                
                item.innerHTML = `
                    <img src="${artUrl}" class="queue-art" alt="Art">
                    <div class="queue-info">
                        <div class="queue-title">${track.name}</div>
                        <div class="queue-artist">${track.artists[0].name}</div>
                    </div>
                `;
                list.appendChild(item);
            });
        } else {
            list.innerHTML = '<div style="text-align:center; padding:20px; color:rgba(255,255,255,0.5)">Queue is empty</div>';
        }
    } catch (e) {
        console.error("Queue fetch failed", e);
    }
}

// --- LIKE BUTTON FUNCTIONS ---

async function checkLikedStatus(trackId) {
    if (!trackId) return;
    try {
        const response = await fetch(`/api/playback/liked?track_id=${trackId}`);
        const data = await response.json();
        
        // FIX: Ensure we are still playing the same track
        if (lastTrackInfo && lastTrackInfo.id === trackId) {
            isLiked = data.liked;
            updateLikeButton();
        }
    } catch (e) { console.error(e); }
}

function updateLikeButton() {
    const btn = document.getElementById('btn-like');
    if (!btn) return;
    
    if (isLiked) {
        btn.innerHTML = '❤️'; // Filled heart
        btn.classList.add('liked');
    } else {
        btn.innerHTML = '♡'; // Outline heart
        btn.classList.remove('liked');
    }
}

async function toggleLike() {
    if (!lastTrackInfo || !lastTrackInfo.id) return;
    
    // Optimistic update
    isLiked = !isLiked;
    updateLikeButton();
    
    try {
        await fetch('/api/playback/liked', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                track_id: lastTrackInfo.id,
                action: isLiked ? 'like' : 'unlike'
            })
        });
    } catch (e) {
        // Revert on failure
        isLiked = !isLiked;
        updateLikeButton();
        showToast("Action failed", "error");
    }
}

// --- VISUAL PREFERENCE FUNCTIONS ---

async function saveBackgroundStyle(style) {
    if (!lastTrackInfo) return;
    
    try {
        // Apply immediately
        manualStyleOverride = true; // Prevent auto-revert
        applyBackgroundStyle(style);
        
        // Save to backend
        const response = await fetch('/api/album-art/background-style', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ style: style })
        });
        
        if (response.ok) {
            showToast(`Saved ${style} style for this album`);
            // Update locally to avoid need for refresh
            lastTrackInfo.background_style = style;
        }
    } catch (e) {
        showToast("Failed to save preference", "error");
    }
}

// Update the Modal UI to show current selection
function updateStyleButtonsInModal(currentStyle) {
    document.querySelectorAll('.style-btn').forEach(btn => {
        if (btn.dataset.style === currentStyle) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
}

// --- TOUCH CONTROLS ---

function setupTouchControls() {
    let touchStartX = 0;
    let touchStartY = 0;
    
    document.addEventListener('touchstart', e => {
        touchStartX = e.changedTouches[0].screenX;
        touchStartY = e.changedTouches[0].screenY;
    }, {passive: true});
    
    document.addEventListener('touchend', e => {
        const touchEndX = e.changedTouches[0].screenX;
        const touchEndY = e.changedTouches[0].screenY;
        
        handleSwipe(touchStartX, touchStartY, touchEndX, touchEndY);
    }, {passive: true});
}

function handleSwipe(startX, startY, endX, endY) {
    const minSwipeDistance = 50;
    const maxVerticalVariance = 70; // Ignore if scrolled up/down too much
    
    // EDGE GUARD: Ignore swipes that start at the very right edge 
    // to prevent conflict with Queue Drawer opening
    const screenWidth = window.innerWidth;
    // Increased edge detection zone to 60px for reliability
    const isRightEdge = startX > (screenWidth - 60); 
    
    if (isRightEdge && !queueDrawerOpen) {
        // Check for leftward swipe (opening queue)
        if ((startX - endX) > minSwipeDistance) {
            toggleQueueDrawer();
            return; // Stop further processing
        }
    }

    /* 
    // DISABLE PLAYBACK SWIPE CONTROLS
    const diffX = endX - startX;
    const diffY = endY - startY;
    
    // Check if it's a horizontal swipe
    if (Math.abs(diffX) > minSwipeDistance && Math.abs(diffY) < maxVerticalVariance) {
        if (diffX > 0) {
            // Swipe Right -> Previous
            fetch('/api/playback/previous', { method: 'POST' });
        } else {
            // Swipe Left -> Next
            fetch('/api/playback/next', { method: 'POST' });
        }
    }
    */
}
