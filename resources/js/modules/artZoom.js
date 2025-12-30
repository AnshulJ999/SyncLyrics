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
import { currentArtistImages, slideshowImagePool, slideshowEnabled, lastTrackInfo, slideshowConfig, artModeZoomOutEnabled } from './state.js';

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

// ========== ZOOM-OUT FEATURE (Art Mode) ==========
// Track which zoom img is currently active for crossfade
let activeZoomImg = 'a';  // 'a' or 'b'

/**
 * Get the URL of the currently visible image
 * Checks slideshow images, art-mode images, then background-layer
 */
function getCurrentVisibleImageUrl() {
    const bgLayer = document.getElementById('background-layer');
    if (!bgLayer) return '';
    
    // Priority 1: Active slideshow image
    const slideshowImg = bgLayer.querySelector('.slideshow-image.active');
    if (slideshowImg) {
        return extractUrlFromBackground(slideshowImg.style.backgroundImage);
    }
    
    // Priority 2: Last art-mode image (topmost)
    const artModeImgs = bgLayer.querySelectorAll('.art-mode-image');
    if (artModeImgs.length > 0) {
        const lastImg = artModeImgs[artModeImgs.length - 1];
        return extractUrlFromBackground(lastImg.style.backgroundImage);
    }
    
    // Priority 3: Background layer itself
    const bgImage = bgLayer.style.backgroundImage;
    if (bgImage && bgImage !== 'none') {
        return extractUrlFromBackground(bgImage);
    }
    
    // Priority 4: From lastTrackInfo
    return lastTrackInfo?.album_art_url || lastTrackInfo?.background_image_url || '';
}

/**
 * Extract URL from CSS background-image value
 */
