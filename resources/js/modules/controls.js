/**
 * controls.js - Playback Controls, Queue, and Like Button
 * 
 * This module handles all playback-related UI controls including
 * play/pause, progress bar, queue drawer, and like functionality.
 * 
 * Level 2 - Imports: state, dom, api, utils
 */

import {
    displayConfig,
    lastTrackInfo,
    queueDrawerOpen,
    queuePollInterval,
    isLiked,
    pendingArtUrl,
    visualModeActive,
    manualVisualModeOverride,
    setLastTrackInfo,
    setPendingArtUrl,
    setQueueDrawerOpen,
    setQueuePollInterval,
    setIsLiked,
    setManualVisualModeOverride
} from './state.js';
import { showToast } from './dom.js';
import { formatTime } from './utils.js';
import {
    playbackCommand,
    getCurrentTrack,
    fetchQueue,
    checkLikedStatus as apiCheckLikedStatus,
    toggleLikeStatus
} from './api.js';

// ========== PLAYBACK CONTROLS ==========

/**
 * Attach event handlers to playback control buttons
 * 
 * @param {Function} enterVisualModeFn - Callback to enter visual mode
 * @param {Function} exitVisualModeFn - Callback to exit visual mode
 */
export function attachControlHandlers(enterVisualModeFn = null, exitVisualModeFn = null) {
    if (!displayConfig.showControls) return;

    const prevBtn = document.getElementById('btn-previous');
    const playPauseBtn = document.getElementById('btn-play-pause');
    const nextBtn = document.getElementById('btn-next');

    if (prevBtn) {
        prevBtn.addEventListener('click', async () => {
            try {
                await playbackCommand('previous');
            } catch (error) {
                console.error('Previous track error:', error);
                showToast('Failed to skip previous', 'error');
            }
        });
    }

    if (playPauseBtn) {
        playPauseBtn.addEventListener('click', async () => {
            try {
                await playbackCommand('play-pause');
                // Force immediate update of track info
                setTimeout(async () => {
                    const trackInfo = await getCurrentTrack();
                    if (trackInfo && !trackInfo.error) {
                        updateControlState(trackInfo);
                    }
                }, 200);
            } catch (error) {
                console.error('Play/Pause error:', error);
                showToast('Failed to toggle playback', 'error');
            }
        });
    }

    if (nextBtn) {
        nextBtn.addEventListener('click', async () => {
            try {
                await playbackCommand('next');
            } catch (error) {
                console.error('Next track error:', error);
                showToast('Failed to skip next', 'error');
            }
        });
    }

    // Visual Mode Toggle Button
    const visualModeBtn = document.getElementById('btn-lyrics-toggle');
    if (visualModeBtn && enterVisualModeFn && exitVisualModeFn) {
        visualModeBtn.addEventListener('click', () => {
            if (visualModeActive) {
                setManualVisualModeOverride(false);
                exitVisualModeFn();
            } else {
                setManualVisualModeOverride(true);
                enterVisualModeFn();
            }
        });
    }
}

/**
 * Update control button states based on track info
 * 
 * @param {Object} trackInfo - Current track information
 */
export function updateControlState(trackInfo) {
    if (!displayConfig.showControls) return;

    const prevBtn = document.getElementById('btn-previous');
    const playPauseBtn = document.getElementById('btn-play-pause');
    const nextBtn = document.getElementById('btn-next');

    // Enable controls for Spotify, Spotify Hybrid, or Windows Media
    // Note: Audio Recognition source does not support playback controls
    const canControl =
        trackInfo.source === 'spotify' ||
        trackInfo.source === 'spotify_hybrid' ||
        trackInfo.source === 'windows_media';

    if (prevBtn) prevBtn.disabled = !canControl;
    if (nextBtn) nextBtn.disabled = !canControl;
    if (playPauseBtn) {
        playPauseBtn.disabled = !canControl;
        const isPlaying = trackInfo.is_playing === true;

        if (isPlaying) {
            playPauseBtn.classList.remove('paused');
            playPauseBtn.classList.add('playing');
        } else {
            playPauseBtn.classList.remove('playing');
            playPauseBtn.classList.add('paused');
        }
    }
}

// ========== PROGRESS BAR ==========

/**
 * Update progress bar and time display
 * 
 * @param {Object} trackInfo - Current track information
 */
