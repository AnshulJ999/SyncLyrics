/**
 * settings.js - Settings Panel & Display Configuration
 * 
 * This module handles the settings panel, URL parameter parsing,
 * and display configuration management.
 * 
 * Level 2 - Imports: state, dom, utils
 */

import {
    displayConfig,
    wordSyncEnabled,
    setWordSyncEnabled,
    setManualStyleOverride
} from './state.js';
import { showToast } from './dom.js';
import { copyToClipboard } from './utils.js';
import { applySoftMode, applySharpMode, updateBackground } from './background.js';
import { showSpectrum, hideSpectrum } from './spectrum.js';

// ========== DISPLAY INITIALIZATION ==========

/**
 * Parse URL parameters and initialize display configuration
 */
export function initializeDisplay() {
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
    if (params.has('showAudioSource')) {
        displayConfig.showAudioSource = params.get('showAudioSource') === 'true';
    }
    if (params.has('showVisualModeToggle')) {
        displayConfig.showVisualModeToggle = params.get('showVisualModeToggle') === 'true';
    }
    if (params.has('showWaveform')) {
        displayConfig.showWaveform = params.get('showWaveform') === 'true';
    }
    if (params.has('showSpectrum')) {
        displayConfig.showSpectrum = params.get('showSpectrum') === 'true';
    }

    // Enforce mutual exclusivity: Waveform <-> Progress (can't have both)
    if (displayConfig.showWaveform) {
        displayConfig.showProgress = false;
    }

    // Word-sync toggle (enabled by default, can be disabled via URL)
    if (params.has('wordSync')) {
        setWordSyncEnabled(params.get('wordSync') !== 'false');
    }

    // Minimal mode overrides all
    if (displayConfig.minimal) {
        displayConfig.showAlbumArt = false;
        displayConfig.showTrackInfo = false;
        displayConfig.showControls = false;
        displayConfig.showProgress = false;
        displayConfig.showBottomNav = false;
        displayConfig.showProvider = false;
        displayConfig.showAudioSource = false;
        displayConfig.showVisualModeToggle = false;
    }

    // Apply visibility
    applyDisplayConfig();

    // Apply mode styling (CSS classes for soft/sharp)
    applySoftMode();
    applySharpMode();

    // Setup settings panel (if not minimal)
    if (!displayConfig.minimal) {
        setupSettingsPanel();
    }
}

/**
 * Apply display configuration to DOM elements
 * 
 * @param {Function} updateBackgroundFn - Optional callback to update background
 */
