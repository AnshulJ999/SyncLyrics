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
    setSlideshowEnabled,
    setSlideshowInterval,
    setSlideshowImagePool,
    setSlideshowPaused,
    setCurrentSlideIndex
} from './state.js';

import { showToast } from './dom.js';
import { isManualArtistImageActive } from './artZoom.js';

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

// ========== BUTTON & TOGGLE ==========

/**
 * Toggle slideshow on/off
 * Called when user clicks the slideshow button
 */
export function toggleSlideshow() {
    const newState = !slideshowEnabled;
    setSlideshowEnabled(newState);
    
    // Save to localStorage
    localStorage.setItem('slideshowEnabled', newState.toString());
    
    // Update UI
    updateSlideshowButtonState();
    
    if (newState) {
        // Starting slideshow - load images and start
        loadImagePoolForCurrentArtist();
        startSlideshow();
        showToast('Slideshow enabled', 'success', 1000);
    } else {
        // Stopping slideshow
        stopSlideshow();
        showToast('Slideshow disabled', 'success', 1000);
    }
    
    console.log(`[Slideshow] Toggled ${newState ? 'ON' : 'OFF'}`);
}

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
 */
export function loadImagePoolForCurrentArtist() {
    // Get current artist name for comparison
    const currentArtist = lastTrackInfo?.artist || '';
    
    // Build image pool: album art (index 0) + artist images
    const pool = [];
    
    // Add currently displayed album art as first image
    const albumArtUrl = lastTrackInfo?.album_art_url || lastTrackInfo?.album_art_path || '';
    if (albumArtUrl) {
        pool.push(albumArtUrl);
    }
    
    // Add artist images (already loaded in state by main.js)
    if (currentArtistImages && currentArtistImages.length > 0) {
        // Filter out album art if it's already in artist images (avoid duplicate)
        const filteredArtistImages = currentArtistImages.filter(img => img !== albumArtUrl);
        pool.push(...filteredArtistImages);
    }
    
    setSlideshowImagePool(pool);
    setCurrentSlideIndex(0);
    lastSlideshowArtist = currentArtist;
    
    console.log(`[Slideshow] Image pool loaded for "${currentArtist}": ${pool.length} images`);
}

/**
 * Handle artist change - reload image pool if needed
 * Called from main.js on track change
 * 
 * @param {string} newArtist - New artist name
 * @param {boolean} sameArtist - Whether it's the same artist as before
 */
export function handleArtistChange(newArtist, sameArtist) {
    if (!slideshowEnabled) return;
    
    if (sameArtist) {
        // Same artist - continue slideshow exactly as-is, don't touch anything
        console.log(`[Slideshow] Same artist "${newArtist}" - continuing without reset`);
        return;  // Early return - do nothing for same artist
    }
    
    // Different artist - reload image pool (but don't restart interval if already running)
    console.log(`[Slideshow] Artist changed to "${newArtist}" - will reload images`);
    // Note: loadImagePoolForCurrentArtist() is called from main.js AFTER artist images are fetched
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
    
    if (slideshowInterval) {
        clearInterval(slideshowInterval);
    }
    
    if (slideshowImagePool.length === 0) {
        console.log('[Slideshow] No images in pool, cannot start');
        return;
    }
    
    // Show first image immediately if not already shown
    showSlide(currentSlideIndex);
    
    // Start interval
    const intervalMs = slideshowConfig.intervalSeconds * 1000;
    const interval = setInterval(() => {
        if (!slideshowPaused && slideshowImagePool.length > 0) {
            advanceSlide();
        }
    }, intervalMs);
    
    setSlideshowInterval(interval);
    setSlideshowPaused(false);
    
    console.log(`[Slideshow] Started with ${intervalMs}ms interval, ${slideshowImagePool.length} images`);
}

/**
 * Stop slideshow - clear interval and cleanup
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
    
    // Clear slideshow images from background
    clearSlideshowImages();
    
    setSlideshowPaused(false);
    console.log('[Slideshow] Stopped');
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
 * Advance to next slide
 */
function advanceSlide() {
    if (slideshowImagePool.length === 0) return;
    
    let nextIndex;
    if (slideshowConfig.shuffle) {
        // Random (true shuffle - may repeat, but simple)
        nextIndex = Math.floor(Math.random() * slideshowImagePool.length);
    } else {
        // Sequential
        nextIndex = (currentSlideIndex + 1) % slideshowImagePool.length;
    }
    
    setCurrentSlideIndex(nextIndex);
    showSlide(nextIndex);
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
    
    // Apply Ken Burns effect if enabled
    if (slideshowConfig.kenBurnsEnabled) {
        applyKenBurnsEffect(newImg);
    }
    
    bgContainer.appendChild(newImg);
    
    // Fade in new image (allow layout to complete first)
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            newImg.classList.add('active');
        });
    });
    
    // Remove old images after transition completes
    const cleanupDelay = (slideshowConfig.transitionDuration + 0.5) * 1000;
    setTimeout(() => {
        const oldImages = bgContainer.querySelectorAll('.slideshow-image:not(:last-child)');
        oldImages.forEach(img => img.remove());
    }, cleanupDelay);
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
