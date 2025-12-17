/**
 * wordSync.js - Word-Synced Lyrics Module
 * 
 * This module handles word-level timing for karaoke-style lyrics display.
 * Supports two visual styles: 'fade' (gradient sweep) and 'pop' (word scale).
 * 
 * Uses requestAnimationFrame for smooth 60-144fps animation with a
 * FLYWHEEL CLOCK that never goes backwards, eliminating visual jitter.
 * 
 * Key architecture:
 * - Frontend owns time (monotonic clock)
 * - Server polls only "nudge" the clock via speed adjustment
 * - Visual position NEVER decreases during normal playback
 * 
 * Level 2 - Imports: state
 */

import {
    wordSyncedLyrics,
    hasWordSync,
    wordSyncStyle,
    wordSyncEnabled,
    wordSyncAnchorPosition,
    wordSyncAnchorTimestamp,
    wordSyncIsPlaying,
    wordSyncAnimationId,
    wordSyncLatencyCompensation,
    wordSyncSpecificLatencyCompensation,
    setWordSyncAnimationId,
    debugTimingEnabled,
    debugRtt,
    debugRttSmoothed,
    debugRttJitter,
    debugServerPosition,
    debugPollTimestamp,
    debugPollInterval,
    debugSource,
    debugBadSamples
} from './state.js';

// ========== MODULE STATE ==========

// DOM recycling: Cache line ID and word element references
let cachedLineId = null;
let wordElements = [];

// FLYWHEEL CLOCK: Monotonic time that never goes backwards
let visualPosition = 0;        // Our smooth, monotonic position (seconds)
let lastFrameTime = 0;         // Last animation frame timestamp (ms)
let visualSpeed = 1.0;         // Current visual speed multiplier (0.9 - 1.1)
// DEAD CODE: lastServerSync is declared but never used. Keeping for reference.
// TODO: Remove in next cleanup
let lastServerSync = 0;        // Last time we synced with server

// Track the currently active line index (single source of truth for dom.js)
let activeLineIndex = -1;

// Transition token for cancelling stale fade callbacks
let transitionToken = 0;

// Track if we've logged word-sync activation (reset on song change)
let _wordSyncLogged = false;

// Debug mode - set to true to see clock behavior
const DEBUG_CLOCK = false;

// Frame rate limiting - cap at 60 FPS to reduce CPU load on high refresh displays
// OnePlus Pad 3 has 144Hz display, running at 144 FPS is unnecessary for word-sync
const TARGET_FPS = 60;
const FRAME_INTERVAL = 1000 / TARGET_FPS;  // 16.67ms
let lastAnimationTime = 0;

// Drift filtering (EMA) - smooths noisy drift measurements to prevent speed "breathing"
// Higher value = more responsive but more jittery, lower = smoother but slower to correct
const DRIFT_SMOOTHING = 0.3;  // 0.3 = 30% new value, 70% previous (smooth but responsive)
let filteredDrift = 0;

// Debug counters for snap events
let snapCount = 0;          // Large forward snaps (>1s drift)
let backSnapCount = 0;      // Small backward snaps (<100ms)

// Line change tracking for line-boundary back-snaps
// Back-snaps only allowed within this window after line change (hidden by transition)
const BACK_SNAP_WINDOW_MS = 240;  // ~2 poll cycles
let lineChangeTime = 0;  // When line changed (performance.now())

// Current word tracking for debug overlay
let currentWordIndex = -1;
let currentWordProgress = 0;
let totalWordsInLine = 0;

// Render position smoothing - reduces micro-jitter in word progress calculations
// renderPosition follows visualPosition with slight inertia for smoother animations
const RENDER_SMOOTHING = 0.25;  // 0.25 = follows quickly but smooths jitter
let renderPosition = 0;

// Ultra-short word threshold - words shorter than this skip pop animation
const ULTRA_SHORT_WORD_MS = 60;  // 60ms

// ========== WORD SYNC UTILITIES ==========

/**
 * Escape HTML special characters to prevent XSS
 * @param {string} text - Raw text
 * @returns {string} Escaped HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Find the current word being sung based on playback position
 * 
 * Handles gaps between words correctly - returns previous word as "done"
 * when position is in a gap (silence) between words.
 * 
 * @param {number} position - Current playback position in seconds
 * @param {Object} lineData - Word-synced data for current line
 * @returns {Object|null} Object with wordIndex and progress (0-1), or null
 */
