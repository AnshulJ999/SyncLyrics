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
const MIN_ZOOM = 0.1;    // 10% - allow zooming out to see cropped parts
const MAX_ZOOM = 8;      // 800% - max zoom for high-res images
const ZOOM_SENSITIVITY = 0.002;  // For scroll wheel
const TRIPLE_TAP_THRESHOLD = 400; // ms between taps
const EDGE_TAP_SIZE = 80; // pixels from edge for image switching
const EDGE_HOLD_INTERVAL = 900; // ms between image switches when holding edge

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

// Pinch center tracking (for zoom-toward-pinch)
let pinchCenterX = 0;
let pinchCenterY = 0;

// Debug: track first move of each gesture
let debugFirstMove = true;

// ========== IMAGE SWITCHING ==========

/**
 * Switch to next artist image
 */
function nextImage() {
    if (currentArtistImages.length === 0) return;
    currentImageIndex = (currentImageIndex + 1) % currentArtistImages.length;
    applyCurrentImage();
    resetArtZoom();
}

/**
 * Switch to previous artist image
 */
function prevImage() {
    if (currentArtistImages.length === 0) return;
    currentImageIndex = (currentImageIndex - 1 + currentArtistImages.length) % currentArtistImages.length;
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
    
    // Transform with origin at 0,0 (top-left) - simpler math
    // Order: translate first, then scale
    const transformValue = `translate(${panX}px, ${panY}px) scale(${zoomLevel})`;
    bg.style.setProperty('transform-origin', '0 0', 'important');
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
    pinchCenterX = 0;
    pinchCenterY = 0;
    if (edgeHoldInterval) {
        clearInterval(edgeHoldInterval);
        edgeHoldInterval = null;
    }
}

// ========== TOUCH HANDLERS ==========

let touchStartX = 0;
let touchStartY = 0;
let touchMoved = false;

