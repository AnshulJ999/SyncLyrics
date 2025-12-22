/**
 * waveform.js - Waveform Seekbar Visualization
 * 
 * Renders a waveform visualization of the audio analysis data.
 * Shows played portion in light grey, unplayed in dark grey.
 * 
 * Level 2 - Imports: state, api
 */

import { displayConfig } from './state.js';
import { formatTime } from './utils.js';
import { seekToPosition } from './api.js';

// ========== WAVEFORM STATE ==========
let waveformData = null;       // Cached waveform data from API
let waveformDuration = 0;      // Track duration in seconds
let waveformTrackId = null;    // Track ID to detect song changes
let waveformSource = null;     // Source to detect source changes (Spotify -> MusicBee)
let isCanvasInitialized = false;

// ========== SEEK STATE ==========
let isDragging = false;        // True when user is dragging to scrub
let seekTimeout = null;        // Debounce timer for seek
let hoverPositionMs = null;    // Position at cursor in ms
let previewPositionMs = null;  // Position to preview during drag
const SEEK_DEBOUNCE_MS = 150;  // Trailing edge debounce delay (faster since drag prevents spam)

// Tooltip element (created once)
let seekTooltip = null;

/**
 * Fetch waveform data from the backend API
 * 
 * @returns {Promise<Object|null>} Waveform data or null if unavailable
 */
async function fetchWaveformData() {
    try {
        const response = await fetch('/api/playback/audio-analysis');
        if (!response.ok) {
            // Analysis not available (likely not using Spicetify)
            console.debug('[Waveform] Audio analysis not available');
            return null;
        }
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('[Waveform] Failed to fetch audio analysis:', error);
        return null;
    }
}

/**
 * Process raw segments into waveform data (amplitude per segment)
 * Converts loudness dB to normalized 0-1 amplitude values
 * 
 * @param {Array} segments - Raw segments from audio analysis
 * @returns {Array} Waveform array with {start, duration, amp} objects
 */
function processSegmentsToWaveform(segments) {
    if (!segments || segments.length === 0) return [];
    
    // Calculate amplitude from loudness (dB to linear)
    let maxAmp = 0;
    const waveform = segments.map(seg => {
        const loudStart = Math.max(seg.loudness_start || -60, -60);  // Floor at -60dB
        const loudMax = Math.max(seg.loudness_max || -60, -60);
        const avgDb = (loudStart + loudMax) / 2;
        const amp = Math.pow(10, avgDb / 20);  // dB to linear amplitude
        maxAmp = Math.max(maxAmp, amp);
        return {
            start: seg.start,
            duration: seg.duration || 0,
            amp: amp  // Will normalize after
        };
    });
    
    // Normalize amplitudes to 0-1 range
    if (maxAmp > 0) {
        for (const w of waveform) {
            w.amp = w.amp / maxAmp;
        }
    }
    
    return waveform;
}

/**
 * Initialize the waveform canvas
 * Sets up canvas sizing and event listeners
 */
export function initWaveform() {
    const canvas = document.getElementById('waveform-canvas');
    if (!canvas) {
        console.debug('[Waveform] Canvas element not found');
        return;
    }

    // Set canvas size to match container
    resizeCanvas(canvas);

    // Handle window resize
    window.addEventListener('resize', () => {
        resizeCanvas(canvas);
        if (waveformData) {
            const duration = waveformData.duration || waveformDuration;
            renderWaveform(canvas, waveformData.waveform, 0, duration); // Re-render on resize
        }
    });

    // Initialize seek interaction (click/drag to seek)
    initSeekInteraction(canvas);

    isCanvasInitialized = true;
    console.debug('[Waveform] Canvas initialized');
}

/**
 * Resize canvas to match container dimensions
 * Uses devicePixelRatio for crisp rendering on high-DPI displays
 * 
 * @param {HTMLCanvasElement} canvas - The canvas element
 */
