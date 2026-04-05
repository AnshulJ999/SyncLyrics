/**
 * dom.js - DOM Manipulation Helpers
 * 
 * This module contains DOM manipulation functions and UI helpers.
 * Centralizes all direct DOM access for consistency.
 * 
 * Level 1 - Imports: state
 */

import {
    lastLyrics,
    updateInProgress,
    visualModeActive,
    hasWordSync,
    wordSyncEnabled,
    pixelScrollEnabled,
    pixelScrollSpeed,
    setLastLyrics,
    setUpdateInProgress
} from './state.js';
import { areLyricsDifferent } from './utils.js';
// Note: Word-sync imports removed - animation loop is now single authority for lyrics during word-sync

// ========== PIXEL SCROLL STATE ==========
let _pixelScrollInner = null;  // Cached inner wrapper element
let _pixelScrollInitialized = false;
let _pixelScrollAnimating = false;

// ========== ELEMENT CACHE ==========
// Cache for frequently accessed elements
const elementCache = new Map();

/**
 * Get element by ID with caching
 * 
 * @param {string} id - Element ID
 * @returns {HTMLElement|null} The element or null
 */
export function getElement(id) {
    if (!elementCache.has(id)) {
        elementCache.set(id, document.getElementById(id));
    }
    return elementCache.get(id);
}

/**
 * Clear element cache (call when DOM changes significantly)
 */
export function clearElementCache() {
    elementCache.clear();
}

// ========== LYRIC ELEMENT UPDATES ==========

/**
 * Update a lyric element's text content only if changed
 * 
 * @param {HTMLElement} element - The element to update
 * @param {string} text - New text content
 */
export function updateLyricElement(element, text) {
    if (element && element.textContent !== text) {
        element.textContent = text;
    }
}

/**
 * Initialize pixel scroll wrapper.
 * Wraps all lyric-line elements inside a .pixel-scroll-inner div.
 */
export function initPixelScroll() {
    if (_pixelScrollInitialized) return;
    const container = document.getElementById('lyrics');
    if (!container) return;
    console.log('[PixelScroll] Initializing pixel scroll wrapper');

    // Ensure the pixel-scroll class is on the container (CSS rules depend on it)
    container.classList.add('pixel-scroll');

    // Create inner wrapper
    const inner = document.createElement('div');
    inner.className = 'pixel-scroll-inner';

    // Move all lyric lines into the wrapper
    const lines = container.querySelectorAll('.lyric-line');
    lines.forEach(line => inner.appendChild(line));
    container.appendChild(inner);

    _pixelScrollInner = inner;
    _pixelScrollInitialized = true;
}

/**
 * Remove pixel scroll wrapper, restoring original DOM structure.
 */
export function destroyPixelScroll() {
    if (!_pixelScrollInitialized || !_pixelScrollInner) return;
    const container = document.getElementById('lyrics');
    if (!container) return;

    // Remove the pixel-scroll class
    container.classList.remove('pixel-scroll');

    // Move lines back out of wrapper
    while (_pixelScrollInner.firstChild) {
        container.appendChild(_pixelScrollInner.firstChild);
    }
    _pixelScrollInner.remove();
    _pixelScrollInner = null;
    _pixelScrollInitialized = false;
}

/**
 * Perform a pixel scroll animation for line-sync mode using Web Animations API.
 * 1. Set content to NEW state
 * 2. Animate inner wrapper from offset DOWN to translateY(0) (smooth scroll up)
 */
function pixelScrollAnimate(lyrics) {
    if (!_pixelScrollInner || _pixelScrollAnimating) {
        updateAllLyricElements(lyrics);
        return;
    }

    const currentEl = document.getElementById('current');
    if (!currentEl) {
        updateAllLyricElements(lyrics);
        return;
    }

    const container = document.getElementById('lyrics');
    const gap = container ? parseFloat(getComputedStyle(container).gap) || 0 : 0;
    const scrollDistance = currentEl.offsetHeight + gap;

    _pixelScrollAnimating = true;

    // 1. Update content to new state
    updateAllLyricElements(lyrics);

    // 2. Animate using Web Animations API (no reflow hacks or CSS class juggling)
    console.log(`[PixelScroll] Animating: distance=${scrollDistance}px, speed=${pixelScrollSpeed}ms`);
    const animation = _pixelScrollInner.animate([
        { transform: `translateY(${scrollDistance}px)` },
        { transform: 'translateY(0)' }
    ], {
        duration: pixelScrollSpeed,
        easing: 'cubic-bezier(0.25, 0.1, 0.25, 1)'
    });

    // 3. Clean up via native Promise (reliable even when tab is backgrounded)
    animation.finished.then(() => {
        _pixelScrollAnimating = false;
    }).catch(() => {
        _pixelScrollAnimating = false;
    });
}

