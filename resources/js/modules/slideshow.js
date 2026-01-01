/**
 * slideshow.js - Slideshow Functionality (Complete Rework)
 * 
 * This module handles the slideshow/art cycling feature that displays
 * artist and album images automatically. It is independent of visual mode.
 * 
 * Features:
 * - Toggle on/off via button (localStorage persisted)
 * - Configurable timing, shuffle, Ken Burns effect
 * - Long-press opens control center (Phase 2)
 * - Per-artist preferences (Phase 2)
 * - Four-finger gesture toggle in art mode (Phase 3)
 * 
 * Level 2 - Imports: state, dom
 */

import {
    slideshowConfig,
    slideshowEnabled,
    slideshowInterval,
    slideshowImagePool,
    slideshowPaused,
    currentSlideIndex,
    currentArtistImages,
    lastTrackInfo,
    displayConfig,
    setSlideshowEnabled,
    setSlideshowInterval,
    setSlideshowImagePool,
    setSlideshowPaused,
    setCurrentSlideIndex
} from './state.js';

import { showToast } from './dom.js';
import { isManualArtistImageActive, resetArtZoom, syncZoomImgIfInArtMode } from './artZoom.js';
import { updateBackground } from './background.js';
import { fetchArtistImages, saveArtistSlideshowPreferences } from './api.js';

// ========== CONSTANTS ==========
const RESUME_DELAY_RATIO = 0.5;  // Resume after half of interval when manual browsing stops

// Ken Burns animation parameters
const KEN_BURNS_SCALES = {
    subtle: { scale: 1.05, translate: 2 },
    medium: { scale: 1.12, translate: 4 },
    cinematic: { scale: 1.20, translate: 6 }
};

// Random directions for Ken Burns
const KEN_BURNS_DIRECTIONS = [
    { x: -1, y: -1 },  // top-left to bottom-right
    { x: 1, y: -1 },   // top-right to bottom-left
    { x: -1, y: 1 },   // bottom-left to top-right
    { x: 1, y: 1 },    // bottom-right to top-left
    { x: 0, y: -1 },   // top to bottom
    { x: 0, y: 1 },    // bottom to top
    { x: -1, y: 0 },   // left to right
    { x: 1, y: 0 }     // right to left
];

// Track last artist to detect artist changes
let lastSlideshowArtist = null;
let resumeTimer = null;
let cleanupTimer = null;  // Track scheduled cleanup to prevent race condition

// Fisher-Yates shuffle state
let shuffledOrder = [];      // Array of indices in shuffled order
let shufflePosition = 0;     // Current position in shuffled order

// ========== INITIALIZATION ==========

/**
 * Initialize slideshow module
 * Called from main.js on app startup
 */
export function initSlideshow() {
    // Load enabled state from localStorage (URL param could override later)
    const savedEnabled = localStorage.getItem('slideshowEnabled');
    if (savedEnabled !== null) {
        setSlideshowEnabled(savedEnabled === 'true');
    } else {
        // Use default from config
        setSlideshowEnabled(slideshowConfig.defaultEnabled);
    }
    
    // Update button state
    updateSlideshowButtonState();
    
    // Setup visibility change handler for background tab pause
    document.addEventListener('visibilitychange', handleVisibilityChange);
    
    // Setup global edge tap handler for slideshow cycling in normal mode
    setupGlobalEdgeTapHandler();
    
    // If slideshow was enabled, start it after a delay to allow track data to load
    if (slideshowEnabled) {
        console.log('[Slideshow] Was enabled, will auto-start after delay...');
        setTimeout(() => {
            if (slideshowEnabled && slideshowImagePool.length === 0) {
                loadImagePoolForCurrentArtist();
            }
            if (slideshowEnabled && slideshowImagePool.length > 0 && !slideshowInterval) {
                startSlideshow();
            }
        }, 2000);  // 2 second delay to let track data load
    }
    
    console.log(`[Slideshow] Initialized. Enabled: ${slideshowEnabled}`);
}

/**
 * Handle visibility change (background tab pause)
 */
function handleVisibilityChange() {
    if (document.hidden) {
        // Tab hidden - pause slideshow
        if (slideshowEnabled && !slideshowPaused) {
            pauseSlideshow('background');
        }
    } else {
        // Tab visible - resume if was paused due to background
        if (slideshowEnabled && slideshowPaused) {
            resumeSlideshow();
        }
    }
}

// ========== GLOBAL EDGE TAP HANDLER ==========
// Constants for edge detection
const EDGE_TAP_SIZE = 150;  // Pixels from edge
const TAP_DURATION_MAX = 500;  // Max ms for a tap

// Global edge tap state
let globalTouchStartTime = 0;
let globalTouchStartX = 0;
let globalTouchStartY = 0;

/**
 * Setup global edge tap handler for slideshow cycling in normal mode
 * Attached to document with capture:true to intercept touches before other handlers
 */
function setupGlobalEdgeTapHandler() {
    // Attach to document for reliable capture
    document.addEventListener('touchstart', (e) => {
        // Only track if slideshow is active and single touch
        if (!slideshowEnabled || e.touches.length !== 1) return;
        
        // Ignore touches on interactive elements
        const target = e.target;
        if (target.closest('button, a, input, .control-btn, .modal, .lyrics-container, .album-art')) {
            return;
        }
        
        // Skip if art-only mode is active - artZoom handles edge taps there
        if (document.body.classList.contains('art-only-mode')) {
            return;
        }
        
        const x = e.touches[0].clientX;
        const screenWidth = window.innerWidth;
        
        // Only track if touch is on edge
        if (x < EDGE_TAP_SIZE || x > screenWidth - EDGE_TAP_SIZE) {
            globalTouchStartTime = Date.now();
            globalTouchStartX = x;
            globalTouchStartY = e.touches[0].clientY;
        }
    }, { passive: true, capture: true });
    
    document.addEventListener('touchend', (e) => {
        // Only process if we tracked a start
        if (globalTouchStartTime === 0) return;
        
        // Only process if slideshow is active
        if (!slideshowEnabled) {
            globalTouchStartTime = 0;
            return;
        }
        
        const duration = Date.now() - globalTouchStartTime;
        globalTouchStartTime = 0;  // Reset
        
        if (duration > TAP_DURATION_MAX) return;  // Not a tap
        
        // Check if it was a stationary tap (minimal movement)
        const endX = e.changedTouches[0]?.clientX || globalTouchStartX;
        const endY = e.changedTouches[0]?.clientY || globalTouchStartY;
        const moveDistance = Math.sqrt(
            Math.pow(endX - globalTouchStartX, 2) + 
            Math.pow(endY - globalTouchStartY, 2)
        );
        
        if (moveDistance > 30) return;  // Too much movement, not a tap
        
        const screenWidth = window.innerWidth;
        
        // Left edge tap - previous image
        if (globalTouchStartX < EDGE_TAP_SIZE) {
            previousSlide();
            console.log('[Slideshow] Edge tap: previous');
            return;
        }
        
        // Right edge tap - next image
        if (globalTouchStartX > screenWidth - EDGE_TAP_SIZE) {
            advanceSlide();
            console.log('[Slideshow] Edge tap: next');
            return;
        }
    }, { passive: true, capture: true });
    
    console.log('[Slideshow] Global edge tap handler attached to document');
}

// ========== BUTTON & TOGGLE ==========
// NOTE: toggleSlideshow() is defined below in SLIDESHOW CONTROL section


/**
 * Update slideshow button visual state
 */
export function updateSlideshowButtonState() {
    const btn = document.getElementById('btn-slideshow-toggle');
    if (!btn) return;
    
    btn.classList.toggle('active', slideshowEnabled);
    btn.title = slideshowEnabled ? 'Slideshow On (click to disable)' : 'Toggle Slideshow';
}

/**
 * Setup slideshow button event handlers
 * Called from main.js during initialization
 * 
 * @param {Function} showModalFn - Function to show slideshow control center (Phase 2)
 */
