/**
 * touchGestures.js - Extensible Multi-Finger Touch Gesture Framework
 * 
 * Provides a registry-based gesture detection system for enhanced touchscreen control.
 * Supports multiple finger counts (1-5+) and gesture types (tap, hold, swipe).
 * 
 * Architecture:
 * - State Machine: IDLE → POSSIBLE → RECOGNIZED/FAILED/CANCELLED
 * - Gesture Registry: Declarative gesture definitions with actions
 * - Stabilization Delay: Prevents conflicts between similar gestures (e.g., 3 vs 4 finger)
 * 
 * Default Gestures:
 * - 3-finger tap: Play/Pause toggle
 * - 4-finger tap: Slideshow toggle
 * 
 * NOTES:
 * - Uses capture phase (capture: true) to intercept events before other handlers
 * - Handles touchcancel as fallback since Android often fires it instead of touchend
 * - Stabilization delay allows staggered finger landings to register correctly
 * 
 * Level 2 - Imports: api, dom, slideshow
 */

import { playbackCommand } from './api.js';
import { showToast } from './dom.js';
import { toggleSlideshow } from './slideshow.js';

// ========== CONSTANTS ==========

// Timing configuration
const STABILIZATION_DELAY = 80;      // ms to wait for finger count to stabilize
const TAP_MAX_DURATION = 400;        // ms - maximum duration for a tap gesture
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
const DEBUG = true;  // Set to true to enable debug overlay and logging

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
let stabilizedFingerCount = 0;      // Finger count after stabilization
let gestureHandled = false;         // Prevent duplicate triggers

// Timers
let stabilizationTimer = null;
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
    stabilizedFingerCount = 0;
    gestureHandled = false;
    
    if (stabilizationTimer) {
        clearTimeout(stabilizationTimer);
        stabilizationTimer = null;
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

// ========== EVENT HANDLERS ==========

/**
 * Handle touch start - begin gesture detection
 */
function handleTouchStart(e) {
    const touchCount = e.touches.length;
    debugLog(`touchstart: ${touchCount} finger(s)`);
    
    // Track maximum touch count seen during this gesture
    maxTouchCount = Math.max(maxTouchCount, touchCount);
    
    // For multi-finger gestures (3+), use stabilization
    if (touchCount >= 3) {
        // If already in POSSIBLE state, just update maxTouchCount
        if (state === GestureState.POSSIBLE) {
            debugLog(`Finger added, maxTouchCount now ${maxTouchCount}`);
            showDebugOverlay(`Fingers: ${maxTouchCount}`);
            return;
        }
        
        // Start new gesture
        state = GestureState.POSSIBLE;
        gestureHandled = false;
        touchStartTime = Date.now();
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
        
        // Start stabilization timer
        // This gives time for additional fingers to land before we commit to a finger count
        if (stabilizationTimer) clearTimeout(stabilizationTimer);
        stabilizationTimer = setTimeout(() => {
            stabilizedFingerCount = maxTouchCount;
            debugLog(`Stabilized at ${stabilizedFingerCount} fingers`);
            showDebugOverlay(`Stabilized: ${stabilizedFingerCount} fingers`);
            
            // Start hold timer for potential hold gestures
            startHoldTimer();
        }, STABILIZATION_DELAY);
        
    } else if (touchCount > 0 && touchCount < 3) {
        // 1-2 finger gestures - currently not intercepted to avoid conflicts with scrolling/zooming
        // Can be enabled in future by removing this condition
        if (state !== GestureState.IDLE) {
            // Fingers were lifted and we're in a weird state - reset
            resetState();
        }
    }
}

/**
 * Start hold timer for hold gesture detection
 */
function startHoldTimer() {
    if (holdTimer) clearTimeout(holdTimer);
    
    holdTimer = setTimeout(() => {
        // Check if we're still in POSSIBLE state and haven't moved too much
        if (state !== GestureState.POSSIBLE) return;
        
        const maxMovement = calculateMaxMovement();
        if (maxMovement <= HOLD_MAX_MOVEMENT) {
            // Hold gesture recognized!
            const fingerCount = stabilizedFingerCount || maxTouchCount;
            const gesture = findMatchingGesture(fingerCount, GestureType.HOLD);
            
            if (gesture) {
                state = GestureState.RECOGNIZED;
                triggerGesture(gesture);
            }
        }
    }, HOLD_MIN_DURATION);
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
        // Movement exceeds tap threshold - could still be a swipe
        debugLog(`Movement: ${maxMovement.toFixed(1)}px (tap threshold exceeded)`);
    }
    
    // Don't fail the gesture yet - could be a swipe
    // Full classification happens on touchend
}

