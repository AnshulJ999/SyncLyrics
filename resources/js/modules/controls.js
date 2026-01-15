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
    lastAlbumArtUrl,
    lastAlbumArtPath,
    visualModeActive,
    manualVisualModeOverride,
    setLastTrackInfo,
    setPendingArtUrl,
    setLastAlbumArtUrl,
    setLastAlbumArtPath,
    setQueueDrawerOpen,
    setQueuePollInterval,
    setIsLiked,
    setManualVisualModeOverride
} from './state.js';
import { showToast } from './dom.js';
import { enableArtZoom, disableArtZoom, resetArtZoom } from './artZoom.js';
import { formatTime } from './utils.js';
import {
    playbackCommand,
    getCurrentTrack,
    fetchQueue,
    checkLikedStatus as apiCheckLikedStatus,
    toggleLikeStatus,
    seekToPosition
} from './api.js';

// ========== SEEK STATE ==========
let seekTimeout = null;
let isDragging = false;
let previewPositionMs = null;
let seekTooltip = null;
const SEEK_DEBOUNCE_MS = 150;  // Match waveform (faster since drag prevents spam)

// ========== VISUAL MODE CALLBACKS ==========
// Stored during attachControlHandlers for use by toggleArtOnlyMode
let _enterVisualModeFn = null;
let _exitVisualModeFn = null;

/**
 * Toggle art-only mode on/off
 * Exported for keyboard shortcut module
 */
export function toggleArtOnlyMode() {
    const isArtOnly = document.body.classList.contains('art-only-mode');
    if (isArtOnly) {
        // Exit art-only mode (no toast - silent exit)
        document.body.classList.remove('art-only-mode');
        // Clear manual override when exiting
        setManualVisualModeOverride(false);
        // Disable zoom/pan and reset transform
        disableArtZoom();
    } else {
        // Set manual override FIRST (prevents auto-exit)
        setManualVisualModeOverride(true);
        // Enter visual mode (triggers auto-sharp background)
        if (!visualModeActive && _enterVisualModeFn) {
            _enterVisualModeFn();
        }
        // Then enter art-only mode (hides all UI including visual mode UI)
        document.body.classList.add('art-only-mode');
        // Enable zoom/pan
        enableArtZoom();
        // Brief toast (800ms)
        showToast('Art mode (pinch to zoom, long-press corners to exit)', 'success', 800);
    }
}

/**
 * Debounced seek - only sends API call after user stops interacting
 * 
 * @param {number} positionMs - Position to seek to in milliseconds
 */