export function setupSlideshowButton(showModalFn = null) {
    const btn = document.getElementById('btn-slideshow-toggle');
    if (!btn) {
        console.warn('[Slideshow] Button not found in DOM');
        return;
    }
    
    // Click to toggle
    btn.addEventListener('click', () => {
        toggleSlideshow();
    });
    
    // Long-press to open control center (Phase 2)
    let pressTimer = null;
    const LONG_PRESS_DURATION = 500;
    
    btn.addEventListener('pointerdown', (e) => {
        if (showModalFn) {
            pressTimer = setTimeout(() => {
                e.preventDefault();
                showModalFn();
            }, LONG_PRESS_DURATION);
        }
    });
    
    btn.addEventListener('pointerup', () => {
        if (pressTimer) {
            clearTimeout(pressTimer);
            pressTimer = null;
        }
    });
    
    btn.addEventListener('pointerleave', () => {
        if (pressTimer) {
            clearTimeout(pressTimer);
            pressTimer = null;
        }
    });
    
    // Keyboard shortcut: S key
    document.addEventListener('keydown', (e) => {
        // Don't trigger if typing in an input
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        
        if (e.key === 's' || e.key === 'S') {
            if (!e.ctrlKey && !e.altKey && !e.metaKey) {
                e.preventDefault();
                toggleSlideshow();
            }
        }
    });
    
    console.log('[Slideshow] Button handlers attached');
}

// ========== IMAGE POOL MANAGEMENT ==========

/**
 * Load image pool for current artist
 * Combines artist images + currently displayed album art (no duplicates)
 * Filters out any images excluded by user in control center
 */
export function loadImagePoolForCurrentArtist() {
    // Get current artist name for comparison and exclusion lookup
    const currentArtist = lastTrackInfo?.artist || '';
    
    // Load excluded images from storage
    let excluded = [];
    try {
        const saved = localStorage.getItem('slideshowExcludedImages');
        if (saved) {
            const allExcluded = JSON.parse(saved);
            excluded = allExcluded[currentArtist] || [];
        }
    } catch (e) {
        console.warn('[Slideshow] Failed to load excluded images');
    }
    
    // Build image pool: album art (index 0) + artist images
    const pool = [];
    
    // Add currently displayed album art as first image
    // Use 'album_art' key for exclusion since URLs change between tracks
    const albumArtUrl = lastTrackInfo?.album_art_url || lastTrackInfo?.album_art_path || '';
    if (albumArtUrl && !excluded.includes('album_art')) {
        pool.push(albumArtUrl);
    }
    
    // Add artist images (already loaded in state by main.js)
    if (currentArtistImages && currentArtistImages.length > 0) {
        // Filter out album art duplicate AND excluded images
        const filteredArtistImages = currentArtistImages.filter(img => 
            img !== albumArtUrl && !excluded.includes(img)
        );
        pool.push(...filteredArtistImages);
    }
    
    setSlideshowImagePool(pool);
    setCurrentSlideIndex(0);
    lastSlideshowArtist = currentArtist;
    
    console.log(`[Slideshow] Image pool loaded for "${currentArtist}": ${pool.length} images (${excluded.length} excluded)`);
}

/**
 * Handle artist change - reload image pool and apply auto-enable preference
 * Called from main.js on track change
 * 
 * @param {string} newArtist - New artist name
 * @param {boolean} sameArtist - Whether it's the same artist as before
 */
export function handleArtistChange(newArtist, sameArtist) {
    if (sameArtist) {
        // Same artist - continue exactly as-is, don't re-evaluate preferences
        console.log(`[Slideshow] Same artist "${newArtist}" - continuing without reset`);
        return;
    }
    
    // Different artist - reset manual override for new artist
    manualOverrideActive = false;
    
    // Clear any pending debounce (user skipping tracks quickly)
    if (artistChangeDebounceTimer) {
        clearTimeout(artistChangeDebounceTimer);
    }
    
    console.log(`[Slideshow] Artist changing to "${newArtist}" - will apply preferences after debounce`);
    
    // Debounce: wait before loading preferences and applying auto-enable
    // This prevents rapid loads when user skips tracks quickly
    const artistAtSchedule = newArtist;  // Capture artist in closure for verification
    artistChangeDebounceTimer = setTimeout(async () => {
        artistChangeDebounceTimer = null;
        
        // Verify artist hasn't changed since we scheduled this timeout
        if (lastTrackInfo?.artist !== artistAtSchedule) {
            console.log(`[Slideshow] Artist changed during debounce, skipping apply for "${artistAtSchedule}"`);
            return;
        }
        
        try {
            // Load preferences for new artist (includes auto_enable)
            await loadExcludedImages();
            
            // Apply auto-enable preference (only if not manually overridden)
            if (!manualOverrideActive) {
                applyAutoEnableForCurrentArtist(newArtist);
            }
        } catch (e) {
            console.warn('[Slideshow] Failed to load preferences for artist change:', e);
            // On failure, keep current state (default behavior)
        }
    }, ARTIST_CHANGE_DEBOUNCE_MS);
}

/**
 * Apply auto-enable preference for current artist
 * @param {string} artistName - Artist name for logging
 */
function applyAutoEnableForCurrentArtist(artistName) {
    if (currentAutoEnable === true) {
        // "Always" - enable slideshow for this artist
        if (!slideshowEnabled) {
            console.log(`[Slideshow] Auto-enabling for "${artistName}" (preference: Always)`);
            enableSlideshow();
        }
    } else if (currentAutoEnable === false) {
        // "Never" - disable slideshow for this artist
        if (slideshowEnabled) {
            console.log(`[Slideshow] Auto-disabling for "${artistName}" (preference: Never)`);
            disableSlideshow();
        }
    }
    // null = "Default" - keep current global state, do nothing
}

// ========== SLIDESHOW CONTROL ==========

/**
 * Start slideshow - begin cycling through images
 */
export function startSlideshow() {
    // Only start if slideshow is enabled
    if (!slideshowEnabled) {
        return;
    }
    
    // Cancel any pending cleanup from a previous stopSlideshow() call
    // This prevents the race condition where cleanup deletes our new images
    if (cleanupTimer) {
        clearTimeout(cleanupTimer);
        cleanupTimer = null;
    }
    
    if (slideshowInterval) {
        clearInterval(slideshowInterval);
    }
    
    if (slideshowImagePool.length === 0) {
        console.log('[Slideshow] No images in pool, cannot start');
        return;
    }
    
    // If shuffle is enabled, shuffle and pick random starting position
    let startIndex = currentSlideIndex;
    if (slideshowConfig.shuffle) {
        shuffleImagePool();
        // Pick random starting position in the shuffled order
        shufflePosition = Math.floor(Math.random() * shuffledOrder.length);
        startIndex = shuffledOrder[shufflePosition];
        setCurrentSlideIndex(startIndex);
    }
    
    // Show first image immediately
    showSlide(startIndex);
    
    // Start interval
    const intervalMs = slideshowConfig.intervalSeconds * 1000;
    const interval = setInterval(() => {
        if (!slideshowPaused && slideshowImagePool.length > 0) {
            advanceSlide();
        }
    }, intervalMs);
    
    setSlideshowInterval(interval);
    setSlideshowPaused(false);
    
    console.log(`[Slideshow] Started with ${intervalMs}ms interval, ${slideshowImagePool.length} images${slideshowConfig.shuffle ? ' (shuffled, starting at ' + startIndex + ')' : ''}`);
}

/**
 * Reset the slideshow interval timer (for manual advance/previous)
 * This ensures the user gets a full interval after manually skipping
 */
function resetSlideshowTimer() {
    // Only reset if slideshow is actively running
    if (!slideshowInterval || !slideshowEnabled) return;
    
    // Clear old interval
    clearInterval(slideshowInterval);
    
    // Start fresh interval
    const intervalMs = slideshowConfig.intervalSeconds * 1000;
    const interval = setInterval(() => {
        if (!slideshowPaused && slideshowImagePool.length > 0) {
            advanceSlide();
        }
    }, intervalMs);
    setSlideshowInterval(interval);
}