/**
 * Handle touch end - classify and trigger gesture
 */
async function handleTouchEnd(e) {
    const remainingTouches = e.touches.length;
    debugLog(`touchend: ${remainingTouches} finger(s) remaining, max was ${maxTouchCount}`);
    
    // Wait until ALL fingers are lifted before evaluating
    if (remainingTouches === 0 && state === GestureState.POSSIBLE) {
        // Clear stabilization timer if still running
        if (stabilizationTimer) {
            clearTimeout(stabilizationTimer);
            stabilizationTimer = null;
            // Use maxTouchCount since stabilization didn't complete
            stabilizedFingerCount = maxTouchCount;
        }
        
        // Clear hold timer (we're evaluating now)
        if (holdTimer) {
            clearTimeout(holdTimer);
            holdTimer = null;
        }
        
        const duration = Date.now() - touchStartTime;
        const maxMovement = calculateMaxMovement();
        const movementVector = calculateMovementVector();
        
        debugLog(`Evaluating: duration=${duration}ms, maxMove=${maxMovement.toFixed(1)}px, distance=${movementVector.distance.toFixed(1)}px`);
        showDebugOverlay(`Duration: ${duration}ms\nMovement: ${maxMovement.toFixed(1)}px`);
        
        // Classify the gesture
        const gestureType = classifyGesture(duration, maxMovement, movementVector);
        
        if (gestureType) {
            debugLog(`Classified as: ${gestureType} with ${stabilizedFingerCount} fingers`);
            
            // Find matching gesture in registry
            const gesture = findMatchingGesture(stabilizedFingerCount, gestureType);
            
            if (gesture) {
                state = GestureState.RECOGNIZED;
                await triggerGesture(gesture);
            } else {
                debugLog(`No registered gesture for ${stabilizedFingerCount}-finger ${gestureType}`);
                state = GestureState.FAILED;
            }
            
            // Track for double-tap detection
            if (gestureType === GestureType.TAP) {
                lastTapTime = Date.now();
                lastTapFingerCount = stabilizedFingerCount;
            }
        } else {
            debugLog('No gesture type matched');
            state = GestureState.FAILED;
            showDebugOverlay('No match');
        }
        
        // Reset for next gesture
        resetState();
        
    } else if (remainingTouches === 0) {
        // All fingers lifted but we weren't in POSSIBLE state
        resetState();
    }
}

/**
 * Handle touch cancel - Android often fires this instead of touchend for multi-touch
 */
function handleTouchCancel(e) {
    debugLog('touchcancel event fired');
    showDebugOverlay('CANCEL event');
    
    // On Android, touchcancel is often fired for valid multi-touch gestures
    // Treat it as touchend if we have an active gesture
    if (state === GestureState.POSSIBLE && maxTouchCount >= 3) {
        // Clear timers
        if (stabilizationTimer) {
            clearTimeout(stabilizationTimer);
            stabilizationTimer = null;
            stabilizedFingerCount = maxTouchCount;
        }
        if (holdTimer) {
            clearTimeout(holdTimer);
            holdTimer = null;
        }
        
        const duration = Date.now() - touchStartTime;
        const maxMovement = calculateMaxMovement();
        const movementVector = calculateMovementVector();
        
        // Classify and trigger
        const gestureType = classifyGesture(duration, maxMovement, movementVector);
        
        if (gestureType) {
            const gesture = findMatchingGesture(stabilizedFingerCount, gestureType);
            if (gesture) {
                state = GestureState.RECOGNIZED;
                triggerGesture(gesture);
            }
        }
    }
    
    // Always reset on cancel
    state = GestureState.CANCELLED;
    resetState();
}

// ========== PUBLIC API ==========

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
        stabilizedFingerCount,
        gestureHandled
    };
}