export function applyDisplayConfig(updateBackgroundFn = null) {
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

    // Progress container: hide when waveform is enabled (mutually exclusive)
    if (progressContainer) {
        const showProgress = displayConfig.showProgress && !displayConfig.showWaveform;
        progressContainer.style.display = showProgress ? 'block' : 'none';
    }

    // Waveform container: show only when enabled
    const waveformContainer = document.getElementById('waveform-container');
    if (waveformContainer) {
        waveformContainer.style.display = displayConfig.showWaveform ? 'block' : 'none';
    }

    // Spectrum container: show only when enabled
    const spectrumContainer = document.getElementById('spectrum-container');
    if (spectrumContainer) {
        spectrumContainer.style.display = displayConfig.showSpectrum ? 'block' : 'none';
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

    // Audio source toggle visibility
    const sourceToggle = document.getElementById('source-toggle');
    if (sourceToggle) {
        sourceToggle.style.display = displayConfig.showAudioSource ? 'block' : 'none';
    }

    // Visual mode toggle button visibility
    const visualModeToggle = document.getElementById('btn-lyrics-toggle');
    if (visualModeToggle) {
        visualModeToggle.style.display = displayConfig.showVisualModeToggle ? 'flex' : 'none';
    }

    // Track info visibility (independent of album art)
    const trackInfoEl = document.querySelector('.track-info');
    if (trackInfoEl) {
        trackInfoEl.style.display = displayConfig.showTrackInfo ? 'block' : 'none';
    }

    // Album art link visibility (independent of track info)
    const albumArtLink = document.getElementById('album-art-link');
    if (albumArtLink) {
        albumArtLink.style.display = displayConfig.showAlbumArt ? 'block' : 'none';
    }

    // Update background if callback provided
    if (updateBackgroundFn) {
        updateBackgroundFn();
    }
}

// ========== SETTINGS PANEL ==========

/**
 * Setup the settings panel event handlers
 */
export function setupSettingsPanel() {
    const settingsToggle = document.getElementById('settings-toggle');
    const settingsPanel = document.getElementById('settings-panel');
    const copyUrlBtn = document.getElementById('copy-url-btn');

    if (!settingsToggle || !settingsPanel) return;

    // Toggle panel
    settingsToggle.addEventListener('click', (e) => {
        e.stopPropagation();
        const isVisible = settingsPanel.style.display !== 'none';
        settingsPanel.style.display = isVisible ? 'none' : 'block';
    });

    // Close panel when clicking outside of it
    document.addEventListener('click', (e) => {
        if (settingsPanel.style.display !== 'none' &&
            !settingsPanel.contains(e.target) &&
            !settingsToggle.contains(e.target)) {
            settingsPanel.style.display = 'none';
        }
    });

    // Prevent panel from closing when clicking inside it
    settingsPanel.addEventListener('click', (e) => {
        e.stopPropagation();
    });

    // Sync checkboxes with current config
    const checkboxMap = {
        'opt-album-art': 'showAlbumArt',
        'opt-track-info': 'showTrackInfo',
        'opt-controls': 'showControls',
        'opt-progress': 'showProgress',
        'opt-bottom-nav': 'showBottomNav',
        'opt-colors': 'useAlbumColors',
        'opt-art-bg': 'artBackground',
        'opt-soft-art-bg': 'softAlbumArt',
        'opt-sharp-art-bg': 'sharpAlbumArt',
        'opt-show-provider': 'showProvider',
        'opt-audio-source': 'showAudioSource',
        'opt-visual-mode-toggle': 'showVisualModeToggle',
        'opt-waveform': 'showWaveform',
        'opt-spectrum': 'showSpectrum'
    };

    // Initialize checkboxes
    Object.entries(checkboxMap).forEach(([id, key]) => {
        const el = document.getElementById(id);
        if (el) {
            el.checked = displayConfig[key];
        }
    });

    // Handle checkbox changes
    const checkboxIds = Object.keys(checkboxMap);

    checkboxIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', (e) => {
                handleCheckboxChange(id, e.target.checked);
            });
        }
    });

    // Word-sync checkbox (separate from displayConfig)
    const wordSyncCheckbox = document.getElementById('opt-word-sync');
    if (wordSyncCheckbox) {
        // Initialize from current state
        wordSyncCheckbox.checked = wordSyncEnabled;
        
        wordSyncCheckbox.addEventListener('change', (e) => {
            setWordSyncEnabled(e.target.checked);
            
            // Update the toggle button state too
            const toggleBtn = document.getElementById('btn-word-sync-toggle');
            if (toggleBtn) {
                toggleBtn.classList.toggle('active', e.target.checked);
            }
            
            // Save to localStorage
            localStorage.setItem('wordSyncEnabled', e.target.checked);
            
            // Update URL
            history.replaceState(null, '', generateCurrentUrl());
            updateUrlDisplay();
        });
    }

    // Fullscreen toggle button (icon-only, updates title for accessibility)
    const fullscreenBtn = document.getElementById('fullscreen-btn');
    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', () => {
            if (!document.fullscreenElement) {
                document.documentElement.requestFullscreen().catch((e) => {
                    console.error(`Error attempting to enable fullscreen: ${e.message}`);
                });
            } else {
                if (document.exitFullscreen) {
                    document.exitFullscreen();
                }
            }
        });

        // Update button icon and title based on fullscreen state
        document.addEventListener('fullscreenchange', () => {
            const icon = fullscreenBtn.querySelector('i');
            if (document.fullscreenElement) {
                fullscreenBtn.title = 'Exit Fullscreen';
                if (icon) icon.className = 'bi bi-fullscreen-exit';
            } else {
                fullscreenBtn.title = 'Enter Fullscreen';
                if (icon) icon.className = 'bi bi-fullscreen';
            }
        });
    }

    // Copy URL button (preserves SVG icon)
    if (copyUrlBtn) {
        const originalHTML = copyUrlBtn.innerHTML;
        copyUrlBtn.addEventListener('click', () => {
            const url = generateCurrentUrl();
            copyToClipboard(url).then(() => {
                copyUrlBtn.innerHTML = '<i class="bi bi-check-lg"></i> Copied!';
                setTimeout(() => {
                    copyUrlBtn.innerHTML = originalHTML;
                }, 2000);
            }).catch(() => {
                copyUrlBtn.innerHTML = '<i class="bi bi-x-lg"></i> Failed';
                setTimeout(() => {
                    copyUrlBtn.innerHTML = originalHTML;
                }, 2000);
            });
        });
    }

    updateUrlDisplay();
}

/**
 * Handle checkbox change in settings panel
 * 
 * @param {string} id - Checkbox ID
 * @param {boolean} checked - Whether checkbox is checked
 */