/**
 * Enable slideshow (turn on)
 * @param {boolean} isManualToggle - True if triggered by user action (sets manual override)
 */
function enableSlideshow(isManualToggle = false) {
    if (slideshowEnabled) return;  // Already enabled
    
    if (isManualToggle) {
        manualOverrideActive = true;
    }
    
    setSlideshowEnabled(true);
    updateSlideshowButtonState();
    localStorage.setItem('slideshowEnabled', 'true');
    
    // Load images and start
    loadImagePoolForCurrentArtist();
    if (slideshowImagePool.length > 0) {
        startSlideshow();
    }
    
    if (isManualToggle) {
        showToast(`Slideshow enabled (${slideshowConfig.intervalSeconds}s)`, 'success', 1200);
    }
    
    console.log(`[Slideshow] Enabled${isManualToggle ? ' (manual)' : ' (auto)'}`);
}

/**
 * Disable slideshow (turn off)
 * @param {boolean} isManualToggle - True if triggered by user action (sets manual override)
 */
function disableSlideshow(isManualToggle = false) {
    if (!slideshowEnabled) return;  // Already disabled
    
    if (isManualToggle) {
        manualOverrideActive = true;
    }
    
    setSlideshowEnabled(false);
    updateSlideshowButtonState();
    localStorage.setItem('slideshowEnabled', 'false');
    stopSlideshow();
    
    if (isManualToggle) {
        showToast('Slideshow disabled', 'success', 1000);
    }
    
    console.log(`[Slideshow] Disabled${isManualToggle ? ' (manual)' : ' (auto)'}`);
}

/**
 * Toggle slideshow on/off (user action)
 */
export function toggleSlideshow() {
    if (slideshowEnabled) {
        disableSlideshow(true);  // Manual toggle
    } else {
        enableSlideshow(true);   // Manual toggle
    }
}

/**
 * Stop slideshow - clear interval and cleanup with smooth transition
 */
export function stopSlideshow() {
    if (slideshowInterval) {
        clearInterval(slideshowInterval);
        setSlideshowInterval(null);
    }
    
    if (resumeTimer) {
        clearTimeout(resumeTimer);
        resumeTimer = null;
    }
    
    const bgContainer = document.getElementById('background-layer');
    
    // Restore proper background first (underneath slideshow images)
    // updateBackground() handles all the logic to determine what should be shown
    updateBackground();
    
    // Fade out slideshow images for smooth transition
    if (bgContainer) {
        const slideshowImages = bgContainer.querySelectorAll('.slideshow-image');
        slideshowImages.forEach(img => img.classList.remove('active'));  // opacity → 0
        
        // Remove slideshow images after fade completes
        // Store timer ID so it can be cancelled if startSlideshow() is called before cleanup
        cleanupTimer = setTimeout(() => {
            clearSlideshowImages();
            cleanupTimer = null;
        }, (slideshowConfig.transitionDuration || 0.8) * 1000);
    }
    
    setSlideshowPaused(false);
    console.log('[Slideshow] Stopped (fading out)');
}

/**
 * Pause slideshow (for manual browsing or background tab)
 * 
 * @param {string} reason - 'manual' or 'background'
 */
export function pauseSlideshow(reason = 'manual') {
    if (!slideshowEnabled || slideshowPaused) return;
    
    setSlideshowPaused(true);
    console.log(`[Slideshow] Paused (${reason})`);
    
    // If paused due to manual browsing, set timer to resume
    if (reason === 'manual') {
        if (resumeTimer) {
            clearTimeout(resumeTimer);
        }
        
        // Resume after half of slideshow interval (or the interval itself)
        const resumeDelay = slideshowConfig.intervalSeconds * RESUME_DELAY_RATIO * 1000;
        resumeTimer = setTimeout(() => {
            if (!isManualArtistImageActive()) {
                resumeSlideshow();
            }
        }, resumeDelay);
    }
}

/**
 * Resume slideshow after pause
 */
export function resumeSlideshow() {
    if (!slideshowEnabled || !slideshowPaused) return;
    
    setSlideshowPaused(false);
    console.log('[Slideshow] Resumed');
    
    if (resumeTimer) {
        clearTimeout(resumeTimer);
        resumeTimer = null;
    }
}

/**
 * Check if slideshow should pause (called from artZoom on manual browse)
 */
export function checkSlideshowPause() {
    if (slideshowEnabled && !slideshowPaused && isManualArtistImageActive()) {
        pauseSlideshow('manual');
    }
}

// ========== SLIDE DISPLAY ==========

/**
 * Fisher-Yates shuffle - creates an array of indices in random order
 * Each image will be shown exactly once before reshuffling
 */
function shuffleImagePool() {
    const n = slideshowImagePool.length;
    shuffledOrder = Array.from({ length: n }, (_, i) => i);
    
    // Fisher-Yates shuffle algorithm
    for (let i = n - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [shuffledOrder[i], shuffledOrder[j]] = [shuffledOrder[j], shuffledOrder[i]];
    }
    
    shufflePosition = 0;
    console.log(`[Slideshow] Shuffled ${n} images`);
}

/**
 * Advance to next slide (exported for edge tap cycling)
 */
export function advanceSlide() {
    if (slideshowImagePool.length === 0) return;
    
    let nextIndex;
    if (slideshowConfig.shuffle) {
        // Fisher-Yates: cycle through shuffled order
        if (shuffledOrder.length !== slideshowImagePool.length) {
            // Pool changed, reshuffle
            shuffleImagePool();
        }
        
        shufflePosition = (shufflePosition + 1) % shuffledOrder.length;
        
        // Reshuffle when we've shown all images
        if (shufflePosition === 0) {
            shuffleImagePool();
        }
        
        nextIndex = shuffledOrder[shufflePosition];
    } else {
        // Sequential
        nextIndex = (currentSlideIndex + 1) % slideshowImagePool.length;
    }
    
    setCurrentSlideIndex(nextIndex);
    showSlide(nextIndex);
    
    // Reset interval timer so user gets full interval after manual skip
    resetSlideshowTimer();
}

/**
 * Go to previous slide (exported for edge tap cycling)
 */
export function previousSlide() {
    if (slideshowImagePool.length === 0) return;
    
    let prevIndex;
    if (slideshowConfig.shuffle) {
        // Fisher-Yates: go backwards through shuffled order
        if (shuffledOrder.length !== slideshowImagePool.length) {
            shuffleImagePool();
        }
        
        shufflePosition = (shufflePosition - 1 + shuffledOrder.length) % shuffledOrder.length;
        prevIndex = shuffledOrder[shufflePosition];
    } else {
        // Sequential backwards
        prevIndex = (currentSlideIndex - 1 + slideshowImagePool.length) % slideshowImagePool.length;
    }
    
    setCurrentSlideIndex(prevIndex);
    showSlide(prevIndex);
    
    // Reset interval timer so user gets full interval after manual skip
    resetSlideshowTimer();
}

/**
 * Show a specific slide
 * 
 * @param {number} index - Index in the image pool
 */
