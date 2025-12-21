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
let isCanvasInitialized = false;

// ========== SEEK STATE ==========
let isDragging = false;        // True when user is dragging to scrub
let seekTimeout = null;        // Debounce timer for seek
let hoverPositionMs = null;    // Position at cursor in ms
let previewPositionMs = null;  // Position to preview during drag
const SEEK_DEBOUNCE_MS = 300;  // Trailing edge debounce delay

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
            renderWaveform(canvas, waveformData.waveform, 0); // Re-render on resize
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

    // Check if track changed (need to re-fetch waveform)
    const currentTrackId = trackInfo?.track_id;
    if (currentTrackId && currentTrackId !== waveformTrackId) {
        waveformTrackId = currentTrackId;
        console.debug('[Waveform] Track changed, fetching new waveform data');
        
        const data = await fetchWaveformData();
        if (data && data.waveform) {
            waveformData = data;
            waveformDuration = data.duration || trackInfo.duration_ms / 1000;
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
        
        const currentPosition = trackInfo.position || 0; // Position in seconds
        renderWaveform(canvas, waveformData.waveform, currentPosition);
        
        // Update waveform time display
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
 */
function renderWaveform(canvas, waveform, currentPosition) {
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
    // Increase this number for smoother waveforms (e.g., 300, 500)
    const TARGET_BAR_COUNT = 220;
    
    // Calculate how many source segments per target bar
    const segmentSize = waveform.length / TARGET_BAR_COUNT;
    
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

    // Track duration for played/unplayed calculation
    const trackDuration = waveform[waveform.length - 1].start;

    for (let i = 0; i < TARGET_BAR_COUNT; i++) {
        const x = i * barWidth;
        
        // Calculate which source segments this bar covers
        const startIdx = Math.floor(i * segmentSize);
        const endIdx = Math.min(Math.floor((i + 1) * segmentSize), waveform.length);
        
        // Average amplitude across grouped segments
        let ampSum = 0;
        const count = Math.max(1, endIdx - startIdx);
        
        for (let j = startIdx; j < endIdx; j++) {
            ampSum += waveform[j].amp || 0;
        }
        
        const avgAmp = ampSum / count;
        
        // Calculate bar height (bidirectional from center)
        const barHeight = avgAmp * maxBarHeight;
        const halfBarHeight = barHeight / 2;

        // Even distribution: bar i represents (i / TARGET_BAR_COUNT) of total duration
        // This ensures smooth, consistent progression regardless of segment distribution
        const barTimePosition = (i / TARGET_BAR_COUNT) * trackDuration;
        const isPlayed = barTimePosition <= currentPosition;

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
 * Reset waveform state (e.g., when switching tracks)
 */
export function resetWaveform() {
    waveformData = null;
    waveformDuration = 0;
    waveformTrackId = null;
    
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
            background: rgba(0, 0, 0, 0.85);
            color: white;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 500;
            pointer-events: none;
            z-index: 10000;
            display: none;
            transform: translateX(-50%);
        `;
        document.body.appendChild(seekTooltip);
    }
    
    // Set cursor to pointer
    canvas.style.cursor = 'pointer';
    
    // Calculate seek position from mouse event
    const calculateSeekPosition = (e) => {
        const rect = canvas.getBoundingClientRect();
        const percent = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        return percent * waveformDuration * 1000; // Return in ms
    };
    
    // Mouse down - start drag
    canvas.addEventListener('mousedown', (e) => {
        if (!waveformDuration) return;
        isDragging = true;
        previewPositionMs = calculateSeekPosition(e);
        updateVisualFeedback();
    });
    
    // Mouse move - update tooltip and preview
    canvas.addEventListener('mousemove', (e) => {
        if (!waveformDuration) return;
        
        hoverPositionMs = calculateSeekPosition(e);
        
        // Show tooltip
        const timeStr = formatTime(hoverPositionMs / 1000);
        seekTooltip.textContent = timeStr;
        seekTooltip.style.display = 'block';
        seekTooltip.style.left = `${e.clientX}px`;
        seekTooltip.style.top = `${e.clientY - 30}px`;
        
        // Update visual preview if dragging
        if (isDragging) {
            previewPositionMs = hoverPositionMs;
            updateVisualFeedback();
        }
    });
    
    // Mouse up - perform seek
    canvas.addEventListener('mouseup', (e) => {
        if (!waveformDuration || !isDragging) return;
        
        const finalPositionMs = calculateSeekPosition(e);
        debouncedSeek(finalPositionMs);
        
        isDragging = false;
        previewPositionMs = null;
    });
    
    // Mouse leave - hide tooltip, cancel drag
    canvas.addEventListener('mouseleave', () => {
        seekTooltip.style.display = 'none';
        isDragging = false;
        previewPositionMs = null;
    });
    
    // Click handler for simple click-to-seek (fallback)
    canvas.addEventListener('click', (e) => {
        if (!waveformDuration) return;
        
        const positionMs = calculateSeekPosition(e);
        debouncedSeek(positionMs);
    });
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
    renderWaveform(canvas, waveformData.waveform, previewPositionMs / 1000);
}