function resizeCanvas(canvas) {
    const container = canvas.parentElement;
    if (!container) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();

    // Set display size
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;

    // Set actual canvas size (scaled for high DPI)
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;

    // FIX: Use setTransform instead of scale to prevent accumulation on resize
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

/**
 * Update waveform display with current track progress
 * 
 * @param {Object} trackInfo - Current track information with position and duration
 */
export async function updateWaveform(trackInfo) {
    if (!displayConfig.showWaveform) return;

    const canvas = document.getElementById('waveform-canvas');
    const container = document.getElementById('waveform-container');

    if (!canvas || !container) return;

    // Show/hide container based on config
    container.style.display = 'block';

    // Check if track OR source changed (need to re-fetch waveform)
    const currentTrackId = trackInfo?.track_id;
    const currentSource = trackInfo?.source;
    const trackChanged = currentTrackId && currentTrackId !== waveformTrackId;
    const sourceChanged = currentSource && currentSource !== waveformSource;
    
    if (trackChanged || sourceChanged) {
        waveformTrackId = currentTrackId;
        waveformSource = currentSource;
        console.debug(`[Waveform] ${trackChanged ? 'Track' : 'Source'} changed, fetching new waveform data`);
        
        const data = await fetchWaveformData();
        const analysis = data?.audio_analysis;
        if (data && analysis && analysis.segments) {
            // CRITICAL: Validate analysis belongs to current track
            // This prevents stale Spotify waveform from showing over MusicBee songs
            // The analysis_track_id is a normalized "artist_title" string
            if (data.analysis_track_id && currentTrackId && data.analysis_track_id !== currentTrackId) {
                console.debug(`[Waveform] Analysis track mismatch (${data.analysis_track_id} vs ${currentTrackId}), ignoring`);
                waveformData = null;
                waveformDuration = 0;
            } else {
                // Process segments into waveform locally
                const waveform = processSegmentsToWaveform(analysis.segments);
                waveformData = {
                    waveform: waveform,
                    duration: analysis.duration || trackInfo.duration_ms / 1000,
                    analysis_track_id: data.analysis_track_id,
                    // Store full analysis for potential future use
                    audio_analysis: analysis
                };
                waveformDuration = waveformData.duration;
                console.debug(`[Waveform] Processed ${waveform.length} segments, duration: ${waveformData.duration.toFixed(1)}s`);
            }
        } else {
            waveformData = null;
            waveformDuration = 0;
        }
    }

    // Initialize canvas if needed
    if (!isCanvasInitialized) {
        initWaveform();
    }

    // Get the regular progress container
    const progressContainer = document.getElementById('progress-container');

    // Render waveform with current progress OR fallback to real progress bar
    if (waveformData && waveformData.waveform) {
        // Has audio data - show waveform, hide progress bar
        container.style.display = 'block';
        if (progressContainer) progressContainer.style.display = 'none';
        
        // IMPORTANT: Skip render if user is dragging (prevents flicker)
        // The visual feedback is handled by updateVisualFeedback() during drag
        if (!isDragging) {
            const currentPosition = trackInfo.position || 0; // Position in seconds
            const duration = waveformData.duration || trackInfo.duration_ms / 1000;
            renderWaveform(canvas, waveformData.waveform, currentPosition, duration);
        }
        
        // Update waveform time display (always, even during drag)
        const currentTimeEl = document.getElementById('waveform-current-time');
        const totalTimeEl = document.getElementById('waveform-total-time');
        if (currentTimeEl) {
            currentTimeEl.textContent = formatTime(trackInfo.position || 0);
        }
        if (totalTimeEl) {
            totalTimeEl.textContent = formatTime(trackInfo.duration_ms / 1000);
        }
    } else {
        // No audio data - hide waveform, show real progress bar
        container.style.display = 'none';
        if (progressContainer) progressContainer.style.display = 'block';
        
        // Manually update the real progress bar (since showProgress might be false)
        const fill = document.getElementById('progress-fill');
        const currentTime = document.getElementById('current-time');
        const totalTime = document.getElementById('total-time');
        
        if (trackInfo.duration_ms && trackInfo.position !== undefined) {
            const percent = Math.min(100, (trackInfo.position * 1000 / trackInfo.duration_ms) * 100);
            if (fill) fill.style.width = `${percent}%`;
            if (currentTime) currentTime.textContent = formatTime(trackInfo.position);
            if (totalTime) totalTime.textContent = formatTime(trackInfo.duration_ms / 1000);
        }
    }
}

/**
 * Render the waveform visualization
 * 
 * @param {HTMLCanvasElement} canvas - The canvas element
 * @param {Array} waveform - Array of {start, amp} objects
 * @param {number} currentPosition - Current playback position in seconds
 * @param {number} trackDuration - Total track duration in seconds
 */
function renderWaveform(canvas, waveform, currentPosition, trackDuration) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.width / dpr;
    const height = canvas.height / dpr;

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    // Reset context state to prevent stale values
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';

    if (!waveform || waveform.length === 0) return;

    // Fixed bar count for consistent appearance across all songs
    const TARGET_BAR_COUNT = 220;
    
    // Bar dimensions
    const barWidth = width / TARGET_BAR_COUNT;
    const barGap = Math.max(0.5, barWidth * 0.1); // Small gap between bars
    const effectiveBarWidth = barWidth - barGap;

    // Colors - Neutral greys (no tint)
    const unplayedColor = 'rgba(75, 75, 75, 1)';      // Dark grey (unplayed)
    const playedColor = 'rgba(180, 180, 180, 1)';     // Light grey (played)

    // Center line for waveform
    const centerY = height / 2;
    const maxBarHeight = height * 0.9;  // 90% of height for max amplitude

    // Use provided duration (from audio analysis API) - more accurate than last segment's start
    // Fallback to last segment's start if duration not provided
    const duration = trackDuration || waveform[waveform.length - 1].start;

    /**
     * Find segment at a given time using binary search
     * Returns the segment where segment.start <= time < next_segment.start
     */
    function findSegmentAtTime(time) {
        let lo = 0, hi = waveform.length - 1;
        while (lo < hi) {
            const mid = Math.floor((lo + hi + 1) / 2);
            if (waveform[mid].start <= time) lo = mid;
            else hi = mid - 1;
        }
        return waveform[lo];
    }

    for (let i = 0; i < TARGET_BAR_COUNT; i++) {
        const x = i * barWidth;
        
        // Bar represents this TIME position (uniform distribution for smooth playback)
        const barTime = (i / TARGET_BAR_COUNT) * duration;
        
        // Find segment at this time and get its amplitude (fixes position accuracy)
        const segment = findSegmentAtTime(barTime);
        const amp = segment.amp || 0;
        
        // Calculate bar height (bidirectional from center)
        const barHeight = amp * maxBarHeight;
        const halfBarHeight = barHeight / 2;

        // Played/unplayed based on current playback position
        const isPlayed = barTime <= currentPosition;

        // Set color based on played status
        ctx.fillStyle = isPlayed ? playedColor : unplayedColor;

        // Draw bar (centered vertically for waveform effect)
        ctx.fillRect(
            x + barGap / 2,
            centerY - halfBarHeight,
            effectiveBarWidth,
            barHeight
        );
    }
}