export function findCurrentWord(position, lineData) {
    if (!lineData || !lineData.words || lineData.words.length === 0) {
        return null;
    }

    const lineStart = lineData.start || 0;
    const words = lineData.words;

    // Before line starts
    if (position < lineStart) {
        return { wordIndex: -1, progress: 0, duration: 0 };
    }

    // Check if we're before the first word even starts
    const firstWordStart = lineStart + (words[0].time || 0);
    if (position < firstWordStart) {
        return { wordIndex: -1, progress: 0, duration: 0 };
    }

    // Word timing: word.time is OFFSET from line start, not absolute time
    for (let i = 0; i < words.length; i++) {
        const word = words[i];
        const wordStart = lineStart + (word.time || 0);
        
        // Calculate word end using duration if available (from Musixmatch/NetEase)
        // Falls back to next word's start for backward compatibility with cached songs
        let wordEnd;
        if (word.duration !== undefined && word.duration > 0) {
            // Use explicit duration from backend (more precise, handles pauses)
            wordEnd = wordStart + word.duration;
        } else if (i + 1 < words.length) {
            // Fallback: next word's start
            wordEnd = lineStart + (words[i + 1].time || 0);
        } else {
            // Last word without duration - use line end or estimate
            wordEnd = lineData.end || (wordStart + 0.5);
        }
        
        // CASE 1: We are currently inside this word
        if (position >= wordStart && position < wordEnd) {
            // Calculate progress within this word (0-1)
            const duration = wordEnd - wordStart;
            const progress = duration > 0 ? Math.min(1, (position - wordStart) / duration) : 1;
            return { wordIndex: i, progress, duration };
        }
        
        // CASE 2: Gap detection - we are BEFORE this word starts
        // Since words are sorted, if we're before word[i], we're in the gap after word[i-1]
        if (position < wordStart && i > 0) {
            // Return previous word as fully sung (with zero duration for gap)
            return { wordIndex: i - 1, progress: 1, duration: 0 };
        }
    }

    // CASE 3: After the last word
    // Check if we're actually past the end of the last word
    const lastWord = words[words.length - 1];
    const lastWordStart = lineStart + (lastWord.time || 0);
    let lastWordEnd;
    if (lastWord.duration !== undefined && lastWord.duration > 0) {
        lastWordEnd = lastWordStart + lastWord.duration;
    } else {
        lastWordEnd = lineData.end || (lastWordStart + 0.5);
    }

    if (position >= lastWordEnd) {
        // Past the entire line - all words are sung
        return { wordIndex: words.length, progress: 0, duration: 0 };
    }

    // Inside last word (fallback)
    const duration = lastWordEnd - lastWordStart;
    const progress = duration > 0 ? Math.min(1, (position - lastWordStart) / duration) : 1;
    return { wordIndex: words.length - 1, progress, duration };
}

/**
 * Find the current line AND its index from word-synced lyrics based on position
 * Returns both to avoid O(n) identity search later
 * 
 * @param {number} position - Current playback position in seconds
 * @returns {{line: Object|null, index: number}} The line object and its index (-1 if not found)
 */
export function findCurrentWordSyncLineWithIndex(position) {
    if (!wordSyncedLyrics || wordSyncedLyrics.length === 0) {
        return { line: null, index: -1 };
    }

    // Find the line that contains the current position
    for (let i = 0; i < wordSyncedLyrics.length; i++) {
        const line = wordSyncedLyrics[i];
        const nextLine = wordSyncedLyrics[i + 1];
        
        const lineStart = line.start || 0;
        const lineEnd = nextLine ? nextLine.start : (line.end || lineStart + 10);
        
        if (position >= lineStart && position < lineEnd) {
            return { line, index: i };
        }
    }

    return { line: null, index: -1 };
}

// Legacy wrapper for backward compatibility
export function findCurrentWordSyncLine(position) {
    return findCurrentWordSyncLineWithIndex(position).line;
}

/**
 * Update the 5 surrounding lyric lines (prev-2, prev-1, next-1, next-2, next-3)
 * Called ONLY when the active line changes, not every frame
 * This is the single authority for surrounding lines during word-sync
 * 
 * @param {number} idx - Current line index (-1 during intro)
 */
