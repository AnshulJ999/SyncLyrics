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

        // Update provider info (NEW)
        if (data.provider) {
            updateProviderDisplay(data.provider);
        }

        return data.lyrics || data;
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
                    }
                }
                if (id === 'opt-show-provider') displayConfig.showProvider = e.target.checked;

                applyDisplayConfig();
                applySoftMode();
                applySharpMode();
                updateUrlDisplay();
                
                // Update browser URL without page reload (enables refresh persistence & direct URL copy)
                history.replaceState(null, '', generateCurrentUrl());
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
        
        // Check if src is actually different (using endsWith to handle relative/absolute mismatch)
        if (albumArt.src !== targetUrl && !albumArt.src.endsWith(trackInfo.album_art_url)) {
            
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
            }
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener('click', async () => {
            try {
                await fetch('/api/playback/next', { method: 'POST' });
            } catch (error) {
                console.error('Next track error:', error);
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

        if (!data.options || data.options.length === 0) {
            grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: rgba(255, 255, 255, 0.5); padding: 40px;">No album art options available yet.</div>';
            return;
        }

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

    } catch (error) {
        console.error('Error loading album art options:', error);
        const grid = document.getElementById('album-art-grid');
        if (grid) {
            grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: rgba(255, 255, 255, 0.5); padding: 40px;">Error loading album art options.</div>';
        }
    }
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

            // Refresh the page to show new album art
            setTimeout(() => {
                window.location.reload();
            }, 500);

            showToast(`Switched to ${providerName} album art`);
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

async function updateLoop() {
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
            // Update lastTrackInfo FIRST so updateBackground() has current data
            // This fixes the stale data issue without needing forced updateBackground() calls
            lastTrackInfo = trackInfo;

            // Update all UI components
            updateAlbumArt(trackInfo);
            updateTrackInfo(trackInfo);
            updateProgress(trackInfo);
            updateControlState(trackInfo);

            // Update lyrics
            const lyrics = await getLyrics();
            if (lyrics) {
                setLyricsInDom(lyrics);
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

    // Setup provider UI
    setupProviderUI();

    // Start the update loop
    updateLoop();
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', main);