/**
 * spectrum.js - Spectrum Analyzer Visualizer (60 FPS)
 * 
 * Renders a full-width spectrum visualizer behind content.
 * Uses pitch and timbre data from Spotify's audio analysis segments.
 * Runs at 60 FPS using requestAnimationFrame with position estimation.
 * 
 * Level 2 - Imports: state
 */

import { displayConfig } from './state.js';

// ========== SPECTRUM CONFIGURATION ==========
const CONFIG = {
    // Visual appearance
    barCount: 12,                    // Number of frequency bars (matches pitches array)
    barGap: 6,                       // Gap between bars in pixels
    minBarHeight: 3,                 // Minimum bar height in pixels
    maxHeightPercent: 0.85,          // Max bar height as % of container
    
    // Animation (tuned for 60 FPS)
    smoothingFactor: 0.25,           // 0-1, lower = faster response
    decayRate: 0.92,                 // How fast bars decay (higher = slower decay)
    
    // Colors (more visible)
    barColor: 'rgba(255, 255, 255, 0.35)',       // Base bar color
    barColorBright: 'rgba(255, 255, 255, 0.55)', // Brighter on beats
    gradientStart: 'rgba(255, 255, 255, 0.25)', // Gradient bottom
    gradientEnd: 'rgba(255, 255, 255, 0.05)',   // Gradient top (fade out)
    
    // Beat pulse
    beatPulseMultiplier: 0.6,        // How much beats boost the bars (0-1)
    beatDecay: 0.88                   // How fast beat pulse decays
};

// ========== SPECTRUM STATE ==========
let spectrumData = null;           // Cached audio analysis data
let spectrumDuration = 0;          // Track duration
let spectrumTrackId = null;        // Track ID for change detection
let currentBarHeights = null;      // Current animated bar heights (for smoothing)
let animationFrameId = null;       // Animation frame ID for cleanup
let isSpectrumInitialized = false;
let isAnimating = false;           // Is the animation loop running?

// Position estimation state
let lastKnownPosition = 0;         // Last position from main loop (seconds)
let lastPositionTime = 0;          // Timestamp when position was updated
let isPlaying = true;              // Whether playback is active

// Beat detection state
let lastBeatIndex = -1;            // Last beat we crossed
let beatPulse = 0;                 // Current beat pulse intensity (0-1)

// Current segment cache
let currentSegmentIndex = 0;       // Index of current segment (for faster lookup)
let currentTimbre = null;          // Current segment's timbre data

/**
 * Fetch audio analysis data for spectrum visualization
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
 */
function resizeSpectrumCanvas(canvas) {
    const container = canvas.parentElement;
    if (!container) return;

    const dpr = window.devicePixelRatio || 1;
    const rect = container.getBoundingClientRect();

    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;

    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
}

/**
 * Get estimated current position using time since last update
 */
function getEstimatedPosition() {
    if (!isPlaying) {
        return lastKnownPosition;
    }
    
    const elapsed = (performance.now() - lastPositionTime) / 1000;
    const estimated = lastKnownPosition + elapsed;
    
    // Clamp to track duration
    return Math.min(estimated, spectrumDuration);
}

/**
 * Start the 60 FPS animation loop
 */
function startAnimationLoop() {
    if (isAnimating) return;
    
    isAnimating = true;
    console.debug('[Spectrum] Starting 60 FPS animation loop');
    
    function animate() {
        if (!isAnimating || !displayConfig.showSpectrum) {
            isAnimating = false;
            return;
        }
        
        const canvas = document.getElementById('spectrum-canvas');
        if (!canvas) {
            isAnimating = false;
            return;
        }
        
        // Get estimated position
        const position = getEstimatedPosition();
        
        // Update beat pulse (decay each frame)
        updateBeatPulse(position);
        
        // Get pitch and timbre data for current position
        const pitchData = getCurrentPitchData(position);
        
        // Render
        renderSpectrum(canvas, pitchData);
        
        // Schedule next frame
        animationFrameId = requestAnimationFrame(animate);
    }
    
    animationFrameId = requestAnimationFrame(animate);
}

/**
 * Stop the animation loop
 */
function stopAnimationLoop() {
    isAnimating = false;
    if (animationFrameId) {
        cancelAnimationFrame(animationFrameId);
        animationFrameId = null;
    }
}