function updateSurroundingLines(idx) {
    const getText = (i) => {
        if (!wordSyncedLyrics || i < 0 || i >= wordSyncedLyrics.length) return "";
        return wordSyncedLyrics[i]?.text || "";
    };
    
    // During intro (idx = -1), show first lines as upcoming
    const effectiveIdx = idx === -1 ? 0 : idx;
    
    const prev2 = document.getElementById('prev-2');
    const prev1 = document.getElementById('prev-1');
    const next1 = document.getElementById('next-1');
    const next2 = document.getElementById('next-2');
    const next3 = document.getElementById('next-3');
    
    if (prev2) prev2.textContent = getText(effectiveIdx - 2);
    if (prev1) prev1.textContent = getText(effectiveIdx - 1);
    if (next1) next1.textContent = getText(effectiveIdx + 1);
    if (next2) next2.textContent = getText(effectiveIdx + 2);
    if (next3) next3.textContent = getText(effectiveIdx + 3);
}

/**
 * Check if word-sync is available for the current song
 * 
 * @returns {boolean} True if word-sync is available
 */
export function isWordSyncAvailable() {
    return hasWordSync && wordSyncedLyrics && wordSyncedLyrics.length > 0;
}

/**
 * Find the INDEX of the current line from word-synced lyrics based on position
 * Used by dom.js to get all 6 lines from word-sync data
 * 
 * @param {number} position - Current playback position in seconds
 * @returns {number} Index of current line, or -1 if not found
 */
export function findCurrentWordSyncLineIndex(position) {
    if (!wordSyncedLyrics || wordSyncedLyrics.length === 0) {
        return -1;
    }

    for (let i = 0; i < wordSyncedLyrics.length; i++) {
        const line = wordSyncedLyrics[i];
        const nextLine = wordSyncedLyrics[i + 1];
        const lineEnd = nextLine ? nextLine.start : (line.end || line.start + 10);
        
        if (position >= line.start && position < lineEnd) {
            return i;
        }
    }
    
    // Check if we're before the first line
    if (position < wordSyncedLyrics[0].start) {
        return -1;
    }
    
    // After last line
    return wordSyncedLyrics.length - 1;
}

/**
 * Get 6 line texts for display from word-sync data
 * Uses the activeLineIndex from the animation loop (single source of truth)
 * This ensures dom.js and word-sync animation show the SAME line.
 * 
 * @param {number} position - NOT USED (kept for API compatibility)
 * @returns {Array<string|null>} [prev2, prev1, null (current), next1, next2, next3]
 */
export function getWordSyncDisplayLines(position) {
    // Use the active line index from the animation loop (single source of truth)
    const idx = activeLineIndex;
    if (idx === -1 || !wordSyncedLyrics || wordSyncedLyrics.length === 0) {
        return null;
    }
    
    const getText = (i) => {
        if (i < 0 || i >= wordSyncedLyrics.length) return "";
        return wordSyncedLyrics[i]?.text || "";
    };
    
    return [
        getText(idx - 2),  // prev-2
        getText(idx - 1),  // prev-1
        null,              // current (handled by word spans in updateWordSyncDOM)
        getText(idx + 1),  // next-1
        getText(idx + 2),  // next-2
        getText(idx + 3)   // next-3
    ];
}

/**
 * Get current position for line detection
 * Returns the same visualPosition that the animation loop uses.
 * This ensures dom.js and word-sync animation show the SAME line.
 * 
 * NOTE: If animation hasn't started yet, visualPosition may be 0.
 * In that case, calculate from anchor data as fallback.
 * 
 * @returns {number} Current position in seconds
 */
export function getFlywheelPosition() {
    // If visualPosition is 0 (animation not started), calculate from anchor
    if (visualPosition === 0 && wordSyncAnchorTimestamp > 0) {
        const elapsed = (performance.now() - wordSyncAnchorTimestamp) / 1000;
        const totalLatencyCompensation = wordSyncLatencyCompensation + wordSyncSpecificLatencyCompensation;
        return wordSyncAnchorPosition + elapsed + totalLatencyCompensation;
    }
    // Return the same position the animation uses
    return visualPosition;
}

// ========== FLYWHEEL CLOCK ==========

