/**
 * spectrum.js - Spectrum Analyzer Visualizer
 * 
 * Renders a full-width spectrum visualizer behind content.
 * Uses pitch data from Spotify's audio analysis segments.
 * Stretches horizontally across the app, centered around the seekbar area.
 * 
 * Level 2 - Imports: state
 */

import { displayConfig } from './state.js';

// ========== SPECTRUM CONFIGURATION ==========
// These values are easily adjustable for future customization
const CONFIG = {
    // Visual appearance
    barCount: 12,                    // Number of frequency bars (matches pitches array)
    barGap: 4,                       // Gap between bars in pixels
    minBarHeight: 2,                 // Minimum bar height in pixels
    maxHeightPercent: 0.9,           // Max bar height as % of container
    
    // Animation (faster response to beats)
    smoothingFactor: 0.15,           // 0-1, lower = faster response to changes
    decayRate: 0.85,                 // How fast bars decay when no data (lower = faster decay)
    
    // Colors (more transparent to not block content)
    barColor: 'rgba(255, 255, 255, 0.12)',      // Base bar color - very transparent
    barColorActive: 'rgba(255, 255, 255, 0.2)', // Color when segment is active
    gradientStart: 'rgba(255, 255, 255, 0.08)', // Gradient top
    gradientEnd: 'rgba(255, 255, 255, 0.02)',   // Gradient bottom (nearly transparent)
    
    // Positioning
    verticalOffset: 0                // Pixels to offset from center
};

// ========== SPECTRUM STATE ==========
let spectrumData = null;           // Cached audio analysis data
let spectrumDuration = 0;          // Track duration
let spectrumTrackId = null;        // Track ID for change detection
let currentBarHeights = null;      // Current animated bar heights (for smoothing)
let animationFrameId = null;       // Animation frame ID for cleanup
let isSpectrumInitialized = false;

/**
 * Fetch audio analysis data for spectrum visualization
 * 
 * @returns {Promise<Object|null>} Audio analysis data or null
 */
async function fetchSpectrumData() {
    try {
        const response = await fetch('/api/playback/audio-analysis');
        if (!response.ok) {
            console.debug('[Spectrum] Audio analysis not available');
            return null;
        }
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('[Spectrum] Failed to fetch audio analysis:', error);
        return null;
    }
}

/**
 * Initialize the spectrum canvas
 */
export function initSpectrum() {
    const canvas = document.getElementById('spectrum-canvas');
    if (!canvas) {
        console.debug('[Spectrum] Canvas element not found');
        return;
    }

    // Initialize bar heights array
    currentBarHeights = new Array(CONFIG.barCount).fill(0);

    // Set canvas size
    resizeSpectrumCanvas(canvas);

    // Handle window resize
    window.addEventListener('resize', () => {
        resizeSpectrumCanvas(canvas);
    });

    isSpectrumInitialized = true;
    console.debug('[Spectrum] Canvas initialized');
}

/**
 * Resize spectrum canvas to fit container
 * 
 * @param {HTMLCanvasElement} canvas - The canvas element
 */
function resizeSpectrumCanvas(canvas) {
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

    // Scale context
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
}

/**
 * Update spectrum visualization with current track progress
 * 
 * @param {Object} trackInfo - Current track information
 */
export async function updateSpectrum(trackInfo) {
    if (!displayConfig.showSpectrum) {
        hideSpectrum();
        return;
    }

    const canvas = document.getElementById('spectrum-canvas');
    const container = document.getElementById('spectrum-container');

    if (!canvas || !container) return;

    // Show container
    container.style.display = 'block';

    // Check if track changed
    const currentTrackId = trackInfo?.track_id;
    if (currentTrackId && currentTrackId !== spectrumTrackId) {
        spectrumTrackId = currentTrackId;
        console.debug('[Spectrum] Track changed, fetching new data');
        
        const data = await fetchSpectrumData();
        if (data && data.waveform) {
            spectrumData = data;
            spectrumDuration = data.duration || trackInfo.duration_ms / 1000;
        } else {
            spectrumData = null;
            spectrumDuration = 0;
        }
    }

    // Initialize if needed
    if (!isSpectrumInitialized) {
        initSpectrum();
    }

    // Find current segment and extract pitch data
    const currentPosition = trackInfo.position || 0;
    const pitchData = getCurrentPitchData(currentPosition);

    // Render spectrum
    renderSpectrum(canvas, pitchData);
}

/**
 * Get pitch data for the current playback position
 * 
 * @param {number} position - Current position in seconds
 * @returns {Array} 12-element array of pitch values (0-1)
 */
