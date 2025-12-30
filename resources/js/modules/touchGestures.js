/**
 * touchGestures.js - Extensible Multi-Finger Touch Gesture Framework
 * 
 * Provides a registry-based gesture detection system for enhanced touchscreen control.
 * Supports multiple finger counts (1-5+) and gesture types (tap, hold, swipe).
 * 
 * Architecture:
 * - State Machine: IDLE → POSSIBLE → RECOGNIZED/FAILED/CANCELLED
 * - Gesture Registry: Declarative gesture definitions with actions
 * - Delayed Evaluation: Waits for finger count to stabilize before triggering
 *   (implements Hammer.js-style "requireFailure" pattern)
 * 
 * Default Gestures:
 * - 3-finger tap: Play/Pause toggle
 * - 4-finger tap: Slideshow toggle
 * 
 * NOTES:
 * - Uses capture phase (capture: true) to intercept events before other handlers
 * - Uses passive:false + preventDefault() for 3+ finger touches to prevent
 *   Android from intercepting gestures for system functions
 * - Tracks maxTouchCount because fingers don't lift simultaneously
 * 
 * Level 2 - Imports: api, dom, slideshow
 */

import { playbackCommand } from './api.js';
import { showToast } from './dom.js';
import { toggleSlideshow } from './slideshow.js';

// ========== CONSTANTS ==========

// Timing configuration
const FINGER_STABILIZATION_DELAY = 70;  // ms to wait after fingers lift before evaluating
const TAP_MAX_DURATION = 500;        // ms - maximum duration for a tap gesture (increased for stabilization)
const TAP_MAX_MOVEMENT = 30;         // px - maximum movement allowed for tap
const HOLD_MIN_DURATION = 600;       // ms - minimum duration for hold gesture
const HOLD_MAX_MOVEMENT = 30;        // px - maximum movement allowed for hold
const SWIPE_MIN_DISTANCE = 100;      // px - minimum distance for swipe gesture
const SWIPE_MAX_DURATION = 600;      // ms - maximum duration for swipe
const DOUBLE_TAP_INTERVAL = 300;     // ms - maximum time between taps for double-tap

// Gesture state enum
const GestureState = {
    IDLE: 'idle',
    POSSIBLE: 'possible',
    RECOGNIZED: 'recognized',
    FAILED: 'failed',
    CANCELLED: 'cancelled'
};

// Gesture type enum
const GestureType = {
    TAP: 'tap',
    HOLD: 'hold',
    SWIPE_LEFT: 'swipe-left',
    SWIPE_RIGHT: 'swipe-right',
    SWIPE_UP: 'swipe-up',
    SWIPE_DOWN: 'swipe-down',
    DOUBLE_TAP: 'double-tap'
};

// ========== DEBUG ==========
const DEBUG = false;  // Set to true to enable debug overlay and logging

function debugLog(...args) {
    if (DEBUG) console.log('[TouchGestures]', ...args);
}

// Visual debug indicator
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
            background: rgba(0, 0, 0, 0.9);
            color: #0f0;
            padding: 10px 20px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 14px;
            z-index: 99999;
            pointer-events: none;
            white-space: pre-line;
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

// ========== GESTURE REGISTRY ==========

/**
 * Gesture Registry - Declarative gesture definitions
 * 
 * Each gesture has:
 * - id: Unique identifier for the gesture
 * - fingers: Number of fingers required (1-5+)
 * - type: Gesture type from GestureType enum
 * - config: Type-specific configuration (optional, uses defaults)
 * - action: Function to execute when gesture is recognized
 * - enabled: Whether this gesture is currently active
 * - description: Human-readable description
 */