/**
 * Update the flywheel clock
 * 
 * Key property: visualPosition NEVER decreases during normal playback.
 * This eliminates all backwards jumps and jitter.
 * 
 * Instead of snapping to server position, we adjust our speed to catch up.
 * 
 * @param {number} timestamp - Current animation frame timestamp
 * @returns {number} Current visual position in seconds
 */
function updateFlywheelClock(timestamp) {
    // Calculate delta time since last frame
    const dt = lastFrameTime ? (timestamp - lastFrameTime) / 1000 : 0;
    lastFrameTime = timestamp;
    
    // If paused, don't advance time
    if (!wordSyncIsPlaying) {
        return visualPosition;
    }
    
    // Calculate where server thinks we are
    // Uses BOTH the source-based line-sync compensation AND the word-sync specific adjustment
    const elapsed = (performance.now() - wordSyncAnchorTimestamp) / 1000;
    const totalLatencyCompensation = wordSyncLatencyCompensation + wordSyncSpecificLatencyCompensation;
    const serverPosition = wordSyncAnchorPosition + elapsed + totalLatencyCompensation;
    
    // Calculate drift (difference between our position and server)
    // This is the RAW drift - used for snap detection
    const rawDrift = serverPosition - visualPosition;
    
    // Handle large jumps (seeks, buffering) - snap threshold 1.0s
    // Use RAW drift for immediate response to seeks
    if (Math.abs(rawDrift) > 1.0) {
        if (DEBUG_CLOCK) {
            console.log(`[WordSync] Seek detected, drift: ${rawDrift.toFixed(2)}s, snapping to server`);
        }
        snapCount++;  // Track for debug overlay
        visualPosition = serverPosition;
        renderPosition = serverPosition;  // Reset render position on snap
        visualSpeed = 1.0;
        filteredDrift = 0;  // Reset filter on snap
        return visualPosition;
    }
    
    // RELAXED BACK-SNAP: Only allow backward snap within window after line changes
    // This prevents constant oscillation mid-word while still correcting at natural break points
    // The 240ms window aligns with line transition animation (hidden from user)
    // For mid-word "ahead" errors, we rely on gentle slowdown (speed < 1.0) instead of snapping
    const inBackSnapWindow = lineChangeTime > 0 && (performance.now() - lineChangeTime) < BACK_SNAP_WINDOW_MS;
    
    if (rawDrift > -0.1 && rawDrift < 0 && inBackSnapWindow) {
        if (DEBUG_CLOCK) {
            console.log(`[WordSync] Line-change backward snap: ${(rawDrift * 1000).toFixed(0)}ms`);
        }
        backSnapCount++;  // Track for debug overlay
        visualPosition = serverPosition;
        renderPosition = serverPosition;  // Reset render position on snap
        visualSpeed = 1.0;
        filteredDrift = 0;  // Reset filter on snap
        lineChangeTime = 0;  // Consume the window
        return visualPosition;
    }
    
    // DRIFT FILTERING (EMA): Smooth the drift signal to prevent speed "breathing"
    // This eliminates visible speed oscillation from noisy measurements
    filteredDrift = filteredDrift * (1 - DRIFT_SMOOTHING) + rawDrift * DRIFT_SMOOTHING;
    
    // IMPROVEMENT: Deadband - don't chase tiny errors (noise)
    // If filtered drift is within 50ms, stay at 1x speed
    if (Math.abs(filteredDrift) < 0.05) {
        visualSpeed = 1.0;
    } else {
        // Soft sync: Adjust speed to correct filtered drift
        // Using filtered drift prevents jerky speed changes
        // Reduced multiplier (0.5) for gentler corrections
        visualSpeed = 1.0 + (filteredDrift * 0.5);
        
        // RELAXED SPEED CLAMP: Allow gentle slowdowns (92% - 108%)
        // This enables smooth correction when ahead, instead of relying on snaps
        visualSpeed = Math.max(0.92, Math.min(1.08, visualSpeed));
    }
    
    // Advance visual position
    visualPosition += dt * visualSpeed;
    
    // Update render position (smoothed display position for animations)
    // This reduces micro-jitter in word progress calculations
    renderPosition = renderPosition + (visualPosition - renderPosition) * RENDER_SMOOTHING;
    
    // Debug logging (1% of frames to avoid spam)
    if (DEBUG_CLOCK && Math.random() < 0.01) {
        console.log(`[Clock] visual: ${visualPosition.toFixed(3)}, server: ${serverPosition.toFixed(3)}, raw: ${rawDrift.toFixed(3)}, filtered: ${filteredDrift.toFixed(3)}, speed: ${visualSpeed.toFixed(3)}`);
    }
    
    return visualPosition;
}