function getCurrentPitchData(position) {
    if (!spectrumData || !spectrumData.waveform) {
        return new Array(CONFIG.barCount).fill(0);
    }

    // Find the segment that contains the current position
    // We need to also get the pitch data from segments
    // Since our API returns waveform data, we need to re-fetch or extend the API
    // For now, simulate using the amplitude data with some variation
    
    const waveform = spectrumData.waveform;
    let currentSegment = null;
    
    for (let i = 0; i < waveform.length; i++) {
        const seg = waveform[i];
        const nextStart = i < waveform.length - 1 ? waveform[i + 1].start : spectrumDuration;
        
        if (position >= seg.start && position < nextStart) {
            currentSegment = seg;
            break;
        }
    }

    // If we found a segment, use its amplitude to drive the visualization
    // Generate a balanced frequency distribution across all bars
    const baseAmp = currentSegment ? currentSegment.amp : 0;
    
    // Create a balanced frequency distribution (not bass-heavy)
    const pitches = new Array(CONFIG.barCount);
    for (let i = 0; i < CONFIG.barCount; i++) {
        // Balanced distribution: slight variation across frequencies
        // Each bar gets roughly equal energy with some random variation for visual interest
        const baseLevel = 0.7 + (Math.sin(i * 0.5) * 0.3);  // Slight wave pattern
        const noise = 0.7 + Math.random() * 0.6;  // More variance for reactivity
        
        pitches[i] = Math.min(1, baseAmp * baseLevel * noise);
    }
    
    return pitches;
}

/**
 * Render the spectrum visualization
 * 
 * @param {HTMLCanvasElement} canvas - The canvas element
 * @param {Array} pitchData - 12-element array of pitch values
 */
function renderSpectrum(canvas, pitchData) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.width / dpr;
    const height = canvas.height / dpr;

    // Clear canvas
    ctx.clearRect(0, 0, width, height);

    if (!pitchData || pitchData.length === 0) return;

    // Calculate bar dimensions
    const totalBarSpace = width - (CONFIG.barGap * (CONFIG.barCount + 1));
    const barWidth = totalBarSpace / CONFIG.barCount;
    const maxBarHeight = height * CONFIG.maxHeightPercent;

    // Apply smoothing to bar heights
    if (!currentBarHeights) {
        currentBarHeights = new Array(CONFIG.barCount).fill(0);
    }

    for (let i = 0; i < CONFIG.barCount; i++) {
        const targetHeight = pitchData[i] * maxBarHeight;
        
        // Smooth transition: rise quickly, fall slowly
        if (targetHeight > currentBarHeights[i]) {
            // Rise: more responsive
            currentBarHeights[i] = currentBarHeights[i] + 
                (targetHeight - currentBarHeights[i]) * (1 - CONFIG.smoothingFactor);
        } else {
            // Fall: smoother decay
            currentBarHeights[i] = currentBarHeights[i] * CONFIG.decayRate + 
                targetHeight * (1 - CONFIG.decayRate);
        }
        
        // Enforce minimum height
        currentBarHeights[i] = Math.max(CONFIG.minBarHeight, currentBarHeights[i]);
    }

    // Draw bars from bottom (growing upward)
    const bottomY = height;

    for (let i = 0; i < CONFIG.barCount; i++) {
        const x = CONFIG.barGap + i * (barWidth + CONFIG.barGap);
        const barHeight = currentBarHeights[i];

        // Create gradient for each bar (bottom to top)
        const gradient = ctx.createLinearGradient(x, bottomY, x, bottomY - barHeight);
        gradient.addColorStop(0, CONFIG.barColor);
        gradient.addColorStop(1, CONFIG.gradientEnd);

        ctx.fillStyle = gradient;
        
        // Draw rounded rectangle bar (from bottom upward)
        const cornerRadius = Math.min(4, barWidth / 4);
        drawRoundedRect(ctx, x, bottomY - barHeight, barWidth, barHeight, cornerRadius);
    }
}

/**
 * Draw a rounded rectangle
 * 
 * @param {CanvasRenderingContext2D} ctx - Canvas context
 * @param {number} x - X position
 * @param {number} y - Y position
 * @param {number} width - Width
 * @param {number} height - Height
 * @param {number} radius - Corner radius
 */
function drawRoundedRect(ctx, x, y, width, height, radius) {
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
 * Hide the spectrum container
 */
export function hideSpectrum() {
    const container = document.getElementById('spectrum-container');
    if (container) {
        container.style.display = 'none';
    }
    
    // Cancel any pending animation
    if (animationFrameId) {
        cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
    }
}

/**
 * Reset spectrum state (e.g., when switching tracks)
 */
export function resetSpectrum() {
    spectrumData = null;
    spectrumDuration = 0;
    spectrumTrackId = null;
    currentBarHeights = new Array(CONFIG.barCount).fill(0);
    
    const canvas = document.getElementById('spectrum-canvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    }
}

/**
 * Get current configuration (for debugging/tuning)
 * 
 * @returns {Object} Current spectrum configuration
 */
export function getSpectrumConfig() {
    return { ...CONFIG };
}

/**
 * Update configuration values at runtime
 * Useful for tuning the visualizer without code changes
 * 
 * @param {Object} newConfig - Partial config object with values to update
 */
export function setSpectrumConfig(newConfig) {
    Object.assign(CONFIG, newConfig);
    console.debug('[Spectrum] Config updated:', CONFIG);
}
