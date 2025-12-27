/**
 * touchGestures.js - Multi-Finger Touch Gesture Support
 * 
 * Provides global touch gesture detection for enhanced touchscreen control.
 * - Three-finger tap: Play/Pause toggle
 * 
 * Level 2 - Imports: api, dom
 */

import { playbackCommand } from './api.js';
import { showToast } from './dom.js';

// ========== CONSTANTS ==========
const THREE_FINGER_TAP_THRESHOLD = 400;  // Max ms for tap detection
const THREE_FINGER_TAP_TOLERANCE = 30;   // Max movement in px before tap is cancelled

// ========== STATE ==========
let touchStartTime = 0;
let touchStartPositions = [];  // Array of {x, y} for each finger
let gestureActive = false;

// ========== GESTURE HANDLERS ==========

/**
 * Handle touch start - track initial finger positions
 */
function handleTouchStart(e) {
    // Only activate for exactly 3 fingers
    if (e.touches.length === 3) {
        gestureActive = true;
        touchStartTime = Date.now();
        touchStartPositions = [];
        
        for (let i = 0; i < e.touches.length; i++) {
            touchStartPositions.push({
                x: e.touches[i].clientX,
                y: e.touches[i].clientY
            });
        }
    } else if (e.touches.length > 3) {
        // More than 3 fingers - cancel gesture
        gestureActive = false;
    }
}

/**
 * Handle touch move - check if fingers moved too much
 */
function handleTouchMove(e) {
    if (!gestureActive || e.touches.length !== 3) return;
    
    // Check if any finger moved beyond tolerance
    for (let i = 0; i < e.touches.length; i++) {
        const startPos = touchStartPositions[i];
        if (!startPos) continue;
        
        const dx = Math.abs(e.touches[i].clientX - startPos.x);
        const dy = Math.abs(e.touches[i].clientY - startPos.y);
        
        if (dx > THREE_FINGER_TAP_TOLERANCE || dy > THREE_FINGER_TAP_TOLERANCE) {
            gestureActive = false;
            return;
        }
    }
}

/**
 * Handle touch end - trigger action if valid tap
 */
async function handleTouchEnd(e) {
    if (!gestureActive) return;
    
    // Check if this is the last finger lifting (all fingers released)
    if (e.touches.length === 0) {
        const tapDuration = Date.now() - touchStartTime;
        
        if (tapDuration <= THREE_FINGER_TAP_THRESHOLD) {
            // Valid three-finger tap detected!
            console.log('[TouchGestures] Three-finger tap detected, toggling playback');
            
            try {
                await playbackCommand('play-pause');
                showToast('⏯️ Playback toggled', 'success', 1000);
            } catch (error) {
                console.error('[TouchGestures] Playback toggle failed:', error);
                showToast('Playback toggle failed', 'error');
            }
        }
        
        // Reset state
        gestureActive = false;
        touchStartPositions = [];
    } else if (e.touches.length < 3) {
        // One or more fingers lifted before all three - cancel gesture
        gestureActive = false;
    }
}

/**
 * Handle touch cancel - reset state
 */
function handleTouchCancel() {
    gestureActive = false;
    touchStartPositions = [];
}

// ========== INITIALIZATION ==========

/**
 * Initialize touch gesture handlers
 * Attaches listeners to document.body for global gesture detection
 */
export function initTouchGestures() {
    // Use passive: false to allow potential preventDefault in future gestures
    document.body.addEventListener('touchstart', handleTouchStart, { passive: true });
    document.body.addEventListener('touchmove', handleTouchMove, { passive: true });
    document.body.addEventListener('touchend', handleTouchEnd, { passive: true });
    document.body.addEventListener('touchcancel', handleTouchCancel, { passive: true });
    
    console.log('[TouchGestures] Module initialized - 3-finger tap for play/pause');
}
