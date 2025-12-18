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
    lastTrackInfo
} from './state.js';
import { showToast } from './dom.js';

// ========== MODULE STATE ==========

let saveTimeoutId = null;
const SAVE_DEBOUNCE_MS = 500;  // Debounce delay for saving
const STEP_SIZE = 0.05;        // 50ms adjustment per click

// ========== CORE FUNCTIONS ==========

/**
 * Adjust the per-song word-sync offset
 * @param {number} delta - Change in seconds (positive = later, negative = earlier)
 */
export function adjustLatency(delta) {
    // Calculate new offset (clamped to ±1.0 second)
    const currentOffset = songWordSyncOffset;
    const newOffset = Math.max(-1.0, Math.min(1.0, currentOffset + delta));
    
    // Apply immediately (frontend state)
    setSongWordSyncOffset(newOffset);
    
    // Update display
    updateLatencyDisplay(newOffset);
    
    // Debounced save to backend
    debouncedSave(newOffset);
    
    // Show toast feedback
    const ms = Math.round(newOffset * 1000);
    const sign = ms >= 0 ? '+' : '';
    showToast(`Timing: ${sign}${ms}ms`, 'success');
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
 * Update the latency display in UI
 * @param {number} offset - Current offset in seconds
 */
export function updateLatencyDisplay(offset) {
    const valueEl = document.getElementById('latency-value');
    if (!valueEl) return;
    
    const ms = Math.round(offset * 1000);
    const sign = ms >= 0 ? '+' : '';
    valueEl.textContent = `${sign}${ms}ms`;
    
    // Add visual indicator when adjusted
    if (Math.abs(ms) > 0) {
        valueEl.classList.add('adjusted');
    } else {
        valueEl.classList.remove('adjusted');
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
 */
export function setupLatencyControls() {
    const minusBtn = document.getElementById('latency-minus');
    const plusBtn = document.getElementById('latency-plus');
    
    if (minusBtn) {
        minusBtn.addEventListener('click', () => adjustLatency(-STEP_SIZE));
    }
    
    if (plusBtn) {
        plusBtn.addEventListener('click', () => adjustLatency(STEP_SIZE));
    }
    
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
