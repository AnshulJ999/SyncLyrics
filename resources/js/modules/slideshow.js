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
const EDGE_TAP_SIZE = 100;  // Pixels from edge
const TAP_DURATION_MAX = 300;  // Max ms for a tap

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
            pauseSlideshow('manual');  // Pause auto-advance
            console.log('[Slideshow] Edge tap: previous');
            return;
        }
        
        // Right edge tap - next image
        if (globalTouchStartX > screenWidth - EDGE_TAP_SIZE) {
            advanceSlide();
            pauseSlideshow('manual');  // Pause auto-advance
            console.log('[Slideshow] Edge tap: next');
            return;
        }
    }, { passive: true, capture: true });
    
    console.log('[Slideshow] Global edge tap handler attached to document');
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
    const cleanupDelay = (slideshowConfig.transitionDuration + 0.6) * 1000;
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

// ========== CONTROL CENTER MODAL ==========

// Track excluded images per artist (stored in localStorage for now, Phase 2.5 will use backend)
let excludedImages = {};  // { artistName: [imageUrl, ...] }

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
export function showSlideshowModal() {
    const modal = document.getElementById('slideshow-modal');
    if (!modal) return;
    
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
        
        providerCounts[displayName] = (providerCounts[displayName] || 0) + 1;
        artistImagesWithProvider.push({ url: img, provider: displayName });
    });
    
    // Second pass: assign names with numbers only if provider has multiple
    const providerIndices = {};
    artistImagesWithProvider.forEach(({ url, provider }) => {
        providerIndices[provider] = (providerIndices[provider] || 0) + 1;
        const count = providerCounts[provider];
        const idx = providerIndices[provider];
        
        // Only add number if this provider has multiple images
        const source = count > 1 ? `${provider} ${idx}` : provider;
        
        allImages.push({ url, source, key: url });
    });
    
    // Load excluded images from storage
    loadExcludedImages();
    const excluded = excludedImages[artistName] || [];
    
    // Count included images (using keys)
    const includedCount = allImages.filter(img => !excluded.includes(img.key)).length;
    if (countEl) {
        countEl.textContent = `${includedCount}/${allImages.length} images`;
    }
    
    // Render cards
    allImages.forEach(img => {
        const card = document.createElement('div');
        card.className = 'slideshow-image-card';
        if (excluded.includes(img.key)) {
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
        
        // Click to toggle include/exclude
        card.addEventListener('click', () => {
            toggleImageExclusion(img.key, artistName);
            card.classList.toggle('excluded');
            card.classList.toggle('selected');
            
            // Update count
            const newExcluded = excludedImages[artistName] || [];
            const newIncluded = allImages.filter(i => !newExcluded.includes(i.key)).length;
            if (countEl) {
                countEl.textContent = `${newIncluded}/${allImages.length} images`;
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
    loadImagePoolForCurrentArtist();
}

/**
 * Load excluded images from localStorage
 */
function loadExcludedImages() {
    try {
        const saved = localStorage.getItem('slideshowExcludedImages');
        if (saved) {
            excludedImages = JSON.parse(saved);
        }
    } catch (e) {
        console.warn('[Slideshow] Failed to load excluded images:', e);
        excludedImages = {};
    }
}

/**
 * Save excluded images to localStorage
 */
function saveExcludedImages() {
    try {
        localStorage.setItem('slideshowExcludedImages', JSON.stringify(excludedImages));
    } catch (e) {
        console.warn('[Slideshow] Failed to save excluded images:', e);
    }
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
