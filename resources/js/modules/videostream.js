/**
 * videostream.js - REAPER Video Stream Module
 *
 * Embeds the REAPER MJPEG streamer directly via <img> tag (not iframe).
 * This gives full CSS control over sizing, transparency, and layout.
 *
 * Features:
 *  - Full-width auto-height overlay (no black bars, no modal backdrop)
 *  - Transparency mode: mix-blend-mode:multiply (Guitar Pro white bg → transparent)
 *  - Fullscreen via native requestFullscreen() API
 *  - Auto-reconnect on stream drop (exponential backoff)
 *  - All prefs persisted in localStorage
 *
 * Level 2 - Imports: nothing (self-contained, no state/api dependencies)
 */

const STREAM_PORT = 9062;
const RECONNECT_BASE_MS = 2000;   // First retry after 2s
const RECONNECT_MAX_MS  = 10000;  // Cap at 10s between retries

const LS_TRANSPARENT = 'reaper_video_transparent';

export function setupVideoStream() {
    const btn             = document.getElementById('btn-video-stream');
    const overlay         = document.getElementById('video-stream-overlay');
    const img             = document.getElementById('video-stream-img');
    const closeBtn        = document.getElementById('vs-close-btn');
    const refreshBtn      = document.getElementById('vs-refresh-btn');
    const transparencyBtn = document.getElementById('vs-transparency-btn');
    const fullscreenBtn   = document.getElementById('vs-fullscreen-btn');

    if (!btn || !overlay || !img) return;

    let isOpen         = false;
    let reconnectTimer = null;
    let reconnectDelay = RECONNECT_BASE_MS;

    // ── URL helpers ─────────────────────────────────────────────────────────

    // Use /stream for the raw MJPEG — we handle our own HTML/CSS, no need for /
    const getStreamUrl = () => `http://${window.location.hostname}:${STREAM_PORT}/stream`;

    // ── Stream load/unload ───────────────────────────────────────────────────

    function loadStream() {
        // Setting src to '' first forces the browser to drop the old connection
        img.src = '';
        img.src = getStreamUrl();
    }

    function stopStream() {
        img.src = '';
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        reconnectDelay = RECONNECT_BASE_MS;
    }

    // ── Auto-reconnect ───────────────────────────────────────────────────────

    // img.onerror fires when the MJPEG connection is lost or server goes down
    img.addEventListener('error', () => {
        if (!isOpen) return;
        scheduleReconnect();
    });

    // img.onload fires on the first successful frame — reset backoff
    img.addEventListener('load', () => {
        reconnectDelay = RECONNECT_BASE_MS;
    });

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (isOpen) {
                loadStream();
                // Exponential backoff: double delay each retry, capped at max
                reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
            }
        }, reconnectDelay);
    }

    // ── Open / Close ─────────────────────────────────────────────────────────

    function open() {
        isOpen = true;
        overlay.classList.remove('hidden');
        btn.classList.add('active');
        loadStream();
    }

    function close() {
        isOpen = false;
        overlay.classList.add('hidden');
        btn.classList.remove('active');
        stopStream();

        // Exit fullscreen if we own it
        if (document.fullscreenElement === overlay) {
            document.exitFullscreen().catch(() => {});
        }
    }

    btn.addEventListener('click', () => {
        if (overlay.classList.contains('hidden')) {
            open();
        } else {
            close();
        }
    });

    if (closeBtn) {
        closeBtn.addEventListener('click', close);
    }

    if (refreshBtn) {
        refreshBtn.addEventListener('click', () => {
            if (reconnectTimer) {
                clearTimeout(reconnectTimer);
                reconnectTimer = null;
            }
            reconnectDelay = RECONNECT_BASE_MS;
            loadStream();
        });
    }

    // ── Transparency ─────────────────────────────────────────────────────────
    //
    // mix-blend-mode: multiply → white pixels (Guitar Pro background) become
    // transparent, showing whatever SyncLyrics has behind the overlay.
    // Controlled entirely client-side — no streamer changes needed.

    function applyTransparency(enabled) {
        overlay.classList.toggle('vs-transparent', enabled);
        localStorage.setItem(LS_TRANSPARENT, enabled ? 'true' : 'false');
        updateTransparencyBtn(enabled);
    }

    function updateTransparencyBtn(enabled) {
        if (!transparencyBtn) return;
        transparencyBtn.classList.toggle('active', enabled);
        transparencyBtn.title = enabled ? 'Disable transparency' : 'Enable transparency';
    }

    // Restore saved transparency state
    const savedTransparent = localStorage.getItem(LS_TRANSPARENT) === 'true';
    applyTransparency(savedTransparent);

    if (transparencyBtn) {
        transparencyBtn.addEventListener('click', () => {
            applyTransparency(!overlay.classList.contains('vs-transparent'));
        });
    }

    // ── Fullscreen ───────────────────────────────────────────────────────────

    function updateFullscreenBtn() {
        if (!fullscreenBtn) return;
        const isFs = document.fullscreenElement === overlay;
        fullscreenBtn.innerHTML = isFs
            ? '<i class="bi bi-fullscreen-exit"></i>'
            : '<i class="bi bi-fullscreen"></i>';
        fullscreenBtn.title = isFs ? 'Exit fullscreen' : 'Fullscreen';
    }

    if (fullscreenBtn) {
        fullscreenBtn.addEventListener('click', () => {
            if (document.fullscreenElement === overlay) {
                document.exitFullscreen().catch(() => {});
            } else {
                // navigationUI: 'hide' suppresses browser's fullscreen UI controls
                overlay.requestFullscreen({ navigationUI: 'hide' }).catch((err) => {
                    console.warn('[VideoStream] Fullscreen request failed:', err);
                });
            }
        });
    }

    document.addEventListener('fullscreenchange', updateFullscreenBtn);

    // ── Keyboard ─────────────────────────────────────────────────────────────

    document.addEventListener('keydown', (e) => {
        // Escape closes overlay (unless we're in OS fullscreen — browser handles that)
        if (e.key === 'Escape' && isOpen && document.fullscreenElement !== overlay) {
            close();
        }
    });
}
