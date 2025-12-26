/**
 * artZoom.js - Zoom and Pan for Art-Only Mode
 * 
 * Provides touch-first zoom/pan controls for the background layer when in art-only mode.
 * - Pinch-to-zoom (touch)
 * - Drag-to-pan (touch + mouse)
 * - Scroll-to-zoom (mouse fallback)
 * - Triple-tap to reset
 * 
 * Level 2 - Imports: state, dom
 */

import { showToast } from './dom.js';
import { currentArtistImages } from './state.js';

// ========== CONSTANTS ==========
const MIN_ZOOM = 0.3;    // 30% - allow zooming out a bit
const MAX_ZOOM = 5;      // 500% - max zoom for high-res images
const ZOOM_SENSITIVITY = 0.002;  // For scroll wheel
const TRIPLE_TAP_THRESHOLD = 400; // ms between taps
const EDGE_TAP_SIZE = 100; // pixels from edge for image switching
const EDGE_HOLD_INTERVAL = 900; // ms between image switches when holding edge
const MANUAL_IMAGE_TIMEOUT = 30 * 60 * 1000;  // 30 min failsafe for manual image flag

// ========== STATE ==========
let zoomLevel = 1;
let panX = 0;
let panY = 0;
let isEnabled = false;

// Touch state
let initialPinchDistance = 0;
let initialZoomLevel = 1;
let isDragging = false;
let lastTouchX = 0;
let lastTouchY = 0;
let lastMouseX = 0;
let lastMouseY = 0;

// Triple-tap detection
let tapCount = 0;
let lastTapTime = 0;

// Image switching state
let currentImageIndex = 0;
let touchStartTime = 0;
let edgeHoldInterval = null; // For hold-to-cycle on edges

// Manual artist image preservation
let isUsingManualArtistImage = false;  // True when user manually browses artist images
let manualImageTimeout = null;  // Failsafe timeout to reset the flag

// ========== IMAGE SWITCHING ==========

/**
 * Mark that user is manually browsing images (with failsafe timeout)
 */
function setManualImageFlag() {
    isUsingManualArtistImage = true;
    // Failsafe: reset flag after 30 min in case it gets stuck
    if (manualImageTimeout) clearTimeout(manualImageTimeout);
    manualImageTimeout = setTimeout(() => {
        isUsingManualArtistImage = false;
        console.log('[ArtZoom] Manual image flag reset by timeout');
    }, MANUAL_IMAGE_TIMEOUT);
}

/**
 * Check if user is manually viewing artist images (for background.js)
 */
export function isManualArtistImageActive() {
    return isEnabled && isUsingManualArtistImage;
}

/**
 * Reset manual image flag (called on artist change or exit art-only)
 */
export function resetManualImageFlag() {
    isUsingManualArtistImage = false;
    if (manualImageTimeout) {
        clearTimeout(manualImageTimeout);
        manualImageTimeout = null;
    }
}

/**
 * Switch to next artist image
 */
function nextImage() {
    if (currentArtistImages.length === 0) return;
    currentImageIndex = (currentImageIndex + 1) % currentArtistImages.length;
    setManualImageFlag();  // User is manually browsing
    applyCurrentImage();
    resetArtZoom();
}

/**
 * Switch to previous artist image
 */
function prevImage() {
    if (currentArtistImages.length === 0) return;
    currentImageIndex = (currentImageIndex - 1 + currentArtistImages.length) % currentArtistImages.length;
    setManualImageFlag();  // User is manually browsing
    applyCurrentImage();
    resetArtZoom();
}

/**
 * Apply current image to background
 */
function applyCurrentImage() {
    const bg = document.getElementById('background-layer');
    if (!bg || currentArtistImages.length === 0) return;
    
    const imageUrl = currentArtistImages[currentImageIndex];
    bg.style.backgroundImage = `url('${imageUrl}')`;
    showToast(`Image ${currentImageIndex + 1}/${currentArtistImages.length}`, 'success', 800);
}