function showSlide(index) {
    if (index < 0 || index >= slideshowImagePool.length) return;
    
    const imageUrl = slideshowImagePool[index];
    if (!imageUrl) return;
    
    const bgContainer = document.getElementById('background-layer');
    if (!bgContainer) return;
    
    // Create new image element for crossfade
    const newImg = document.createElement('div');
    newImg.className = 'slideshow-image';
    newImg.style.backgroundImage = `url("${imageUrl}")`;
    newImg.style.transition = `opacity ${slideshowConfig.transitionDuration}s ease`;
    
    // Apply background fill mode from localStorage (user's preference)
    const fillMode = localStorage.getItem('backgroundFillMode') || 'cover';
    switch (fillMode) {
        case 'contain':
            newImg.style.backgroundSize = 'contain';
            break;
        case 'stretch':
            newImg.style.backgroundSize = '100% 100%';
            break;
        case 'original':
            newImg.style.backgroundSize = 'auto';
            break;
        case 'cover':
        default:
            newImg.style.backgroundSize = 'cover';
            break;
    }
    
    bgContainer.appendChild(newImg);
    
    // Clear base layer background to prevent flicker on track change
    // Slideshow images overlay on top, so base layer should be empty
    bgContainer.style.backgroundImage = 'none';
    
    // Fade in new image (allow layout to complete first)
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            newImg.classList.add('active');
            
            // Apply Ken Burns effect AFTER element is active (visible)
            // This ensures the animation starts from the visible state
            if (slideshowConfig.kenBurnsEnabled) {
                // Small delay to let the opacity transition start
                setTimeout(() => {
                    applyKenBurnsEffect(newImg);
                }, 50);
            }
        });
    });
    
    // Remove old images after transition completes
    // Use longer delay to ensure smooth transitions
    const cleanupDelay = (slideshowConfig.transitionDuration + 1.5) * 1000;
    setTimeout(() => {
        const oldImages = bgContainer.querySelectorAll('.slideshow-image:not(:last-child)');
        oldImages.forEach(img => img.remove());
    }, cleanupDelay);
    
    // Reset zoom/pan if in art-only mode (consistency with artZoom behavior)
    if (document.body.classList.contains('art-only-mode')) {
        resetArtZoom();
        // Sync the zoom img with the new slideshow image
        syncZoomImgIfInArtMode(imageUrl);
    }
    
    // Preload adjacent images for smoother rapid cycling
    preloadAdjacentSlides(index);
}

// Track preloaded URLs to avoid duplicate preloads
const preloadedSlideUrls = new Set();

/**
 * Preload images within ±3 of current index for smoother cycling
 * Similar to artZoom's preloadAdjacentImages but for slideshow
 * 
 * @param {number} currentIndex - Current slide index
 */
function preloadAdjacentSlides(currentIndex) {
    if (slideshowImagePool.length === 0) return;
    
    const PRELOAD_RANGE = 3;  // Preload 3 images in each direction
    
    // Create or get container for preloaded images
    let container = document.getElementById('slideshow-preload-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'slideshow-preload-container';
        container.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;visibility:hidden;';
        document.body.appendChild(container);
    }
    
    for (let offset = -PRELOAD_RANGE; offset <= PRELOAD_RANGE; offset++) {
        if (offset === 0) continue;  // Skip current (already loaded)
        
        const index = (currentIndex + offset + slideshowImagePool.length) % slideshowImagePool.length;
        const url = slideshowImagePool[index];
        
        // Skip if already preloaded
        if (preloadedSlideUrls.has(url)) continue;
        preloadedSlideUrls.add(url);
        
        // Add link preload hint
        const link = document.createElement('link');
        link.rel = 'preload';
        link.as = 'image';
        link.href = url;
        document.head.appendChild(link);
        
        // Add hidden img to DOM for reliable caching
        const img = document.createElement('img');
        img.src = url;
        img.loading = 'eager';
        container.appendChild(img);
    }
}

/**
 * Clear all slideshow images from the background
 */
function clearSlideshowImages() {
    const bgContainer = document.getElementById('background-layer');
    if (bgContainer) {
        const slideshowImages = bgContainer.querySelectorAll('.slideshow-image');
        slideshowImages.forEach(img => img.remove());
    }
}

/**
 * Apply Ken Burns effect to an element
 * 
 * @param {HTMLElement} element - The element to animate
 */
function applyKenBurnsEffect(element) {
    const intensity = slideshowConfig.kenBurnsIntensity || 'subtle';
    const params = KEN_BURNS_SCALES[intensity] || KEN_BURNS_SCALES.subtle;
    
    // Pick random direction
    const direction = KEN_BURNS_DIRECTIONS[Math.floor(Math.random() * KEN_BURNS_DIRECTIONS.length)];
    
    // Random choice: zoom in or zoom out
    const zoomIn = Math.random() > 0.5;
    
    const translateX = direction.x * params.translate;
    const translateY = direction.y * params.translate;
    
    // Set initial state
    if (zoomIn) {
        element.style.transform = 'scale(1) translate(0%, 0%)';
    } else {
        element.style.transform = `scale(${params.scale}) translate(${-translateX}%, ${-translateY}%)`;
    }
    
    // Apply the animation
    element.style.transition = `opacity ${slideshowConfig.transitionDuration}s ease, transform ${slideshowConfig.intervalSeconds}s ease-out`;
    
    // Start animation after a small delay
    requestAnimationFrame(() => {
        if (zoomIn) {
            element.style.transform = `scale(${params.scale}) translate(${translateX}%, ${translateY}%)`;
        } else {
            element.style.transform = 'scale(1) translate(0%, 0%)';
        }
    });
}

// ========== EXPORTS FOR MAIN.JS ==========

/**
 * Check if slideshow is currently running
 */
export function isSlideshowActive() {
    return slideshowEnabled && slideshowInterval !== null && !slideshowPaused;
}

/**
 * Get current slideshow state for debugging
 */
export function getSlideshowState() {
    return {
        enabled: slideshowEnabled,
        paused: slideshowPaused,
        imageCount: slideshowImagePool.length,
        currentIndex: currentSlideIndex,
        intervalActive: slideshowInterval !== null
    };
}

// ========== CONTROL CENTER MODAL ==========

// Track excluded images per artist (now synced with backend, localStorage as cache)
let excludedImages = {};  // { artistName: [filename, ...] }

// Track favorite images per artist (synced with backend)
let favoriteImages = {};  // { artistName: [filename, ...] }

// Current image metadata cache (loaded from backend when modal opens)
let currentImageMetadata = [];  // [{source, filename, width, height, added_at}, ...]

// Sorting state (persisted to localStorage)
const SORT_OPTIONS = ['original', 'name', 'resolution', 'provider', 'date'];
let currentSortOption = localStorage.getItem('slideshowSortOption') || 'original';

// Filtering state (not persisted - resets on modal close)
let activeFilters = new Set(['all']);  // 'all', provider names, 'favorites'

// Auto-enable state for current artist (loaded from backend preferences)
let currentAutoEnable = null;  // null (use global), true (always), false (never)

// Manual override flag - set true when user manually toggles slideshow
// Prevents auto-enable from overriding user's choice until artist changes
let manualOverrideActive = false;

// Debounce timer for artist changes (handles quick track skipping)
let artistChangeDebounceTimer = null;
const ARTIST_CHANGE_DEBOUNCE_MS = 1500;  // Wait 500ms before applying auto-enable

// Default settings for reset
const DEFAULT_SETTINGS = {
    intervalSeconds: 6,
    shuffle: false,
    kenBurnsEnabled: true,
    kenBurnsIntensity: 'subtle',
    transitionDuration: 0.8
};


/**
 * Show the slideshow control center modal
 */
export async function showSlideshowModal() {
    const modal = document.getElementById('slideshow-modal');
    if (!modal) return;
    
    // If no artist images loaded yet, fetch them now
    if (currentArtistImages.length === 0 && lastTrackInfo?.artist_id) {
        try {
            const data = await fetchArtistImages(lastTrackInfo.artist_id, true);
            if (data?.images) {
                setCurrentArtistImages(data.images);
                currentImageMetadata = data.metadata || [];
            }
        } catch (e) {
            console.warn('[Slideshow] Failed to fetch images for modal:', e);
        }
    }
    
    // Update UI to reflect current settings
    updateModalUIFromConfig();
    
    // Render image grid
    renderImageGrid();
    
    // Show modal
    modal.classList.remove('hidden');
    
    console.log('[Slideshow] Control center opened');
}

/**
 * Hide the slideshow control center modal
 */
