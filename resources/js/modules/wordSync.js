/**
 * wordSync.js - Word-Synced Lyrics Module
 * 
 * This module handles word-level timing for karaoke-style lyrics display.
 * Supports two visual styles: 'fade' (gradient sweep) and 'pop' (word scale).
 * 
 * Uses requestAnimationFrame for smooth 60-144fps animation, interpolating
 * position between 100ms server polls.
 * 
 * Performance optimizations:
 * - DOM recycling: Only rebuild HTML when line changes, not every frame
 * - Time smoothing: Exponential smoothing to reduce poll jitter
 * - Gap detection: Correctly handle silences between words
 * 
 * Level 2 - Imports: state
 */

import {
    wordSyncedLyrics,
    hasWordSync,
    wordSyncStyle,
    wordSyncAnchorPosition,
    wordSyncAnchorTimestamp,
    wordSyncIsPlaying,
    wordSyncAnimationId,
    wordSyncLatencyCompensation,
    setWordSyncAnimationId
} from './state.js';

// ========== MODULE STATE ==========

// DOM recycling: Cache line ID and word element references
let cachedLineId = null;
let wordElements = [];

// Time smoothing: Exponential smoothing for position
let smoothedPosition = 0;
const SMOOTHING_FACTOR = 0.2; // Higher = more responsive, Lower = smoother

// Track last frame time for delta calculations
let lastFrameTime = 0;

// Track if we've logged word-sync activation (reset on song change)
let _wordSyncLogged = false;

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
        return { wordIndex: -1, progress: 0 };
    }

    // Check if we're before the first word even starts
    const firstWordStart = lineStart + (words[0].time || 0);
    if (position < firstWordStart) {
        return { wordIndex: -1, progress: 0 };
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
            return { wordIndex: i, progress };
        }
        
        // CASE 2: Gap detection - we are BEFORE this word starts
        // Since words are sorted, if we're before word[i], we're in the gap after word[i-1]
        if (position < wordStart && i > 0) {
            // Return previous word as fully sung
            return { wordIndex: i - 1, progress: 1 };
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
        return { wordIndex: words.length, progress: 0 };
    }

    // Inside last word (fallback)
    const duration = lastWordEnd - lastWordStart;
    const progress = duration > 0 ? Math.min(1, (position - lastWordStart) / duration) : 1;
    return { wordIndex: words.length - 1, progress };
}

/**
 * Find the current line from word-synced lyrics based on position
 * 
 * @param {number} position - Current playback position in seconds
 * @returns {Object|null} The line object or null
 */
export function findCurrentWordSyncLine(position) {
    if (!wordSyncedLyrics || wordSyncedLyrics.length === 0) {
        return null;
    }

    // Find the line that contains the current position
    for (let i = 0; i < wordSyncedLyrics.length; i++) {
        const line = wordSyncedLyrics[i];
        const nextLine = wordSyncedLyrics[i + 1];
        
        const lineStart = line.start || 0;
        const lineEnd = nextLine ? nextLine.start : (line.end || lineStart + 10);
        
        if (position >= lineStart && position < lineEnd) {
            return line;
        }
    }

    return null;
}

/**
 * Check if word-sync is available for the current song
 * 
 * @returns {boolean} True if word-sync is available
 */
export function isWordSyncAvailable() {
    return hasWordSync && wordSyncedLyrics && wordSyncedLyrics.length > 0;
}

// ========== ANIMATION LOOP ==========

/**
 * Calculate interpolated position based on anchor + elapsed time
 * 
 * @returns {number} Current interpolated position in seconds
 */
function getInterpolatedPosition() {
    if (!wordSyncIsPlaying) {
        // Paused - return anchor position without interpolation
        // Apply latency compensation even when paused for consistency
        return wordSyncAnchorPosition + wordSyncLatencyCompensation;
    }
    
    // Calculate elapsed time since last server poll
    const elapsed = (performance.now() - wordSyncAnchorTimestamp) / 1000;
    
    // If elapsed is huge (>0.5s), we likely just resumed from pause
    // Don't interpolate forward - wait for next poll to update anchor
    // This prevents the 2-second jump bug on resume
    if (elapsed > 0.5) {
        return wordSyncAnchorPosition + wordSyncLatencyCompensation;
    }
    
    // Cap interpolation to 2 seconds to prevent runaway drift
    // (Server polls every 100ms, so anything > 1s means we missed a poll)
    const cappedElapsed = Math.min(elapsed, 2.0);
    
    // Apply latency compensation (negative = lyrics appear later)
    // This matches the backend line-sync behavior
    return wordSyncAnchorPosition + cappedElapsed + wordSyncLatencyCompensation;
}

/**
 * Get smoothed position using exponential smoothing
 * Reduces jitter from 100ms poll variance
 * 
 * @returns {number} Smoothed position in seconds
 */
