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
    useAlbumColors: true
};

let lastTrackInfo = null;

async function getConfig() {
    try {
        const response = await fetch('/config');
        const config = await response.json();
        updateInterval = config.updateInterval;
        console.log(`Update interval set to: ${updateInterval}ms`);  // Debug log
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
        if (data.colors && displayConfig.useAlbumColors && (data.colors[0] !== currentColors[0] || data.colors[1] !== currentColors[1])) {
            updateBackgroundColors(data.colors);
            currentColors = data.colors;
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

function updateBackgroundColors(colors) {
    if (!colors || !Array.isArray(colors)) return;

    document.body.style.background = `linear-gradient(135deg, ${colors[0]} 0%, ${colors[1]} 100%)`;

    // Add subtle animation
    document.body.style.transition = 'background 1s ease-in-out';
}

function updateLyricElement(element, text) {
    if (element && element.textContent !== text) {
        element.textContent = text;
    }
}

function setLyricsInDom(lyrics) {
    if (updateInProgress) return;
    if (!Array.isArray(lyrics)) {
        lyrics = ['', '', lyrics.msg, '', '', ''];
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

    // Parse parameters
    displayConfig.minimal = params.get('minimal') === 'true';
    displayConfig.showAlbumArt = params.get('showAlbumArt') !== 'false';
    displayConfig.showTrackInfo = params.get('showTrackInfo') !== 'false';
    displayConfig.showControls = params.get('showControls') !== 'false';
    displayConfig.showProgress = params.get('showProgress') !== 'false';
    displayConfig.showBottomNav = params.get('showBottomNav') !== 'false';
    displayConfig.useAlbumColors = params.get('useAlbumColors') !== 'false';

    // Minimal mode overrides all
    if (displayConfig.minimal) {
        displayConfig.showAlbumArt = false;
        displayConfig.showTrackInfo = false;
        displayConfig.showControls = false;
        displayConfig.showProgress = false;
        displayConfig.showBottomNav = false;

    }

    // Apply visibility
    applyDisplayConfig();

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
}

function setupSettingsPanel() {
    const settingsToggle = document.getElementById('settings-toggle');
    const settingsPanel = document.getElementById('settings-panel');
    const copyUrlBtn = document.getElementById('copy-url-btn');

    if (!settingsToggle || !settingsPanel) return;

    // Toggle panel
    settingsToggle.addEventListener('click', () => {
        const isVisible = settingsPanel.style.display !== 'none';
        settingsPanel.style.display = isVisible ? 'none' : 'block';
    });

    // Sync checkboxes with current config
    document.getElementById('opt-album-art').checked = displayConfig.showAlbumArt;
    document.getElementById('opt-track-info').checked = displayConfig.showTrackInfo;
    document.getElementById('opt-controls').checked = displayConfig.showControls;
    document.getElementById('opt-progress').checked = displayConfig.showProgress;
    document.getElementById('opt-bottom-nav').checked = displayConfig.showBottomNav;
    document.getElementById('opt-colors').checked = displayConfig.useAlbumColors;

    // Handle checkbox changes
    document.getElementById('opt-album-art').addEventListener('change', (e) => {
        displayConfig.showAlbumArt = e.target.checked;
        applyDisplayConfig();
        updateUrlDisplay();
    });

    document.getElementById('opt-track-info').addEventListener('change', (e) => {
        displayConfig.showTrackInfo = e.target.checked;
        applyDisplayConfig();
        updateUrlDisplay();
    });

    document.getElementById('opt-controls').addEventListener('change', (e) => {
        displayConfig.showControls = e.target.checked;
        applyDisplayConfig();
        updateUrlDisplay();
    });

    document.getElementById('opt-progress').addEventListener('change', (e) => {
        displayConfig.showProgress = e.target.checked;
        applyDisplayConfig();
        updateUrlDisplay();
    });

    document.getElementById('opt-bottom-nav').addEventListener('change', (e) => {
        displayConfig.showBottomNav = e.target.checked;
        applyDisplayConfig();
        updateUrlDisplay();
    });

    document.getElementById('opt-colors').addEventListener('change', (e) => {
        displayConfig.useAlbumColors = e.target.checked;
        updateUrlDisplay();
    });

    // Copy URL button
    if (copyUrlBtn) {
        copyUrlBtn.addEventListener('click', () => {
            const url = generateCurrentUrl();
            navigator.clipboard.writeText(url).then(() => {
                copyUrlBtn.textContent = '✓ Copied!';
                setTimeout(() => {
                    copyUrlBtn.textContent = 'Copy Current URL';
                }, 2000);
            }).catch(() => {
                copyUrlBtn.textContent = '✗ Copy failed';
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
    if (!displayConfig.useAlbumColors) params.set('useAlbumColors', 'false');

    return params.toString() ? `${base}?${params.toString()}` : base;
}

function updateAlbumArt(trackInfo) {
    if (!displayConfig.showAlbumArt) return;

    const albumArt = document.getElementById('album-art');
    const trackHeader = document.getElementById('track-header');

    if (!albumArt || !trackHeader) return;

    if (trackInfo.album_art_url) {
        albumArt.src = trackInfo.album_art_url;
        albumArt.style.display = 'block';
    } else {
        // Hide album art if not available
        albumArt.style.display = 'none';
    }

    // Show/hide header based on whether we have art or track info
    const hasContent = (trackInfo.album_art_url && displayConfig.showAlbumArt) || displayConfig.showTrackInfo;
    trackHeader.style.display = hasContent ? 'flex' : 'none';
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
        // Update play/pause icon
        playPauseBtn.textContent = trackInfo.is_playing ? '⏸' : '▶';
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

            lastTrackInfo = trackInfo;
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

    // Start the update loop
    updateLoop();
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', main);