export function hideSlideshowModal() {
    const modal = document.getElementById('slideshow-modal');
    if (!modal) return;
    
    modal.classList.add('hidden');
    
    // Update slideshow pool with any include/exclude changes made in modal
    if (slideshowEnabled) {
        loadImagePoolForCurrentArtist();
    }
    
    console.log('[Slideshow] Control center closed');
}

/**
 * Setup control center modal event handlers
 */
export function setupControlCenter() {
    // Close button
    const closeBtn = document.getElementById('slideshow-modal-close');
    if (closeBtn) {
        closeBtn.addEventListener('click', hideSlideshowModal);
    }
    
    // Backdrop click to close
    const modal = document.getElementById('slideshow-modal');
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                hideSlideshowModal();
            }
        });
    }
    
    // Escape key to close
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const modal = document.getElementById('slideshow-modal');
            if (modal && !modal.classList.contains('hidden')) {
                hideSlideshowModal();
            }
        }
    });
    
    // Reset button
    const resetBtn = document.getElementById('slideshow-reset-btn');
    if (resetBtn) {
        resetBtn.addEventListener('click', handleResetToDefaults);
    }
    
    // Timing preset buttons (3, 6, 9, 15, 30)
    document.querySelectorAll('.slideshow-timing-btn[data-timing]').forEach(btn => {
        btn.addEventListener('click', () => {
            const timing = parseInt(btn.dataset.timing);
            if (!isNaN(timing)) {
                handleTimingClick(timing);
            }
        });
    });
    
    // Custom button - applies immediately with default or current value
    const customBtn = document.getElementById('slideshow-custom-btn');
    const customInput = document.getElementById('slideshow-custom-timing');
    if (customBtn && customInput) {
        customBtn.addEventListener('click', () => {
            // Get current value or default to 6
            const currentValue = parseInt(customInput.value) || 6;
            
            // Apply custom timing immediately
            handleTimingClick(currentValue, true);
            
            // Show input for adjustment
            customInput.classList.remove('hidden');
            customInput.value = currentValue;
            customInput.focus();
            customInput.select();
        });
        
        // Apply on blur (when input loses focus)
        customInput.addEventListener('blur', () => {
            const value = parseInt(customInput.value);
            if (value >= 1 && value <= 600) {
                handleTimingClick(value, true);
            } else if (customInput.value === '' || isNaN(value)) {
                // If empty or invalid, default to 6
                handleTimingClick(6, true);
                customInput.value = 6;
            }
        });
        
        // Enter key blurs the input (which triggers apply)
        customInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                customInput.blur();
            }
        });
    }
    
    // Shuffle toggle
    const shuffleBtn = document.getElementById('slideshow-shuffle-btn');
    if (shuffleBtn) {
        shuffleBtn.addEventListener('click', () => {
            slideshowConfig.shuffle = !slideshowConfig.shuffle;
            shuffleBtn.classList.toggle('active', slideshowConfig.shuffle);
            saveSettingsToLocalStorage();
            showToast(slideshowConfig.shuffle ? 'Shuffle enabled' : 'Shuffle disabled', 'success', 1000);
        });
    }
    
    // Ken Burns toggle
    const kenBurnsBtn = document.getElementById('slideshow-ken-burns-btn');
    if (kenBurnsBtn) {
        kenBurnsBtn.addEventListener('click', () => {
            slideshowConfig.kenBurnsEnabled = !slideshowConfig.kenBurnsEnabled;
            kenBurnsBtn.classList.toggle('active', slideshowConfig.kenBurnsEnabled);
            updateKenBurnsOptionsVisibility();
            saveSettingsToLocalStorage();
            showToast(slideshowConfig.kenBurnsEnabled ? 'Ken Burns enabled' : 'Ken Burns disabled', 'success', 1000);
        });
    }
    
    // Ken Burns intensity buttons
    document.querySelectorAll('.slideshow-intensity-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const intensity = btn.dataset.intensity;
            slideshowConfig.kenBurnsIntensity = intensity;
            document.querySelectorAll('.slideshow-intensity-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            saveSettingsToLocalStorage();
        });
    });
    
    // Select All / Deselect All
    const selectAllBtn = document.getElementById('slideshow-select-all');
    const deselectAllBtn = document.getElementById('slideshow-deselect-all');
    
    if (selectAllBtn) {
        selectAllBtn.addEventListener('click', () => {
            const artistName = lastTrackInfo?.artist || 'unknown';
            excludedImages[artistName] = [];
            saveExcludedImages();
            renderImageGrid();
            loadImagePoolForCurrentArtist();
        });
    }
    
    if (deselectAllBtn) {
        deselectAllBtn.addEventListener('click', () => {
            const artistName = lastTrackInfo?.artist || 'unknown';
            const allImages = [...currentArtistImages];
            const albumArt = lastTrackInfo?.album_art_url || lastTrackInfo?.album_art_path;
            if (albumArt && !allImages.includes(albumArt)) {
                allImages.unshift(albumArt);
            }
            excludedImages[artistName] = allImages;
            saveExcludedImages();
            renderImageGrid();
            loadImagePoolForCurrentArtist();
        });
    }
    
    // Sort dropdown
    const sortSelect = document.getElementById('slideshow-sort-select');
    if (sortSelect) {
        // Set initial value from localStorage
        sortSelect.value = currentSortOption;
        sortSelect.addEventListener('change', (e) => {
            handleSortChange(e.target.value);
        });
    }
    
    // Filter chips - attach to all static chips
    const filterChipsContainer = document.getElementById('slideshow-filter-chips');
    if (filterChipsContainer) {
        filterChipsContainer.addEventListener('click', (e) => {
            const chip = e.target.closest('.slideshow-filter-chip');
            if (chip && chip.dataset.filter) {
                toggleFilter(chip.dataset.filter);
            }
        });
    }
    
    // Reset filters button
    const resetFiltersBtn = document.getElementById('slideshow-reset-filters');
    if (resetFiltersBtn) {
        resetFiltersBtn.addEventListener('click', resetFilters);
    }
    
    // Auto-enable buttons
    document.querySelectorAll('.slideshow-auto-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const value = btn.dataset.auto;
            // Convert string to proper type
            if (value === 'null') {
                currentAutoEnable = null;
            } else if (value === 'true') {
                currentAutoEnable = true;
            } else {
                currentAutoEnable = false;
            }
            
            // Update button states
            document.querySelectorAll('.slideshow-auto-btn').forEach(b => {
                b.classList.toggle('active', b.dataset.auto === value);
            });
            
            // Save to backend (saveExcludedImages now includes auto_enable)
            saveExcludedImages();
            
            const artistName = lastTrackInfo?.artist || 'unknown';
            console.log(`[Slideshow] Auto-enable for "${artistName}" set to: ${currentAutoEnable}`);
        });
    });
    
    console.log('[Slideshow] Control center handlers attached');
}



/**
 * Handle timing button click
 * @param {number} seconds - The timing value in seconds
 * @param {boolean} isCustom - True if value came from custom input
 */
function handleTimingClick(seconds, isCustom = false) {
    if (isNaN(seconds) || seconds < 1 || seconds > 600) return;
    
    slideshowConfig.intervalSeconds = seconds;
    
    const presets = [3, 6, 9, 15, 30];
    const customBtn = document.getElementById('slideshow-custom-btn');
    const customInput = document.getElementById('slideshow-custom-timing');
    
    // Update preset button states
    document.querySelectorAll('.slideshow-timing-btn[data-timing]').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.timing) === seconds);
    });
    
    // Handle custom button and input
    if (isCustom || !presets.includes(seconds)) {
        // Custom value - show input with value, mark custom button active
        if (customBtn) customBtn.classList.add('active');
        if (customInput) {
            customInput.value = seconds;
            customInput.classList.remove('hidden');
        }
    } else {
        // Preset value - clear custom UI
        if (customBtn) customBtn.classList.remove('active');
        if (customInput) {
            customInput.value = '';
            customInput.classList.add('hidden');
        }
    }
    
    saveSettingsToLocalStorage();
    
    // Restart slideshow with new timing if running
    if (slideshowEnabled && slideshowInterval) {
        startSlideshow();
    }
    
    showToast(`Slideshow: ${seconds}s per image`, 'success', 1000);
}

