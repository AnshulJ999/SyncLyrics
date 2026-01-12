/**
 * latency.js - Per-Song Word-Sync Latency Adjustment
 * 
 * This module handles user adjustments to word-sync timing for individual songs.
 * Offsets are applied immediately and saved to the backend with debouncing.
 * 
 * Level 3 - Imports: state, api, dom
 */

import {
    songWordSyncOffset,
    setSongWordSyncOffset,
    lastTrackInfo,
    hasWordSync,
    wordSyncEnabled
} from './state.js';
import { showToast } from './dom.js';

// ========== MODULE STATE ==========

let saveTimeoutId = null;
let toastTimeoutId = null;  // Separate debounce for toast feedback
let lastManualAdjustMs = 0; // Timestamp of last manual adjustment (for override guard)
const SAVE_DEBOUNCE_MS = 800;  // Debounce delay for saving
const TOAST_DEBOUNCE_MS = 150; // Debounce delay for toast (show final value)
const MANUAL_OVERRIDE_WINDOW_MS = 1000; // Ignore server offset for 1s after manual adjustment
const STEP_SIZE = 0.05;        // 50ms adjustment per click

// ========== GUARD FUNCTION ==========

/**
 * Check if user is actively adjusting latency (within override window)
 * Used by api.js to skip applying server offset during manual adjustments
 * @returns {boolean} True if manual adjustment is in progress
 */
export function isLatencyBeingAdjusted() {
    return performance.now() - lastManualAdjustMs < MANUAL_OVERRIDE_WINDOW_MS;
}

// ========== CORE FUNCTIONS ==========

/**
 * Adjust the per-song word-sync offset
 * @param {number} delta - Change in seconds (positive = later, negative = earlier)
 */
export function adjustLatency(delta) {
    // Mark as manual adjustment (prevents polling from overwriting)
    lastManualAdjustMs = performance.now();
    
    // Calculate new offset (clamped to ±1.0 second)
    const currentOffset = songWordSyncOffset;
    const newOffset = Math.max(-1.0, Math.min(1.0, currentOffset + delta));
    
    // Apply immediately (frontend state)
    setSongWordSyncOffset(newOffset);
    
    // Update display immediately
    updateLatencyDisplay(newOffset);
    
    // Debounced save to backend
    debouncedSave(newOffset);
    
    // Debounced toast feedback (prevents DOM spam during rapid clicks)
    if (toastTimeoutId) {
        clearTimeout(toastTimeoutId);
    }
    toastTimeoutId = setTimeout(() => {
        const ms = Math.round(newOffset * 1000);
        const sign = ms >= 0 ? '+' : '';
        showToast(`Timing: ${sign}${ms}ms`, 'success');
        toastTimeoutId = null;
    }, TOAST_DEBOUNCE_MS);
}

/**
 * Reset per-song offset to 0
 */
export function resetLatency() {
    setSongWordSyncOffset(0);
    updateLatencyDisplay(0);
    debouncedSave(0);
    showToast('Timing reset to default');
}

/**
 * Update the latency display in UI (both modal and main UI)
 * @param {number} offset - Current offset in seconds
 */
export function updateLatencyDisplay(offset) {
    const ms = Math.round(offset * 1000);
    const sign = ms >= 0 ? '+' : '';
    const displayText = `${sign}${ms}ms`;
    const isAdjusted = Math.abs(ms) > 0;
    
    // Update modal latency value
    const modalValueEl = document.getElementById('latency-value');
    if (modalValueEl) {
        modalValueEl.textContent = displayText;
        modalValueEl.classList.toggle('adjusted', isAdjusted);
    }
    
    // Update main UI latency value
    const mainValueEl = document.getElementById('main-latency-value');
    if (mainValueEl) {
        mainValueEl.textContent = displayText;
        mainValueEl.classList.toggle('adjusted', isAdjusted);
    }
}

/**
 * Debounced save to backend
 * @param {number} offset - Offset to save
 */
function debouncedSave(offset) {
    // Clear existing timeout
    if (saveTimeoutId) {
        clearTimeout(saveTimeoutId);
    }
    
    // Schedule save
    saveTimeoutId = setTimeout(async () => {
        await saveOffsetToBackend(offset);
        saveTimeoutId = null;
    }, SAVE_DEBOUNCE_MS);
}

/**
 * Save offset to backend
 * @param {number} offset - Offset to save
 */