function extractUrlFromBackground(bgImage) {
    if (!bgImage || bgImage === 'none') return '';
    // Remove url(" and ") or url(' and ') or url( and )
    return bgImage.replace(/url\(["']?/, '').replace(/["']?\)/, '');
}

/**
 * Apply fill mode to zoom img based on user preference
 */
function applyFillModeToImg(img) {
    const fillMode = localStorage.getItem('backgroundFillMode') || 'cover';
    switch (fillMode) {
        case 'contain':
            img.style.objectFit = 'contain';
            break;
        case 'stretch':
            img.style.objectFit = 'fill';  // 'fill' stretches to fit
            break;
        case 'original':
            img.style.objectFit = 'none';  // Shows at original size
            break;
        case 'cover':
        default:
            img.style.objectFit = 'cover';
            break;
    }
}

/**
 * Crossfade to a new image on the zoom imgs
 * Alternates between img-a and img-b for smooth transitions
 */
function crossfadeZoomImg(newUrl) {
    const duration = slideshowConfig.transitionDuration || 0.8;
    const currentId = `art-zoom-img-${activeZoomImg}`;
    const nextId = `art-zoom-img-${activeZoomImg === 'a' ? 'b' : 'a'}`;
    
    const currentImg = document.getElementById(currentId);
    const nextImg = document.getElementById(nextId);
    
    if (!currentImg || !nextImg) return;
    
    // Setup next image
    nextImg.src = newUrl;
    applyFillModeToImg(nextImg);
    nextImg.style.opacity = '0';
    nextImg.style.zIndex = '902';  // Above current
    nextImg.style.transition = `opacity ${duration}s ease`;
    
    // Copy transform from current to next (preserve zoom/pan)
    nextImg.style.transform = currentImg.style.transform;
    nextImg.style.transformOrigin = currentImg.style.transformOrigin;
    
    // Crossfade after image loads
    const doFade = () => {
        requestAnimationFrame(() => {
            nextImg.style.opacity = '1';
            currentImg.style.opacity = '0';
            currentImg.style.zIndex = '901';  // Below next
        });
        activeZoomImg = activeZoomImg === 'a' ? 'b' : 'a';
    };
    
    // Check if image is already loaded (cached)
    if (nextImg.complete) {
        doFade();
    } else {
        nextImg.onload = doFade;
    }
}

/**
 * Update zoom img from external source (background.js, slideshow.js)
 * Called when image changes while in art mode
 */
export function syncZoomImgIfInArtMode(newUrl) {
    if (!isEnabled || !artModeZoomOutEnabled) return;
    if (!document.body.classList.contains('zoom-out-enabled')) return;
    
    crossfadeZoomImg(newUrl);
}

// ========== IMAGE SWITCHING ==========

// Forward references for slideshow callbacks (avoids circular import)
let pauseSlideshowFn = null;
let advanceSlideFn = null;
let previousSlideFn = null;
let isSlideshowActiveFn = null;

/**
 * Set slideshow pause callback (called from main.js)
 */
export function setPauseSlideshowFn(fn) {
    pauseSlideshowFn = fn;
}

/**
 * Set slideshow cycling callbacks (called from main.js)
 */
export function setSlideshowCycleFns(advanceFn, previousFn, isActiveFn) {
    advanceSlideFn = advanceFn;
    previousSlideFn = previousFn;
    isSlideshowActiveFn = isActiveFn;
}

/**
 * Mark that user is manually browsing images (with failsafe timeout)
 * Note: No longer pauses slideshow - slideshow continues naturally
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
 * Uses slideshow cycling if slideshow is active, otherwise normal artZoom cycling
 */
function nextImage() {
    // If slideshow is actively cycling, use slideshow advance
    if (isSlideshowActiveFn && isSlideshowActiveFn()) {
        if (advanceSlideFn) {
            advanceSlideFn();
            setManualImageFlag();  // For background.js protection
        }
        return;
    }
    
    // Normal artZoom cycling - use slideshow pool if available
    const imagePool = slideshowEnabled && slideshowImagePool.length > 0 
        ? slideshowImagePool 
        : currentArtistImages;
    
    if (imagePool.length === 0) return;
    currentImageIndex = (currentImageIndex + 1) % imagePool.length;
    setManualImageFlag();  // For background.js protection
    applyCurrentImage();
    resetArtZoom();
}

/**
 * Switch to previous artist image
 * Uses slideshow cycling if slideshow is active, otherwise normal artZoom cycling
 */
function prevImage() {
    // If slideshow is actively cycling, use slideshow previous
    if (isSlideshowActiveFn && isSlideshowActiveFn()) {
        if (previousSlideFn) {
            previousSlideFn();
            setManualImageFlag();  // For background.js protection
        }
        return;
    }
    
    // Normal artZoom cycling - use slideshow pool if available
    const imagePool = slideshowEnabled && slideshowImagePool.length > 0 
        ? slideshowImagePool 
        : currentArtistImages;
    
    if (imagePool.length === 0) return;
    currentImageIndex = (currentImageIndex - 1 + imagePool.length) % imagePool.length;
    setManualImageFlag();  // For background.js protection
    applyCurrentImage();
    resetArtZoom();
}

/**
 * Apply current image to background with crossfade effect
 * Uses slideshow pool when slideshow is enabled, otherwise uses currentArtistImages
 */
function applyCurrentImage() {
    const bg = document.getElementById('background-layer');
    if (!bg) return;
    
    // Use slideshow pool when slideshow is enabled, otherwise use currentArtistImages
    const imagePool = slideshowEnabled && slideshowImagePool.length > 0 
        ? slideshowImagePool 
        : currentArtistImages;
    
    if (imagePool.length === 0) return;
    
    // Clamp index to valid range
    if (currentImageIndex >= imagePool.length) {
        currentImageIndex = 0;
    }
    
    const imageUrl = imagePool[currentImageIndex];
    
    // If zoom-out feature is enabled and active, use zoom img crossfade
    if (artModeZoomOutEnabled && document.body.classList.contains('zoom-out-enabled')) {
        crossfadeZoomImg(imageUrl);
        showToast(`Image ${currentImageIndex + 1}/${imagePool.length}`, 'success', 800);
        preloadAdjacentImages();
        return;  // Skip the old background-layer crossfade
    }
    
    // Original behavior: crossfade using background-layer divs
    
    // Use crossfade technique (like slideshow) to avoid flicker
    const newImg = document.createElement('div');
    newImg.className = 'art-mode-image';
    newImg.style.position = 'absolute';
    newImg.style.top = '0';
    newImg.style.left = '0';
    newImg.style.width = '100%';
    newImg.style.height = '100%';
    newImg.style.backgroundImage = `url('${imageUrl}')`;
    newImg.style.backgroundPosition = 'center';
    newImg.style.opacity = '0';
    const transitionDuration = slideshowConfig.transitionDuration || 0.4;
    newImg.style.transition = `opacity ${transitionDuration}s ease`;
    newImg.style.zIndex = '1';
    
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
    
    bg.appendChild(newImg);
    
    // Fade in
    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            newImg.style.opacity = '1';
        });
    });
    
    // Remove old art-mode images and clear background after transition
    const cleanupDelay = (transitionDuration + 1.1) * 1000;
    setTimeout(() => {
        const oldImages = bg.querySelectorAll('.art-mode-image:not(:last-child)');
        oldImages.forEach(img => img.remove());
        bg.style.backgroundImage = 'none';
    }, cleanupDelay);
    
    // Show toast with correct count based on which pool we're using
    showToast(`Image ${currentImageIndex + 1}/${imagePool.length}`, 'success', 800);
    
    // Preload adjacent images for smooth subsequent browsing
    preloadAdjacentImages();
}

