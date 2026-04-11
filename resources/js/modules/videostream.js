/**
 * videostream.js - Video Stream Module
 * 
 * Provides an embedded iframe modal to show the REAPER Video Streamer.
 */

export function setupVideoStream() {
    const btn = document.getElementById('btn-video-stream');
    const modal = document.getElementById('video-stream-modal');
    const frame = document.getElementById('video-stream-frame');
    const closeBtn = document.getElementById('video-stream-close');
    const refreshBtn = document.getElementById('video-stream-refresh');
    
    if (!btn || !modal || !frame) return;

    // The Python streamer runs on port 9062
    const getStreamUrl = () => `http://${window.location.hostname}:9062/`;

    btn.addEventListener('click', () => {
        if (modal.classList.contains('hidden')) {
            frame.src = getStreamUrl();
            modal.classList.remove('hidden');
            btn.classList.add('active');
        } else {
            modal.classList.add('hidden');
            btn.classList.remove('active');
            frame.src = ''; // stop stream
        }
    });

    if (closeBtn) {
        closeBtn.addEventListener('click', () => {
            modal.classList.add('hidden');
            btn.classList.remove('active');
            frame.src = '';
        });
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            frame.src = getStreamUrl();
        });
    }

    // Close on escape
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !modal.classList.contains('hidden')) {
            modal.classList.add('hidden');
            btn.classList.remove('active');
            frame.src = '';
        }
    });

    // Close on backdrop click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.add('hidden');
            btn.classList.remove('active');
            frame.src = '';
        }
    });
}