const GESTURE_REGISTRY = [
    // ===== Active Gestures =====
    {
        id: 'play-pause',
        fingers: 3,
        type: GestureType.TAP,
        config: { maxDuration: TAP_MAX_DURATION, maxMovement: TAP_MAX_MOVEMENT },
        action: async () => {
            try {
                await playbackCommand('play-pause');
                showToast('⏯️ Playback toggled', 'success', 500);
            } catch (error) {
                console.error('[TouchGestures] Playback toggle failed:', error);
                showToast('Playback toggle failed', 'error');
            }
        },
        enabled: true,
        description: '3-finger tap: Play/Pause'
    },
    {
        id: 'slideshow-toggle',
        fingers: 4,
        type: GestureType.TAP,
        config: { maxDuration: TAP_MAX_DURATION, maxMovement: TAP_MAX_MOVEMENT },
        action: () => {
            toggleSlideshow();
        },
        enabled: true,
        description: '4-finger tap: Toggle Slideshow'
    },
    
    // ===== Placeholder Gestures (disabled, for future use) =====
    {
        id: 'next-track',
        fingers: 3,
        type: GestureType.SWIPE_RIGHT,
        config: { minDistance: SWIPE_MIN_DISTANCE, maxDuration: SWIPE_MAX_DURATION },
        action: async () => {
            try {
                await playbackCommand('next');
                showToast('⏭️ Next track', 'success', 500);
            } catch (error) {
                console.error('[TouchGestures] Next track failed:', error);
            }
        },
        enabled: false,  // Placeholder for future
        description: '3-finger swipe right: Next Track'
    },
    {
        id: 'prev-track',
        fingers: 3,
        type: GestureType.SWIPE_LEFT,
        config: { minDistance: SWIPE_MIN_DISTANCE, maxDuration: SWIPE_MAX_DURATION },
        action: async () => {
            try {
                await playbackCommand('previous');
                showToast('⏮️ Previous track', 'success', 500);
            } catch (error) {
                console.error('[TouchGestures] Previous track failed:', error);
            }
        },
        enabled: false,  // Placeholder for future
        description: '3-finger swipe left: Previous Track'
    },
    {
        id: 'four-finger-hold',
        fingers: 4,
        type: GestureType.HOLD,
        config: { minDuration: HOLD_MIN_DURATION, maxMovement: HOLD_MAX_MOVEMENT },
        action: () => {
            // Placeholder - could be fullscreen toggle, etc.
            showToast('4-finger hold detected', 'success', 500);
        },
        enabled: false,  // Placeholder for future
        description: '4-finger hold: (Reserved)'
    }
];

// ========== STATE MACHINE ==========

let state = GestureState.IDLE;
let touchStartTime = 0;
let touchStartPositions = [];       // Array of {x, y} for each finger at start
let touchCurrentPositions = [];     // Array of {x, y} for current finger positions
let maxTouchCount = 0;              // Maximum fingers seen during this gesture
let gestureHandled = false;         // Prevent duplicate triggers

// Timers
let evaluationTimer = null;         // Delay before evaluating gesture (allows more fingers to be added)
let holdTimer = null;

// Double-tap tracking
let lastTapTime = 0;
let lastTapFingerCount = 0;

// ========== HELPER FUNCTIONS ==========

/**
 * Reset state machine to IDLE
 */
function resetState() {
    state = GestureState.IDLE;
    touchStartTime = 0;
    touchStartPositions = [];
    touchCurrentPositions = [];
    maxTouchCount = 0;
    gestureHandled = false;
    
    if (evaluationTimer) {
        clearTimeout(evaluationTimer);
        evaluationTimer = null;
    }
    if (holdTimer) {
        clearTimeout(holdTimer);
        holdTimer = null;
    }
}

/**
 * Calculate the maximum movement from start positions
 * @returns {number} Maximum distance any finger moved (in pixels)
 */
function calculateMaxMovement() {
    let maxMovement = 0;
    
    for (let i = 0; i < Math.min(touchStartPositions.length, touchCurrentPositions.length); i++) {
        const start = touchStartPositions[i];
        const current = touchCurrentPositions[i];
        if (!start || !current) continue;
        
        const dx = Math.abs(current.x - start.x);
        const dy = Math.abs(current.y - start.y);
        const distance = Math.sqrt(dx * dx + dy * dy);
        maxMovement = Math.max(maxMovement, distance);
    }
    
    return maxMovement;
}

/**
 * Calculate the average movement vector from start to current positions
 * @returns {{dx: number, dy: number, distance: number}} Movement vector
 */
function calculateMovementVector() {
    if (touchStartPositions.length === 0 || touchCurrentPositions.length === 0) {
        return { dx: 0, dy: 0, distance: 0 };
    }
    
    let totalDx = 0;
    let totalDy = 0;
    let count = 0;
    
    for (let i = 0; i < Math.min(touchStartPositions.length, touchCurrentPositions.length); i++) {
        const start = touchStartPositions[i];
        const current = touchCurrentPositions[i];
        if (!start || !current) continue;
        
        totalDx += current.x - start.x;
        totalDy += current.y - start.y;
        count++;
    }
    
    if (count === 0) return { dx: 0, dy: 0, distance: 0 };
    
    const dx = totalDx / count;
    const dy = totalDy / count;
    const distance = Math.sqrt(dx * dx + dy * dy);
    
    return { dx, dy, distance };
}

