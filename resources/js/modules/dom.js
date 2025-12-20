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
    setLastLyrics,
    setUpdateInProgress
} from './state.js';
import { areLyricsDifferent } from './utils.js';
// Note: Word-sync imports removed - animation loop is now single authority for lyrics during word-sync

// ========== SCROLL ANIMATION CONFIG ==========
// CONFIGURABLE: Change this value to experiment with different animation durations
// Try: 100, 150, 200, 250, 300 (milliseconds)
// Note: This should match SCROLL_DURATION_MS in wordSync.js
const SCROLL_DURATION_MS = 100;  // Total animation duration

// Token for scroll animation cancellation (prevents stale timeouts)
let scrollToken = 0;

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

    // Check if the current line (index 2) actually changed
    // Store old value BEFORE updating lastLyrics
    const oldCurrentLine = lastLyrics ? lastLyrics[2] : undefined;
    const currentLineChanged = oldCurrentLine !== lyrics[2];
    
    setUpdateInProgress(true);
    setLastLyrics([...lyrics]);

    const lyricsContainer = document.getElementById('lyrics');
    
    // SCROLL ANIMATION: Only trigger if the current line changed
    // (not for initial load or just surrounding lines changing)
    if (currentLineChanged && oldCurrentLine !== '' && oldCurrentLine !== undefined) {
        // Claim a new scroll token (cancels any pending callbacks)
        const myToken = ++scrollToken;
        
        // Calculate EXACT pixel distance between current and prev-1
        const currentEl = document.getElementById('current');
        const prev1El = document.getElementById('prev-1');
        const scrollDist = (currentEl && prev1El) ? (currentEl.offsetTop - prev1El.offsetTop) : 50;
        
        // Set CSS variables and trigger animation
        if (lyricsContainer) {
            lyricsContainer.style.setProperty('--scroll-duration', `${SCROLL_DURATION_MS}ms`);
            lyricsContainer.style.setProperty('--dynamic-scroll-dist', `${scrollDist}px`);
            lyricsContainer.classList.add('scrolling-up');
        }
        
        // MIDPOINT SWAP: Update content at peak motion (hidden by blur + speed)
        const midpoint = Math.floor(SCROLL_DURATION_MS / 2);
        setTimeout(() => {
            if (scrollToken !== myToken) return;
            
            // Update all elements while in motion
            updateLyricElement(document.getElementById('prev-2'), lyrics[0]);
            updateLyricElement(document.getElementById('prev-1'), lyrics[1]);
            updateLyricElement(document.getElementById('current'), lyrics[2]);
            updateLyricElement(document.getElementById('next-1'), lyrics[3]);
            updateLyricElement(document.getElementById('next-2'), lyrics[4]);
            updateLyricElement(document.getElementById('next-3'), lyrics[5]);
        }, midpoint);
        
        // END: Remove animation class after full duration
        setTimeout(() => {
            if (scrollToken !== myToken) return;
            
            if (lyricsContainer) {
                lyricsContainer.classList.remove('scrolling-up');
            }
            setUpdateInProgress(false);
        }, SCROLL_DURATION_MS);
    } else {
        // No scroll animation needed - just update immediately
        // (initial load, or only surrounding lines changed)
        updateLyricElement(document.getElementById('prev-2'), lyrics[0]);
        updateLyricElement(document.getElementById('prev-1'), lyrics[1]);
        updateLyricElement(document.getElementById('current'), lyrics[2]);
        updateLyricElement(document.getElementById('next-1'), lyrics[3]);
        updateLyricElement(document.getElementById('next-2'), lyrics[4]);
        updateLyricElement(document.getElementById('next-3'), lyrics[5]);
        
        setTimeout(() => {
            setUpdateInProgress(false);
        }, 100);
    }

    // Self-healing: If we are showing lyrics and NOT in visual mode, ensure the hidden class is gone
    if (!visualModeActive) {
        if (lyricsContainer && lyricsContainer.classList.contains('visual-mode-hidden')) {
            console.log('[Visual Mode] Found hidden class while inactive - removing (Self-healing)');
            lyricsContainer.classList.remove('visual-mode-hidden');
        }
    }
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
 */
export function showToast(message, type = 'success') {
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
    }, 3000);
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
