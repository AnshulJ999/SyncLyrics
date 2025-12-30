/**
 * background.js - Background Styles and Visual Mode
 * 
 * This module handles background image/color management,
 * soft/sharp/blur modes, and visual mode state machine.
 * 
 * Level 2 - Imports: state, dom
 */

import {
    displayConfig,
    visualModeConfig,
    lastTrackInfo,
    currentColors,
    visualModeActive,
    visualModeTimer,
    visualModeDebounceTimer,
    visualModeTrackId,
    visualModeTimerId,
    manualVisualModeOverride,
    savedBackgroundState,
    manualStyleOverride,
    currentArtistImages,
    setVisualModeActive,
    setVisualModeTimer,
    setVisualModeDebounceTimer,
    setVisualModeTrackId,
    setVisualModeTimerId,
    setSavedBackgroundState,
    setManualStyleOverride
} from './state.js';

// Import from artZoom to check if user is manually browsing artist images
import { isManualArtistImageActive } from './artZoom.js';

// Forward reference for slideshow functions (will be imported dynamically when needed)
let startSlideshowFn = null;
let stopSlideshowFn = null;

/**
 * Set slideshow functions for visual mode integration
 * Called from main.js after slideshow module is loaded
 */
export function setSlideshowFunctions(startFn, stopFn) {
    startSlideshowFn = startFn;
    stopSlideshowFn = stopFn;
}

// ========== BACKGROUND MANAGEMENT ==========

/**
 * Update background based on current configuration
 */
export function updateBackground() {
    const bgLayer = document.getElementById('background-layer');
    const bgOverlay = document.getElementById('background-overlay');

    // Skip update if user is manually browsing artist images in art-only mode
    // This preserves their selected image when track changes (same artist)
    if (isManualArtistImageActive()) {
        console.log('[Background] Skipping update - user has manual artist image selected');
        return;
    }

    // In minimal mode, always keep the gradient background
    if (displayConfig.minimal) {
        bgLayer.classList.remove('visible');
        bgOverlay.classList.remove('visible');
        document.body.style.background = '';
        return;
    }

    // FIX: Removed incorrect check that conflated thumbnail visibility with background
    // The background should only be controlled by artBackground/softAlbumArt/sharpAlbumArt
    // The fallback to default gradient is already handled by the else block below (lines 110-115)
    // 
    // ORIGINAL CODE (commented for reference):
    // // If showAlbumArt is false, force default background
    // if (!displayConfig.showAlbumArt) {
    //     bgLayer.classList.remove('visible');
    //     bgOverlay.classList.remove('visible');
    //
    //     if (displayConfig.useAlbumColors && currentColors) {
    //         document.body.style.background = `linear-gradient(135deg, ${currentColors[0]} 0%, ${currentColors[1]} 100%)`;
    //     } else {
    //         document.body.style.background = `linear-gradient(135deg, #1e2030 0%, #2f354d 100%)`;
    //     }
    //
    //     document.body.classList.remove('soft-mode');
    //     document.body.classList.remove('sharp-mode');
    //     return;
    // }

    // Use background_image_url if available, otherwise album_art_url
    const backgroundUrl = lastTrackInfo?.background_image_url || lastTrackInfo?.album_art_url;

    // Check for album art backgrounds in priority order: Sharp > Soft > Blur
    if (displayConfig.sharpAlbumArt && lastTrackInfo && backgroundUrl) {
        const safeUrl = encodeURI(backgroundUrl);
        bgLayer.style.backgroundImage = `url("${safeUrl}")`;
        bgLayer.classList.add('visible');
        bgOverlay.classList.add('visible');
        document.body.style.background = 'transparent';
    }
    else if (displayConfig.softAlbumArt && lastTrackInfo && backgroundUrl) {
        const safeUrl = encodeURI(backgroundUrl);
        bgLayer.style.backgroundImage = `url("${safeUrl}")`;
        bgLayer.classList.add('visible');
        bgOverlay.classList.add('visible');
        document.body.style.background = 'transparent';
    }
    else if (displayConfig.artBackground && lastTrackInfo && backgroundUrl) {
        const safeUrl = encodeURI(backgroundUrl);
        bgLayer.style.backgroundImage = `url("${safeUrl}")`;
        bgLayer.classList.add('visible');
        bgOverlay.classList.add('visible');
        document.body.style.background = 'transparent';
    }
    else if (displayConfig.useAlbumColors && currentColors) {
        bgLayer.classList.remove('visible');
        bgOverlay.classList.remove('visible');
        bgLayer.style.backgroundImage = '';
        document.body.style.background = `linear-gradient(135deg, ${currentColors[0]} 0%, ${currentColors[1]} 100%)`;
    }
    else {
        bgLayer.classList.remove('visible');
        bgOverlay.classList.remove('visible');
        bgLayer.style.backgroundImage = '';
        document.body.style.background = `linear-gradient(135deg, #1e2030 0%, #2f354d 100%)`;
    }

    // Apply mode styling
    applySoftMode();
    applySharpMode();

    // Add subtle animation
    document.body.style.transition = 'background 1s ease-in-out';
}

// ========== MODE STYLING ==========