/**
 * Apply current zoom and pan to background layer
 */
function updateTransform() {
    const bg = document.getElementById('background-layer');
    if (!bg) {
        console.warn('[ArtZoom] Background layer not found');
        return;
    }
    
    // Apply bounds checking - keep at least 25% of image visible
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const maxPanX = vw * 0.75 * zoomLevel;  // 75% can go offscreen = 25% visible
    const maxPanY = vh * 0.75 * zoomLevel;
    panX = Math.max(-maxPanX, Math.min(maxPanX, panX));
    panY = Math.max(-maxPanY, Math.min(maxPanY, panY));
    
    // Transform with origin at center - natural zoom behavior
    const transformValue = `scale(${zoomLevel}) translate(${panX}px, ${panY}px)`;
    bg.style.setProperty('transform-origin', 'center center', 'important');
    bg.style.setProperty('transform', transformValue, 'important');
}

/**
 * Clamp zoom level to valid range
 */
function clampZoom(zoom) {
    return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom));
}

/**
 * Reset zoom and pan to defaults
 */
export function resetArtZoom() {
    zoomLevel = 1;
    panX = 0;
    panY = 0;
    updateTransform();
}

/**
 * Reset all touch state (called when disabling to prevent stale values)
 */
function resetTouchState() {
    isDragging = false;
    initialPinchDistance = 0;
    initialZoomLevel = 1;
    lastTouchX = 0;
    lastTouchY = 0;
    lastMouseX = 0;
    lastMouseY = 0;
    touchStartX = 0;
    touchStartY = 0;
    touchMoved = false;
    touchStartTime = 0;
    tapCount = 0;
    lastTapTime = 0;
    if (edgeHoldInterval) {
        clearInterval(edgeHoldInterval);
        edgeHoldInterval = null;
    }
}

/**
 * Reset image index to 0 (called when artist changes)
 */
export function resetImageIndex() {
    currentImageIndex = 0;
}

// ========== TOUCH HANDLERS ==========

let touchStartX = 0;
let touchStartY = 0;
let touchMoved = false;

function handleTouchStart(e) {
    if (!isEnabled) return;
    
    touchStartTime = Date.now();
    touchMoved = false;
    
    if (e.touches.length === 2) {
        // Pinch start - calculate initial distance between fingers
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        initialPinchDistance = Math.hypot(dx, dy);
        initialZoomLevel = zoomLevel;
        // Clear any edge hold interval (two fingers = not edge hold)
        if (edgeHoldInterval) {
            clearInterval(edgeHoldInterval);
            edgeHoldInterval = null;
        }
    } else if (e.touches.length === 1) {
        // Single touch - start drag
        isDragging = true;
        lastTouchX = e.touches[0].clientX;
        lastTouchY = e.touches[0].clientY;
        touchStartX = e.touches[0].clientX;
        touchStartY = e.touches[0].clientY;
        
        // Check if on edge - start hold-to-cycle interval
        // Guard against double-firing (we have listeners on both bg and body)
        if (currentArtistImages.length > 1 && !edgeHoldInterval) {
            const isLeftEdge = touchStartX < EDGE_TAP_SIZE;
            const isRightEdge = touchStartX > window.innerWidth - EDGE_TAP_SIZE;
            if (isLeftEdge || isRightEdge) {
                // Start interval with initial delay before first cycle
                edgeHoldInterval = setTimeout(() => {
                    // First cycle
                    if (isLeftEdge) prevImage();
                    else nextImage();
                    // Then continue cycling
                    edgeHoldInterval = setInterval(() => {
                        if (isLeftEdge) prevImage();
                        else nextImage();
                    }, EDGE_HOLD_INTERVAL);
                }, EDGE_HOLD_INTERVAL);
            }
        }
        
        // Triple-tap detection
        const now = Date.now();
        if (now - lastTapTime < TRIPLE_TAP_THRESHOLD) {
            tapCount++;
            if (tapCount >= 3) {
                resetArtZoom();
                showToast('Zoom reset', 'success', 1000);
                tapCount = 0;
            }
        } else {
            tapCount = 1;
        }
        lastTapTime = now;
    }
}

