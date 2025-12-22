/**
 * spectrum.js - Spectrum Analyzer Visualizer (60 FPS)
 * 
 * Renders a full-width spectrum visualizer behind content.
 * Uses pitch, loudness, and beat data from Spotify's audio analysis.
 * Runs at 60 FPS using requestAnimationFrame with position estimation.
 * 
 * Features:
 * - Beat-synced bar jumps (filtered by confidence)
 * - Section-level energy scaling (quiet sections = smaller bars)
 * - Self-calibrating per-track loudness range
 * - Toggle between linear and true dB conversion
 * 
 * Level 2 - Imports: state
 */

import { displayConfig } from './state.js';

// ========== CONFIGURATION (All tunables at top) ==========
const CONFIG = {
    // === Visual Appearance ===
    barCount: 12,                    // Number of bars (matches 12 pitch classes)
    barGap: 6,                       // Gap between bars in pixels
    minBarHeight: 3,                 // Minimum bar height in pixels
    maxHeightPercent: 0.85,          // Max bar height as % of container
    barAlpha: 0.45,                  // Bar opacity (0-1)
    
    // === Animation ===
    decayRate: 0.90,                 // How fast bars decay between beats (0.85=fast, 0.95=slow)
    
    // === Beat Detection ===
    beatConfidenceThreshold: 0.1,    // Ignore beats with confidence below this
    
    // === Energy Scaling ===
    useLinearScaling: true,          // true = linear, false = true dB (Math.pow(10, db/20))
    // No minimum energy clamp - quiet sections can be truly quiet
    
    // === Debug ===
    logSectionChanges: false,
    logBeatHits: false
};

// ========== STATE ==========
let spectrumData = null;           // Cached audio analysis data
let spectrumDuration = 0;          // Track duration
let spectrumTrackId = null;        // Track ID for change detection
let currentBarHeights = null;      // Current animated bar heights
let animationFrameId = null;       // Animation frame ID for cleanup
let isSpectrumInitialized = false;
let isAnimating = false;

// Position estimation
let lastKnownPosition = 0;
let lastPositionTime = 0;
let isPlaying = true;

// Beat detection
let lastBeatIndex = -1;
let beatJustHit = false;
let currentBeatConfidence = 0;

// Section/segment tracking
let currentSegmentIndex = 0;
let currentSectionIndex = 0;

// Self-calibrating energy range (calculated per track)
let trackMinLoudness = -60;        // Quietest section in this track
let trackMaxLoudness = 0;          // Loudest section in this track

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

    currentBarHeights = new Array(CONFIG.barCount).fill(0);
    resizeSpectrumCanvas(canvas);

    window.addEventListener('resize', () => {
        resizeSpectrumCanvas(canvas);
    });

    isSpectrumInitialized = true;
    console.debug('[Spectrum] Canvas initialized');
}

/**
 * Resize spectrum canvas to fit container
 * Uses setTransform to avoid scaling accumulation bug
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

    // FIX: Use setTransform instead of scale to prevent accumulation
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

/**
 * Get estimated current position using time since last update
 */