function handleCheckboxChange(id, checked) {
    if (id === 'opt-album-art') displayConfig.showAlbumArt = checked;
    if (id === 'opt-track-info') displayConfig.showTrackInfo = checked;
    if (id === 'opt-controls') displayConfig.showControls = checked;
    if (id === 'opt-progress') displayConfig.showProgress = checked;
    if (id === 'opt-bottom-nav') displayConfig.showBottomNav = checked;
    if (id === 'opt-colors') displayConfig.useAlbumColors = checked;
    if (id === 'opt-show-provider') displayConfig.showProvider = checked;
    if (id === 'opt-audio-source') displayConfig.showAudioSource = checked;
    if (id === 'opt-visual-mode-toggle') displayConfig.showVisualModeToggle = checked;

    // Handle mutually exclusive background options
    if (id === 'opt-art-bg') {
        displayConfig.artBackground = checked;
        if (checked) {
            displayConfig.softAlbumArt = false;
            displayConfig.sharpAlbumArt = false;
            document.getElementById('opt-soft-art-bg').checked = false;
            document.getElementById('opt-sharp-art-bg').checked = false;
        }
    }
    if (id === 'opt-soft-art-bg') {
        displayConfig.softAlbumArt = checked;
        if (checked) {
            displayConfig.artBackground = false;
            displayConfig.sharpAlbumArt = false;
            document.getElementById('opt-art-bg').checked = false;
            document.getElementById('opt-sharp-art-bg').checked = false;
        }
    }
    if (id === 'opt-sharp-art-bg') {
        displayConfig.sharpAlbumArt = checked;
        if (checked) {
            displayConfig.artBackground = false;
            displayConfig.softAlbumArt = false;
            document.getElementById('opt-art-bg').checked = false;
            document.getElementById('opt-soft-art-bg').checked = false;
        }
    }

    // Handle mutually exclusive waveform/progress bar options
    if (id === 'opt-waveform') {
        displayConfig.showWaveform = checked;
        if (checked) {
            // Waveform replaces standard progress bar
            displayConfig.showProgress = false;
            const progressCheckbox = document.getElementById('opt-progress');
            if (progressCheckbox) progressCheckbox.checked = false;
        }
    }
    if (id === 'opt-progress') {
        displayConfig.showProgress = checked;
        if (checked) {
            // Standard progress bar replaces waveform
            displayConfig.showWaveform = false;
            const waveformCheckbox = document.getElementById('opt-waveform');
            if (waveformCheckbox) waveformCheckbox.checked = false;
        }
    }

    // Spectrum visualizer (independent setting)
    if (id === 'opt-spectrum') {
        displayConfig.showSpectrum = checked;
        // Manually start/stop animation to avoid needing page reload
        if (checked) {
            showSpectrum();
        } else {
            hideSpectrum();
        }
    }

    applyDisplayConfig();
    applySoftMode();
    applySharpMode();
    updateBackground();
    updateUrlDisplay();

    // Update browser URL without page reload
    history.replaceState(null, '', generateCurrentUrl());

    setManualStyleOverride(true);
}

// ========== URL GENERATION ==========

/**
 * Update the URL display in settings panel
 */
export function updateUrlDisplay() {
    const urlDisplay = document.getElementById('url-display');
    if (urlDisplay) {
        urlDisplay.textContent = generateCurrentUrl();
    }
}

/**
 * Generate current URL with all display parameters
 * 
 * @returns {string} Full URL with query parameters
 */
export function generateCurrentUrl() {
    const base = window.location.origin + window.location.pathname;
    const params = new URLSearchParams();

    if (!displayConfig.showAlbumArt) params.set('showAlbumArt', 'false');
    if (!displayConfig.showTrackInfo) params.set('showTrackInfo', 'false');
    if (!displayConfig.showControls) params.set('showControls', 'false');
    if (!displayConfig.showProgress) params.set('showProgress', 'false');
    if (!displayConfig.showBottomNav) params.set('showBottomNav', 'false');
    if (!displayConfig.showProvider) params.set('showProvider', 'false');
    if (!displayConfig.showAudioSource) params.set('showAudioSource', 'false');
    if (!displayConfig.showVisualModeToggle) params.set('showVisualModeToggle', 'false');
    if (!wordSyncEnabled) params.set('wordSync', 'false');
    if (displayConfig.useAlbumColors) params.set('useAlbumColors', 'true');

    // Enforce mutual exclusivity: only add one of artBackground, softAlbumArt, or sharpAlbumArt
    if (displayConfig.sharpAlbumArt) {
        params.set('sharpAlbumArt', 'true');
    } else if (displayConfig.softAlbumArt) {
        params.set('softAlbumArt', 'true');
    } else if (displayConfig.artBackground) {
        params.set('artBackground', 'true');
    }

    // Waveform and spectrum visualizer settings
    if (displayConfig.showWaveform) params.set('showWaveform', 'true');
    if (displayConfig.showSpectrum) params.set('showSpectrum', 'true');

    return params.toString() ? `${base}?${params.toString()}` : base;
}