/**
 * Handle reset to defaults
 */
function handleResetToDefaults() {
    if (!confirm('Reset all slideshow settings to defaults?')) {
        return;
    }
    
    // Reset config
    slideshowConfig.intervalSeconds = DEFAULT_SETTINGS.intervalSeconds;
    slideshowConfig.shuffle = DEFAULT_SETTINGS.shuffle;
    slideshowConfig.kenBurnsEnabled = DEFAULT_SETTINGS.kenBurnsEnabled;
    slideshowConfig.kenBurnsIntensity = DEFAULT_SETTINGS.kenBurnsIntensity;
    slideshowConfig.transitionDuration = DEFAULT_SETTINGS.transitionDuration;
    
    // Clear excluded images for current artist
    const artistName = lastTrackInfo?.artist || 'unknown';
    excludedImages[artistName] = [];
    
    // Save and update UI
    saveSettingsToLocalStorage();
    saveExcludedImages();
    updateModalUIFromConfig();
    renderImageGrid();
    loadImagePoolForCurrentArtist();
    
    showToast('Settings reset to defaults', 'success', 1500);
}

/**
 * Update modal UI to reflect current config
 */
function updateModalUIFromConfig() {
    // Timing buttons
    document.querySelectorAll('.slideshow-timing-btn').forEach(btn => {
        btn.classList.toggle('active', parseInt(btn.dataset.timing) === slideshowConfig.intervalSeconds);
    });
    
    // Custom input
    const customInput = document.getElementById('slideshow-custom-timing');
    if (customInput && ![3, 6, 9].includes(slideshowConfig.intervalSeconds)) {
        customInput.value = slideshowConfig.intervalSeconds;
    }
    
    // Shuffle button
    const shuffleBtn = document.getElementById('slideshow-shuffle-btn');
    if (shuffleBtn) {
        shuffleBtn.classList.toggle('active', slideshowConfig.shuffle);
    }
    
    // Ken Burns button
    const kenBurnsBtn = document.getElementById('slideshow-ken-burns-btn');
    if (kenBurnsBtn) {
        kenBurnsBtn.classList.toggle('active', slideshowConfig.kenBurnsEnabled);
    }
    
    // Intensity buttons
    document.querySelectorAll('.slideshow-intensity-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.intensity === slideshowConfig.kenBurnsIntensity);
    });
    
    // Auto-enable buttons (per-artist preference)
    document.querySelectorAll('.slideshow-auto-btn').forEach(btn => {
        const btnValue = btn.dataset.auto;
        const currentValue = currentAutoEnable === null ? 'null' : String(currentAutoEnable);
        btn.classList.toggle('active', btnValue === currentValue);
    });
    
    // Sort dropdown (sync value on modal open)
    const sortSelect = document.getElementById('slideshow-sort-select');
    if (sortSelect) {
        sortSelect.value = currentSortOption;
    }
    
    updateKenBurnsOptionsVisibility();
}

/**
 * Show/hide Ken Burns intensity options
 */
function updateKenBurnsOptionsVisibility() {
    const options = document.getElementById('slideshow-ken-burns-options');
    if (options) {
        options.style.display = slideshowConfig.kenBurnsEnabled ? 'flex' : 'none';
    }
}

/**
 * Render the image grid in the modal
 */
function renderImageGrid() {
    const grid = document.getElementById('slideshow-image-grid');
    const countEl = document.getElementById('slideshow-image-count');
    if (!grid) return;
    
    grid.innerHTML = '';
    
    // Get all available images
    const artistName = lastTrackInfo?.artist || 'unknown';
    const albumArt = lastTrackInfo?.album_art_url || lastTrackInfo?.album_art_path;
    const allImages = [];
    
    // Add album art first (use 'album_art' key for exclusion)
    if (albumArt) {
        allImages.push({ url: albumArt, source: 'Album Art', key: 'album_art' });
    }
    
    // Extract provider from URL and build smart naming
    // URL format: /api/album-art/image/Artist/fanart_tv_1.jpg
    // Provider mapping for display names
    const providerDisplayNames = {
        'fanart': 'FanArt',
        'fanarttv': 'FanArt',
        'deezer': 'Deezer',
        'spotify': 'Spotify',
        'theaudiodb': 'AudioDB',
        'audiodb': 'AudioDB',
        'spicetify': 'Spicetify',
        'lastfm': 'LastFM',
        'last_fm': 'LastFM',
        'custom': 'Custom',
        'Custom': 'Custom',
        'Unknown': 'Custom',
        'itunes': 'iTunes'
    };
    
    // First pass: count images per provider
    const providerCounts = {};
    const artistImagesWithProvider = [];
    
    // Known provider prefixes (lowercase for matching)
    const knownProviders = ['fanart', 'fanart_tv', 'FanArt.tv', 'fanarttv', 'deezer', 'spotify', 'theaudiodb', 'audiodb', 'spicetify', 'lastfm', 'last_fm', 'last.fm', 'itunes'];
    
    currentArtistImages.forEach((img) => {
        if (img === albumArt) return;  // Skip duplicate
        
        // Extract filename from URL
        const filename = img.split('/').pop() || '';
        const filenameLower = filename.toLowerCase();
        
        // Check if filename starts with a known provider
        let displayName = 'Custom';  // Default to Custom
        for (const prefix of knownProviders) {
            if (filenameLower.startsWith(prefix + '_') || filenameLower.startsWith(prefix + '.')) {
                displayName = providerDisplayNames[prefix] || prefix;
                break;
            }
        }
        
        // Extract the number from filename (e.g., "fanarttv_21.jpg" → "21", "Custom49.jpg" → "49")
        const numMatch = filename.match(/(\d+)\.(?:jpg|png|webp|jpeg)$/i);
        const fileNum = numMatch ? numMatch[1] : null;
        
        providerCounts[displayName] = (providerCounts[displayName] || 0) + 1;
        artistImagesWithProvider.push({ url: img, provider: displayName, fileNum });
    });
    
    // Second pass: assign names with numbers
    // Use filename number if available, otherwise use sequential index
    const providerIndices = {};
    artistImagesWithProvider.forEach(({ url, provider, fileNum }) => {
        providerIndices[provider] = (providerIndices[provider] || 0) + 1;
        const count = providerCounts[provider];
        
        // Determine what number to show
        let source;
        if (count === 1) {
            // Single image from this provider - no number needed
            source = provider;
        } else if (fileNum !== null) {
            // Use the actual filename number
            source = `${provider} ${fileNum}`;
        } else {
            // Fallback to sequential index
            source = `${provider} ${providerIndices[provider]}`;
        }
        
        allImages.push({ url, source, key: url });
    });
    
    // Build metadata map for O(1) lookup (instead of O(n) .find per image)
    const metadataMap = new Map(currentImageMetadata.map(m => [m.filename, m]));
    
    // Enrich allImages with backend metadata (for sorting by resolution/date)
    allImages.forEach(img => {
        if (img.key === 'album_art') return;  // Skip album art (no metadata)
        
        // Extract filename from URL to match with backend metadata
        const filename = img.url.split('/').pop();
        const meta = metadataMap.get(filename);  // O(1) lookup
        if (meta) {
            img.width = meta.width;
            img.height = meta.height;
            img.added_at = meta.added_at;
            img.filename = meta.filename;
        } else {
            // Fallback: use filename from URL
            img.filename = filename;
        }
    });
    
    // NOTE: loadExcludedImages() commented out - data already loaded by showSlideshowModal()
    // loadExcludedImages();
    const excluded = excludedImages[artistName] || [];
    const excludedSet = new Set(excluded);  // O(1) lookups
    const favoritesSet = new Set(favoriteImages[artistName] || []);  // O(1) lookups
    
    // Create dynamic provider chips before sorting/filtering
    updateProviderChips(allImages);
    
    // Apply sorting
    let displayImages = sortImages(allImages);
    
    // Apply filtering
    displayImages = filterImages(displayImages, artistName);
    
    // Count included images using Set for O(1) lookup
    let includedCount = allImages.filter(img => !excludedSet.has(img.key)).length;
    if (countEl) {
        countEl.textContent = `${includedCount}/${allImages.length} images`;
    }
    
    // Render cards (use displayImages which is sorted and filtered)
    displayImages.forEach(img => {

        const card = document.createElement('div');
        card.className = 'slideshow-image-card';
        if (excludedSet.has(img.key)) {
            card.classList.add('excluded');
        } else {
            card.classList.add('selected');
        }
        
        const imgEl = document.createElement('img');
        imgEl.src = img.url;
        imgEl.loading = 'lazy';
        imgEl.alt = img.source;
        
        // Add resolution when image loads
        imgEl.onload = function() {
            const resEl = card.querySelector('.slideshow-image-resolution');
            if (!resEl) {
                const res = document.createElement('span');
                res.className = 'slideshow-image-resolution';
                res.textContent = `${this.naturalWidth}×${this.naturalHeight}`;
                card.querySelector('.slideshow-image-card-overlay')?.appendChild(res);
            }
        };
        
        const overlay = document.createElement('div');
        overlay.className = 'slideshow-image-card-overlay';
        
        const sourceEl = document.createElement('span');
        sourceEl.className = 'slideshow-image-source';
        sourceEl.textContent = img.source;
        overlay.appendChild(sourceEl);
        
        card.appendChild(imgEl);
        card.appendChild(overlay);
        
        // Add favorite star button
        const favBtn = document.createElement('button');
        favBtn.className = 'slideshow-favorite-btn';
        favBtn.innerHTML = '★';
        favBtn.title = 'Toggle favorite';
        
        // Check if this image is favorited (using Set for O(1) lookup)
        if (favoritesSet.has(img.key)) {
            favBtn.classList.add('active');
        }
        
        // Favorite button click (stop propagation to not toggle exclusion)
        favBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleFavorite(img.key, artistName);
            favBtn.classList.toggle('active');
        });
        
        card.appendChild(favBtn);
        
        // Click to toggle include/exclude

        card.addEventListener('click', () => {
            toggleImageExclusion(img.key, artistName);
            card.classList.toggle('excluded');
            card.classList.toggle('selected');
            
            // Update count using counter variable (O(1) instead of O(n) filter)
            if (card.classList.contains('excluded')) {
                includedCount--;
            } else {
                includedCount++;
            }
            if (countEl) {
                countEl.textContent = `${includedCount}/${allImages.length} images`;
            }
        });
        
        grid.appendChild(card);
    });
}