/**
 * Render fallback progress bar when waveform data is unavailable
 * Matches the regular progress bar styling (6px height, white colors)
 * 
 * @param {HTMLCanvasElement} canvas - The canvas element
 * @param {Object} trackInfo - Current track information
 */
function renderFallback(canvas, trackInfo) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.width / dpr;
    const height = canvas.height / dpr;

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    // Calculate progress
    const duration = trackInfo.duration_ms / 1000 || 1;
    const position = trackInfo.position || 0;
    const progress = Math.min(1, position / duration);

    // Match regular progress bar: 6px height, centered
    const barHeight = 6;
    const y = (height - barHeight) / 2;
    const radius = 4;  // Match .progress-bar border-radius

    // Background (unplayed) - matches .progress-bar CSS (faint)
    ctx.fillStyle = 'rgba(255, 255, 255, 0.15)';
    drawRoundedBar(ctx, 0, y, width, barHeight, radius);

    // Played portion - matches .progress-fill CSS (bright)
    if (progress > 0) {
        ctx.fillStyle = 'rgba(255, 255, 255, 0.95)';
        drawRoundedBar(ctx, 0, y, width * progress, barHeight, radius);
    }
}

/**
 * Draw a rounded rectangle bar
 */
function drawRoundedBar(ctx, x, y, width, height, radius) {
    if (width < radius * 2) radius = width / 2;
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + width - radius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
    ctx.lineTo(x + width, y + height - radius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    ctx.lineTo(x + radius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
    ctx.fill();
}

/**
 * Hide the waveform container
 */
export function hideWaveform() {
    const container = document.getElementById('waveform-container');
    if (container) {
        container.style.display = 'none';
    }
}

/**
 * Reset waveform state (e.g., when switching tracks or sources)
 */
export function resetWaveform() {
    waveformData = null;
    waveformDuration = 0;
    waveformTrackId = null;
    waveformSource = null;
    
    const canvas = document.getElementById('waveform-canvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    }
}
// ========== SEEK INTERACTION ==========

/**
 * Initialize seek interaction on the waveform canvas
 * Supports click-to-seek and drag-to-scrub with debouncing
 * Works with both mouse and touch events for tablet/mobile support
 * 
 * @param {HTMLCanvasElement} canvas - The waveform canvas element
 */
function initSeekInteraction(canvas) {
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
    
    // Set cursor to pointer
    canvas.style.cursor = 'pointer';
    // Enable touch scrolling prevention on the canvas
    canvas.style.touchAction = 'none';
    
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
        const rect = canvas.getBoundingClientRect();
        const percent = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
        return percent * waveformDuration * 1000; // Return in ms
    };
    
    // Show tooltip at position
    const showTooltip = (clientX, clientY, positionMs) => {
        const timeStr = formatTime(positionMs / 1000);
        seekTooltip.textContent = timeStr;
        seekTooltip.style.display = 'block';
        seekTooltip.style.left = `${clientX}px`;
        // Position tooltip above the touch/cursor, with more clearance for touch
        const offset = isDragging ? 50 : 35;
        seekTooltip.style.top = `${clientY - offset}px`;
    };
    
    // Hide tooltip
    const hideTooltip = () => {
        seekTooltip.style.display = 'none';
    };
    
    // Track if we already sought (to prevent click from also firing after drag)
    let didSeek = false;
    
    // ========== POINTER START (mousedown / touchstart) ==========
    const handlePointerStart = (e) => {
        if (!waveformDuration) return;
        e.preventDefault(); // Prevent scrolling on touch
        
        const pos = getClientPos(e);
        isDragging = true;
        didSeek = false;
        previewPositionMs = calculateSeekPosition(pos.x);
        showTooltip(pos.x, pos.y, previewPositionMs);
        updateVisualFeedback();
    };
    
    // ========== POINTER MOVE (mousemove / touchmove) ==========
    const handlePointerMove = (e) => {
        if (!waveformDuration) return;
        
        const pos = getClientPos(e);
        hoverPositionMs = calculateSeekPosition(pos.x);
        
        // Always show tooltip on move (hover or drag)
        showTooltip(pos.x, pos.y, hoverPositionMs);
        
        // Update visual preview if dragging
        if (isDragging) {
            e.preventDefault(); // Prevent scrolling during drag
            previewPositionMs = hoverPositionMs;
            updateVisualFeedback();
        }
    };
    
    // ========== POINTER END (mouseup / touchend) ==========
    const handlePointerEnd = (e) => {
        if (!waveformDuration) return;
        
        if (isDragging && previewPositionMs !== null) {
            // Seek to the drag end position
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
        // Don't cancel drag - wait for mouseup on document
    };
    
    // ========== CLICK (for simple tap/click without drag) ==========
    const handleClick = (e) => {
        if (!waveformDuration) return;
        
        // Skip if we already seeked via drag
        if (didSeek) {
            didSeek = false;
            return;
        }
        
        const pos = getClientPos(e);
        const positionMs = calculateSeekPosition(pos.x);
        debouncedSeek(positionMs);
    };
    
    // ========== ATTACH CANVAS EVENTS ==========
    // Mouse events
    canvas.addEventListener('mousedown', handlePointerStart);
    canvas.addEventListener('mousemove', handlePointerMove);
    canvas.addEventListener('mouseleave', handleMouseLeave);
    canvas.addEventListener('click', handleClick);
    
    // Touch events
    canvas.addEventListener('touchstart', handlePointerStart, { passive: false });
    canvas.addEventListener('touchmove', handlePointerMove, { passive: false });
    canvas.addEventListener('touchend', handlePointerEnd);
    canvas.addEventListener('touchcancel', handlePointerCancel);
    
    // ========== GLOBAL END EVENTS (for drag completion outside canvas) ==========
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

/**
 * Debounced seek - only sends API call after user stops interacting
 * Uses trailing edge: waits SEEK_DEBOUNCE_MS after last call before executing
 * 
 * @param {number} positionMs - Position to seek to in milliseconds
 */
function debouncedSeek(positionMs) {
    // Clear any pending seek
    if (seekTimeout) {
        clearTimeout(seekTimeout);
    }
    
    // Set new debounce timer (trailing edge)
    seekTimeout = setTimeout(async () => {
        console.log(`[Waveform] Seeking to ${formatTime(positionMs / 1000)} (${positionMs}ms)`);
        
        try {
            const result = await seekToPosition(positionMs);
            if (result.error) {
                console.error('[Waveform] Seek failed:', result.error);
            }
        } catch (error) {
            console.error('[Waveform] Seek error:', error);
        }
    }, SEEK_DEBOUNCE_MS);
}

/**
 * Update visual feedback during drag (preview seek position)
 * Re-renders waveform with the preview position instead of actual position
 */
function updateVisualFeedback() {
    if (previewPositionMs === null || !waveformData || !waveformData.waveform) return;
    
    const canvas = document.getElementById('waveform-canvas');
    if (!canvas) return;
    
    // Re-render with preview position
    const duration = waveformData.duration || waveformDuration;
    renderWaveform(canvas, waveformData.waveform, previewPositionMs / 1000, duration);
}