/**
 * Update spectrum with current track info (called from main loop at 10 FPS)
 * This syncs position and fetches data, not for rendering
 */
export async function updateSpectrum(trackInfo) {
    if (!displayConfig.showSpectrum) {
        hideSpectrum();
        return;
    }

    const container = document.getElementById('spectrum-container');
    if (!container) return;

    container.style.display = 'block';

    // Check if track changed
    const currentTrackId = trackInfo?.track_id;
    if (currentTrackId && currentTrackId !== spectrumTrackId) {
        spectrumTrackId = currentTrackId;
        console.debug('[Spectrum] Track changed, fetching new data');
        
        // Reset segment search index
        currentSegmentIndex = 0;
        lastBeatIndex = -1;
        beatPulse = 0;
        
        const data = await fetchSpectrumData();
        if (data && data.segments) {
            spectrumData = data;
            spectrumDuration = data.duration || trackInfo.duration_ms / 1000;
            console.debug(`[Spectrum] Loaded ${data.segments.length} segments, ${data.beats?.length || 0} beats`);
        } else {
            spectrumData = null;
            spectrumDuration = 0;
        }
    }

    // Initialize if needed
    if (!isSpectrumInitialized) {
        initSpectrum();
    }

    // Sync position from main loop
    lastKnownPosition = trackInfo.position || 0;
    lastPositionTime = performance.now();
    isPlaying = trackInfo.is_playing !== false;

    // Start animation loop if not running
    if (!isAnimating && spectrumData) {
        startAnimationLoop();
    }
}

/**
 * Update beat pulse - called every frame
 */