function getEstimatedPosition() {
    if (!isPlaying) {
        return lastKnownPosition;
    }
    const elapsed = (performance.now() - lastPositionTime) / 1000;
    return Math.min(lastKnownPosition + elapsed, spectrumDuration);
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
        
        const position = getEstimatedPosition();
        
        // Check beat crossing (sets beatJustHit flag)
        checkBeatCrossing(position);
        
        // Get pitch data and current segment
        const pitchData = getCurrentPitchData(position);
        
        // Get section energy scale
        const sectionEnergy = getSectionEnergy(position);
        
        // Render with energy scaling
        renderSpectrum(canvas, pitchData, sectionEnergy);
        
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
 * Calibrate energy range from this track's sections
 * Called once when track data is loaded
 */
function calibrateEnergyRange() {
    if (!spectrumData || !spectrumData.sections || spectrumData.sections.length === 0) {
        trackMinLoudness = -60;
        trackMaxLoudness = 0;
        return;
    }
    
    const loudnesses = spectrumData.sections.map(s => s.loudness);
    trackMinLoudness = Math.min(...loudnesses);
    trackMaxLoudness = Math.max(...loudnesses);
    
    console.debug(`[Spectrum] Track loudness range: ${trackMinLoudness.toFixed(1)}dB to ${trackMaxLoudness.toFixed(1)}dB`);
}

/**
 * Get current section and calculate energy scale (0 to 1)
 * Uses self-calibrated min/max from this track
 */
function getSectionEnergy(position) {
    if (!spectrumData || !spectrumData.sections || spectrumData.sections.length === 0) {
        return 1.0;  // No sections = full energy
    }
    
    const sections = spectrumData.sections;
    let currentSection = null;
    
    // Find current section
    for (let i = 0; i < sections.length; i++) {
        const section = sections[i];
        const sectionEnd = section.start + section.duration;
        
        if (position >= section.start && position < sectionEnd) {
            currentSection = section;
            
            // Log section changes
            if (CONFIG.logSectionChanges && i !== currentSectionIndex) {
                console.debug(`[Spectrum] Section changed: index ${i}, loudness ${section.loudness}dB`);
            }
            currentSectionIndex = i;
            break;
        }
    }
    
    if (!currentSection) {
        return 1.0;
    }
    
    const sectionLoudness = currentSection.loudness;
    
    // Calculate energy based on scaling mode
    if (CONFIG.useLinearScaling) {
        // Linear normalization within track's range
        const range = trackMaxLoudness - trackMinLoudness;
        if (range <= 0) return 1.0;
        return (sectionLoudness - trackMinLoudness) / range;
    } else {
        // True dB to linear conversion
        // Clamp to reasonable range first
        const clampedDb = Math.max(-60, Math.min(0, sectionLoudness));
        return Math.pow(10, clampedDb / 20);
    }
}

/**
 * Check if we crossed a beat - uses confidence filtering
 */
function checkBeatCrossing(position) {
    beatJustHit = false;
    currentBeatConfidence = 0;
    
    if (!spectrumData || !spectrumData.beats || spectrumData.beats.length === 0) {
        return;
    }
    
    const beats = spectrumData.beats;
    
    // Binary search for current beat
    let lo = 0, hi = beats.length - 1;
    let beatIndex = -1;
    
    while (lo <= hi) {
        const mid = Math.floor((lo + hi) / 2);
        if (beats[mid].start <= position) {
            beatIndex = mid;
            lo = mid + 1;
        } else {
            hi = mid - 1;
        }
    }
    
    // Check if we crossed to a new beat
    if (beatIndex !== lastBeatIndex && beatIndex >= 0) {
        const beat = beats[beatIndex];
        currentBeatConfidence = beat.confidence || 0;
        
        // Only trigger if confidence meets threshold
        if (currentBeatConfidence >= CONFIG.beatConfidenceThreshold) {
            beatJustHit = true;
            
            if (CONFIG.logBeatHits) {
                console.debug(`[Spectrum] Beat hit: index ${beatIndex}, confidence ${currentBeatConfidence.toFixed(2)}`);
            }
        }
        
        lastBeatIndex = beatIndex;
    }
}

/**
 * Get pitch data for current position
 */
function getCurrentPitchData(position) {
    if (!spectrumData || !spectrumData.segments || spectrumData.segments.length === 0) {
        return new Array(CONFIG.barCount).fill(0);
    }

    const segments = spectrumData.segments;
    let currentSegment = null;
    
    // Check cached segment first
    if (currentSegmentIndex < segments.length) {
        const cached = segments[currentSegmentIndex];
        if (position >= cached.start && position < cached.start + cached.duration) {
            currentSegment = cached;
        }
    }
    
    // Search forward if not in cache
    if (!currentSegment) {
        for (let i = currentSegmentIndex; i < segments.length; i++) {
            const seg = segments[i];
            if (position >= seg.start && position < seg.start + seg.duration) {
                currentSegment = seg;
                currentSegmentIndex = i;
                break;
            }
            if (seg.start > position) {
                // Search backward
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
        return currentSegment.pitches;
    }
    
    return new Array(CONFIG.barCount).fill(0);
}

/**
 * Update spectrum with current track info (called from main loop at ~10 FPS)
 * This syncs position and fetches data when track changes
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
        
        // Reset state
        currentSegmentIndex = 0;
        currentSectionIndex = 0;
        lastBeatIndex = -1;
        beatJustHit = false;
        
        const data = await fetchSpectrumData();
        const analysis = data?.audio_analysis;
        if (data && analysis && analysis.segments) {
            // Store the audio_analysis for use by other functions
            spectrumData = {
                segments: analysis.segments,
                beats: analysis.beats || [],
                sections: analysis.sections || [],
                duration: analysis.duration,
                tempo: analysis.tempo,
                // Store full analysis for future visualizer features
                audio_analysis: analysis
            };
            spectrumDuration = analysis.duration || trackInfo.duration_ms / 1000;
            
            // Self-calibrate energy range for this track
            calibrateEnergyRange();
            
            console.debug(`[Spectrum] Loaded ${analysis.segments.length} segments, ${analysis.beats?.length || 0} beats, ${analysis.sections?.length || 0} sections, tempo: ${analysis.tempo}bpm`);
        } else {
            spectrumData = null;
            spectrumDuration = 0;
        }
    }

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
 * Render the spectrum visualization
 * 
 * @param {HTMLCanvasElement} canvas
 * @param {Array} pitchData - 12 pitch values (0-1)
 * @param {number} sectionEnergy - Section energy scale (0-1)
 */
function renderSpectrum(canvas, pitchData, sectionEnergy) {
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.width / dpr;
    const height = canvas.height / dpr;

    ctx.clearRect(0, 0, width, height);

    if (!pitchData || pitchData.length === 0) return;

    const totalBarSpace = width - (CONFIG.barGap * (CONFIG.barCount + 1));
    const barWidth = totalBarSpace / CONFIG.barCount;
    
    // Apply section energy to max height (no minimum clamp)
    const maxBarHeight = height * CONFIG.maxHeightPercent * sectionEnergy;

    if (!currentBarHeights) {
        currentBarHeights = new Array(CONFIG.barCount).fill(0);
    }

    const alpha = CONFIG.barAlpha;

    for (let i = 0; i < CONFIG.barCount; i++) {
        const targetHeight = pitchData[i] * maxBarHeight;
        
        if (beatJustHit) {
            // BEAT: Jump instantly to target
            currentBarHeights[i] = targetHeight;
        } else {
            // BETWEEN BEATS: Decay smoothly
            currentBarHeights[i] = currentBarHeights[i] * CONFIG.decayRate;
        }
        
        // Apply minimum height
        currentBarHeights[i] = Math.max(CONFIG.minBarHeight, currentBarHeights[i]);
    }

    const bottomY = height;

    for (let i = 0; i < CONFIG.barCount; i++) {
        const x = CONFIG.barGap + i * (barWidth + CONFIG.barGap);
        const barHeight = currentBarHeights[i];

        // Gradient from bottom (solid) to top (fade)
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
    currentSectionIndex = 0;
    
    lastBeatIndex = -1;
    beatJustHit = false;
    lastKnownPosition = 0;
    lastPositionTime = 0;
    
    trackMinLoudness = -60;
    trackMaxLoudness = 0;
    
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
