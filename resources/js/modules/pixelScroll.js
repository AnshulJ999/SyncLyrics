/**
 * pixelScroll.js - Smooth Scrolling Lyrics Module
 *
 * Implements a lerp-based continuous scroll system following the teleprompter
 * architecture: all lyric lines are rendered in a tall inner strip, and the
 * strip is translated via hardware-accelerated CSS transforms each frame.
 *
 * Key concepts:
 *   - All lines rendered once in a .pixel-scroll-content div
 *   - Active line determined by comparing playback position to timestamps
 *   - targetTranslateY calculated to anchor the active line at 35% of container
 *   - contentTranslateY lerps toward target every frame for smooth motion
 *   - .active class toggled for highlighting; .hidden-line for distant lines
 *   - Word-sync (karaoke) mode: uses word_synced_lyrics timestamps and
 *     injects word spans into the active line for per-word highlighting
 */

// ========== MODULE STATE ==========
let _enabled = false;
let _lerpFactor = 0.12;          // Easing factor (higher = snappier)
let _anchorPercent = 0.35;       // Active line anchored at 35% from top
let _container = null;           // The #lyrics element
let _content = null;             // The .pixel-scroll-content inner strip
let _lineElements = {};          // Cached line DOM elements by index
let _activeLyricIndex = -1;     // Currently highlighted line
let _contentTranslateY = 0;     // Current Y position of the strip
let _targetTranslateY = 0;      // Target Y position (lerp destination)
let _rafId = null;               // requestAnimationFrame ID
let _displayLines = [];          // [{time, text}, ...] all lyrics with timestamps
let _lastSongKey = null;         // Track song changes to re-render
let _originalDisplay = '';       // Original display style of 6-slot elements

// Position tracking (anchor-based interpolation between polls)
let _anchorPosition = 0;         // Position in seconds at last poll
let _anchorTimestamp = 0;        // performance.now() at last poll
let _isPlaying = true;           // Whether playback is active

// Word-sync integration
let _wordSyncActive = false;
let _wordSyncedLyrics = null;    // Word-synced lyrics data from API
let _cachedWordLineId = null;    // Track which line has word spans
let _wordElements = [];          // Cached word span elements

// ========== PUBLIC API ==========

/**
 * Enable pixel scroll mode. Hides the 6-slot elements and creates
 * the scrolling container.
 */
export function enablePixelScroll() {
    _enabled = true;
}

/**
 * Disable pixel scroll mode. Restores the 6-slot elements.
 */
export function disablePixelScroll() {
    _enabled = false;
    _destroy();
}

/**
 * Check if pixel scroll is currently enabled and active.
 */
export function isPixelScrollActive() {
    return _enabled && _content !== null;
}

/**
 * Set the lerp speed factor. Higher = faster snapping.
 * The settings "scroll speed" in ms maps to this: faster ms = higher factor.
 * @param {number} speedMs - Scroll speed in milliseconds (50-3000)
 */
export function setScrollSpeed(speedMs) {
    // Map ms to lerp factor: 50ms → 0.25 (snappy), 3000ms → 0.03 (floaty)
    // Using inverse relationship: factor = k / speedMs
    _lerpFactor = Math.max(0.02, Math.min(0.3, 30 / speedMs));
}

/**
 * Update lyrics data. Called when getLyrics() receives new data.
 * If the song changed, re-render all lines.
 * @param {Array} allLyrics - [{time, text}, ...] from server
 * @param {string} songKey - Unique song identifier (artist-title)
 */
export function updateLyrics(allLyrics, songKey) {
    if (!_enabled || !allLyrics || allLyrics.length === 0) return;

    // Re-render if song changed
    if (songKey !== _lastSongKey) {
        _lastSongKey = songKey;
        _displayLines = allLyrics;
        _activeLyricIndex = -1;
        _cachedWordLineId = null;
        _wordElements = [];
        _renderLines();
    }
}

/**
 * Update word-sync data for karaoke mode.
 * @param {Array|null} wordSyncData - Word-synced lyrics array
 * @param {boolean} active - Whether word-sync is currently active
 */
export function updateWordSync(wordSyncData, active) {
    _wordSyncActive = active;
    _wordSyncedLyrics = wordSyncData;
}

/**
 * Called every frame with the current playback position.
 * Determines active line, calculates target scroll, and lerps.
 * @param {number} position - Current playback position in seconds
 */