// Track preloaded URLs to avoid duplicates
const preloadedUrls = new Set();

/**
 * Preload images within ±3 of current index using hidden DOM elements
 * DOM-attached images are cached more persistently than new Image()
 */
function preloadAdjacentImages() {
    // Use the same pool that applyCurrentImage uses
    const imagePool = slideshowEnabled && slideshowImagePool.length > 0 
        ? slideshowImagePool 
        : currentArtistImages;
    
    if (imagePool.length === 0) return;
    
    const PRELOAD_RANGE = 3;  // Preload 3 images in each direction
    
    // Create or get container for preloaded images
    let container = document.getElementById('preload-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'preload-container';
        container.style.cssText = 'position:absolute;width:0;height:0;overflow:hidden;visibility:hidden;';
        document.body.appendChild(container);
    }
    
    for (let offset = -PRELOAD_RANGE; offset <= PRELOAD_RANGE; offset++) {
        if (offset === 0) continue;  // Skip current (already loaded)
        
        const index = (currentImageIndex + offset + imagePool.length) % imagePool.length;
        const url = imagePool[index];
        
        // Skip if already preloaded
        if (preloadedUrls.has(url)) continue;
        preloadedUrls.add(url);
        
        // Option 3: Add link preload hint
        const link = document.createElement('link');
        link.rel = 'preload';
        link.as = 'image';
        link.href = url;
        document.head.appendChild(link);
        
        // Option 4: Add hidden img to DOM (more reliable caching)
        const img = document.createElement('img');
        img.src = url;
        img.loading = 'eager';  // Force immediate load
        container.appendChild(img);
    }
}

/**
 * Apply current zoom and pan to background layer (or zoom imgs if feature enabled)
 */