/**
 * Toggle an image's exclusion status
 */
function toggleImageExclusion(url, artistName) {
    if (!excludedImages[artistName]) {
        excludedImages[artistName] = [];
    }
    
    const idx = excludedImages[artistName].indexOf(url);
    if (idx >= 0) {
        excludedImages[artistName].splice(idx, 1);
    } else {
        excludedImages[artistName].push(url);
    }
    
    saveExcludedImages();
    // NOTE: loadImagePoolForCurrentArtist() commented out - pool updates on modal close
    // loadImagePoolForCurrentArtist();
}

/**
 * Load excluded images and favorites - backend first, localStorage as fallback
 * Also handles one-time migration from localStorage to backend
 */
async function loadExcludedImages() {
    const currentArtist = lastTrackInfo?.artist || '';
    
    // If no artist, just load from localStorage
    if (!currentArtist) {
        loadExcludedImagesFromLocalStorage();
        loadFavoritesFromLocalStorage();
        return;
    }
    
    try {
        // Try backend first
        const artistId = lastTrackInfo?.artist_id;
        if (artistId) {
            const data = await fetchArtistImages(artistId, true);
            if (data?.preferences) {
                // Use backend data
                excludedImages[currentArtist] = data.preferences.excluded || [];
                favoriteImages[currentArtist] = data.preferences.favorites || [];
                currentImageMetadata = data.metadata || [];
                currentAutoEnable = data.preferences.auto_enable;  // null, true, or false
                
                // Cache to localStorage
                saveExcludedImagesToLocalStorage();
                saveFavoritesToLocalStorage();
                
                console.log(`[Slideshow] Loaded preferences from backend for "${currentArtist}": ${excludedImages[currentArtist].length} excluded, ${favoriteImages[currentArtist].length} favorites, auto_enable=${currentAutoEnable}`);
                return;
            }
        }
    } catch (e) {
        console.warn('[Slideshow] Backend fetch failed, using localStorage:', e);
    }
    
    // Fallback: localStorage
    loadExcludedImagesFromLocalStorage();
    loadFavoritesFromLocalStorage();
    
    // Migration: if localStorage has data but backend didn't, push to backend
    const hasLocalExcluded = excludedImages[currentArtist]?.length > 0;
    const hasLocalFavorites = favoriteImages[currentArtist]?.length > 0;
    if (hasLocalExcluded || hasLocalFavorites) {
        migratePrefsToBackend(currentArtist);
    }
}

/**
 * Load excluded images from localStorage only (fallback)
 */
function loadExcludedImagesFromLocalStorage() {
    try {
        const saved = localStorage.getItem('slideshowExcludedImages');
        if (saved) {
            excludedImages = JSON.parse(saved);
        }
    } catch (e) {
        console.warn('[Slideshow] Failed to load excluded images from localStorage:', e);
        excludedImages = {};
    }
}

/**
 * Save excluded images to localStorage only (cache)
 */
function saveExcludedImagesToLocalStorage() {
    try {
        localStorage.setItem('slideshowExcludedImages', JSON.stringify(excludedImages));
    } catch (e) {
        console.warn('[Slideshow] Failed to save excluded images to localStorage:', e);
    }
}

/**
 * Load favorites from localStorage (fallback)
 */
function loadFavoritesFromLocalStorage() {
    try {
        const saved = localStorage.getItem('slideshowFavoriteImages');
        if (saved) {
            favoriteImages = JSON.parse(saved);
        }
    } catch (e) {
        favoriteImages = {};
    }
}

/**
 * Save favorites to localStorage (cache)
 */
function saveFavoritesToLocalStorage() {
    try {
        localStorage.setItem('slideshowFavoriteImages', JSON.stringify(favoriteImages));
    } catch (e) {
        console.warn('[Slideshow] Failed to save favorites to localStorage');
    }
}

/**
 * Save excluded images and favorites - to both localStorage (cache) and backend
 */
async function saveExcludedImages() {
    const currentArtist = lastTrackInfo?.artist || '';
    
    // Always save to localStorage (cache)
    saveExcludedImagesToLocalStorage();
    saveFavoritesToLocalStorage();
    
    // Also save to backend
    if (currentArtist) {
        try {
            await saveArtistSlideshowPreferences(
                currentArtist,
                excludedImages[currentArtist] || [],
                currentAutoEnable,  // auto_enable per-artist preference
                favoriteImages[currentArtist] || []
            );
            console.log(`[Slideshow] Saved preferences to backend for "${currentArtist}"`);
        } catch (e) {
            console.warn('[Slideshow] Failed to save to backend:', e);
            showToast('Preferences saved locally', 'warning', 1500);
        }
    }
}

/**
 * Migrate existing localStorage preferences to backend (one-time)
 */
async function migratePrefsToBackend(artist) {
    try {
        await saveArtistSlideshowPreferences(
            artist,
            excludedImages[artist] || [],
            null,
            favoriteImages[artist] || []
        );
        showToast('Preferences synced', 'success', 1200);
        console.log(`[Slideshow] Migrated preferences for "${artist}" to backend`);
    } catch (e) {
        console.warn('[Slideshow] Migration failed:', e);
    }
}