export function updateProgress(trackInfo) {
    if (!displayConfig.showProgress) return;

    const fill = document.getElementById('progress-fill');
    const currentTime = document.getElementById('current-time');
    const totalTime = document.getElementById('total-time');
    const progressContainer = document.getElementById('progress-container');

    // Handle null duration gracefully
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

// ========== TRACK INFO ==========

/**
 * Update track title and artist display
 * 
 * @param {Object} trackInfo - Current track information
 */
export function updateTrackInfo(trackInfo) {
    if (!displayConfig.showTrackInfo) return;

    const titleEl = document.getElementById('track-title');
    const artistEl = document.getElementById('track-artist');

    if (titleEl) titleEl.textContent = trackInfo.title || 'Unknown Track';
    if (artistEl) artistEl.textContent = trackInfo.artist || 'Unknown Artist';
}

// ========== ALBUM ART ==========

/**
 * Update album art display
 * 
 * @param {Object} trackInfo - Current track information
 * @param {Function} updateBackgroundFn - Optional callback to update background
 */
export function updateAlbumArt(trackInfo, updateBackgroundFn = null) {
    const albumArt = document.getElementById('album-art');
    const trackHeader = document.getElementById('track-header');
    const albumArtLink = document.getElementById('album-art-link');

    // Update Spotify link on album art
    if (albumArtLink) {
        const genericSpotifyUrl = 'spotify:';
        albumArtLink.href = genericSpotifyUrl;
        albumArtLink.style.cursor = 'pointer';
        albumArtLink.title = "Open Spotify App";
        albumArtLink.onclick = null;
    }

    if (!albumArt || !trackHeader) return;

    if (trackInfo.album_art_url) {
        // Add cache buster to force reload when song changes
        // Uses track_id (unique per song) or stable artist+title fallback
        let targetUrl = new URL(trackInfo.album_art_url, window.location.href).href;
        // Stable fallback: use artist_title instead of Date.now() to prevent reload spam
        const stableFallback = `${trackInfo.artist || ''}_${trackInfo.title || ''}`.replace(/\s+/g, '_');
        const cacheBuster = trackInfo.track_id || trackInfo.id || stableFallback;
        targetUrl = targetUrl.includes('?')
            ? `${targetUrl}&cb=${cacheBuster}`
            : `${targetUrl}?cb=${cacheBuster}`;

        if (albumArt.src !== targetUrl) {
            if (pendingArtUrl !== targetUrl) {
                setPendingArtUrl(targetUrl);

                const img = new Image();

                img.onload = () => {
                    if (pendingArtUrl === targetUrl) {
                        const currentSrc = albumArt.src || '';
                        const hasExistingImage = currentSrc &&
                            currentSrc !== window.location.href &&
                            currentSrc !== '' &&
                            currentSrc !== targetUrl;

                        if (hasExistingImage) {
                            albumArt.style.opacity = '0';
                            setTimeout(() => {
                                albumArt.src = targetUrl;
                                setTimeout(() => {
                                    albumArt.style.opacity = '1';
                                }, 10);
                            }, 150);
                        } else {
                            albumArt.src = targetUrl;
                            albumArt.style.opacity = '1';
                        }

                        if (updateBackgroundFn && (displayConfig.artBackground || displayConfig.softAlbumArt || displayConfig.sharpAlbumArt)) {
                            updateBackgroundFn();
                        }

                        albumArt.style.display = displayConfig.showAlbumArt ? 'block' : 'none';
                        setPendingArtUrl(null);
                    }
                };

                img.onerror = () => {
                    if (pendingArtUrl === targetUrl) setPendingArtUrl(null);
                };

                img.src = targetUrl;
            }
        } else {
            albumArt.style.display = displayConfig.showAlbumArt ? 'block' : 'none';
        }
    } else {
        if (!pendingArtUrl) {
            albumArt.style.display = 'none';
        }
    }

    // Set individual element visibility independently
    // const albumArtLink = document.getElementById('album-art-link');
    if (albumArtLink) {
        albumArtLink.style.display = displayConfig.showAlbumArt ? 'block' : 'none';
    }

    const trackInfoEl = document.querySelector('.track-info');
    if (trackInfoEl) {
        trackInfoEl.style.display = displayConfig.showTrackInfo ? 'block' : 'none';
    }

    // Show header if either element is visible
    const hasContent = (trackInfo.album_art_url && displayConfig.showAlbumArt) || displayConfig.showTrackInfo;
    trackHeader.style.display = hasContent ? 'flex' : 'none';
}

// ========== QUEUE DRAWER ==========

/**
 * Setup queue interactions (backdrop for click-outside close)
 */
export function setupQueueInteractions() {
    let backdrop = document.querySelector('.queue-backdrop');
    if (!backdrop) {
        backdrop = document.createElement('div');
        backdrop.className = 'queue-backdrop';
        document.body.appendChild(backdrop);

        backdrop.addEventListener('click', () => {
            if (queueDrawerOpen) toggleQueueDrawer();
        });
    }
}

/**
 * Toggle queue drawer open/close
 */
export async function toggleQueueDrawer() {
    const drawer = document.getElementById('queue-drawer');
    const backdrop = document.querySelector('.queue-backdrop');

    setQueueDrawerOpen(!queueDrawerOpen);

    if (queueDrawerOpen) {
        drawer.classList.add('open');
        if (backdrop) {
            backdrop.classList.add('visible');
            backdrop.style.pointerEvents = 'auto';
        }
        await fetchAndRenderQueue();

        // Start polling when drawer is open
        if (queuePollInterval) clearInterval(queuePollInterval);
        setQueuePollInterval(setInterval(() => {
            if (queueDrawerOpen) {
                fetchAndRenderQueue();
            }
        }, 5000));

    } else {
        drawer.classList.remove('open');
        if (backdrop) {
            backdrop.classList.remove('visible');
            backdrop.style.pointerEvents = 'none';
        }
        // Stop polling when closed
        if (queuePollInterval) {
            clearInterval(queuePollInterval);
            setQueuePollInterval(null);
        }
    }
}

/**
 * Fetch queue from API and render to DOM
 */
export async function fetchAndRenderQueue() {
    try {
        const data = await fetchQueue();
        if (data.error) return;

        const list = document.getElementById('queue-list');
        list.innerHTML = '';

        if (data.queue && data.queue.length > 0) {
            data.queue.forEach(track => {
                const item = document.createElement('div');
                item.className = 'queue-item';

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

// ========== LIKE BUTTON ==========

/**
 * Check if current track is liked and update button
 * 
 * @param {string} trackId - Spotify track ID
 */
export async function checkLikedStatus(trackId) {
    if (!trackId) return;
    try {
        const data = await apiCheckLikedStatus(trackId);

        // Ensure we are still playing the same track
        if (lastTrackInfo && lastTrackInfo.id === trackId) {
            setIsLiked(data.liked);
            updateLikeButton();
        }
    } catch (e) {
        console.error(e);
    }
}

/**
 * Update like button UI based on current state
 */
export function updateLikeButton() {
    const btn = document.getElementById('btn-like');
    if (!btn) return;

    if (isLiked) {
        btn.innerHTML = '❤️';
        btn.classList.add('liked');
    } else {
        btn.innerHTML = '♡';
        btn.classList.remove('liked');
    }
}

/**
 * Toggle like status for current track
 */
export async function toggleLike() {
    if (!lastTrackInfo || !lastTrackInfo.id) return;

    // Optimistic update
    setIsLiked(!isLiked);
    updateLikeButton();

    try {
        await toggleLikeStatus(lastTrackInfo.id, isLiked ? 'like' : 'unlike');
    } catch (e) {
        // Revert on failure
        setIsLiked(!isLiked);
        updateLikeButton();
        showToast("Action failed", "error");
    }
}

// ========== TOUCH CONTROLS ==========

/**
 * Setup touch/swipe controls
 */
export function setupTouchControls() {
    let touchStartX = 0;
    let touchStartY = 0;
    let touchStartedInModal = false;

    document.addEventListener('touchstart', e => {
        touchStartX = e.changedTouches[0].screenX;
        touchStartY = e.changedTouches[0].screenY;

        const providerModal = document.getElementById('provider-modal');
        if (providerModal && !providerModal.classList.contains('hidden')) {
            touchStartedInModal = providerModal.contains(e.target);
        } else {
            touchStartedInModal = false;
        }
    }, { passive: true });

    document.addEventListener('touchend', e => {
        if (touchStartedInModal) return;

        const touchEndX = e.changedTouches[0].screenX;
        const touchEndY = e.changedTouches[0].screenY;

        handleSwipe(touchStartX, touchStartY, touchEndX, touchEndY);
    }, { passive: true });
}

/**
 * Handle swipe gesture
 */
function handleSwipe(startX, startY, endX, endY) {
    const minSwipeDistance = 50;
    const screenWidth = window.innerWidth;
    const isRightEdge = startX > (screenWidth - 60);

    if (isRightEdge && !queueDrawerOpen) {
        if ((startX - endX) > minSwipeDistance) {
            toggleQueueDrawer();
            return;
        }
    }
}