async function saveOffsetToBackend(offset) {
    if (!lastTrackInfo || !lastTrackInfo.artist || !lastTrackInfo.title) {
        console.warn('[Latency] No track info available for saving offset');
        return;
    }
    
    try {
        const response = await fetch('/api/word-sync-offset', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                artist: lastTrackInfo.artist,
                title: lastTrackInfo.title,
                offset: offset
            })
        });
        
        const result = await response.json();
        if (!result.success) {
            console.error('[Latency] Failed to save offset:', result.error);
        }
    } catch (error) {
        console.error('[Latency] Error saving offset:', error);
    }
}

// ========== SETUP ==========

/**
 * Initialize latency controls event handlers
 * Supports both click and press-and-hold for rapid adjustments
 * Handles both modal controls and main UI controls
 */
export function setupLatencyControls() {
    // Modal controls
    const minusBtn = document.getElementById('latency-minus');
    const plusBtn = document.getElementById('latency-plus');
    
    // Main UI controls
    const mainMinusBtn = document.getElementById('main-latency-minus');
    const mainPlusBtn = document.getElementById('main-latency-plus');
    
    // Press-and-hold auto-repeat configuration
    const INITIAL_DELAY = 300;  // ms before repeat starts
    const REPEAT_INTERVAL = 100; // ms between repeats (fast!)
    
    /**
     * Setup press-and-hold behavior for a latency button
     * @param {HTMLElement} btn - The button element
     * @param {number} delta - Change per step (positive or negative)
     */
    function setupHoldToRepeat(btn, delta) {
        if (!btn) return;
        
        let holdTimeout = null;
        let repeatInterval = null;
        
        function startRepeat() {
            // First adjustment on initial press
            adjustLatency(delta);
            
            // Start repeat after delay
            holdTimeout = setTimeout(() => {
                repeatInterval = setInterval(() => {
                    adjustLatency(delta);
                }, REPEAT_INTERVAL);
            }, INITIAL_DELAY);
        }
        
        function stopRepeat() {
            if (holdTimeout) {
                clearTimeout(holdTimeout);
                holdTimeout = null;
            }
            if (repeatInterval) {
                clearInterval(repeatInterval);
                repeatInterval = null;
            }
        }
        
        // Mouse events
        btn.addEventListener('mousedown', (e) => {
            e.preventDefault();
            startRepeat();
        });
        btn.addEventListener('mouseup', stopRepeat);
        btn.addEventListener('mouseleave', stopRepeat);
        
        // Touch events (for mobile)
        btn.addEventListener('touchstart', (e) => {
            e.preventDefault();
            startRepeat();
        }, { passive: false });
        btn.addEventListener('touchend', stopRepeat);
        btn.addEventListener('touchcancel', stopRepeat);
    }
    
    // Setup modal controls
    setupHoldToRepeat(minusBtn, -STEP_SIZE);
    setupHoldToRepeat(plusBtn, STEP_SIZE);
    
    // Setup main UI controls
    setupHoldToRepeat(mainMinusBtn, -STEP_SIZE);
    setupHoldToRepeat(mainPlusBtn, STEP_SIZE);
    
    // Initialize display with current offset
    updateLatencyDisplay(songWordSyncOffset);
}

/**
 * Setup keyboard shortcuts for latency adjustment
 * [ = -50ms, ] = +50ms, Shift+R = reset
 */
export function setupLatencyKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Comprehensive input protection:
        // 1. Typing in input/textarea
        // 2. ContentEditable elements
        // 3. Ctrl/Meta/Alt modifiers (except Shift for Shift+R)
        const isTyping = 
            e.target.tagName === 'INPUT' || 
            e.target.tagName === 'TEXTAREA' ||
            e.target.isContentEditable ||
            e.ctrlKey || e.metaKey || e.altKey;
        
        if (isTyping) return;
        
        // [ key = decrease (earlier)
        if (e.key === '[') {
            adjustLatency(-STEP_SIZE);
            e.preventDefault();
        }
        
        // ] key = increase (later)
        if (e.key === ']') {
            adjustLatency(STEP_SIZE);
            e.preventDefault();
        }
        
        // Shift+R = reset
        if (e.key === 'R' && e.shiftKey) {
            resetLatency();
            e.preventDefault();
        }
    });
}

/**
 * Update visibility of main UI latency controls based on word-sync state
 * Should be called whenever hasWordSync or wordSyncEnabled changes
 */
export function updateMainLatencyVisibility() {
    const mainControls = document.getElementById('main-latency-controls');
    if (!mainControls) return;
    
    // Show only when word-sync is active (both available AND enabled)
    const shouldShow = hasWordSync && wordSyncEnabled;
    
    if (shouldShow) {
        mainControls.classList.remove('hidden');
    } else {
        mainControls.classList.add('hidden');
    }
}
