/**
 * touchGestures.js - Multi-Finger Touch Gesture Support
 * 
 * Provides global touch gesture detection for enhanced touchscreen control.
 * - Three-finger tap: Play/Pause toggle
 * 
 * NOTES:
 * - Uses capture phase (capture: true) to intercept events before other handlers
 * - Handles touchcancel as fallback since Android often fires it instead of touchend
 * - Tracks maxTouchCount because fingers don't lift simultaneously
 * 
 * Level 2 - Imports: api, dom
 */

import { playbackCommand } from './api.js';
import { showToast } from './dom.js';

// ========== CONSTANTS ==========
const THREE_FINGER_TAP_THRESHOLD = 400;  // Max ms for tap detection
const THREE_FINGER_TAP_TOLERANCE = 30;   // Max movement in px before tap is cancelled

// ========== DEBUG ==========
const DEBUG = false;  // Set to false to disable debug logging

function debugLog(...args) {
    if (DEBUG) console.log('[TouchGestures]', ...args);
}

// Visual debug indicator (shows touch count on screen)
let debugOverlay = null;

function showDebugOverlay(text) {
    if (!DEBUG) return;
    
    if (!debugOverlay) {
        debugOverlay = document.createElement('div');
        debugOverlay.style.cssText = `
            position: fixed;
            top: 10px;
            left: 50%;
            transform: translateX(-50%);
            background: rgba(0, 0, 0, 0.8);
            color: #0f0;
            padding: 8px 16px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 14px;
            z-index: 99999;
            pointer-events: none;
        `;
        document.body.appendChild(debugOverlay);
    }
    
    debugOverlay.textContent = text;
    debugOverlay.style.display = 'block';
    
    // Auto-hide after 2 seconds
    clearTimeout(debugOverlay._hideTimeout);
    debugOverlay._hideTimeout = setTimeout(() => {
        if (debugOverlay) debugOverlay.style.display = 'none';
    }, 2000);
}

// ========== STATE ==========
let touchStartTime = 0;
let touchStartPositions = [];  // Array of {x, y} for each finger
let gestureActive = false;
let maxTouchCount = 0;  // Track max touches seen during gesture
let gestureHandled = false;  // Prevent duplicate triggers from touchend + touchcancel

// ========== GESTURE HANDLERS ==========

/**
 * Handle touch start - track initial finger positions
 */
function handleTouchStart(e) {
    const touchCount = e.touches.length;
    debugLog(`touchstart: ${touchCount} finger(s)`);
    showDebugOverlay(`Touches: ${touchCount}`);
    
    // Track maximum touch count seen
    maxTouchCount = Math.max(maxTouchCount, touchCount);
    
    // Only activate for exactly 3 fingers
    if (touchCount === 3) {
        gestureActive = true;
        gestureHandled = false;  // Reset for new gesture
        touchStartTime = Date.now();
        touchStartPositions = [];
        
        for (let i = 0; i < e.touches.length; i++) {
            touchStartPositions.push({
                x: e.touches[i].clientX,
                y: e.touches[i].clientY
            });
        }
        debugLog('3-finger gesture STARTED');
        showDebugOverlay('3-finger: STARTED');
    } else if (touchCount > 3) {
        // More than 3 fingers - cancel gesture
        gestureActive = false;
        debugLog('Gesture cancelled: >3 fingers');
    }
}

/**
 * Handle touch move - check if fingers moved too much
 */
function handleTouchMove(e) {
    if (!gestureActive) return;
    
    // Check movement against stored start positions
    // Note: e.touches may have fewer than 3 if fingers are lifting
    for (let i = 0; i < e.touches.length && i < touchStartPositions.length; i++) {
        const startPos = touchStartPositions[i];
        if (!startPos) continue;
        
        const dx = Math.abs(e.touches[i].clientX - startPos.x);
        const dy = Math.abs(e.touches[i].clientY - startPos.y);
        
        if (dx > THREE_FINGER_TAP_TOLERANCE || dy > THREE_FINGER_TAP_TOLERANCE) {
            gestureActive = false;
            debugLog('Gesture cancelled: movement exceeded tolerance');
            showDebugOverlay('3-finger: CANCELLED (moved)');
            return;
        }
    }
}

/**
 * Trigger the 3-finger tap action (play/pause)
 */
async function triggerThreeFingerAction() {
    if (gestureHandled) {
        debugLog('Action already handled, skipping duplicate');
        return;
    }
    gestureHandled = true;
    
    debugLog('✓ Triggering play/pause');
    showDebugOverlay('3-finger: SUCCESS!');
    
    try {
        await playbackCommand('play-pause');
        showToast('⏯️ Playback toggled', 'success', 500);
    } catch (error) {
        console.error('[TouchGestures] Playback toggle failed:', error);
        showToast('Playback toggle failed', 'error');
    }
}

/**
 * Handle touch end - trigger action if valid tap
 */
async function handleTouchEnd(e) {
    const remainingTouches = e.touches.length;
    debugLog(`touchend: ${remainingTouches} finger(s) remaining, max was ${maxTouchCount}`);
    showDebugOverlay(`END: ${remainingTouches} left`);
    
    // Wait until ALL fingers are lifted before evaluating
    if (remainingTouches === 0) {
        const tapDuration = Date.now() - touchStartTime;
        debugLog(`All fingers lifted. Duration: ${tapDuration}ms, gestureActive: ${gestureActive}`);
        
        // Check if we had a valid 3-finger gesture that wasn't cancelled
        if (gestureActive && tapDuration <= THREE_FINGER_TAP_THRESHOLD && maxTouchCount === 3) {
            await triggerThreeFingerAction();
        } else if (maxTouchCount >= 3) {
            debugLog(`✗ Invalid: active=${gestureActive}, duration=${tapDuration}ms`);
            showDebugOverlay(`FAIL: ${gestureActive ? tapDuration + 'ms' : 'cancelled'}`);
        }
        
        // Reset state after all fingers lifted
        gestureActive = false;
        touchStartPositions = [];
        maxTouchCount = 0;
    }
}

/**
 * Handle touch cancel - Android often fires this instead of touchend for multi-touch
 */
function handleTouchCancel(e) {
    debugLog('touchcancel event fired');
    showDebugOverlay('CANCEL event');
    
    // Treat cancel as valid end if we had an active 3-finger gesture
    if (maxTouchCount === 3 && gestureActive) {
        const tapDuration = Date.now() - touchStartTime;
        if (tapDuration <= THREE_FINGER_TAP_THRESHOLD) {
            triggerThreeFingerAction();
        }
    }
    
    // Reset state
    gestureActive = false;
    touchStartPositions = [];
    maxTouchCount = 0;
}

// ========== INITIALIZATION ==========

/**
 * Initialize touch gesture handlers
 * Attaches listeners to document in capture phase for reliable multi-touch detection
 */
export function initTouchGestures() {
    // Attach to document with capture: true for broader capture
    // This intercepts events before they can be stopped by other handlers
    document.addEventListener('touchstart', handleTouchStart, { passive: true, capture: true });
    document.addEventListener('touchmove', handleTouchMove, { passive: true, capture: true });
    document.addEventListener('touchend', handleTouchEnd, { passive: true, capture: true });
    document.addEventListener('touchcancel', handleTouchCancel, { passive: true, capture: true });
    
    console.log('[TouchGestures] Module initialized - 3-finger tap for play/pause');
    if (DEBUG) console.log('[TouchGestures] DEBUG MODE ON');
}