/**
 * Determine swipe direction from movement vector
 * @param {{dx: number, dy: number}} vector - Movement vector
 * @returns {string|null} Direction ('left', 'right', 'up', 'down') or null
 */
function getSwipeDirection(vector) {
    const { dx, dy } = vector;
    
    // Determine if horizontal or vertical based on which component is larger
    if (Math.abs(dx) > Math.abs(dy)) {
        // Horizontal swipe
        return dx > 0 ? 'right' : 'left';
    } else {
        // Vertical swipe
        return dy > 0 ? 'down' : 'up';
    }
}

/**
 * Classify the gesture type based on duration and movement
 * @param {number} duration - Gesture duration in ms
 * @param {number} maxMovement - Maximum movement in pixels
 * @param {{dx: number, dy: number, distance: number}} movementVector - Movement vector
 * @returns {string} Gesture type from GestureType enum
 */
function classifyGesture(duration, maxMovement, movementVector) {
    // Check for swipe first (significant directional movement)
    if (movementVector.distance >= SWIPE_MIN_DISTANCE && duration <= SWIPE_MAX_DURATION) {
        const direction = getSwipeDirection(movementVector);
        switch (direction) {
            case 'left': return GestureType.SWIPE_LEFT;
            case 'right': return GestureType.SWIPE_RIGHT;
            case 'up': return GestureType.SWIPE_UP;
            case 'down': return GestureType.SWIPE_DOWN;
        }
    }
    
    // Check for tap (quick, minimal movement)
    if (duration <= TAP_MAX_DURATION && maxMovement <= TAP_MAX_MOVEMENT) {
        // Check for double-tap
        const timeSinceLastTap = Date.now() - lastTapTime;
        if (timeSinceLastTap <= DOUBLE_TAP_INTERVAL && lastTapFingerCount === maxTouchCount) {
            return GestureType.DOUBLE_TAP;
        }
        return GestureType.TAP;
    }
    
    // Check for hold (long duration, minimal movement)
    if (duration >= HOLD_MIN_DURATION && maxMovement <= HOLD_MAX_MOVEMENT) {
        return GestureType.HOLD;
    }
    
    // No recognized gesture type
    return null;
}

/**
 * Find a matching gesture in the registry
 * @param {number} fingerCount - Number of fingers
 * @param {string} gestureType - Type of gesture
 * @returns {Object|null} Matching gesture definition or null
 */
function findMatchingGesture(fingerCount, gestureType) {
    // SUPPRESSION RULE: If user ever touched 4+ fingers during this gesture,
    // block 3-finger actions. This prevents staggered 4-finger taps from
    // accidentally triggering the 3-finger play/pause action.
    if (fingerCount === 3 && maxTouchCount >= 4) {
        debugLog(`Suppressing 3-finger ${gestureType}: maxTouchCount was ${maxTouchCount}`);
        return null;
    }
    
    return GESTURE_REGISTRY.find(g => 
        g.enabled && 
        g.fingers === fingerCount && 
        g.type === gestureType
    ) || null;
}

/**
 * Trigger a gesture action
 * @param {Object} gesture - Gesture definition from registry
 */
async function triggerGesture(gesture) {
    if (gestureHandled) {
        debugLog('Action already handled, skipping duplicate');
        return;
    }
    gestureHandled = true;
    
    debugLog(`✓ Triggering: ${gesture.description}`);
    showDebugOverlay(`✓ ${gesture.description}`);
    
    try {
        await gesture.action();
    } catch (error) {
        console.error(`[TouchGestures] Error executing ${gesture.id}:`, error);
    }
}

/**
 * Evaluate and trigger gesture after stabilization delay
 * This is the core of the "requireFailure" pattern - we delay evaluation
 * to give higher finger-count gestures a chance to be recognized
 */
