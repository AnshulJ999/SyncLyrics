
// ========== MEDIA BROWSER HELPER ==========

/**
 * Setup Media Browser button and modal
 * Opens Spotify library browser or Music Assistant iframe based on current source
 */
function setupMediaBrowser() {
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
            
            // Update icon to MA icon
            const icon = browserBtn.querySelector('i');
            if (icon) icon.className = 'ph-bold ph-music-notes';
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
            const icon = browserBtn.querySelector('i');
            if (icon) icon.className = 'ph-bold ph-spotify-logo';
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