function debouncedSeek(positionMs) {
    if (seekTimeout) clearTimeout(seekTimeout);
    
    seekTimeout = setTimeout(async () => {
        console.log(`[ProgressBar] Seeking to ${formatTime(positionMs / 1000)} (${positionMs}ms)`);
        try {
            const result = await seekToPosition(positionMs);
            if (result.error) {
                showToast('Seek failed', 'error');
            }
        } catch (error) {
            console.error('[ProgressBar] Seek error:', error);
        }
    }, SEEK_DEBOUNCE_MS);
}

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
        // Store callbacks at module level for toggleArtOnlyMode
        _enterVisualModeFn = enterVisualModeFn;
        _exitVisualModeFn = exitVisualModeFn;
        
        // Long-press state
        let longPressTimer = null;
        let isLongPress = false;
        const LONG_PRESS_DURATION = 500; // ms

        // Handle long-press to enter art-only mode
        const handlePressStart = (e) => {
            isLongPress = false;
            longPressTimer = setTimeout(() => {
                isLongPress = true;
                toggleArtOnlyMode();  // Use exported function
            }, LONG_PRESS_DURATION);
        };

        const handlePressEnd = (e) => {
            if (longPressTimer) {
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }
        };

        // Regular click handler (only fires if NOT long-press)
        visualModeBtn.addEventListener('click', (e) => {
            if (isLongPress) {
                isLongPress = false;
                return; // Ignore click after long-press
            }
            if (visualModeActive) {
                setManualVisualModeOverride(false);
                exitVisualModeFn();
            } else {
                setManualVisualModeOverride(true);
                enterVisualModeFn();
            }
        });

        // Long-press events (mouse + touch)
        visualModeBtn.addEventListener('mousedown', handlePressStart);
        visualModeBtn.addEventListener('mouseup', handlePressEnd);
        visualModeBtn.addEventListener('mouseleave', handlePressEnd);
        visualModeBtn.addEventListener('touchstart', handlePressStart, { passive: true });
        visualModeBtn.addEventListener('touchend', handlePressEnd);
        visualModeBtn.addEventListener('touchcancel', handlePressEnd);

        // Corner-based exit for art-only mode
        // Long-press (750ms) in any corner to exit - no conflict with pan/zoom
        const CORNER_SIZE = 100;           // pixels from corner
        const CORNER_HOLD_DURATION = 450; // ms
        const EDGE_TAP_SIZE = 50;         // pixels from left/right edge for image switching
        
        let cornerExitTimer = null;

        const isInCorner = (x, y) => {
            const w = window.innerWidth;
            const h = window.innerHeight;
            const inLeft = x < CORNER_SIZE;
            const inRight = x > w - CORNER_SIZE;
            const inTop = y < CORNER_SIZE;
            const inBottom = y > h - CORNER_SIZE;
            return (inLeft || inRight) && (inTop || inBottom);
        };

        const isOnLeftEdge = (x) => x < EDGE_TAP_SIZE;
        const isOnRightEdge = (x) => x > window.innerWidth - EDGE_TAP_SIZE;

        const clearCornerTimer = () => {
            if (cornerExitTimer) {
                clearTimeout(cornerExitTimer);
                cornerExitTimer = null;
            }
        };

        const handleCornerPress = (x, y) => {
            if (!document.body.classList.contains('art-only-mode')) return;
            
            if (isInCorner(x, y)) {
                cornerExitTimer = setTimeout(() => {
                    if (document.body.classList.contains('art-only-mode')) {
                        toggleArtOnlyMode();
                    }
                }, CORNER_HOLD_DURATION);
            }
        };

        // Mouse events for corner exit
        document.addEventListener('mousedown', (e) => {
            handleCornerPress(e.clientX, e.clientY);
        });
        document.addEventListener('mouseup', clearCornerTimer);
        document.addEventListener('mouseleave', clearCornerTimer);

        // Touch events for corner exit
        document.addEventListener('touchstart', (e) => {
            if (e.touches.length === 1) {
                handleCornerPress(e.touches[0].clientX, e.touches[0].clientY);
            }
        }, { passive: true });
        document.addEventListener('touchend', clearCornerTimer);
        document.addEventListener('touchcancel', clearCornerTimer);
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

    // Enable controls for Spotify, Spotify Hybrid, Spicetify, Windows Media, or plugin sources
    // Note: Audio Recognition source does not support playback controls
    const canControl =
        trackInfo.source === 'spotify' ||
        trackInfo.source === 'spotify_hybrid' ||
        trackInfo.source === 'spicetify' ||
        trackInfo.source === 'windows_media' ||
        trackInfo.source === 'music_assistant' ||
        trackInfo.source === 'linux';

    if (prevBtn) prevBtn.disabled = !canControl;
    if (nextBtn) nextBtn.disabled = !canControl;
    if (playPauseBtn) {
        playPauseBtn.disabled = !canControl;
        const isPlaying = trackInfo.is_playing === true;
        const icon = playPauseBtn.querySelector('i');

        if (icon) {
            if (isPlaying) {
                icon.className = 'bi bi-pause-fill';
                playPauseBtn.title = 'Pause';
            } else {
                icon.className = 'bi bi-play-fill';
                playPauseBtn.title = 'Play';
            }
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

    // Only hide if position is undefined (no data at all)
    // Still show progress bar when duration is 0 - at least position is visible
    if (trackInfo.position === undefined) {
        if (progressContainer) progressContainer.style.display = 'none';
        return;
    }

    if (progressContainer) progressContainer.style.display = 'block';

    // Calculate percent (handle duration_ms = 0 gracefully)
    const durationMs = trackInfo.duration_ms || 0;
    const percent = durationMs > 0 
        ? Math.min(100, (trackInfo.position * 1000 / durationMs) * 100)
        : 0;  // No progress if duration unknown
    if (fill) fill.style.width = `${percent}%`;

    if (currentTime) currentTime.textContent = formatTime(trackInfo.position);
    if (totalTime) totalTime.textContent = formatTime(durationMs / 1000);
}

/**
 * Attach seek handler to progress bar
 * Full-featured implementation matching waveform.js:
 * - Click-to-seek
 * - Drag-to-scrub with visual preview
 * - Touch support for mobile/tablet
 * - Time tooltip during hover/drag
 */
export function attachProgressBarSeek() {
    const progressBar = document.getElementById('progress-bar');
    const progressFill = document.getElementById('progress-fill');
    if (!progressBar) return;
    
    // Create tooltip element if it doesn't exist
    if (!seekTooltip) {
        seekTooltip = document.createElement('div');
        seekTooltip.className = 'seek-tooltip';
        seekTooltip.style.cssText = `
            position: fixed;
            background: rgba(0, 0, 0, 0.9);
            color: white;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 600;
            pointer-events: none;
            z-index: 10000;
            display: none;
            transform: translateX(-50%);
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
            white-space: nowrap;
        `;
        document.body.appendChild(seekTooltip);
    }
    
    // Make it clickable
    progressBar.style.cursor = 'pointer';
    progressBar.style.touchAction = 'none';  // Prevent touch scrolling on the bar
    
    // Get client position from mouse or touch event
    const getClientPos = (e) => {
        if (e.touches && e.touches.length > 0) {
            return { x: e.touches[0].clientX, y: e.touches[0].clientY };
        }
        if (e.changedTouches && e.changedTouches.length > 0) {
            return { x: e.changedTouches[0].clientX, y: e.changedTouches[0].clientY };
        }
        return { x: e.clientX, y: e.clientY };
    };
    
    // Calculate seek position from client coordinates
    const calculateSeekPosition = (clientX) => {
        const rect = progressBar.getBoundingClientRect();
        const percent = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        const duration = lastTrackInfo?.duration_ms || 0;
        return percent * duration;  // Return in ms
    };
    
    // Show tooltip at position
    const showTooltip = (clientX, clientY, positionMs) => {
        const timeStr = formatTime(positionMs / 1000);
        seekTooltip.textContent = timeStr;
        seekTooltip.style.display = 'block';
        seekTooltip.style.left = `${clientX}px`;
        // Position tooltip above the touch/cursor
        const offset = isDragging ? 50 : 35;
        seekTooltip.style.top = `${clientY - offset}px`;
    };
    
    // Hide tooltip
    const hideTooltip = () => {
        seekTooltip.style.display = 'none';
    };
    
    // Update visual preview during drag
    const updateVisualPreview = (positionMs) => {
        if (!progressFill) return;
        const duration = lastTrackInfo?.duration_ms || 0;
        if (!duration) return;
        const percent = Math.min(100, (positionMs / duration) * 100);
        progressFill.style.width = `${percent}%`;
    };
    
    // Track if we already sought (to prevent click from also firing after drag)
    let didSeek = false;
    
    // ========== POINTER START (mousedown / touchstart) ==========
    const handlePointerStart = (e) => {
        const duration = lastTrackInfo?.duration_ms || 0;
        if (!duration) return;
        e.preventDefault();
        
        const pos = getClientPos(e);
        isDragging = true;
        didSeek = false;
        previewPositionMs = calculateSeekPosition(pos.x);
        showTooltip(pos.x, pos.y, previewPositionMs);
        updateVisualPreview(previewPositionMs);
    };
    
    // ========== POINTER MOVE (mousemove / touchmove) ==========
    const handlePointerMove = (e) => {
        const duration = lastTrackInfo?.duration_ms || 0;
        if (!duration) return;
        
        const pos = getClientPos(e);
        const hoverPositionMs = calculateSeekPosition(pos.x);
        
        // Always show tooltip on move (hover or drag)
        showTooltip(pos.x, pos.y, hoverPositionMs);
        
        // Update visual preview if dragging
        if (isDragging) {
            e.preventDefault();
            previewPositionMs = hoverPositionMs;
            updateVisualPreview(previewPositionMs);
        }
    };
    
    // ========== POINTER END (mouseup / touchend) ==========
    const handlePointerEnd = (e) => {
        const duration = lastTrackInfo?.duration_ms || 0;
        if (!duration) return;
        
        if (isDragging && previewPositionMs !== null) {
            debouncedSeek(previewPositionMs);
            didSeek = true;
        }
        
        isDragging = false;
        previewPositionMs = null;
        hideTooltip();
    };
    
    // ========== POINTER CANCEL (touchcancel) ==========
    const handlePointerCancel = () => {
        isDragging = false;
        previewPositionMs = null;
        hideTooltip();
    };
    
    // ========== MOUSE LEAVE ==========
    const handleMouseLeave = () => {
        if (!isDragging) {
            hideTooltip();
        }
    };
    
    // ========== CLICK (for simple tap/click without drag) ==========
    const handleClick = (e) => {
        const duration = lastTrackInfo?.duration_ms || 0;
        if (!duration) return;
        
        // Skip if we already seeked via drag
        if (didSeek) {
            didSeek = false;
            return;
        }
        
        const pos = getClientPos(e);
        const positionMs = calculateSeekPosition(pos.x);
        debouncedSeek(positionMs);
    };
    
    // ========== ATTACH PROGRESS BAR EVENTS ==========
    // Mouse events
    progressBar.addEventListener('mousedown', handlePointerStart);
    progressBar.addEventListener('mousemove', handlePointerMove);
    progressBar.addEventListener('mouseleave', handleMouseLeave);
    progressBar.addEventListener('click', handleClick);
    
    // Touch events
    progressBar.addEventListener('touchstart', handlePointerStart, { passive: false });
    progressBar.addEventListener('touchmove', handlePointerMove, { passive: false });
    progressBar.addEventListener('touchend', handlePointerEnd);
    progressBar.addEventListener('touchcancel', handlePointerCancel);
    
    // ========== GLOBAL END EVENTS (for drag completion outside bar) ==========
    document.addEventListener('mouseup', (e) => {
        if (isDragging) {
            handlePointerEnd(e);
        }
    });
    
    document.addEventListener('touchend', (e) => {
        if (isDragging) {
            handlePointerEnd(e);
        }
    }, { passive: true });
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
    const albumEl = document.getElementById('track-album');

    if (titleEl) titleEl.textContent = trackInfo.title || 'Unknown Track';
    if (artistEl) artistEl.textContent = trackInfo.artist || 'Unknown Artist';
    if (albumEl) {
        albumEl.textContent = trackInfo.album || '';
        albumEl.style.display = displayConfig.showAlbumName && trackInfo.album ? 'block' : 'none';
    }
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
        // SOLUTION 3: Compare by path first (most stable), fall back to URL
        // Path is stable across enrichment (remote->local transition), URL changes format
        // This prevents flicker when enrichment replaces remote URL with local /cover-art URL
        const currentArtKey = trackInfo.album_art_path || trackInfo.album_art_url;
        const lastArtKey = lastAlbumArtPath || lastAlbumArtUrl;
        
        // Check if the art is the same - if so, skip reload but still run visibility logic
        const artUnchanged = (currentArtKey && currentArtKey === lastArtKey);
        
        if (artUnchanged) {
            // FIX 1: Cancel any pending loads from intermediate skips (e.g. A -> B -> A)
            // If we are back to the "stable" image, we don't want a pending "B" image to overwrite it later.
            setPendingArtUrl(null);
            
            // FIX 2: Ensure opacity is 1 (could be 0 if transition was interrupted)
            albumArt.style.opacity = '1';
            albumArt.style.display = displayConfig.showAlbumArt ? 'block' : 'none';
            // NOTE: Don't return here - we still need to run visibility logic below
        } else {
            // Art changed - normalize URL for consistent comparison
            // NOTE: No cache buster needed! Backend provides t=mtime param for cache invalidation.
            // Adding Date.now() cache buster caused a race condition where each 100ms poll
            // created a different URL, causing in-flight loads to be orphaned.
            let targetUrl = new URL(trackInfo.album_art_url, window.location.href).href;

            // Prevent duplicate loads if already loading this URL
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

                        // Store path and URL after successful load for future comparison
                        // Path is primary (stable), URL is fallback
                        setLastAlbumArtPath(trackInfo.album_art_path || null);
                        setLastAlbumArtUrl(trackInfo.album_art_url);

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
        }
    } else {
        // No album art URL provided
        if (!pendingArtUrl) {
            albumArt.style.display = 'none';
        }
        // FIX 3: Clear both path and URL when no art, for cleanliness
        setLastAlbumArtPath(null);
        setLastAlbumArtUrl(null);
    }

    // FIX 4: Visibility logic ALWAYS runs (no early return above)
    // Set individual element visibility independently
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
            // Limit queue items on mobile for cleaner display
            const isMobile = window.matchMedia('(max-width: 600px)').matches;
            const maxItems = isMobile ? 13 : data.queue.length;
            const displayQueue = data.queue.slice(0, maxItems);
            
            displayQueue.forEach(track => {
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
 * @param {string} trackId - Track ID (Spotify ID or MA item_id)
 * @param {string} source - Optional source ('music_assistant' for MA routing)
 */
export async function checkLikedStatus(trackId, source = '') {
    if (!trackId) return;
    try {
        const data = await apiCheckLikedStatus(trackId, source);

        // Ensure we are still playing the same track
        // Check both id (Spotify) and ma_item_id (Music Assistant)
        const currentId = lastTrackInfo?.id || lastTrackInfo?.ma_item_id;
        if (lastTrackInfo && currentId === trackId) {
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

    const icon = btn.querySelector('i');
    if (icon) {
        if (isLiked) {
            icon.className = 'bi bi-heart-fill';
            btn.classList.add('liked');
        } else {
            icon.className = 'bi bi-heart';
            btn.classList.remove('liked');
        }
    }
}

/**
 * Toggle like status for current track
 * Works with both Spotify (id) and Music Assistant (ma_item_id)
 */
export async function toggleLike() {
    // Get track ID - use Spotify id or MA ma_item_id
    const trackId = lastTrackInfo?.id || lastTrackInfo?.ma_item_id;
    const source = lastTrackInfo?.source || '';
    
    if (!lastTrackInfo || !trackId) return;

    // Optimistic update
    setIsLiked(!isLiked);
    updateLikeButton();

    try {
        await toggleLikeStatus(trackId, isLiked ? 'like' : 'unlike', source);
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
