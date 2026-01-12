/**
 * keyboard.js - Global Keyboard Shortcuts
 * 
 * Centralized keyboard shortcut handler for playback, UI toggles,
 * and image navigation. Shortcuts are disabled when typing in inputs.
 * 
 * Level 3 - Imports: api, state, slideshow, dom
 */

import { playbackCommand } from './api.js';
import { showToast } from './dom.js';
import {
    slideshowEnabled,
    visualModeActive,
    wordSyncEnabled,
    hasWordSync,
    displayConfig,
    setWordSyncEnabled,
    setManualVisualModeOverride
} from './state.js';
import {
    toggleSlideshow,
    advanceSlide,
    previousSlide
} from './slideshow.js';
import { updateMainLatencyVisibility } from './latency.js';

// ========== MODULE STATE ==========

// Callbacks set during init (from main.js)
let enterVisualModeFn = null;
let exitVisualModeFn = null;
let toggleArtOnlyModeFn = null;
let toggleMinimalModeFn = null;
let updateWordSyncToggleUIFn = null;

// ========== HELPER ==========

/**
 * Check if event target is an input field (typing)
 * @param {Event} e - Keyboard event
 * @returns {boolean} True if user is typing
 */
function isTyping(e) {
    return (
        e.target.tagName === 'INPUT' ||
        e.target.tagName === 'TEXTAREA' ||
        e.target.isContentEditable
    );
}

// ========== SHORTCUT HANDLERS ==========

/**
 * Handle Space key - Play/Pause
 */
async function handlePlayPause() {
    try {
        await playbackCommand('play-pause');
    } catch (error) {
        console.error('[Keyboard] Play/Pause failed:', error);
        showToast('Playback failed', 'error', 800);
    }
}

/**
 * Handle Ctrl+Arrow - Previous/Next Track
 */
async function handlePreviousTrack() {
    try {
        await playbackCommand('previous');
    } catch (error) {
        console.error('[Keyboard] Previous track failed:', error);
    }
}

async function handleNextTrack() {
    try {
        await playbackCommand('next');
    } catch (error) {
        console.error('[Keyboard] Next track failed:', error);
    }
}

/**
 * Handle Arrow Left/Right - Previous/Next Slide (when slideshow active)
 */
function handlePreviousSlide() {
    if (slideshowEnabled) {
        previousSlide();
    }
}

function handleNextSlide() {
    if (slideshowEnabled) {
        advanceSlide();
    }
}

/**
 * Handle V key - Toggle Visual Mode
 */
function handleVisualModeToggle() {
    if (!enterVisualModeFn || !exitVisualModeFn) return;
    
    if (visualModeActive) {
        setManualVisualModeOverride(false);
        exitVisualModeFn();
    } else {
        setManualVisualModeOverride(true);
        enterVisualModeFn();
    }
}

/**
 * Handle W key - Toggle Word-Sync
 */
function handleWordSyncToggle() {
    const newState = !wordSyncEnabled;
    setWordSyncEnabled(newState);
    localStorage.setItem('wordSyncEnabled', newState);
    
    // Update UI
    if (updateWordSyncToggleUIFn) {
        updateWordSyncToggleUIFn();
    }
    updateMainLatencyVisibility();
    
    // Update URL
    const url = new URL(window.location.href);
    if (newState) {
        url.searchParams.set('wordSync', 'true');
    } else {
        url.searchParams.delete('wordSync');
    }
    history.replaceState(null, '', url.toString());
}

/**
 * Handle M key - Toggle Minimal Mode
 */
function handleMinimalModeToggle() {
    if (toggleMinimalModeFn) {
        toggleMinimalModeFn();
    }
}

/**
 * Handle A key - Toggle Art-Only Mode
 */
function handleArtOnlyToggle() {
    if (toggleArtOnlyModeFn) {
        toggleArtOnlyModeFn();
    }
}

/**
 * Handle Escape key - Exit Art-Only Mode
 */
function handleEscape() {
    const isArtOnly = document.body.classList.contains('art-only-mode');
    if (isArtOnly && toggleArtOnlyModeFn) {
        toggleArtOnlyModeFn(); // Exits art-only mode
    }
}

/**
 * Handle F key - Toggle Fullscreen
 */
function handleFullscreenToggle() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
    } else if (document.exitFullscreen) {
        document.exitFullscreen();
    }
}

// ========== MAIN HANDLER ==========

/**
 * Main keydown handler - routes to appropriate action
 */
function handleKeydown(e) {
    // Skip if typing in input
    if (isTyping(e)) return;
    
    const key = e.key;
    const ctrl = e.ctrlKey || e.metaKey;
    
    // Ctrl+Arrow: Previous/Next Track
    if (ctrl && key === 'ArrowLeft') {
        e.preventDefault();
        handlePreviousTrack();
        return;
    }
    if (ctrl && key === 'ArrowRight') {
        e.preventDefault();
        handleNextTrack();
        return;
    }
    
    // Skip other shortcuts if Ctrl/Alt/Meta held (except Ctrl+Arrows above)
    if (ctrl || e.altKey) return;
    
    switch (key) {
        case ' ':
            e.preventDefault();
            handlePlayPause();
            break;
            
        case 'ArrowLeft':
            e.preventDefault();
            handlePreviousSlide();
            break;
            
        case 'ArrowRight':
            e.preventDefault();
            handleNextSlide();
            break;
            
        case 'v':
        case 'V':
            e.preventDefault();
            handleVisualModeToggle();
            break;
            
        case 'w':
        case 'W':
            e.preventDefault();
            handleWordSyncToggle();
            break;
            
        case 'm':
        case 'M':
            e.preventDefault();
            handleMinimalModeToggle();
            break;
            
        case 'a':
        case 'A':
            e.preventDefault();
            handleArtOnlyToggle();
            break;
            
        case 'Escape':
            handleEscape();
            break;
            
        case 's':
        case 'S':
            e.preventDefault();
            toggleSlideshow();
            break;
            
        case 'f':
        case 'F':
            e.preventDefault();
            handleFullscreenToggle();
            break;
    }
}

// ========== INITIALIZATION ==========

/**
 * Initialize keyboard shortcuts
 * 
 * @param {Object} callbacks - Callback functions from main.js
 * @param {Function} callbacks.enterVisualMode - Enter visual mode
 * @param {Function} callbacks.exitVisualMode - Exit visual mode
 * @param {Function} callbacks.toggleArtOnlyMode - Toggle art-only mode
 * @param {Function} callbacks.toggleMinimalMode - Toggle minimal mode
 * @param {Function} callbacks.updateWordSyncToggleUI - Update word-sync button UI
 */
export function initKeyboardShortcuts(callbacks = {}) {
    // Store callbacks
    enterVisualModeFn = callbacks.enterVisualMode || null;
    exitVisualModeFn = callbacks.exitVisualMode || null;
    toggleArtOnlyModeFn = callbacks.toggleArtOnlyMode || null;
    toggleMinimalModeFn = callbacks.toggleMinimalMode || null;
    updateWordSyncToggleUIFn = callbacks.updateWordSyncToggleUI || null;
    
    // Attach global keydown handler
    document.addEventListener('keydown', handleKeydown);
    
    console.log('[Keyboard] Shortcuts initialized');
    console.log('[Keyboard] Space=Play/Pause, ←/→=Images, Ctrl+←/→=Tracks');
    console.log('[Keyboard] V=Visual, W=Word-Sync, M=Minimal, A=Art-Only, F=Fullscreen, S=Slideshow');
}
