/**
 * wordSync.js - Word-Synced Lyrics Module
 * 
 * This module handles word-level timing for karaoke-style lyrics display.
 * Supports two visual styles: 'fade' (gradient sweep) and 'pop' (word scale).
 * 
 * Uses requestAnimationFrame for smooth 60-144fps animation, interpolating
 * position between 100ms server polls.
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
    setWordSyncAnimationId
} from './state.js';

// ========== WORD SYNC UTILITIES ==========

/**
 * Find the current word being sung based on playback position
 * 
 * @param {number} position - Current playback position in seconds
 * @param {Array} lineData - Word-synced data for current line
 * @returns {Object|null} { wordIndex, progress } or null if not found
 */
export function findCurrentWord(position, lineData) {
    if (!lineData || !lineData.words || lineData.words.length === 0) {
        return null;
    }

    const words = lineData.words;
    const lineStart = lineData.start || 0;
    
    // Calculate absolute time for first word
    const firstWordStart = lineStart + (words[0].time || 0);
    
    // Check if we're before the first word
    if (position < firstWordStart) {
        return { wordIndex: -1, progress: 0 };
    }

    // Find current word
    // Word timing: word.time is OFFSET from line start, not absolute time
    for (let i = 0; i < words.length; i++) {
        const word = words[i];
        const wordStart = lineStart + (word.time || 0);
        
        // Calculate word end: either next word's start or estimate based on line end
        let wordEnd;
        if (i + 1 < words.length) {
            wordEnd = lineStart + (words[i + 1].time || 0);
        } else {
            // Last word - use line end or estimate
            wordEnd = lineData.end || (wordStart + 0.5);
        }
        
        if (position >= wordStart && position < wordEnd) {
            // Calculate progress within this word (0-1)
            const duration = wordEnd - wordStart;
            const progress = duration > 0 ? Math.min(1, (position - wordStart) / duration) : 1;
            return { wordIndex: i, progress };
        }
    }

    // After last word
    return { wordIndex: words.length - 1, progress: 1 };
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
 * Render a word-synced line with span elements for each word
 * 
 * @param {Object} lineData - Word-synced line data
 * @param {number} position - Current playback position
 * @param {string} style - Animation style ('fade' or 'pop')
 * @returns {string} HTML string with word spans
 */
export function renderWordSyncLine(lineData, position, style = 'fade') {
    if (!lineData || !lineData.words) {
        return lineData?.text || '';
    }

    const currentWord = findCurrentWord(position, lineData);
    const words = lineData.words;
    
    let html = '';
    
    for (let i = 0; i < words.length; i++) {
        const word = words[i];
        // Word text is in 'word' field (from Musixmatch) or 'text' field (fallback)
        const wordText = word.word || word.text || '';
        
        // Determine word state
        let classes = ['word-sync-word'];
        let inlineStyle = '';
        
        if (currentWord) {
            if (i < currentWord.wordIndex) {
                // Already sung
                classes.push('word-sung');
            } else if (i === currentWord.wordIndex) {
                // Currently being sung
                classes.push('word-active');
                
                if (style === 'fade') {
                    // Gradient progress effect
                    const progress = Math.round(currentWord.progress * 100);
                    inlineStyle = `--word-progress: ${progress}%;`;
                } else if (style === 'pop') {
                    // Scale effect based on progress (peaks at 50% through word)
                    const scale = 1 + (0.15 * Math.sin(currentWord.progress * Math.PI));
                    inlineStyle = `transform: scale(${scale.toFixed(3)});`;
                }
            } else {
                // Not yet sung
                classes.push('word-upcoming');
            }
        }
        
        const styleAttr = inlineStyle ? ` style="${inlineStyle}"` : '';
        html += `<span class="${classes.join(' ')}"${styleAttr}>${wordText}</span> `;
    }
    
    return html.trim();
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

// Track if we've logged word-sync activation (reset on song change)
let _wordSyncLogged = false;

/**
 * Calculate interpolated position based on anchor + elapsed time
 * 
 * @returns {number} Current interpolated position in seconds
 */
function getInterpolatedPosition() {
    if (!wordSyncIsPlaying) {
        // Paused - return anchor position without interpolation
        return wordSyncAnchorPosition;
    }
    
    // Calculate elapsed time since last server poll
    const elapsed = (performance.now() - wordSyncAnchorTimestamp) / 1000;
    
    // Cap interpolation to 2 seconds to prevent runaway drift
    // (Server polls every 100ms, so anything > 1s means we missed a poll)
    const cappedElapsed = Math.min(elapsed, 2.0);
    
    return wordSyncAnchorPosition + cappedElapsed;
}

/**
 * Core animation frame callback - runs at display refresh rate (60-144fps)
 * 
 * @param {DOMHighResTimeStamp} timestamp - High resolution timestamp from rAF
 */
function animateWordSync(timestamp) {
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
    
    // Get interpolated position (smooth between polls)
    const position = getInterpolatedPosition();
    
    // Find the matching word-sync line
    const wordSyncLine = findCurrentWordSyncLine(position);
    
    if (!wordSyncLine || !wordSyncLine.words || wordSyncLine.words.length === 0) {
        // No word-sync data for this position, clean up classes
        currentEl.classList.remove('word-sync-active', 'word-sync-fade', 'word-sync-pop');
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
    
    // Render word-synced line
    const html = renderWordSyncLine(wordSyncLine, position, wordSyncStyle);
    
    // Only update if HTML actually changed (performance optimization)
    if (currentEl.innerHTML !== html) {
        currentEl.innerHTML = html;
    }
    
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
    _wordSyncLogged = false;
}
