/**
 * waveform.js - Waveform Seekbar Visualization
 * 
 * Renders a waveform visualization of the audio analysis data.
 * Shows played portion in light grey, unplayed in dark grey.
 * 
 * Level 2 - Imports: state, api
 */

import { displayConfig } from './state.js';

// ========== WAVEFORM STATE ==========
let waveformData = null;       // Cached waveform data from API
let waveformDuration = 0;      // Track duration in seconds
let waveformTrackId = null;    // Track ID to detect song changes
let isCanvasInitialized = false;

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

    // Scale context to match
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
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

    // Render waveform with current progress
    if (waveformData && waveformData.waveform) {
        const currentPosition = trackInfo.position || 0; // Position in seconds
        renderWaveform(canvas, waveformData.waveform, currentPosition);
    } else {
        // No waveform data - show fallback progress bar style
        renderFallback(canvas, trackInfo);
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

    if (!waveform || waveform.length === 0) return;

    // Calculate bar width based on number of segments and canvas width
    const barCount = waveform.length;
    const barWidth = width / barCount;
    const barGap = Math.max(0.5, barWidth * 0.1); // Small gap between bars
    const effectiveBarWidth = barWidth - barGap;

    // Colors (matching MusicBee style)
    const unplayedColor = 'rgba(60, 60, 60, 0.8)';    // Dark grey for unplayed
    const playedColor = 'rgba(180, 180, 180, 0.95)';  // Light grey for played

    // Center line for waveform
    const centerY = height / 2;
    const maxBarHeight = height * 0.9;  // 90% of height for max amplitude

    for (let i = 0; i < waveform.length; i++) {
        const segment = waveform[i];
        const x = i * barWidth;
        const amp = segment.amp || 0;
        
        // Calculate bar height (bidirectional from center)
        const barHeight = amp * maxBarHeight;
        const halfBarHeight = barHeight / 2;

        // Determine if this segment has been played
        const segmentMidpoint = segment.start + (i < waveform.length - 1 
            ? (waveform[i + 1].start - segment.start) / 2 
            : 0);
        const isPlayed = segmentMidpoint <= currentPosition;

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

    // Draw simple progress bar
    const barHeight = height * 0.3;
    const y = (height - barHeight) / 2;

    // Unplayed portion
    ctx.fillStyle = 'rgba(60, 60, 60, 0.8)';
    ctx.fillRect(0, y, width, barHeight);

    // Played portion
    ctx.fillStyle = 'rgba(180, 180, 180, 0.95)';
    ctx.fillRect(0, y, width * progress, barHeight);
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