/**
 * Update word elements with current state (DOM recycling approach)
 * Only rebuilds DOM when line changes, otherwise just updates classes/styles
 * 
 * @param {HTMLElement} currentEl - The current lyric element
 * @param {Object} lineData - Word-synced line data
 * @param {number} position - Current playback position
 * @param {string} style - Animation style ('fade' or 'pop')
 * @param {boolean} lineChanged - Whether the line just changed (triggers surrounding lines update)
 */
function updateWordSyncDOM(currentEl, lineData, position, style, lineChanged) {
    // FIX 3: Generate unique ID for this line (prevents cache collisions)
    // Include start, end, and first few words to ensure uniqueness
    const lineId = `${lineData.start}_${lineData.end || 0}_${lineData.words.length}_${(lineData.words[0]?.word || '').substring(0, 10)}`;
    
    // PHASE A: Rebuild DOM only when LINE changes
    if (cachedLineId !== lineId) {
        cachedLineId = lineId;
        
        // Build word spans for the new line
        const html = lineData.words.map((word, i) => {
            const text = escapeHtml(word.word || word.text || '');
            return `<span class="word-sync-word word-upcoming" data-idx="${i}">${text}</span>`;
        }).join(' ');
        
        // Update surrounding lines (single authority - only when line changes)
        updateSurroundingLines(activeLineIndex);
        
        // Claim a new transition token (cancels any pending fade callbacks)
        const myToken = ++transitionToken;
        
        // SMOOTH TRANSITION: Fade-out old line, wait, then swap content
        currentEl.classList.remove('line-entering');
        currentEl.classList.add('line-exiting');
        
        // Delay content swap until fade-out completes
        setTimeout(() => {
            // Check if this transition was cancelled by a newer one
            if (transitionToken !== myToken) return;
            
            // Now swap the content (old line has faded out)
            currentEl.innerHTML = html;
            currentEl.classList.remove('line-exiting');
            currentEl.classList.add('line-entering');
            
            // Cache element references for fast updates
            wordElements = Array.from(currentEl.querySelectorAll('.word-sync-word'));
            
            // Remove entering class after animation completes
            setTimeout(() => {
                if (transitionToken !== myToken) return;
                currentEl.classList.remove('line-entering');
            }, 75);  // Match CSS animation duration
        }, 75); // Wait 75ms for fade-out (match CSS transition)
        
        return; // Skip word updates during transition
    }
    
    // PHASE B: Update only classes/styles (no DOM rebuild)
    const currentWord = findCurrentWord(position, lineData);
    
    // Update word tracking for debug overlay
    totalWordsInLine = lineData.words.length;
    if (currentWord) {
        currentWordIndex = currentWord.wordIndex;
        currentWordProgress = currentWord.progress;
    } else {
        currentWordIndex = -1;
        currentWordProgress = 0;
    }
    
    wordElements.forEach((el, i) => {
        // Efficiently toggle classes
        const isSung = currentWord && i < currentWord.wordIndex;
        const isActive = currentWord && i === currentWord.wordIndex;
        const isUpcoming = !currentWord || i > currentWord.wordIndex;
        
        // Only update if state changed (minor optimization)
        const wasSung = el.classList.contains('word-sung');
        const wasActive = el.classList.contains('word-active');
        
        if (isSung && !wasSung) {
            el.classList.remove('word-active', 'word-upcoming');
            el.classList.add('word-sung');
            el.style.removeProperty('--word-progress');
            el.style.removeProperty('transform');
            el.style.removeProperty('transitionDuration');  // Reset to CSS default
        } else if (isActive) {
            if (!wasActive) {
                el.classList.remove('word-sung', 'word-upcoming');
                el.classList.add('word-active');
            }
            
            // Update progress for active word
            if (style === 'fade') {
                const progress = Math.round(currentWord.progress * 100);
                el.style.setProperty('--word-progress', `${progress}%`);
            } else if (style === 'pop') {
                // Get word duration for dynamic animation
                const wordDuration = currentWord.duration || 0.15;  // Fallback 150ms
                const wordDurationMs = wordDuration * 1000;
                
                // Ultra-short words (<60ms): skip pop animation entirely
                if (wordDurationMs < ULTRA_SHORT_WORD_MS) {
                    el.style.transform = 'scale(1.0)';
                    el.style.transitionDuration = '0ms';
                } else {
                    // Dynamic transition: ~90% of word duration, capped at 150ms
                    const transitionMs = Math.min(wordDurationMs * 0.9, 150);
                    el.style.transitionDuration = `${transitionMs.toFixed(0)}ms`;
                    
                    // Scale peaks at 50% through word, creates nice "pop" feel
                    const scale = 1 + (0.15 * Math.sin(currentWord.progress * Math.PI));
                    el.style.transform = `scale(${scale.toFixed(3)})`;
                }
            }
        } else if (isUpcoming && (wasSung || wasActive)) {
            // This shouldn't normally happen during forward playback
            // but handles seeking backwards
            el.classList.remove('word-sung', 'word-active');
            el.classList.add('word-upcoming');
            el.style.removeProperty('--word-progress');
            el.style.removeProperty('transform');
            el.style.removeProperty('transitionDuration');  // Reset to CSS default
        }
    });
}