function getSmoothedPosition() {
    const rawPosition = getInterpolatedPosition();
    
    // Handle seeks (large jumps > 1 second) - bypass smoothing
    if (Math.abs(rawPosition - smoothedPosition) > 1.0) {
        smoothedPosition = rawPosition;
        return smoothedPosition;
    }
    
    // Exponential smoothing: gently chase the raw position
    smoothedPosition += (rawPosition - smoothedPosition) * SMOOTHING_FACTOR;
    return smoothedPosition;
}

/**
 * Update word elements with current state (DOM recycling approach)
 * Only rebuilds DOM when line changes, otherwise just updates classes/styles
 * 
 * @param {HTMLElement} currentEl - The current lyric element
 * @param {Object} lineData - Word-synced line data
 * @param {number} position - Current playback position
 * @param {string} style - Animation style ('fade' or 'pop')
 */
function updateWordSyncDOM(currentEl, lineData, position, style) {
    // Generate unique ID for this line
    const lineId = `${lineData.start}_${lineData.words.length}`;
    
    // PHASE A: Rebuild DOM only when LINE changes
    if (cachedLineId !== lineId) {
        cachedLineId = lineId;
        
        // Build word spans once
        const html = lineData.words.map((word, i) => {
            const text = escapeHtml(word.word || word.text || '');
            return `<span class="word-sync-word word-upcoming" data-idx="${i}">${text}</span>`;
        }).join(' ');
        
        currentEl.innerHTML = html;
        
        // Cache element references for fast updates
        wordElements = Array.from(currentEl.querySelectorAll('.word-sync-word'));
    }
    
    // PHASE B: Update only classes/styles (no DOM rebuild)
    const currentWord = findCurrentWord(position, lineData);
    
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
                // Scale peaks at 50% through word, creates nice "pop" feel
                const scale = 1 + (0.15 * Math.sin(currentWord.progress * Math.PI));
                el.style.transform = `scale(${scale.toFixed(3)})`;
            }
        } else if (isUpcoming && (wasSung || wasActive)) {
            // This shouldn't normally happen during forward playback
            // but handles seeking backwards
            el.classList.remove('word-sung', 'word-active');
            el.classList.add('word-upcoming');
            el.style.removeProperty('--word-progress');
            el.style.removeProperty('transform');
        }
    });
}

/**
 * Core animation frame callback - runs at display refresh rate (60-144fps)
 * 
 * @param {DOMHighResTimeStamp} timestamp - High resolution timestamp from rAF
 */
function animateWordSync(timestamp) {
    // Track frame time for future delta-based animations
    lastFrameTime = timestamp;
    
    // Check if we should continue animating
    if (!hasWordSync || !wordSyncedLyrics || wordSyncedLyrics.length === 0) {
        // No word-sync, clean up and stop
        cleanupWordSync();
        setWordSyncAnimationId(null);
        return;
    }
    
    // Log activation once per song
    if (!_wordSyncLogged) {
        console.log(`[WordSync] Animation started! ${wordSyncedLyrics.length} lines, style: ${wordSyncStyle}, running at display refresh rate`);
        _wordSyncLogged = true;
    }
    
    const currentEl = document.getElementById('current');
    if (!currentEl) {
        // Element not found, request next frame anyway
        setWordSyncAnimationId(requestAnimationFrame(animateWordSync));
        return;
    }
    
    // Get smoothed interpolated position (reduces poll jitter)
    const position = getSmoothedPosition();
    
    // Find the matching word-sync line
    const wordSyncLine = findCurrentWordSyncLine(position);
    
    if (!wordSyncLine || !wordSyncLine.words || wordSyncLine.words.length === 0) {
        // No word-sync data for this position, clean up classes
        currentEl.classList.remove('word-sync-active', 'word-sync-fade', 'word-sync-pop');
        // Clear cached state so we rebuild on next valid line
        cachedLineId = null;
        wordElements = [];
        // Request next frame
        setWordSyncAnimationId(requestAnimationFrame(animateWordSync));
        return;
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
    updateWordSyncDOM(currentEl, wordSyncLine, position, wordSyncStyle);
    
    // Request next frame (automatically runs at display refresh rate)
    setWordSyncAnimationId(requestAnimationFrame(animateWordSync));
}

/**
 * Clean up word-sync classes from the current element
 */
function cleanupWordSync() {
    const currentEl = document.getElementById('current');
    if (currentEl) {
        currentEl.classList.remove('word-sync-active', 'word-sync-fade', 'word-sync-pop');
    }
    // Reset module state
    cachedLineId = null;
    wordElements = [];
    smoothedPosition = 0;
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
    
    // Don't start if no word-sync data
    if (!hasWordSync || !wordSyncedLyrics) {
        return;
    }
    
    console.log('[WordSync] Starting animation loop');
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
    // Reset smoothing position to prevent carry-over between songs
    smoothedPosition = 0;
    cachedLineId = null;
    wordElements = [];
    _wordSyncLogged = false;
}

// Legacy export for backward compatibility (not used in new DOM recycling approach)
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
