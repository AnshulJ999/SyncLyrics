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

// ========== CONSTANTS ==========
const MIN_ZOOM = 1;      // 100% - no zoom out beyond original
const MAX_ZOOM = 8;      // 800% - max zoom for high-res images
const ZOOM_SENSITIVITY = 0.002;  // For scroll wheel
const TRIPLE_TAP_THRESHOLD = 400; // ms between taps

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

// ========== CORE FUNCTIONS ==========

/**
 * Apply current zoom and pan to background layer
 */
function updateTransform() {
    const bg = document.getElementById('background-layer');
    if (!bg) {
        console.warn('[ArtZoom] Background layer not found');
        return;
    }
    
    // Use setProperty with 'important' to override CSS transform: none
    const transformValue = `scale(${zoomLevel}) translate(${panX}px, ${panY}px)`;
    bg.style.setProperty('transform-origin', 'center center', 'important');
    bg.style.setProperty('transform', transformValue, 'important');
    
    // console.log(`[ArtZoom] Transform: scale=${zoomLevel.toFixed(2)}, pan=(${panX.toFixed(0)}, ${panY.toFixed(0)})`);
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

// ========== TOUCH HANDLERS ==========

function handleTouchStart(e) {
    if (!isEnabled) return;
    
    if (e.touches.length === 2) {
        // Pinch start - calculate initial distance between fingers
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        initialPinchDistance = Math.hypot(dx, dy);
        initialZoomLevel = zoomLevel;
    } else if (e.touches.length === 1) {
        // Single touch - start drag
        isDragging = true;
        lastTouchX = e.touches[0].clientX;
        lastTouchY = e.touches[0].clientY;
        
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
    
    if (e.touches.length === 2 && initialPinchDistance > 0) {
        // Pinch zoom
        e.preventDefault();
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        const currentDistance = Math.hypot(dx, dy);
        
        const scale = currentDistance / initialPinchDistance;
        zoomLevel = clampZoom(initialZoomLevel * scale);
        updateTransform();
    } else if (e.touches.length === 1 && isDragging && zoomLevel > 1) {
        // Pan (only when zoomed in)
        e.preventDefault();
        const deltaX = e.touches[0].clientX - lastTouchX;
        const deltaY = e.touches[0].clientY - lastTouchY;
        
        // Divide by zoom level so pan speed feels consistent at any zoom
        panX += deltaX / zoomLevel;
        panY += deltaY / zoomLevel;
        
        lastTouchX = e.touches[0].clientX;
        lastTouchY = e.touches[0].clientY;
        updateTransform();
    }
}

function handleTouchEnd(e) {
    if (!isEnabled) return;
    
    if (e.touches.length < 2) {
        initialPinchDistance = 0;
    }
    if (e.touches.length === 0) {
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
    
    // Touch events
    bg.addEventListener('touchstart', handleTouchStart, { passive: false });
    bg.addEventListener('touchmove', handleTouchMove, { passive: false });
    bg.addEventListener('touchend', handleTouchEnd);
    bg.addEventListener('touchcancel', handleTouchEnd);
    
    // Mouse events
    bg.addEventListener('wheel', handleWheel, { passive: false });
    bg.addEventListener('mousedown', handleMouseDown);
    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    
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
    
    // Remove touch events
    bg.removeEventListener('touchstart', handleTouchStart);
    bg.removeEventListener('touchmove', handleTouchMove);
    bg.removeEventListener('touchend', handleTouchEnd);
    bg.removeEventListener('touchcancel', handleTouchEnd);
    
    // Remove mouse events
    bg.removeEventListener('wheel', handleWheel);
    bg.removeEventListener('mousedown', handleMouseDown);
    document.removeEventListener('mousemove', handleMouseMove);
    document.removeEventListener('mouseup', handleMouseUp);
    
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