/**
 * Apply soft mode styling (medium blur)
 */
export function applySoftMode() {
    if (displayConfig.softAlbumArt) {
        document.body.classList.add('soft-mode');
    } else {
        document.body.classList.remove('soft-mode');
    }
}

/**
 * Apply sharp mode styling (no blur)
 */
export function applySharpMode() {
    if (displayConfig.sharpAlbumArt) {
        document.body.classList.add('sharp-mode');
    } else {
        document.body.classList.remove('sharp-mode');
    }
}

/**
 * Get current background style
 * 
 * @returns {string} Current style ('sharp', 'soft', 'blur', or 'none')
 */
export function getCurrentBackgroundStyle() {
    if (displayConfig.sharpAlbumArt) return 'sharp';
    if (displayConfig.softAlbumArt) return 'soft';
    if (displayConfig.artBackground) return 'blur';
    return 'none';
}

/**
 * Apply background style programmatically
 * 
 * @param {string} style - Style to apply ('sharp', 'soft', 'blur', or 'none')
 */
export function applyBackgroundStyle(style) {
    // Skip style changes if user is manually browsing artist images
    // This prevents soft mode from overriding sharp mode in art-only mode on track change
    if (isManualArtistImageActive()) {
        console.log('[Background] Skipping style change - user has manual artist image selected');
        return;
    }
    
    // Skip style changes if slideshow is running in art-only mode
    // Import slideshowEnabled from state would create circular dep, so check DOM state
    if (document.body.classList.contains('art-only-mode')) {
        // Check if any slideshow images exist (indicates slideshow is active)
        const bgLayer = document.getElementById('background-layer');
        const hasSlideshowImages = bgLayer && bgLayer.querySelector('.slideshow-image');
        if (hasSlideshowImages) {
            console.log('[Background] Skipping style change - slideshow active in art-only mode');
            return;
        }
    }

    // Reset all styles
    displayConfig.sharpAlbumArt = false;
    displayConfig.softAlbumArt = false;
    displayConfig.artBackground = false;

    // Apply selected style
    if (style === 'sharp') {
        displayConfig.sharpAlbumArt = true;
        applySharpMode();
    } else if (style === 'soft') {
        displayConfig.softAlbumArt = true;
        applySoftMode();
    } else if (style === 'blur') {
        displayConfig.artBackground = true;
    }

    updateBackground();
}

// ========== VISUAL MODE ==========

/**
 * Check if we should enter visual mode based on lyrics availability
 * 
 * @param {Object} data - Lyrics data with has_lyrics and is_instrumental flags
 * @param {string} trackId - Current track ID
 */
export function checkForVisualMode(data, trackId) {
    if (!visualModeConfig.enabled) return;

    // Clear timer if we're checking a DIFFERENT track
    if (visualModeTimer && visualModeTrackId !== trackId) {
        console.log(`[Visual Mode] Track changed (${visualModeTrackId} -> ${trackId}), clearing stale timer`);
        clearTimeout(visualModeTimer);
        setVisualModeTimer(null);
        setVisualModeTimerId(null);
        setVisualModeTrackId(null);
    }

    const lyricsAvailable = data && data.has_lyrics;
    const isInstrumental = (data && data.is_instrumental_manual === true) || (data && data.is_instrumental);
    const shouldEnterVisualMode = !lyricsAvailable || isInstrumental;

    if (shouldEnterVisualMode) {
        // Cancel exit debounce if status flickered
        if (visualModeDebounceTimer) {
            console.log('[Visual Mode] Status flickered, cancelling exit/reset');
            clearTimeout(visualModeDebounceTimer);
            setVisualModeDebounceTimer(null);
        }

        if (visualModeActive) return;

        if (visualModeTimer && visualModeTrackId === trackId) return;

        // Determine delay
        let delayMs;
        if (data && data.is_instrumental_manual === true) {
            delayMs = 0;
        } else if (isInstrumental) {
            delayMs = 2000;
        } else {
            delayMs = visualModeConfig.delaySeconds * 1000;
        }

        console.log(`[Visual Mode] Starting timer: ${delayMs}ms for ${trackId}`);

        const currentTimerId = Date.now();
        setVisualModeTimerId(currentTimerId);
        setVisualModeTrackId(trackId);
        const storedTrackId = trackId;

        const timer = setTimeout(async () => {
            setVisualModeTimer(null);

            if (visualModeTimerId !== currentTimerId) {
                console.log('[Visual Mode] Timer invalidated, aborting');
                return;
            }

            if (visualModeDebounceTimer) {
                console.log('[Visual Mode] Cancelling exit debounce since entering visual mode');
                clearTimeout(visualModeDebounceTimer);
                setVisualModeDebounceTimer(null);
            }

            // Verify track ID match
            let currentId = storedTrackId;
            if (lastTrackInfo) {
                if (lastTrackInfo.track_id && lastTrackInfo.track_id.trim()) {
                    currentId = lastTrackInfo.track_id.trim();
                } else {
                    const artist = (lastTrackInfo.artist || '').trim();
                    const title = (lastTrackInfo.title || '').trim();
                    if (artist && title) {
                        currentId = `${artist} - ${title}`;
                    } else if (title) {
                        currentId = title;
                    } else if (artist) {
                        currentId = artist;
                    } else {
                        currentId = 'unknown';
                    }
                }
            }

            if (currentId !== storedTrackId) {
                console.log(`[Visual Mode] Track changed during timer, aborting`);
                return;
            }

            // Final check: verify lyrics status
            const currentLyricElement = document.getElementById('current');
            const currentText = currentLyricElement ? currentLyricElement.textContent.trim() : '';
            const isErrorMessage = currentText === "Lyrics not found" || currentText === "No song playing";

            if (currentLyricElement && currentText !== '' && !isInstrumental && !isErrorMessage) {
                console.log('[Visual Mode] Lyrics appeared during timer, aborting');
                return;
            }

            console.log('[Visual Mode] Activation conditions met, entering...');
            enterVisualMode();
        }, delayMs);

        setVisualModeTimer(timer);
    } else {
        // Lyrics available - exit visual mode if needed
        if (visualModeTimer && !manualVisualModeOverride) {
            console.log('[Visual Mode] Lyrics available, cancelling entry timer');
            clearTimeout(visualModeTimer);
            setVisualModeTimer(null);
            setVisualModeTimerId(null);
            setVisualModeTrackId(null);
        }

        // Check if we're in outro (lyrics show "End") - don't auto-exit during outro
        const lyrics = data?.lyrics || data;
        const isInOutro = Array.isArray(lyrics) && lyrics.length >= 2 && lyrics[1] === 'End';

        if (visualModeActive && !manualVisualModeOverride && !isInOutro) {
            console.log('[Visual Mode] Lyrics available, exiting');
            exitVisualMode();
        }
    }
}

