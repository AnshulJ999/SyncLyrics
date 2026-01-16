/**
 * mediabrowser.js - Media Browser Module
 * 
 * Provides embedded Spotify library browser and Music Assistant iframe.
 * 
 * Level 2 - Imports: state, controls
 */

import { lastTrackInfo } from './state.js';
import { openDevicePickerModal } from './controls.js';

// ========== MEDIA BROWSER SETUP ==========

// Track current source for modal (can differ from playback source when user toggles)
let currentModalSource = 'spotify';
let currentFrameUrl = '';

/**
 * Get Spotify iframe URL with fresh token
 */
async function getSpotifyUrl() {
    try {
        const tokenRes = await fetch('/api/spotify/browser-token');
        if (!tokenRes.ok) {
            console.error('[MediaBrowser] Failed to get token:', tokenRes.status);
            return '/media-browser/';
        }
        const data = await tokenRes.json();
        if (data.access_token) {
            return `/media-browser/?token=${encodeURIComponent(data.access_token)}`;
        }
        return '/media-browser/';
    } catch (e) {
        console.error('[MediaBrowser] Token fetch error:', e);
        return '/media-browser/';
    }
}

/**
 * Get Music Assistant iframe URL
 */
function getMAUrl() {
    return '/media-browser/?source=music_assistant';
}

/**
 * Update the toggle button icon to show the OTHER source (what you'll switch to)
 * If currently showing Spotify → show MA icon (click to switch to MA)
 * If currently showing MA → show Spotify icon (click to switch to Spotify)
 */
function updateToggleButton(toggleBtn, currentSource) {
    if (!toggleBtn) return;
    
    const currentIcon = toggleBtn.querySelector('i, .icon-ma');
    if (!currentIcon) return;
    
    if (currentSource === 'music_assistant') {
        // Currently showing MA → show Spotify icon (click to switch to Spotify)
        if (!currentIcon.classList?.contains('bi-spotify')) {
            const spotifyIcon = document.createElement('i');
            spotifyIcon.className = 'bi bi-spotify';
            currentIcon.replaceWith(spotifyIcon);
        }
        toggleBtn.title = 'Switch to Spotify';
    } else {
        // Currently showing Spotify → show MA icon (click to switch to MA)
        if (!currentIcon.classList?.contains('icon-ma')) {
            const maIcon = document.createElement('span');
            maIcon.className = 'icon-ma';
            currentIcon.replaceWith(maIcon);
        }
        toggleBtn.title = 'Switch to Music Assistant';
    }
}

/**
 * Setup Media Browser button and modal
 * Opens Spotify library browser or Music Assistant iframe based on current source
 */
export function setupMediaBrowser() {
    const browserBtn = document.getElementById('btn-media-browser');
    const modal = document.getElementById('media-browser-modal');
    const frame = document.getElementById('media-browser-frame');
    const closeBtn = document.getElementById('media-browser-close');
    const refreshBtn = document.getElementById('media-browser-refresh');
    const toggleBtn = document.getElementById('media-browser-toggle-source');
    const devicesBtn = document.getElementById('media-browser-devices');
    
    if (!browserBtn || !modal || !frame) return;
    
    // Helper to close modal
    const closeModal = () => {
        modal.classList.add('hidden');
        frame.src = '';  // Unload iframe
        currentFrameUrl = '';
        browserBtn.classList.remove('active', 'active-ma');
    };
    
    // Helper to load a source
    const loadSource = async (source) => {
        currentModalSource = source;
        
        if (source === 'music_assistant') {
            currentFrameUrl = getMAUrl();
            frame.src = currentFrameUrl;
            browserBtn.classList.add('active-ma');
            browserBtn.classList.remove('active');
        } else {
            currentFrameUrl = await getSpotifyUrl();
            frame.src = currentFrameUrl;
            browserBtn.classList.add('active');
            browserBtn.classList.remove('active-ma');
        }
        
        // Update button icon on main button
        const icon = browserBtn.querySelector('i, span');
        if (icon) {
            if (source === 'music_assistant') {
                const maIcon = document.createElement('span');
                maIcon.className = 'icon-ma';
                icon.replaceWith(maIcon);
            } else if (!icon.classList.contains('bi-spotify')) {
                const spotifyIcon = document.createElement('i');
                spotifyIcon.className = 'bi bi-spotify';
                icon.replaceWith(spotifyIcon);
            }
        }
        
        // Update toggle button to show OTHER source
        updateToggleButton(toggleBtn, source);
    };
    
    // Open media browser
    browserBtn.addEventListener('click', async () => {
        // Determine initial source based on current track source
        const trackSource = lastTrackInfo?.source || 'spotify';
        currentModalSource = trackSource === 'music_assistant' ? 'music_assistant' : 'spotify';
        
        await loadSource(currentModalSource);
        modal.classList.remove('hidden');
    });
    
    // Toggle between sources
    if (toggleBtn) {
        toggleBtn.addEventListener('click', async () => {
            const newSource = currentModalSource === 'music_assistant' ? 'spotify' : 'music_assistant';
            await loadSource(newSource);
        });
    }
    
    // Close modal - button
    if (closeBtn) {
        closeBtn.addEventListener('click', closeModal);
    }
    
    // Refresh iframe (reloads current URL, not the whole page)
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            if (currentFrameUrl) {
                frame.src = currentFrameUrl;
            }
        });
    }
    
    // Device picker button - opens device picker for current modal source
    if (devicesBtn) {
        devicesBtn.addEventListener('click', () => {
            openDevicePickerModal(currentModalSource);
        });
    }
    
    // Click outside to close (click on backdrop, not on content)
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            closeModal();
        }
    });
    
    // Close on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            closeModal();
        }
    });
}

/**
 * Update media browser button icon based on current audio source
 * Called from main.js update loop when source changes
 */
export function updateMediaBrowserIcon() {
    const browserBtn = document.getElementById('btn-media-browser');
    if (!browserBtn) return;
    
    // Don't update if modal is open (user is browsing)
    const modal = document.getElementById('media-browser-modal');
    if (modal && !modal.classList.contains('hidden')) return;
    
    const currentSource = lastTrackInfo?.source || 'spotify';
    const isMA = currentSource === 'music_assistant';
    
    // Find current icon (could be i or span)
    const currentIcon = browserBtn.querySelector('i, span');
    if (!currentIcon) return;
    
    if (isMA) {
        // Switch to MA icon if not already
        if (!currentIcon.classList.contains('icon-ma')) {
            const maIcon = document.createElement('span');
            maIcon.className = 'icon-ma';
            currentIcon.replaceWith(maIcon);
        }
        browserBtn.title = 'Music Assistant Browser';
    } else {
        // Switch to Spotify icon if not already
        if (!currentIcon.classList.contains('bi-spotify')) {
            const spotifyIcon = document.createElement('i');
            spotifyIcon.className = 'bi bi-spotify';
            currentIcon.replaceWith(spotifyIcon);
        }
        browserBtn.title = 'Spotify Browser';
    }
}