export function updatePosition(position) {
    if (!_enabled || !_content || !_container || _displayLines.length === 0) return;

    // A. Determine which line should be active
    const highlightPositionMs = Math.round((position + 0.3) * 1000); // 300ms lookahead
    let newIndex = -1;

    for (let i = 0; i < _displayLines.length; i++) {
        if (Math.round(_displayLines[i].time * 1000) <= highlightPositionMs) {
            newIndex = i;
        } else {
            break;
        }
    }

    // B. If active line changed, update classes and calculate new target
    if (newIndex !== _activeLyricIndex) {
        _activeLyricIndex = newIndex;

        for (const [idx, el] of Object.entries(_lineElements)) {
            const i = parseInt(idx);
            const isActive = i === _activeLyricIndex;
            // Window: show 1 line above, 2 lines below active
            const isHidden = _activeLyricIndex >= 0
                ? (i < _activeLyricIndex - 1 || i > _activeLyricIndex + 2)
                : (i > 2);

            el.classList.toggle('ps-active', isActive);
            el.classList.toggle('ps-hidden', isHidden);
        }

        if (newIndex >= 0) {
            const el = _lineElements[newIndex];
            if (el) {
                const anchorY = _container.clientHeight * _anchorPercent;
                _targetTranslateY = anchorY - el.offsetTop - (el.offsetHeight / 2);
            }
        }

        // Word-sync: inject word spans into new active line
        if (_wordSyncActive && _wordSyncedLyrics && newIndex >= 0) {
            _injectWordSpans(newIndex);
        }
    }

    // C. Word-sync: update per-word highlighting on the active line
    if (_wordSyncActive && _wordSyncedLyrics && _wordElements.length > 0 && _activeLyricIndex >= 0) {
        _updateWordHighlights(position);
    }

    // D. Smooth lerp scroll (runs every frame)
    const diff = _targetTranslateY - _contentTranslateY;
    if (Math.abs(diff) > 0.5) {
        _contentTranslateY += diff * _lerpFactor;
    } else {
        _contentTranslateY = _targetTranslateY;
    }
    _content.style.transform = `translateY(${_contentTranslateY}px)`;
}

/**
 * Update the position anchor (called from polling loop when new track data arrives).
 * @param {number} position - Current playback position in seconds
 * @param {boolean} isPlaying - Whether the track is currently playing
 */
export function setPositionAnchor(position, isPlaying) {
    _anchorPosition = position;
    _anchorTimestamp = performance.now();
    _isPlaying = isPlaying;
}

/**
 * Initialize pixel scroll if enabled. Called once after config loads.
 * Starts the internal rAF loop.
 */
export function init() {
    if (!_enabled) return;
    _container = document.getElementById('lyrics');
    if (!_container) return;
    _ensureContentDiv();
    _startLoop();
}

/**
 * Reset state for a new song (clears rendered lines).
 */
export function reset() {
    _lastSongKey = null;
    _activeLyricIndex = -1;
    _displayLines = [];
    _cachedWordLineId = null;
    _wordElements = [];
    if (_content) {
        _content.innerHTML = '';
    }
}

/**
 * Internal rAF loop - interpolates position and calls updatePosition each frame.
 */
function _startLoop() {
    if (_rafId) return; // Already running

    function tick() {
        if (!_enabled) {
            _rafId = null;
            return;
        }

        // Interpolate position: anchor + elapsed since last poll
        let position = _anchorPosition;
        if (_isPlaying) {
            const elapsed = (performance.now() - _anchorTimestamp) / 1000;
            position += elapsed;
        }

        updatePosition(position);
        _rafId = requestAnimationFrame(tick);
    }

    _rafId = requestAnimationFrame(tick);
}

// ========== PRIVATE HELPERS ==========

/**
 * Create the .pixel-scroll-content div inside #lyrics and hide the 6-slot elements.
 */
function _ensureContentDiv() {
    if (_content) return;

    _container.classList.add('pixel-scroll');

    // Hide the 6 fixed lyric-line elements
    const slots = _container.querySelectorAll('.lyric-line');
    slots.forEach(el => {
        _originalDisplay = el.style.display || '';
        el.style.display = 'none';
    });

    // Create scrolling content strip
    _content = document.createElement('div');
    _content.className = 'pixel-scroll-content';
    _container.appendChild(_content);
}