/**
 * Toggle favorite status for an image
 */
function toggleFavorite(filename, artistName) {
    if (!favoriteImages[artistName]) {
        favoriteImages[artistName] = [];
    }
    
    const idx = favoriteImages[artistName].indexOf(filename);
    if (idx >= 0) {
        favoriteImages[artistName].splice(idx, 1);
    } else {
        favoriteImages[artistName].push(filename);
    }
    
    saveFavoritesToLocalStorage();
    saveExcludedImages();  // This also saves favorites to backend
    // Note: UI update is handled by click handler (favBtn.classList.toggle)
}

// ========== SORTING ==========

/**
 * Sort image metadata array based on current sort option
 * @param {Array} metadata - Array of image metadata objects
 * @returns {Array} Sorted copy of metadata array
 */
function sortImages(metadata) {
    if (!metadata || metadata.length === 0 || currentSortOption === 'original') {
        return metadata;  // Keep original order
    }
    
    const sorted = [...metadata];
    
    switch (currentSortOption) {
        case 'name':
            sorted.sort((a, b) => (a.filename || '').localeCompare(b.filename || ''));
            break;
        case 'resolution':
            sorted.sort((a, b) => {
                const resA = (a.width || 0) * (a.height || 0);
                const resB = (b.width || 0) * (b.height || 0);
                return resB - resA;  // Descending (largest first)
            });
            break;
        case 'provider':
            sorted.sort((a, b) => (a.source || '').localeCompare(b.source || ''));
            break;
        case 'date':
            sorted.sort((a, b) => {
                const dateA = new Date(a.added_at || 0);
                const dateB = new Date(b.added_at || 0);
                return dateB - dateA;  // Descending (newest first)
            });
            break;
    }
    
    return sorted;
}

/**
 * Handle sort option change
 * @param {string} option - Sort option from SORT_OPTIONS
 */
function handleSortChange(option) {
    if (!SORT_OPTIONS.includes(option)) return;
    
    currentSortOption = option;
    localStorage.setItem('slideshowSortOption', option);
    renderImageGrid();  // Re-render with new sort
    console.log(`[Slideshow] Sort changed to: ${option}`);
}

// ========== FILTERING ==========

/**
 * Get list of unique providers from metadata
 * @param {Array} metadata - Array of image metadata objects
 * @returns {Array<string>} Array of provider names (lowercase)
 */
function getProviderList(metadata) {
    const providers = new Set();
    metadata.forEach(img => {
        if (img.source) {
            providers.add(img.source.toLowerCase());
        }
    });
    return Array.from(providers).sort();
}

/**
 * Filter images based on active filters
 * @param {Array} images - Array of image objects {url, source, key, ...}
 * @param {string} artistName - Artist name for favorites lookup
 * @returns {Array} Filtered copy of images array
 */
function filterImages(images, artistName) {
    if (!images || activeFilters.has('all')) {
        return images;
    }
    
    const favs = favoriteImages[artistName] || [];
    
    return images.filter(img => {
        // Check favorites filter (favorites stored as keys/URLs)
        if (activeFilters.has('favorites') && favs.includes(img.key)) {
            return true;
        }
        
        // Check provider filters
        // Extract base provider name from source (e.g., "FanArt 1" → "fanart")
        const baseSource = (img.source || '').replace(/\s+\d+$/, '').toLowerCase();
        for (const filter of activeFilters) {
            if (filter !== 'favorites' && baseSource === filter) {
                return true;
            }
        }
        
        return false;
    });
}


/**
 * Toggle a filter chip
 * @param {string} filter - Filter name ('all', provider name, or 'favorites')
 */
function toggleFilter(filter) {
    if (filter === 'all') {
        // 'All' is exclusive
        activeFilters.clear();
        activeFilters.add('all');
    } else {
        // Remove 'all' when selecting specific filters
        activeFilters.delete('all');
        if (activeFilters.has(filter)) {
            activeFilters.delete(filter);
        } else {
            activeFilters.add(filter);
        }
        // If no filters left, default to 'all'
        if (activeFilters.size === 0) {
            activeFilters.add('all');
        }
    }
    updateFilterChips();
    renderImageGrid();
}

/**
 * Reset all filters to 'all'
 */
function resetFilters() {
    activeFilters.clear();
    activeFilters.add('all');
    updateFilterChips();
    renderImageGrid();
}

/**
 * Update filter chip UI to reflect active state
 */
function updateFilterChips() {
    const chips = document.querySelectorAll('.slideshow-filter-chip');
    chips.forEach(chip => {
        const filter = chip.dataset.filter;
        chip.classList.toggle('active', activeFilters.has(filter));
    });
}

/**
 * Update provider chips dynamically based on available images
 * @param {Array} images - Array of image objects with source property
 */
function updateProviderChips(images) {
    const container = document.getElementById('slideshow-filter-chips');
    if (!container) return;
    
    // Get unique base provider names and count images per provider
    const providerCounts = new Map();
    images.forEach(img => {
        if (img.source && img.source !== 'Album Art') {
            const baseProvider = img.source.replace(/\s+\d+$/, '').toLowerCase();
            providerCounts.set(baseProvider, (providerCounts.get(baseProvider) || 0) + 1);
        }
    });
    
    // Remove existing dynamic provider chips (keep "All", "Favorites", and "Reset")
    const existingDynamicChips = container.querySelectorAll('.slideshow-filter-chip.dynamic-provider');
    existingDynamicChips.forEach(chip => chip.remove());
    
    // Find the "Favorites" chip to insert before it
    const favoritesChip = container.querySelector('[data-filter="favorites"]');
    if (!favoritesChip) return;
    
    // Create and insert provider chips with count
    const sortedProviders = Array.from(providerCounts.keys()).sort();
    sortedProviders.forEach(provider => {
        const count = providerCounts.get(provider);
        const chip = document.createElement('button');
        chip.className = 'slideshow-filter-chip dynamic-provider';
        chip.dataset.filter = provider;
        const displayName = provider.charAt(0).toUpperCase() + provider.slice(1);  // Capitalize
        chip.textContent = `${displayName} (${count})`;  // Show count
        if (activeFilters.has(provider)) {
            chip.classList.add('active');
        }
        container.insertBefore(chip, favoritesChip);
    });
}


/**
 * Save settings to localStorage
 */
function saveSettingsToLocalStorage() {

    try {
        localStorage.setItem('slideshowSettings', JSON.stringify({
            intervalSeconds: slideshowConfig.intervalSeconds,
            shuffle: slideshowConfig.shuffle,
            kenBurnsEnabled: slideshowConfig.kenBurnsEnabled,
            kenBurnsIntensity: slideshowConfig.kenBurnsIntensity,
            transitionDuration: slideshowConfig.transitionDuration
        }));
    } catch (e) {
        console.warn('[Slideshow] Failed to save settings:', e);
    }
}

/**
 * Load settings from localStorage
 */
export function loadSettingsFromLocalStorage() {
    try {
        const saved = localStorage.getItem('slideshowSettings');
        if (saved) {
            const settings = JSON.parse(saved);
            if (settings.intervalSeconds) slideshowConfig.intervalSeconds = settings.intervalSeconds;
            if (settings.shuffle !== undefined) slideshowConfig.shuffle = settings.shuffle;
            if (settings.kenBurnsEnabled !== undefined) slideshowConfig.kenBurnsEnabled = settings.kenBurnsEnabled;
            if (settings.kenBurnsIntensity) slideshowConfig.kenBurnsIntensity = settings.kenBurnsIntensity;
            if (settings.transitionDuration) slideshowConfig.transitionDuration = settings.transitionDuration;
            console.log('[Slideshow] Settings loaded from localStorage');
        }
    } catch (e) {
        console.warn('[Slideshow] Failed to load settings:', e);
    }
    
    // Also load excluded images
    loadExcludedImages();
}
