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
    const frame = document.getElementById('media-browser-frame');
    const closeBtn = document.getElementById('media-browser-close');
    const titleEl = document.querySelector('.media-browser-title');
    
    if (!browserBtn || !modal || !frame) return;
    
    // Open media browser
    browserBtn.addEventListener('click', async () => {
        // Determine source based on current track source
        const currentSource = lastTrackInfo?.source || 'spotify';
        const isMA = currentSource === 'music_assistant';
        
        if (isMA) {
            // Music Assistant - just open iframe to MA server
            frame.src = '/media-browser/?source=music_assistant';
            if (titleEl) titleEl.textContent = 'Music Assistant';
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
                    // Still open but may require login
                    frame.src = '/media-browser/';
                } else {
                    const data = await tokenRes.json();
                    if (data.access_token) {
                        frame.src = `/media-browser/?token=${encodeURIComponent(data.access_token)}`;
                    } else {
                        frame.src = '/media-browser/';
                    }
                }
            } catch (e) {
                console.error('[MediaBrowser] Token fetch error:', e);
                frame.src = '/media-browser/';
            }
            
            if (titleEl) titleEl.textContent = 'Spotify Browser';
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
    
    // Close modal
    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            modal.classList.add('hidden');
            frame.src = '';  // Unload iframe
            browserBtn.classList.remove('active', 'active-ma');
        });
    }
    
    // Close on Escape key
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            modal.classList.add('hidden');
            frame.src = '';
            browserBtn.classList.remove('active', 'active-ma');
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
    const icon = browserBtn.querySelector('i');
    
    if (!icon) return;
    
    if (isMA) {
        // Replace with MA icon span
        if (!icon.classList.contains('icon-ma')) {
            const maIcon = document.createElement('span');
            maIcon.className = 'icon-ma';
            icon.replaceWith(maIcon);
        }
        browserBtn.title = 'Music Assistant Browser';
    } else {
        // Replace with Spotify Bootstrap icon
        if (!icon.classList.contains('bi-spotify')) {
            const spotifyIcon = document.createElement('i');
            spotifyIcon.className = 'bi bi-spotify';
            const currentIcon = browserBtn.querySelector('i, span');
            if (currentIcon) currentIcon.replaceWith(spotifyIcon);
        }
        browserBtn.title = 'Spotify Browser';
    }
}