function handleTouchMove(e) {
    if (!isEnabled) return;
    
    touchMoved = true;  // Mark that we moved (not just a tap)
    
    // Clear edge hold interval if user starts moving (they're panning, not holding)
    if (edgeHoldInterval) {
        const dx = Math.abs(e.touches[0].clientX - touchStartX);
        const dy = Math.abs(e.touches[0].clientY - touchStartY);
        if (dx > 10 || dy > 10) {
            clearInterval(edgeHoldInterval);
            edgeHoldInterval = null;
        }
    }
    
    if (e.touches.length === 2 && initialPinchDistance > 0) {
        // Pinch zoom - simple zoom toward center
        e.preventDefault();
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        const currentDistance = Math.hypot(dx, dy);
        
        // Calculate new zoom based on pinch distance ratio
        const scale = currentDistance / initialPinchDistance;
        zoomLevel = clampZoom(initialZoomLevel * scale);
        updateTransform();
    } else if (e.touches.length === 1 && isDragging) {
        // Pan - but only if movement exceeds dead zone (prevents jitter-induced jumps)
        const dx = e.touches[0].clientX - touchStartX;
        const dy = e.touches[0].clientY - touchStartY;
        const DRAG_DEADZONE = 15;  // Must move 15px from start before pan activates
        
        if (Math.abs(dx) < DRAG_DEADZONE && Math.abs(dy) < DRAG_DEADZONE) {
            return;  // Ignore micro-movements (jitter)
        }
        
        e.preventDefault();
        const deltaX = e.touches[0].clientX - lastTouchX;
        const deltaY = e.touches[0].clientY - lastTouchY;
        
        // Scale pan by zoom level for consistent feel (clamp to prevent huge jumps)
        const maxDelta = 100;
        const scaledDeltaX = Math.max(-maxDelta, Math.min(maxDelta, deltaX / zoomLevel));
        const scaledDeltaY = Math.max(-maxDelta, Math.min(maxDelta, deltaY / zoomLevel));
        panX += scaledDeltaX;
        panY += scaledDeltaY;
        
        lastTouchX = e.touches[0].clientX;
        lastTouchY = e.touches[0].clientY;
        updateTransform();
    }
}

function handleTouchEnd(e) {
    if (!isEnabled) return;
    
    // Clear edge hold interval (could be timeout or interval, clearTimeout handles both)
    if (edgeHoldInterval) {
        clearTimeout(edgeHoldInterval);
        clearInterval(edgeHoldInterval);
        edgeHoldInterval = null;
    }
    
    if (e.touches.length < 2) {
        initialPinchDistance = 0;
    }
    
    // Check for edge tap (quick tap, minimal movement)
    if (e.touches.length === 0 && isDragging) {
        const tapDuration = Date.now() - touchStartTime;
        const dx = Math.abs(lastTouchX - touchStartX);
        const dy = Math.abs(lastTouchY - touchStartY);
        const isQuickTap = tapDuration < 300 && dx < 20 && dy < 20;
        
        if (isQuickTap && currentArtistImages.length > 0) {
            // Check if tap was on left or right edge
            if (touchStartX < EDGE_TAP_SIZE) {
                prevImage();
                e.preventDefault();  // Prevent synthetic mouse click
            } else if (touchStartX > window.innerWidth - EDGE_TAP_SIZE) {
                nextImage();
                e.preventDefault();  // Prevent synthetic mouse click
            }
        }
        
        isDragging = false;
    }
}

// ========== MOUSE HANDLERS ==========