/**
 * Destroy pixel scroll mode, restore 6-slot elements.
 */
function _destroy() {
    if (_rafId) {
        cancelAnimationFrame(_rafId);
        _rafId = null;
    }

    if (_container) {
        _container.classList.remove('pixel-scroll');

        // Restore 6-slot elements
        const slots = _container.querySelectorAll('.lyric-line');
        slots.forEach(el => {
            el.style.display = _originalDisplay;
        });

        // Remove scrolling content
        if (_content) {
            _content.remove();
            _content = null;
        }
    }

    _lineElements = {};
    _activeLyricIndex = -1;
    _contentTranslateY = 0;
    _targetTranslateY = 0;
    _lastSongKey = null;
    _displayLines = [];
    _cachedWordLineId = null;
    _wordElements = [];
}

/**
 * Render all lyric lines into the content strip.
 */
function _renderLines() {
    if (!_content || !_container) {
        _ensureContentDiv();
        if (!_content) return;
    }

    let html = '';
    for (let i = 0; i < _displayLines.length; i++) {
        const text = _escapeHtml(_displayLines[i].text);
        html += `<div class="ps-line" data-line-index="${i}">${text}</div>`;
    }
    _content.innerHTML = html;

    // Cache DOM elements for fast loop access
    _lineElements = {};
    _content.querySelectorAll('[data-line-index]').forEach(el => {
        _lineElements[parseInt(el.dataset.lineIndex)] = el;
    });

    // Set initial position (first line below the visible area)
    if (_lineElements[0]) {
        _contentTranslateY = _container.clientHeight;
        _targetTranslateY = _contentTranslateY;
        _content.style.transform = `translateY(${_contentTranslateY}px)`;
    }
}

/**
 * Inject word-sync spans into the active line for karaoke highlighting.
 */
function _injectWordSpans(lineIndex) {
    if (!_wordSyncedLyrics || lineIndex < 0 || lineIndex >= _wordSyncedLyrics.length) return;

    const lineData = _wordSyncedLyrics[lineIndex];
    if (!lineData || !lineData.words || lineData.words.length === 0) return;

    const lineId = `${lineData.start}_${lineIndex}`;
    if (_cachedWordLineId === lineId) return; // Already injected
    _cachedWordLineId = lineId;

    const el = _lineElements[lineIndex];
    if (!el) return;

    // Build word spans
    const spans = lineData.words.map((word, i) => {
        const text = _escapeHtml(word.word || word.text || '');
        return `<span class="ps-word ps-word-upcoming" data-word-idx="${i}">${text}</span>`;
    });

    // Smart join: handle contractions
    const html = spans.reduce((acc, span, i) => {
        if (i === 0) return span;
        const currentWord = (lineData.words[i].word || '').toLowerCase();
        const prevWord = (lineData.words[i - 1].word || '');
        const isApostrophe = currentWord === "'" || currentWord === "\u2019";
        const prevEndsWithApostrophe = /['\u2019]$/.test(prevWord);
        const isContractionSuffix = /^[msdt]$|^(re|ve|ll)$/i.test(currentWord);
        if (isApostrophe || (prevEndsWithApostrophe && isContractionSuffix)) {
            return acc + span;
        }
        return acc + ' ' + span;
    }, '');

    el.innerHTML = html;
    _wordElements = Array.from(el.querySelectorAll('.ps-word'));
}

/**
 * Update per-word highlights based on playback position.
 */
function _updateWordHighlights(position) {
    if (!_wordSyncedLyrics || _activeLyricIndex < 0) return;
    const lineData = _wordSyncedLyrics[_activeLyricIndex];
    if (!lineData || !lineData.words) return;

    const positionMs = position * 1000;

    for (let i = 0; i < _wordElements.length && i < lineData.words.length; i++) {
        const word = lineData.words[i];
        const wordStart = (word.start || word.startTimeMs / 1000 || 0) * 1000;
        const wordEnd = (word.end || word.endTimeMs / 1000 || wordStart + 200) * 1000;
        const el = _wordElements[i];

        if (positionMs >= wordEnd) {
            el.className = 'ps-word ps-word-sung';
        } else if (positionMs >= wordStart) {
            el.className = 'ps-word ps-word-active';
        } else {
            el.className = 'ps-word ps-word-upcoming';
        }
    }
}

/**
 * Escape HTML entities.
 */
function _escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
