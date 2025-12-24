/**
 * utils.js - Pure Utility Functions
 * 
 * This module contains pure helper functions with no side effects.
 * These functions don't depend on any other modules.
 * 
 * Level 1 - No dependencies on other modules
 */

// ========== TRACK ID NORMALIZATION ==========

/**
 * Generates a consistent, source-agnostic track ID.
 * Matches backend _normalize_track_id() logic exactly.
 * Used to prevent UI flickering when switching sources (e.g. Windows -> Spotify Hybrid).
 * 
 * @param {string} artist - Artist name
 * @param {string} title - Track title
 * @returns {string} Normalized track ID in format: "artist_title"
 */
export function normalizeTrackId(artist, title) {
    // Handle null/undefined/empty strings (matches backend: if not artist: artist = "")
    if (!artist) artist = "";
    if (!title) title = "";

    // Unicode-aware alphanumeric normalization (matches Python's isalnum() exactly)
    // \p{L} = any Unicode letter, \p{N} = any Unicode number
    // This ensures accented characters like "À" are kept, matching backend behavior
    const normArtist = artist.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
    const normTitle = title.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");

    // Join with underscore (matches backend: f"{norm_artist}_{norm_title}")
    return `${normArtist}_${normTitle}`;
}

// ========== CLIPBOARD ==========

/**
 * Robust clipboard copy that works on both HTTPS and HTTP (mobile LAN)
 * 
 * @param {string} text - Text to copy to clipboard
 * @returns {Promise<void>}
 */
export async function copyToClipboard(text) {
    // Try modern API first (Works on HTTPS / Localhost)
    if (navigator.clipboard && window.isSecureContext) {
        return navigator.clipboard.writeText(text);
    }

    // Fallback for HTTP (Mobile LAN)
    return new Promise((resolve, reject) => {
        try {
            const textArea = document.createElement("textarea");
            textArea.value = text;

            // Ensure it's not visible but part of DOM
            textArea.style.position = "fixed";
            textArea.style.left = "-9999px";
            textArea.style.top = "0";
            document.body.appendChild(textArea);

            textArea.focus();
            textArea.select();

            // Mobile specific selection
            textArea.setSelectionRange(0, 99999);

            const successful = document.execCommand('copy');
            document.body.removeChild(textArea);

            if (successful) resolve();
            else reject(new Error("execCommand failed"));
        } catch (err) {
            reject(err);
        }
    });
}

// ========== ASYNC HELPERS ==========

/**
 * Sleep for a specified number of milliseconds
 * 
 * @param {number} ms - Milliseconds to sleep
 * @returns {Promise<void>}
 */
export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ========== COMPARISON HELPERS ==========

/**
 * Check if two lyrics arrays are different
 * 
 * @param {Array} oldLyrics - Previous lyrics array
 * @param {Array} newLyrics - New lyrics array
 * @returns {boolean} True if lyrics are different
 */
export function areLyricsDifferent(oldLyrics, newLyrics) {
    if (!oldLyrics || !newLyrics) return true;
    if (!Array.isArray(oldLyrics) || !Array.isArray(newLyrics)) return true;
    return JSON.stringify(oldLyrics) !== JSON.stringify(newLyrics);
}

// ========== TIME FORMATTING ==========

/**
 * Format seconds into MM:SS format
 * 
 * @param {number} seconds - Time in seconds
 * @returns {string} Formatted time string (e.g., "3:45")
 */
export function formatTime(seconds) {
    // Handle invalid inputs (NaN, undefined, null, negative) gracefully
    if (seconds === undefined || seconds === null || isNaN(seconds) || seconds < 0) {
        return '0:00';
    }
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
}