function handleTouchStart(e) {
    if (!isEnabled) return;
    
    touchStartTime = Date.now();
    touchMoved = false;
    debugFirstMove = true;  // Reset for new gesture
    
    if (e.touches.length === 2) {
        // Pinch start - calculate initial distance and center between fingers
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        initialPinchDistance = Math.hypot(dx, dy);
        initialZoomLevel = zoomLevel;
        // Track pinch center for zoom-toward-point
        pinchCenterX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        pinchCenterY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
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
        if (currentArtistImages.length > 1) {
            const isLeftEdge = touchStartX < EDGE_TAP_SIZE;
            const isRightEdge = touchStartX > window.innerWidth - EDGE_TAP_SIZE;
            if (isLeftEdge || isRightEdge) {
                // Start interval after initial delay
                edgeHoldInterval = setInterval(() => {
                    if (isLeftEdge) prevImage();
                    else nextImage();
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
        // Pinch zoom with focal point
        e.preventDefault();
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        const currentDistance = Math.hypot(dx, dy);
        
        // Get current pinch center (it moves during gesture)
        const currentFocalX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
        const currentFocalY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        
        // Calculate new zoom
        const scale = currentDistance / initialPinchDistance;
        const newZoom = clampZoom(initialZoomLevel * scale);
        
        // Zoom toward pinch center using correct formula for origin 0 0
        // Formula: newTranslate = currentFocal - ((initialFocal - oldTranslate) / oldScale) * newScale
        if (newZoom !== zoomLevel) {
            panX = currentFocalX - ((pinchCenterX - panX) / zoomLevel) * newZoom;
            panY = currentFocalY - ((pinchCenterY - panY) / zoomLevel) * newZoom;
            zoomLevel = newZoom;
        }
        updateTransform();
    } else if (e.touches.length === 1 && isDragging) {
        // DIAGNOSTIC: Check if lastTouchX was never initialized
        if (lastTouchX === 0 && lastTouchY === 0 && touchStartX !== 0) {
            showToast('BUG: lastTouch=0,0 but start=' + Math.round(touchStartX), 'error', 3000);
        }
        
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
        
        // For origin 0 0 with translate-then-scale, pan is 1:1 (no zoom division needed)
        // But clamp to prevent huge jumps
        const maxDelta = 100;
        panX += Math.max(-maxDelta, Math.min(maxDelta, deltaX));
        panY += Math.max(-maxDelta, Math.min(maxDelta, deltaY));
        
        // DEBUG: Show toast on first movement of gesture
        if (debugFirstMove) {
            debugFirstMove = false;
            showToast(`Pan: ${Math.round(panX)},${Math.round(panY)} | Δ:${Math.round(deltaX)},${Math.round(deltaY)} | z:${zoomLevel.toFixed(1)}`, 'info', 2000);
        }
        
        lastTouchX = e.touches[0].clientX;
        lastTouchY = e.touches[0].clientY;
        updateTransform();
    }
}

function handleTouchEnd(e) {
    if (!isEnabled) return;
    
    // Clear edge hold interval
    if (edgeHoldInterval) {
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
        
        if (isQuickTap && currentArtistImages.length > 1) {
            // Check if tap was on left or right edge
            if (touchStartX < EDGE_TAP_SIZE) {
                prevImage();
            } else if (touchStartX > window.innerWidth - EDGE_TAP_SIZE) {
                nextImage();
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
    if (zoomLevel <= 1) return; // Only pan when zoomed
    
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

// ========== ENABLE/DISABLE ==========

/**
 * Enable zoom/pan controls (called when entering art-only mode)
 */
export function enableArtZoom() {
    if (isEnabled) return;
    isEnabled = true;
    
    const bg = document.getElementById('background-layer');
    if (!bg) return;
    
    // Touch events on background layer
    bg.addEventListener('touchstart', handleTouchStart, { passive: false });
    bg.addEventListener('touchmove', handleTouchMove, { passive: false });
    bg.addEventListener('touchend', handleTouchEnd);
    bg.addEventListener('touchcancel', handleTouchEnd);
    
    // Fallback: also listen on document.body for when image goes offscreen
    document.body.addEventListener('touchstart', handleTouchStart, { passive: false });
    document.body.addEventListener('touchmove', handleTouchMove, { passive: false });
    document.body.addEventListener('touchend', handleTouchEnd);
    document.body.addEventListener('touchcancel', handleTouchEnd);
    
    // CRITICAL: Disable browser's default touch handling on body
    // Without this, browser may still handle gestures before our JS gets them
    document.body.style.touchAction = 'none';
    
    // Mouse events
    bg.addEventListener('wheel', handleWheel, { passive: false });
    bg.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    
    // Prevent context menu (long-press on Android)
    bg.addEventListener('contextmenu', (e) => e.preventDefault());
    document.body.addEventListener('contextmenu', (e) => {
        if (isEnabled) e.preventDefault();
    });
    
    // Set cursor hint
    bg.style.cursor = zoomLevel > 1 ? 'grab' : 'zoom-in';
    
    console.log('[ArtZoom] Enabled');
}

/**
 * Disable zoom/pan controls (called when exiting art-only mode)
 */
export function disableArtZoom() {
    if (!isEnabled) return;
    isEnabled = false;
    
    const bg = document.getElementById('background-layer');
    if (!bg) return;
    
    // Remove touch events from background layer
    bg.removeEventListener('touchstart', handleTouchStart);
    bg.removeEventListener('touchmove', handleTouchMove);
    bg.removeEventListener('touchend', handleTouchEnd);
    bg.removeEventListener('touchcancel', handleTouchEnd);
    
    // Remove touch events from body fallback
    document.body.removeEventListener('touchstart', handleTouchStart);
    document.body.removeEventListener('touchmove', handleTouchMove);
    document.body.removeEventListener('touchend', handleTouchEnd);
    document.body.removeEventListener('touchcancel', handleTouchEnd);
    
    // Remove mouse events
    bg.removeEventListener('wheel', handleWheel);
    bg.removeEventListener('mousedown', handleMouseDown);
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
    
    // Restore body's touch handling
    document.body.style.touchAction = '';
    
    // Reset all touch state (prevents stale values causing jumps on re-entry)
    resetTouchState();
    
    // Reset transform and cursor
    resetArtZoom();
    bg.style.cursor = '';
    
    console.log('[ArtZoom] Disabled');
}

/**
 * Initialize art zoom module (called once on page load)
 */
export function initArtZoom() {
    // Just setup - actual enable/disable happens via art-only mode toggle
    console.log('[ArtZoom] Module initialized');
}