function updateBeatPulse(position) {
    // Decay the pulse each frame
    beatPulse *= CONFIG.beatDecay;
    
    if (!spectrumData || !spectrumData.beats || spectrumData.beats.length === 0) {
        return;
    }
    
    const beats = spectrumData.beats;
    
    // Binary search for current beat (optimization for large arrays)
    let lo = 0, hi = beats.length - 1;
    let currentBeatIndex = -1;
    
    while (lo <= hi) {
        const mid = Math.floor((lo + hi) / 2);
        if (beats[mid].start <= position) {
            currentBeatIndex = mid;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    
    // If we crossed a new beat, pulse!
    if (currentBeatIndex !== lastBeatIndex && currentBeatIndex >= 0) {
        const beat = beats[currentBeatIndex];
        // Pulse intensity based on beat confidence
        beatPulse = 0.4 + (beat.confidence || 0.5) * 0.6;
        lastBeatIndex = currentBeatIndex;
    }
}

/**
 * Get pitch data for the current playback position
 * Uses binary search for efficiency
 */
function getCurrentPitchData(position) {
    if (!spectrumData || !spectrumData.segments || spectrumData.segments.length === 0) {
        return new Array(CONFIG.barCount).fill(0);
    }

    const segments = spectrumData.segments;
    
    // Start search from last known segment (optimization)
    let currentSegment = null;
    
    // Check if position is still in cached segment
    if (currentSegmentIndex < segments.length) {
        const cached = segments[currentSegmentIndex];
        if (position >= cached.start && position < cached.start + cached.duration) {
            currentSegment = cached;
        }
    }
    
    // If not in cached segment, search forward (most common case)
    if (!currentSegment) {
        for (let i = currentSegmentIndex; i < segments.length; i++) {
            const seg = segments[i];
            if (position >= seg.start && position < seg.start + seg.duration) {
                currentSegment = seg;
                currentSegmentIndex = i;
                break;
            }
            if (seg.start > position) {
                // Position is before this segment, search backward
                for (let j = currentSegmentIndex - 1; j >= 0; j--) {
                    const seg2 = segments[j];
                    if (position >= seg2.start && position < seg2.start + seg2.duration) {
                        currentSegment = seg2;
                        currentSegmentIndex = j;
                        break;
                    }
                }
                break;
            }
        }
    }

    if (currentSegment && currentSegment.pitches && currentSegment.pitches.length === 12) {
        // Cache timbre for brightness calculation
        currentTimbre = currentSegment.timbre || null;
        
        // Scale pitches by beat pulse
        const scaleFactor = 1 + beatPulse * CONFIG.beatPulseMultiplier;
        return currentSegment.pitches.map(p => Math.min(1, p * scaleFactor));
    }
    
    return new Array(CONFIG.barCount).fill(0);
}

/**
 * Get brightness factor from timbre (0-1)
 * Uses timbre[1] which represents brightness/spectral centroid
 */
function getBrightnessFactor() {
    if (!currentTimbre || currentTimbre.length < 2) {
        return 0.5;
    }
    
    // Timbre[1] typically ranges from -100 to +150 (roughly)
    // Normalize to 0-1 range
    const brightness = currentTimbre[1];
    const normalized = (brightness + 100) / 250;
    return Math.max(0, Math.min(1, normalized));
}

/**
 * Render the spectrum visualization
 */
function renderSpectrum(canvas, pitchData) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.width / dpr;
    const height = canvas.height / dpr;

    ctx.clearRect(0, 0, width, height);

    if (!pitchData || pitchData.length === 0) return;

    const totalBarSpace = width - (CONFIG.barGap * (CONFIG.barCount + 1));
    const barWidth = totalBarSpace / CONFIG.barCount;
    const maxBarHeight = height * CONFIG.maxHeightPercent;

    if (!currentBarHeights) {
        currentBarHeights = new Array(CONFIG.barCount).fill(0);
    }

    // Get brightness factor (disabled - using fixed value)
    // const brightness = getBrightnessFactor();
    const brightness = 0.5;  // Fixed value, timbre adjustment disabled
    
    // Fixed alpha - beat pulse disabled to prevent flickering
    const alpha = 0.4;  // Solid, no flickering

    for (let i = 0; i < CONFIG.barCount; i++) {
        const targetHeight = pitchData[i] * maxBarHeight;
        
        if (targetHeight > currentBarHeights[i]) {
            currentBarHeights[i] += (targetHeight - currentBarHeights[i]) * (1 - CONFIG.smoothingFactor);
        } else {
            currentBarHeights[i] = currentBarHeights[i] * CONFIG.decayRate + 
                targetHeight * (1 - CONFIG.decayRate);
        }
        
        currentBarHeights[i] = Math.max(CONFIG.minBarHeight, currentBarHeights[i]);
    }

    const bottomY = height;

    for (let i = 0; i < CONFIG.barCount; i++) {
        const x = CONFIG.barGap + i * (barWidth + CONFIG.barGap);
        const barHeight = currentBarHeights[i];

        // Create gradient with dynamic alpha based on beat and brightness
        const gradient = ctx.createLinearGradient(x, bottomY, x, bottomY - barHeight);
        gradient.addColorStop(0, `rgba(255, 255, 255, ${alpha})`);
        gradient.addColorStop(1, `rgba(255, 255, 255, ${alpha * 0.15})`);

        ctx.fillStyle = gradient;
        
        const cornerRadius = Math.min(4, barWidth / 4);
        drawRoundedRect(ctx, x, bottomY - barHeight, barWidth, barHeight, cornerRadius);
    }
}

/**
 * Draw a rounded rectangle
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
    
    stopAnimationLoop();
}

/**
 * Reset spectrum state (e.g., when switching tracks)
 */
export function resetSpectrum() {
    spectrumData = null;
    spectrumDuration = 0;
    spectrumTrackId = null;
    currentBarHeights = new Array(CONFIG.barCount).fill(0);
    currentSegmentIndex = 0;
    currentTimbre = null;
    
    lastBeatIndex = -1;
    beatPulse = 0;
    lastKnownPosition = 0;
    lastPositionTime = 0;
    
    stopAnimationLoop();
    
    const canvas = document.getElementById('spectrum-canvas');
    if (canvas) {
        const ctx = canvas.getContext('2d');
        const dpr = window.devicePixelRatio || 1;
        ctx.clearRect(0, 0, canvas.width / dpr, canvas.height / dpr);
    }
}

/**
 * Get current configuration (for debugging/tuning)
 */
export function getSpectrumConfig() {
    return { ...CONFIG };
}

/**
 * Update configuration values at runtime
 */
export function setSpectrumConfig(newConfig) {
    Object.assign(CONFIG, newConfig);
    console.debug('[Spectrum] Config updated:', CONFIG);
}
