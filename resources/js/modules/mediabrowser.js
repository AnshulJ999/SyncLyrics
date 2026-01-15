/**
 * mediabrowser.js - Media Browser Module
 * 
 * Provides embedded Spotify library browser and Music Assistant iframe.
 * 
 * Level 2 - Imports: state
 */

import { lastTrackInfo } from './state.js';

// ========== MEDIA BROWSER SETUP ==========

/**
 * Setup Media Browser button and modal
 * Opens Spotify library browser or Music Assistant iframe based on current source
 */
export function setupMediaBrowser() {
    const browserBtn = document.getElementById('btn-media-browser');
    const modal = document.getElementById('media-browser-modal');
    const content = modal?.querySelector('.media-browser-content');
    const frame = document.getElementById('media-browser-frame');
    const closeBtn = document.getElementById('media-browser-close');
    const refreshBtn = document.getElementById('media-browser-refresh');
    
    if (!browserBtn || !modal || !frame) return;
    
    // Track current URL for refresh
    let currentFrameUrl = '';
    
    // Helper to close modal
    const closeModal = () => {
        modal.classList.add('hidden');
        frame.src = '';  // Unload iframe
        currentFrameUrl = '';
        browserBtn.classList.remove('active', 'active-ma');
    };
    
    // Open media browser
    browserBtn.addEventListener('click', async () => {
        // Determine source based on current track source
        const currentSource = lastTrackInfo?.source || 'spotify';
        const isMA = currentSource === 'music_assistant';
        
        if (isMA) {
            // Music Assistant - just open iframe to MA server
            currentFrameUrl = '/media-browser/?source=music_assistant';
            frame.src = currentFrameUrl;
            browserBtn.classList.add('active-ma');
            browserBtn.classList.remove('active');
            
            // Update icon to MA icon (custom SVG)
            const icon = browserBtn.querySelector('i, span');
            if (icon) {
                const maIcon = document.createElement('span');
                maIcon.className = 'icon-ma';
                icon.replaceWith(maIcon);
            }
        } else {
            // Spotify - fetch fresh token first
            try {
                const tokenRes = await fetch('/api/spotify/browser-token');
                if (!tokenRes.ok) {
                    console.error('[MediaBrowser] Failed to get token:', tokenRes.status);
                    currentFrameUrl = '/media-browser/';
                } else {
                    const data = await tokenRes.json();
                    if (data.access_token) {
                        currentFrameUrl = `/media-browser/?token=${encodeURIComponent(data.access_token)}`;
                    } else {
                        currentFrameUrl = '/media-browser/';
                    }
                }
            } catch (e) {
                console.error('[MediaBrowser] Token fetch error:', e);
                currentFrameUrl = '/media-browser/';
            }
            
            frame.src = currentFrameUrl;
            browserBtn.classList.add('active');
            browserBtn.classList.remove('active-ma');
            
            // Update icon to Spotify icon
            const icon = browserBtn.querySelector('i, span');
            if (icon && !icon.classList.contains('bi-spotify')) {
                const spotifyIcon = document.createElement('i');
                spotifyIcon.className = 'bi bi-spotify';
                icon.replaceWith(spotifyIcon);
            }
        }
        
        modal.classList.remove('hidden');
    });
    
    // Close modal - button
    if (closeBtn) {
        closeBtn.addEventListener('click', closeModal);
    }
    
    // Refresh iframe
    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            if (currentFrameUrl) {
                frame.src = currentFrameUrl;
            }
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