function handleWheel(e) {
    if (!isEnabled) return;
    
    e.preventDefault();
    
    // Zoom based on scroll delta
    const delta = -e.deltaY * ZOOM_SENSITIVITY;
    const newZoom = clampZoom(zoomLevel * (1 + delta));
    
    // Zoom toward cursor position for natural feel
    if (newZoom !== zoomLevel) {
        zoomLevel = newZoom;
        updateTransform();
    }
}

function handleMouseDown(e) {
    if (!isEnabled) return;
    
    isDragging = true;
    lastMouseX = e.clientX;
    lastMouseY = e.clientY;
    document.body.style.cursor = 'grabbing';
}

function handleMouseMove(e) {
    if (!isEnabled || !isDragging) return;
    
    const deltaX = e.clientX - lastMouseX;
    const deltaY = e.clientY - lastMouseY;
    
    panX += deltaX / zoomLevel;
    panY += deltaY / zoomLevel;
    
    lastMouseX = e.clientX;
    lastMouseY = e.clientY;
    updateTransform();
}

function handleMouseUp() {
    if (!isEnabled) return;
    
    isDragging = false;
    document.body.style.cursor = '';
}

/**
 * Handle click events for PC edge clicking
 * Separate from mousedown/mouseup because we need to detect clean clicks
 */
function handleClick(e) {
    if (!isEnabled) return;
    if (currentArtistImages.length === 0) return;
    
    // Check if click was on left or right edge
    if (e.clientX < EDGE_TAP_SIZE) {
        prevImage();
    } else if (e.clientX > window.innerWidth - EDGE_TAP_SIZE) {
        nextImage();
    }
}

// ========== ENABLE/DISABLE ==========

/**
 * Enable zoom/pan controls (called when entering art-only mode)
 */
export function enableArtZoom() {
    if (isEnabled) return;
    isEnabled = true;
    
    const bg = document.getElementById('background-layer');
    if (!bg) return;
    // Touch events on body (covers everything, avoids double-firing)
    document.body.addEventListener('touchstart', handleTouchStart, { passive: false });
    document.body.addEventListener('touchmove', handleTouchMove, { passive: false });
    document.body.addEventListener('touchend', handleTouchEnd);
    document.body.addEventListener('touchcancel', handleTouchEnd);
    
    // Disable browser's default touch handling
    document.body.style.touchAction = 'none';
    
    // Mouse events
    document.addEventListener('wheel', handleWheel, { passive: false });
    document.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    document.addEventListener('click', handleClick);  // PC edge clicking
    
    // Prevent context menu (long-press on Android)
    document.body.addEventListener('contextmenu', (e) => {
        if (isEnabled) e.preventDefault();
    });
    
    // Set cursor hint on background
    bg.style.cursor = 'grab';
    
    console.log('[ArtZoom] Enabled');
}

/**
 * Disable zoom/pan controls (called when exiting art-only mode)
 */
export function disableArtZoom() {
    if (!isEnabled) return;
    isEnabled = false;
    
    const bg = document.getElementById('background-layer');
    
    // Remove touch events
    document.body.removeEventListener('touchstart', handleTouchStart);
    document.body.removeEventListener('touchmove', handleTouchMove);
    document.body.removeEventListener('touchend', handleTouchEnd);
    document.body.removeEventListener('touchcancel', handleTouchEnd);
    
    // Remove mouse events
    document.removeEventListener('wheel', handleWheel);
    document.removeEventListener('mousedown', handleMouseDown);
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
    document.removeEventListener('click', handleClick);
    
    // Restore body's touch handling
    document.body.style.touchAction = '';
    
    // Reset manual image flag
    resetManualImageFlag();
    
    // Reset all touch state
    resetTouchState();
    
    // Reset transform and cursor
    resetArtZoom();
    if (bg) bg.style.cursor = '';
    
    console.log('[ArtZoom] Disabled');
}

/**
 * Initialize art zoom module (called once on page load)
 */
export function initArtZoom() {
    // Just setup - actual enable/disable happens via art-only mode toggle
    console.log('[ArtZoom] Module initialized');
}
