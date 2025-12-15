/**
 * wordSync.js - Word-Synced Lyrics Module
 * 
 * This module handles word-level timing for karaoke-style lyrics display.
 * Supports two visual styles: 'fade' (gradient sweep) and 'pop' (word scale).
 * 
 * Level 2 - Imports: state
 */

import {
    wordSyncedLyrics,
    hasWordSync,
    wordSyncStyle,
    lastTrackInfo
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
    
    // Check if we're before the first word
    if (position < words[0].start) {
        return { wordIndex: -1, progress: 0 };
    }

    // Find current word
    for (let i = 0; i < words.length; i++) {
        const word = words[i];
        const wordEnd = word.start + (word.duration || 0.5);
        
        if (position >= word.start && position < wordEnd) {
            // Calculate progress within this word (0-1)
            const duration = word.duration || 0.5;
            const progress = Math.min(1, (position - word.start) / duration);
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
        const wordText = word.text || word.word || '';
        
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
                    // Scale effect based on progress
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
 * Check if current line has word-sync data available
 * 
 * @param {string} currentLineText - The current line text to match
 * @returns {Object|null} Matching word-sync line data or null
 */
export function getWordSyncForLine(currentLineText) {
    if (!hasWordSync || !wordSyncedLyrics) {
        return null;
    }

    // Try to find matching line by text
    const normalizedText = currentLineText.toLowerCase().trim();
    
    for (const line of wordSyncedLyrics) {
        const lineText = (line.text || '').toLowerCase().trim();
        if (lineText === normalizedText) {
            return line;
        }
    }

    return null;
}

/**
 * Get word-sync line by position
 * 
 * @param {number} position - Current playback position in seconds
 * @returns {Object|null} Word-sync line data or null
 */
export function getWordSyncByPosition(position) {
    if (!hasWordSync || !wordSyncedLyrics) {
        return null;
    }

    return findCurrentWordSyncLine(position);
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
 * Get current playback position from track info
 * 
 * @returns {number} Position in seconds
 */
export function getCurrentPosition() {
    if (!lastTrackInfo) return 0;
    return lastTrackInfo.position || 0;
}

/**
 * Update the current line element with word-sync highlighting
 * Call this on each update loop iteration when word-sync is available
 * 
 * @param {number} position - Current playback position in seconds
 * @param {string} currentLineText - Current line text for matching
 */
export function updateCurrentLineWithWordSync(position, currentLineText) {
    if (!hasWordSync || !wordSyncedLyrics) {
        // No word-sync available, ensure we clean up any previous word-sync state
        const currentEl = document.getElementById('current');
        if (currentEl) {
            currentEl.classList.remove('word-sync-active', 'word-sync-fade', 'word-sync-pop');
        }
        return;
    }

    const currentEl = document.getElementById('current');
    if (!currentEl) return;

    // Find the matching word-sync line by position (more reliable than text matching)
    const wordSyncLine = findCurrentWordSyncLine(position);
    
    if (!wordSyncLine || !wordSyncLine.words || wordSyncLine.words.length === 0) {
        // No word-sync data for this position, show plain text
        currentEl.classList.remove('word-sync-active', 'word-sync-fade', 'word-sync-pop');
        // Don't modify innerHTML - let setLyricsInDom handle text updates
        return;
    }

    // Add word-sync classes
    currentEl.classList.add('word-sync-active');
    currentEl.classList.add(`word-sync-${wordSyncStyle}`);
    
    // Render word-synced line
    const html = renderWordSyncLine(wordSyncLine, position, wordSyncStyle);
    
    // Only update if HTML actually changed (performance optimization)
    if (currentEl.innerHTML !== html) {
        currentEl.innerHTML = html;
    }
}