function evaluateGesture() {
    if (state !== GestureState.POSSIBLE) {
        debugLog('evaluateGesture: not in POSSIBLE state, skipping');
        return;
    }
    
    const timestamp = Date.now();
    const duration = timestamp - touchStartTime;
    const maxMovement = calculateMaxMovement();
    const movementVector = calculateMovementVector();
    
    debugLog(`[${timestamp}] Evaluating: duration=${duration}ms, maxMove=${maxMovement.toFixed(1)}px, fingers=${maxTouchCount}`);
    showDebugOverlay(`Evaluating...\nFingers: ${maxTouchCount}\nDuration: ${duration}ms`);
    
    // Classify the gesture
    const gestureType = classifyGesture(duration, maxMovement, movementVector);
    
    if (gestureType) {
        debugLog(`Classified as: ${gestureType} with ${maxTouchCount} fingers`);
        
        // Find matching gesture in registry
        const gesture = findMatchingGesture(maxTouchCount, gestureType);
        
        if (gesture) {
            state = GestureState.RECOGNIZED;
            triggerGesture(gesture);
        } else {
            debugLog(`No registered gesture for ${maxTouchCount}-finger ${gestureType}`);
            state = GestureState.FAILED;
            showDebugOverlay(`No gesture for\n${maxTouchCount}-finger ${gestureType}`);
        }
        
        // Track for double-tap detection
        if (gestureType === GestureType.TAP) {
            lastTapTime = timestamp;
            lastTapFingerCount = maxTouchCount;
        }
    } else {
        debugLog('No gesture type matched');
        state = GestureState.FAILED;
        showDebugOverlay('No match');
    }
    
    // Reset for next gesture
    resetState();
}

// ========== EVENT HANDLERS ==========

/**
 * Handle touch start - begin gesture detection
 */
function handleTouchStart(e) {
    const touchCount = e.touches.length;
    const timestamp = Date.now();
    
    // For multi-finger gestures (3+), use special handling
    if (touchCount >= 3) {
        // CRITICAL: Prevent default to stop Android from intercepting 3-finger gestures
        // Android reserves 3-finger gestures for system functions (screenshot, etc.)
        e.preventDefault();
        
        // Track maximum touch count seen during this gesture
        const prevMax = maxTouchCount;
        maxTouchCount = Math.max(maxTouchCount, touchCount);
        
        debugLog(`[${timestamp}] touchstart: ${touchCount} fingers, max: ${prevMax} → ${maxTouchCount}, state=${state}`);
        
        // Cancel any pending evaluation timer - more fingers might be coming
        if (evaluationTimer) {
            clearTimeout(evaluationTimer);
            evaluationTimer = null;
            debugLog('Cancelled pending evaluation - more fingers detected');
        }
        
        // If already in POSSIBLE state, just update maxTouchCount
        if (state === GestureState.POSSIBLE) {
            showDebugOverlay(`Fingers: ${prevMax} → ${maxTouchCount}`);
            return;
        }
        
        // Start new gesture
        state = GestureState.POSSIBLE;
        gestureHandled = false;
        touchStartTime = timestamp;
        touchStartPositions = [];
        touchCurrentPositions = [];
        
        // Record start positions
        for (let i = 0; i < e.touches.length; i++) {
            const pos = { x: e.touches[i].clientX, y: e.touches[i].clientY };
            touchStartPositions.push(pos);
            touchCurrentPositions.push({ ...pos });
        }
        
        debugLog(`${touchCount}-finger gesture STARTED`);
        showDebugOverlay(`${touchCount}-finger: STARTED`);
        
    } else if (touchCount > 0 && touchCount < 3) {
        // 1-2 finger gestures - we track maxTouchCount but don't activate gesture
        // This handles the case where user starts with 1-2 fingers then adds more
        maxTouchCount = Math.max(maxTouchCount, touchCount);
        
        if (state !== GestureState.IDLE && state !== GestureState.POSSIBLE) {
            resetState();
        }
    }
}

/**
 * Handle touch move - track movement for gesture classification
 */
function handleTouchMove(e) {
    if (state !== GestureState.POSSIBLE) return;
    
    // Update current positions
    for (let i = 0; i < e.touches.length && i < touchCurrentPositions.length; i++) {
        touchCurrentPositions[i] = {
            x: e.touches[i].clientX,
            y: e.touches[i].clientY
        };
    }
    
    // Check if movement exceeds tap threshold (for early tap failure)
    const maxMovement = calculateMaxMovement();
    if (maxMovement > TAP_MAX_MOVEMENT) {
        debugLog(`Movement: ${maxMovement.toFixed(1)}px (tap threshold exceeded)`);
    }
}

/**
 * Handle touch end - schedule gesture evaluation
 * 
 * KEY INSIGHT: We don't evaluate immediately when fingers lift.
 * Instead, we schedule evaluation after a short delay.
 * This gives time for more fingers to be added (staggered landing).
 * If more fingers are added, the timer is cancelled and restarted.
 */