/**
 * Update all 6 lyric line elements with new text.
 */
function updateAllLyricElements(lyrics) {
    updateLyricElement(document.getElementById('prev-2'), lyrics[0]);
    updateLyricElement(document.getElementById('prev-1'), lyrics[1]);
    updateLyricElement(document.getElementById('current'), lyrics[2]);
    updateLyricElement(document.getElementById('next-1'), lyrics[3]);
    updateLyricElement(document.getElementById('next-2'), lyrics[4]);
    updateLyricElement(document.getElementById('next-3'), lyrics[5]);
}

/**
 * Set lyrics in the DOM
 *
 * @param {Array|Object} lyrics - Lyrics array or object with msg property
 */
export function setLyricsInDom(lyrics) {
    if (updateInProgress) return;
    if (!Array.isArray(lyrics)) {
        lyrics = ['', '', lyrics.msg || '', '', '', ''];
    }

    // When word-sync is active and enabled, the animation loop (wordSync.js) is
    // the SINGLE AUTHORITY for all 6 lyric lines. It updates surrounding lines
    // exactly when line changes, preventing timing mismatches.
    // We still need to handle the initial state before animation starts.
    if (hasWordSync && wordSyncEnabled) {
        // Only update lastLyrics for tracking, but don't touch DOM
        setLastLyrics([...lyrics]);
        return;
    }

    // Line-sync mode: handle normally with change detection
    if (!areLyricsDifferent(lastLyrics, lyrics)) {
        return;
    }

    setUpdateInProgress(true);

    // Check if the active line (index 2) changed - that's a line transition
    const activeLineChanged = lastLyrics && lyrics[2] !== lastLyrics[2];

    setLastLyrics([...lyrics]);

    // Pixel scroll: animate if enabled and active line changed
    if (pixelScrollEnabled && _pixelScrollInitialized && activeLineChanged) {
        pixelScrollAnimate(lyrics);
    } else {
        updateAllLyricElements(lyrics);
    }

    // Self-healing: If we are showing lyrics and NOT in visual mode, ensure the hidden class is gone
    if (!visualModeActive) {
        const lyricsContainer = document.getElementById('lyrics');
        if (lyricsContainer && lyricsContainer.classList.contains('visual-mode-hidden')) {
            console.log('[Visual Mode] Found hidden class while inactive - removing (Self-healing)');
            lyricsContainer.classList.remove('visual-mode-hidden');
        }
    }

    setTimeout(() => {
        setUpdateInProgress(false);
    }, 100);
}

// ========== THEME COLOR ==========

/**
 * Update the theme-color meta tag dynamically when album colors change.
 * This updates the Android status bar and task switcher preview color.
 * 
 * @param {string} color - The color to set (hex format, e.g., "#1db954")
 */
export function updateThemeColor(color) {
    const metaThemeColor = document.querySelector('meta[name="theme-color"]');
    if (metaThemeColor && color) {
        metaThemeColor.setAttribute('content', color);
    }
}

// ========== TOAST NOTIFICATIONS ==========

/**
 * Show a toast notification
 * 
 * @param {string} message - Message to display
 * @param {string} type - 'success' or 'error'
 * @param {number} durationMs - Duration in milliseconds (default 3000)
 */
export function showToast(message, type = 'success', durationMs = 3000) {
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    document.body.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('show');
    }, 10);

    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, durationMs);
}

// ========== UTILITY DOM FUNCTIONS ==========

/**
 * Toggle a class on an element based on a condition
 * 
 * @param {HTMLElement} element - The element
 * @param {string} className - Class name to toggle
 * @param {boolean} condition - Whether to add (true) or remove (false) the class
 */
export function toggleClass(element, className, condition) {
    if (element) {
        element.classList.toggle(className, condition);
    }
}

/**
 * Set visibility of an element
 * 
 * @param {HTMLElement|string} elementOrId - Element or element ID
 * @param {boolean} visible - Whether to show (true) or hide (false)
 * @param {string} displayType - CSS display type when visible (default: 'block')
 */
export function setVisible(elementOrId, visible, displayType = 'block') {
    const element = typeof elementOrId === 'string'
        ? document.getElementById(elementOrId)
        : elementOrId;
    if (element) {
        element.style.display = visible ? displayType : 'none';
    }
}

/**
 * Safely encode a URL for use in CSS background-image
 * 
 * @param {string} url - URL to encode
 * @returns {string} Safe URL for CSS
 */
export function encodeBackgroundUrl(url) {
    return encodeURI(url);
}