/**
 * Core animation frame callback - runs at display refresh rate (60-144fps)
 * 
 * @param {DOMHighResTimeStamp} timestamp - High resolution timestamp from rAF
 */
function animateWordSync(timestamp) {
    // 60 FPS THROTTLE: Skip frames on high refresh rate displays (e.g., 144Hz)
    // This reduces CPU/GPU load by 50-60% without affecting visual quality
    if (timestamp - lastAnimationTime < FRAME_INTERVAL) {
        // Schedule next frame but do no work this frame
        setWordSyncAnimationId(requestAnimationFrame(animateWordSync));
        return;
    }
    lastAnimationTime = timestamp;
    
    // Check if we should continue animating
    // wordSyncEnabled = global toggle, hasWordSync = current song has word-sync data
    if (!wordSyncEnabled || !hasWordSync || !wordSyncedLyrics || wordSyncedLyrics.length === 0) {
        // No word-sync or disabled, clean up and stop
        cleanupWordSync();
        setWordSyncAnimationId(null);
        return;
    }
    
    // Log activation once per song
    if (!_wordSyncLogged) {
        console.log(`[WordSync] Animation started! ${wordSyncedLyrics.length} lines, style: ${wordSyncStyle}, using flywheel clock`);
        _wordSyncLogged = true;
    }
    
    const currentEl = document.getElementById('current');
    if (!currentEl) {
        // Element not found, request next frame anyway
        setWordSyncAnimationId(requestAnimationFrame(animateWordSync));
        return;
    }
    
    // Get position from FLYWHEEL CLOCK (monotonic, never goes backwards)
    const position = updateFlywheelClock(timestamp);
    
    // Find the matching word-sync line AND its index (avoids O(n) search later)
    const { line: wordSyncLine, index: lineIdx } = findCurrentWordSyncLineWithIndex(position);
    
    if (!wordSyncLine || !wordSyncLine.words || wordSyncLine.words.length === 0) {
        // No word-sync data for this position, clean up classes
        currentEl.classList.remove('word-sync-active', 'word-sync-fade', 'word-sync-pop', 'line-entering', 'line-exiting');
        // FIX: Clear #current content to prevent stale "Searching lyrics..." stuck
        currentEl.textContent = '';
        // Update surrounding lines for intro (show upcoming lines)
        if (activeLineIndex !== -1) {
            activeLineIndex = -1;
            updateSurroundingLines(-1);
        }
        // Clear cached state so we rebuild on next valid line
        cachedLineId = null;
        wordElements = [];
        // Request next frame
        setWordSyncAnimationId(requestAnimationFrame(animateWordSync));
        return;
    }
    
    // Store the active line index (single source of truth)
    const previousLineIndex = activeLineIndex;
    activeLineIndex = lineIdx;
    
    // Set timer for line-change back-snap window (240ms opportunity)
    if (lineIdx !== previousLineIndex) {
        lineChangeTime = performance.now();
    }
    
    // Add word-sync classes
    currentEl.classList.add('word-sync-active');
    currentEl.classList.add(`word-sync-${wordSyncStyle}`);
    // Remove other style class if present
    if (wordSyncStyle === 'fade') {
        currentEl.classList.remove('word-sync-pop');
    } else {
        currentEl.classList.remove('word-sync-fade');
    }
    
    // Update DOM using recycling approach (fast path)
    // Use renderPosition (smoothed) for word finding to reduce jitter
    updateWordSyncDOM(currentEl, wordSyncLine, renderPosition, wordSyncStyle);
    
    // Update debug overlay if enabled (throttled to reduce overhead)
    updateDebugOverlay();
    
    // Request next frame (automatically runs at display refresh rate)
    setWordSyncAnimationId(requestAnimationFrame(animateWordSync));
}