/**
 * Enter visual mode - hide lyrics, show background
 */
export function enterVisualMode() {
    if (visualModeActive) return;

    console.log('Entering Visual Mode');
    setVisualModeActive(true);

    // Hide lyrics container
    const lyricsContainer = document.querySelector('.lyrics-container') || document.getElementById('lyrics');
    if (lyricsContainer) {
        lyricsContainer.classList.add('visual-mode-hidden');
    }

    // Save current state before changing
    setSavedBackgroundState(getCurrentBackgroundStyle());

    // Auto-switch to sharp mode if configured
    // NOTE: This currently applies art BG even if user hasn't opted-in via URL.
    // Visual mode is for instrumentals, so showing art may be desired behavior.
    // 
    // OPTIONAL FIX: Uncomment the hasArtBgEnabled check below to only auto-sharp
    // when art BG is already enabled via URL params or settings:
    // const hasArtBgEnabled = displayConfig.artBackground || displayConfig.softAlbumArt || displayConfig.sharpAlbumArt;
    // if (visualModeConfig.autoSharp && !manualStyleOverride && !displayConfig.minimal && hasArtBgEnabled) {
    //
    // CURRENT BEHAVIOR: Always apply sharp in visual mode (ignores URL art params)
    if (visualModeConfig.autoSharp && !displayConfig.minimal) {
        if (savedBackgroundState !== 'sharp') {
            applyBackgroundStyle('sharp');
        }
    }
    
    // NOTE: Slideshow is completely independent of visual mode
    // User controls slideshow via dedicated button, 'S' key, or 4-finger gesture
}

/**
 * Exit visual mode - show lyrics again
 */
export function exitVisualMode() {
    if (!visualModeActive) return;

    console.log('Exiting Visual Mode');
    setVisualModeActive(false);

    // NOTE: Slideshow is now independent of visual mode
    // We no longer stop slideshow when exiting visual mode
    // The user controls slideshow via the dedicated button or 'S' key

    // Show lyrics container
    const lyricsContainer = document.querySelector('.lyrics-container') || document.getElementById('lyrics');
    if (lyricsContainer) {
        lyricsContainer.classList.remove('visual-mode-hidden');
    }

    // Restore previous background style
    if (savedBackgroundState) {
        applyBackgroundStyle(savedBackgroundState);
        setSavedBackgroundState(null);
    }
}

/**
 * Completely reset visual mode state
 * Used on track change to ensure clean slate
 */
export function resetVisualModeState() {
    console.log('[Visual Mode] Resetting state for track change');

    setVisualModeActive(false);
    setVisualModeTrackId(null);

    // Clear all pending timers
    if (visualModeTimer) {
        clearTimeout(visualModeTimer);
        setVisualModeTimer(null);
        setVisualModeTimerId(null);
    }
    if (visualModeDebounceTimer) {
        clearTimeout(visualModeDebounceTimer);
        setVisualModeDebounceTimer(null);
    }

    // Remove hidden class
    const lyricsContainer = document.querySelector('.lyrics-container') || document.getElementById('lyrics');
    if (lyricsContainer) {
        lyricsContainer.classList.remove('visual-mode-hidden');
    }

    // NOTE: Slideshow is NOT stopped here - it's an independent feature
    // managed by main.js based on artist change logic

    // Restore background style
    if (savedBackgroundState) {
        applyBackgroundStyle(savedBackgroundState);
        setSavedBackgroundState(null);
    }
}