function updateTransform() {
    // Determine target element based on feature flag
    let target;
    if (isEnabled && artModeZoomOutEnabled && document.body.classList.contains('zoom-out-enabled')) {
        // Apply transform to BOTH zoom imgs (they share transform state)
        const imgA = document.getElementById('art-zoom-img-a');
        const imgB = document.getElementById('art-zoom-img-b');
        
        // Get viewport dimensions
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        
        // Get the active image to read natural dimensions
        const activeImg = activeZoomImg === 'a' ? imgA : imgB;
        if (!activeImg || !activeImg.naturalWidth) {
            // Image not loaded yet, skip
            return;
        }
        
        // Calculate the "cover scale" - minimum scale to make image cover viewport
        // This is what object-fit: cover does, but we calculate it manually
        const imgW = activeImg.naturalWidth;
        const imgH = activeImg.naturalHeight;
        const coverScale = Math.max(vw / imgW, vh / imgH);
        
        // Apply zoom level on top of cover scale
        // At zoomLevel=1, we match object-fit:cover behavior
        // At zoomLevel<1, we reveal more of the image (zoom out)
        // At zoomLevel>1, we zoom in further
        const finalScale = coverScale * zoomLevel;
        
        // Apply bounds checking for panning
        const maxPanX = vw * 0.75 * zoomLevel;
        const maxPanY = vh * 0.75 * zoomLevel;
        panX = Math.max(-maxPanX, Math.min(maxPanX, panX));
        panY = Math.max(-maxPanY, Math.min(maxPanY, panY));
        
        // Combine centering + scale + pan
        // CSS already has translate(-50%, -50%) for centering, but we override with full transform
        const transformValue = `translate(-50%, -50%) scale(${finalScale}) translate(${panX / finalScale}px, ${panY / finalScale}px)`;
        
        if (imgA) {
            imgA.style.setProperty('transform', transformValue, 'important');
        }
        if (imgB) {
            imgB.style.setProperty('transform', transformValue, 'important');
        }
        return;
    }
    
    // Fallback: use background-layer (original behavior)
    target = document.getElementById('background-layer');
    if (!target) {
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
    target.style.setProperty('transform-origin', 'center center', 'important');
    target.style.setProperty('transform', transformValue, 'important');
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
        if (dx > 90 || dy > 90) {
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
    
    // Setup zoom imgs if feature is enabled
    if (artModeZoomOutEnabled) {
        const currentUrl = getCurrentVisibleImageUrl();
        const imgA = document.getElementById('art-zoom-img-a');
        const imgB = document.getElementById('art-zoom-img-b');
        
        if (imgA && imgB && currentUrl) {
            // Setup primary image
            imgA.src = currentUrl;
            applyFillModeToImg(imgA);
            imgA.style.opacity = '1';
            imgA.style.zIndex = '901';
            imgA.style.transform = '';
            
            // Setup secondary (hidden, for crossfade)
            imgB.style.opacity = '0';
            imgB.style.zIndex = '900';
            imgB.style.transform = '';
            
            activeZoomImg = 'a';
            
            // Enable zoom-out mode
            document.body.classList.add('zoom-out-enabled');
            console.log('[ArtZoom] Zoom-out enabled with image:', currentUrl.substring(0, 50) + '...');
        }
    }
    
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
    
    // Set cursor hint on background (or zoom img)
    if (artModeZoomOutEnabled) {
        const activeImg = document.getElementById(`art-zoom-img-${activeZoomImg}`);
        if (activeImg) activeImg.style.cursor = 'grab';
    } else {
        bg.style.cursor = 'grab';
    }
    
    console.log('[ArtZoom] Enabled');
}

/**
 * Disable zoom/pan controls (called when exiting art-only mode)
 */
export function disableArtZoom() {
    if (!isEnabled) return;
    isEnabled = false;
    
    const bg = document.getElementById('background-layer');
    
    // Cleanup zoom imgs if feature was enabled
    if (artModeZoomOutEnabled) {
        document.body.classList.remove('zoom-out-enabled');
        
        const imgA = document.getElementById('art-zoom-img-a');
        const imgB = document.getElementById('art-zoom-img-b');
        
        if (imgA) {
            imgA.style.transform = '';
            imgA.style.cursor = '';
        }
        if (imgB) {
            imgB.style.transform = '';
            imgB.style.cursor = '';
        }
    }
    
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