/**
 * Clean up word-sync classes from the current element
 */
function cleanupWordSync() {
    const currentEl = document.getElementById('current');
    if (currentEl) {
        currentEl.classList.remove('word-sync-active', 'word-sync-fade', 'word-sync-pop', 'line-entering', 'line-exiting');
    }
    // Reset module state
    cachedLineId = null;
    wordElements = [];
    visualPosition = 0;
    visualSpeed = 1.0;
    lastFrameTime = 0;
    lastAnimationTime = 0;  // Reset FPS throttle
    filteredDrift = 0;      // Reset drift filter
    activeLineIndex = -1;
    transitionToken++;  // Cancel any pending fade callbacks
    _wordSyncLogged = false;
}

/**
 * Start the word-sync animation loop
 * Safe to call multiple times - will not create duplicate loops
 */
export function startWordSyncAnimation() {
    // Don't start if already running
    if (wordSyncAnimationId !== null) {
        return;
    }
    
    // Don't start if word-sync is disabled or no data
    if (!wordSyncEnabled || !hasWordSync || !wordSyncedLyrics) {
        return;
    }
    
    // Initialize flywheel clock from current anchor
    // Account for time elapsed since anchor was set + latency compensation
    const elapsed = (performance.now() - wordSyncAnchorTimestamp) / 1000;
    const totalLatencyCompensation = wordSyncLatencyCompensation + wordSyncSpecificLatencyCompensation;
    visualPosition = wordSyncAnchorPosition + elapsed + totalLatencyCompensation;
    visualSpeed = 1.0;
    lastFrameTime = 0;
    
    console.log('[WordSync] Starting animation loop with flywheel clock');
    setWordSyncAnimationId(requestAnimationFrame(animateWordSync));
}

/**
 * Stop the word-sync animation loop
 */
export function stopWordSyncAnimation() {
    if (wordSyncAnimationId !== null) {
        cancelAnimationFrame(wordSyncAnimationId);
        setWordSyncAnimationId(null);
        console.log('[WordSync] Animation loop stopped');
    }
    cleanupWordSync();
}

/**
 * Reset word-sync state (call on song change)
 */
export function resetWordSyncState() {
    stopWordSyncAnimation();
    // Reset flywheel clock
    visualPosition = 0;
    visualSpeed = 1.0;
    lastFrameTime = 0;
    cachedLineId = null;
    wordElements = [];
    _wordSyncLogged = false;
}

// DEAD CODE: renderWordSyncLine is a legacy function not used in new DOM recycling approach.
// Kept for backward compatibility reference. TODO: Remove in next cleanup.
// @deprecated Use updateWordSyncDOM instead
export function renderWordSyncLine(lineData, position, style = 'fade') {
    if (!lineData || !lineData.words) {
        return lineData?.text || '';
    }

    const currentWord = findCurrentWord(position, lineData);
    const words = lineData.words;
    
    let html = '';
    
    for (let i = 0; i < words.length; i++) {
        const word = words[i];
        const wordText = escapeHtml(word.word || word.text || '');
        
        let classes = ['word-sync-word'];
        let inlineStyle = '';
        
        if (currentWord) {
            if (i < currentWord.wordIndex) {
                classes.push('word-sung');
            } else if (i === currentWord.wordIndex) {
                classes.push('word-active');
                
                if (style === 'fade') {
                    const progress = Math.round(currentWord.progress * 100);
                    inlineStyle = `--word-progress: ${progress}%;`;
                } else if (style === 'pop') {
                    const scale = 1 + (0.15 * Math.sin(currentWord.progress * Math.PI));
                    inlineStyle = `transform: scale(${scale.toFixed(3)});`;
                }
            } else {
                classes.push('word-upcoming');
            }
        } else {
            classes.push('word-upcoming');
        }
        
        const styleAttr = inlineStyle ? ` style="${inlineStyle}"` : '';
        html += `<span class="${classes.join(' ')}"${styleAttr}>${wordText}</span> `;
    }
    
    return html.trim();
}

