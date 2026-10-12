/**
 * lyricsFlow.js - Lyrics Flow display mode
 *
 * Shows the whole song as one sheet and glides it so the current line stays at
 * a fixed spot, instead of redrawing six fixed lines in one frame. The reader's
 * eye can follow the line it was reading as it moves away.
 *
 * The server still decides which line is current (same timing, latency offsets
 * and intro/outro handling); /lyrics?flow=1 returns the sheet rows and the
 * current row (see lyrics_flow.py). This module only renders and moves them:
 *   - one layer inside #lyrics; the six slots stay populated underneath (karaoke
 *     and other code keep using them) and are hidden while the sheet shows
 *   - glide = CSS transform transition (retargets mid-glide on fast lines)
 *   - jump  = same transform with transitions off (new sheet, seek, re-show,
 *     re-measure, reduced motion)
 *   - row positions measured once per layout and cached
 *
 * Level 1 - Imports: state
 */

import { flowSheet, flowSheetId } from './state.js';

// ========== CONSTANTS ==========

const ANCHOR_RATIO = 0.3;  // fixed spot: this fraction of the lyrics area's height, to the top of the current row
const JUMP_DISTANCE = 2;   // a row step larger than this is a seek or skip, not playback: jump instead of gliding

const reducedMotion = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;

// ========== STATE ==========

let container = null;     // #lyrics
let layer = null;         // the sheet
let rowEls = [];
let rowTops = [];         // cached offsetTop per row (relative to #lyrics)
let renderedSheetId = null;
let currentRow = null;
let visible = false;
let measured = false;

// ========== LAYOUT ==========

function ensureLayer() {
    if (layer) return true;
    container = document.getElementById('lyrics');
    if (!container) return false;
    layer = document.createElement('div');
    layer.className = 'lyrics-flow';
    container.appendChild(layer);

    // Re-measure and snap (no glide) when the area or the text layout changes:
    // rotation, display toggles, font scale, late web fonts
    const remeasure = () => {
        measured = false;
        if (visible && currentRow !== null) requestAnimationFrame(() => moveTo(currentRow, true));
    };
    if (window.ResizeObserver) new ResizeObserver(remeasure).observe(container);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(remeasure);
    return true;
}

function build(rows) {
    layer.textContent = '';
    rowEls = rows.map(text => {
        const el = document.createElement('div');
        el.className = 'flow-line';
        el.textContent = text;
        layer.appendChild(el);
        return el;
    });
    renderedSheetId = flowSheetId;
    currentRow = null;
    measured = false;
}

function measure() {
    // One batched read pass; offsetTop ignores the layer's own transform
    rowTops = rowEls.map(el => el.offsetTop);
    measured = true;
}

function moveTo(row, jump) {
    if (!rowEls[row]) return;
    if (!measured) measure();
    const y = container.clientHeight * ANCHOR_RATIO - rowTops[row];
    if (jump || (reducedMotion && reducedMotion.matches)) {
        layer.classList.add('flow-no-transition');
        layer.style.transform = `translateY(${y}px)`;
        void layer.offsetHeight; // commit the jump before transitions come back
        layer.classList.remove('flow-no-transition');
    } else {
        layer.style.transform = `translateY(${y}px)`;
    }
}

function setCurrent(row) {
    if (currentRow !== null && rowEls[currentRow]) {
        rowEls[currentRow].classList.remove('flow-current');
        rowEls[currentRow].removeAttribute('aria-current');
    }
    rowEls[row].classList.add('flow-current');
    rowEls[row].setAttribute('aria-current', 'true');
}

// ========== PUBLIC API ==========

/**
 * Show the sheet at the given row. Call every poll while Lyrics Flow should show.
 * Hides itself when there is no sheet for the current state.
 *
 * @param {number|null} row - current sheet row from /lyrics flow.row
 */
export function showLyricsFlow(row) {
    if (row === null || row === undefined || !flowSheet || !ensureLayer()) {
        hideLyricsFlow();
        return;
    }

    let jump = false;
    if (renderedSheetId !== flowSheetId) {
        build(flowSheet);
        jump = true;
    }
    if (!visible) {
        visible = true;
        document.body.classList.add('lyrics-flow-active');
        measured = false; // the layer had no layout while hidden
        jump = true;
    }
    if (!rowEls[row]) {
        hideLyricsFlow();
        return;
    }
    if (row === currentRow && !jump) return;
    if (currentRow !== null && Math.abs(row - currentRow) > JUMP_DISTANCE) jump = true;

    setCurrent(row);
    measured = false; // the current line is heavier, so a long line can rewrap and move the rows below it
    moveTo(row, jump);
    currentRow = row;
}

/**
 * Hide the sheet and show today's six-line display (no sheet yet, karaoke, mode off).
 */
export function hideLyricsFlow() {
    if (!visible) return;
    visible = false;
    document.body.classList.remove('lyrics-flow-active');
}