function handleTouchEnd(e) {
    const remainingTouches = e.touches.length;
    const timestamp = Date.now();
    
    debugLog(`[${timestamp}] touchend: ${remainingTouches} remaining, max=${maxTouchCount}, state=${state}`);
    
    // Only process if we're in POSSIBLE state
    if (state !== GestureState.POSSIBLE) {
        if (remainingTouches === 0) {
            resetState();
        }
        return;
    }
    
    // If fingers remain, don't evaluate yet
    if (remainingTouches > 0) {
        debugLog(`Still ${remainingTouches} fingers down, waiting...`);
        showDebugOverlay(`${remainingTouches} fingers remain\nmax: ${maxTouchCount}`);
        return;
    }
    
    // All fingers lifted - schedule evaluation after delay
    // This delay allows for the "requireFailure" pattern:
    // If user puts fingers back down quickly, we cancel and continue
    debugLog(`All fingers lifted, scheduling evaluation in ${FINGER_STABILIZATION_DELAY}ms`);
    showDebugOverlay(`Lifted. Wait ${FINGER_STABILIZATION_DELAY}ms...\nmax: ${maxTouchCount}`);
    
    if (evaluationTimer) {
        clearTimeout(evaluationTimer);
    }
    
    evaluationTimer = setTimeout(() => {
        evaluateGesture();
    }, FINGER_STABILIZATION_DELAY);
}

/**
 * Handle touch cancel - Android often fires this instead of touchend for multi-touch
 */
function handleTouchCancel(e) {
    debugLog('touchcancel event fired');
    showDebugOverlay('CANCEL event');
    
    // On Android, touchcancel is often fired for valid multi-touch gestures
    // Treat it like touchend - schedule evaluation
    if (state === GestureState.POSSIBLE && maxTouchCount >= 3) {
        debugLog(`touchcancel with ${maxTouchCount} fingers, scheduling evaluation`);
        
        if (evaluationTimer) {
            clearTimeout(evaluationTimer);
        }
        
        evaluationTimer = setTimeout(() => {
            evaluateGesture();
        }, FINGER_STABILIZATION_DELAY);
    } else {
        // Not an active gesture, just reset
        resetState();
    }
}

// ========== PUBLIC API ==========

/**
 * Initialize touch gesture handlers
 * Attaches listeners to document in capture phase for reliable multi-touch detection
 */
export function initTouchGestures() {
    // Attach to document with capture: true for broader capture
    // This intercepts events before they can be stopped by other handlers
    // IMPORTANT: touchstart uses passive:false to allow preventDefault()
    // This is required to prevent Android from intercepting 3-finger gestures
    document.addEventListener('touchstart', handleTouchStart, { passive: false, capture: true });
    document.addEventListener('touchmove', handleTouchMove, { passive: true, capture: true });
    document.addEventListener('touchend', handleTouchEnd, { passive: true, capture: true });
    document.addEventListener('touchcancel', handleTouchCancel, { passive: true, capture: true });
    
    // Log registered gestures
    const enabledGestures = GESTURE_REGISTRY.filter(g => g.enabled);
    console.log(`[TouchGestures] Initialized with ${enabledGestures.length} active gestures:`);
    enabledGestures.forEach(g => console.log(`  - ${g.description}`));
    
    if (DEBUG) {
        console.log('[TouchGestures] DEBUG MODE ON');
        console.log('[TouchGestures] All registered gestures:', GESTURE_REGISTRY.map(g => `${g.id} (${g.enabled ? 'enabled' : 'disabled'})`));
    }
}

/**
 * Get the gesture registry (for debugging or runtime modification)
 * @returns {Array} Copy of the gesture registry
 */
export function getGestureRegistry() {
    return [...GESTURE_REGISTRY];
}

/**
 * Enable or disable a gesture by ID
 * @param {string} gestureId - The gesture ID to modify
 * @param {boolean} enabled - Whether to enable or disable
 * @returns {boolean} True if gesture was found and modified
 */
export function setGestureEnabled(gestureId, enabled) {
    const gesture = GESTURE_REGISTRY.find(g => g.id === gestureId);
    if (gesture) {
        gesture.enabled = enabled;
        console.log(`[TouchGestures] ${gestureId} ${enabled ? 'enabled' : 'disabled'}`);
        return true;
    }
    return false;
}

/**
 * Get current state (for debugging)
 * @returns {Object} Current state information
 */
export function getGestureState() {
    return {
        state,
        maxTouchCount,
        gestureHandled
    };
}