// ========== DEBUG OVERLAY ==========

/**
 * Get current timing debug data for overlay display
 * @returns {Object} Debug timing data
 */
export function getDebugTimingData() {
    const elapsed = (performance.now() - wordSyncAnchorTimestamp) / 1000;
    const totalLatencyCompensation = wordSyncLatencyCompensation + wordSyncSpecificLatencyCompensation;
    const serverPosition = wordSyncAnchorPosition + elapsed + totalLatencyCompensation;
    const drift = (serverPosition - visualPosition) * 1000;  // in ms
    const pollAge = performance.now() - debugPollTimestamp;  // ms since last poll
    
    // Get total lines count
    const totalLines = wordSyncedLyrics ? wordSyncedLyrics.length : 0;
    
    return {
        serverPos: serverPosition,
        visualPos: visualPosition,
        drift: drift,
        rtt: debugRtt,
        rttSmoothed: debugRttSmoothed,
        rttJitter: debugRttJitter,
        speed: visualSpeed,
        source: debugSource,
        pollAge: pollAge,
        pollInterval: debugPollInterval,
        isPlaying: wordSyncIsPlaying,
        lineIndex: activeLineIndex,
        totalLines: totalLines,
        wordIndex: currentWordIndex,
        wordProgress: currentWordProgress,
        totalWords: totalWordsInLine,
        snapCount: snapCount,
        backSnapCount: backSnapCount,
        badSamples: debugBadSamples,
        latencyComp: totalLatencyCompensation
    };
}

/**
 * Update the debug overlay element with current timing data
 * Called from animation loop when debug is enabled
 */
export function updateDebugOverlay() {
    if (!debugTimingEnabled) return;
    
    const overlay = document.getElementById('debug-timing-overlay');
    if (!overlay) return;
    
    const data = getDebugTimingData();
    
    // Format poll interval with warning for spikes
    const pollWarn = data.pollInterval > 200 ? 'debug-warn' : '';
    
    overlay.innerHTML = `
        <div class="debug-row"><span class="debug-label">Est pos:</span> ${data.serverPos.toFixed(3)}s</div>
        <div class="debug-row"><span class="debug-label">Visual:</span> ${data.visualPos.toFixed(3)}s</div>
        <div class="debug-row"><span class="debug-label">Drift:</span> <span class="${Math.abs(data.drift) > 50 ? 'debug-warn' : ''}">${data.drift >= 0 ? '+' : ''}${data.drift.toFixed(0)}ms</span></div>
        <div class="debug-row"><span class="debug-label">RTT:</span> ${data.rtt.toFixed(0)}ms (avg: ${data.rttSmoothed.toFixed(0)}, jit: ${data.rttJitter.toFixed(0)})</div>
        <div class="debug-row"><span class="debug-label">Speed:</span> ${data.speed.toFixed(3)}x</div>
        <div class="debug-row"><span class="debug-label">dt_poll:</span> <span class="${pollWarn}">${data.pollInterval.toFixed(0)}ms</span></div>
        <div class="debug-row"><span class="debug-label">Source:</span> ${data.source || 'unknown'}</div>
        <div class="debug-row"><span class="debug-label">Snaps:</span> ${data.snapCount} / ${data.backSnapCount}</div>
        <div class="debug-row"><span class="debug-label">Bad:</span> ${data.badSamples} ignored</div>
        <div class="debug-row"><span class="debug-label">Line:</span> ${data.lineIndex + 1}/${data.totalLines}</div>
        <div class="debug-row"><span class="debug-label">Word:</span> ${data.wordIndex + 1}/${data.totalWords} (${(data.wordProgress * 100).toFixed(0)}%)</div>
    `;
}
