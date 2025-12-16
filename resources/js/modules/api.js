/**
 * api.js - API Client Module
 * 
 * This module contains all fetch calls to the backend.
 * Centralizes network logic and error handling.
 * 
 * Level 1 - Imports: state
 */

import {
    displayConfig,
    visualModeConfig,
    currentColors,
    setUpdateInterval,
    setCurrentColors,
    setWordSyncedLyrics,
    setHasWordSync,
    setWordSyncProvider,
    setWordSyncAnchorPosition,
    setWordSyncAnchorTimestamp,
    setWordSyncIsPlaying,
    setWordSyncLatencyCompensation,
    setWordSyncSpecificLatencyCompensation
} from './state.js';

// ========== CORE FETCH WRAPPER ==========

/**
 * Base fetch wrapper with error handling
 * 
 * @param {string} url - URL to fetch
 * @param {Object} options - Fetch options
 * @returns {Promise<Object>} JSON response or error object
 */
async function apiFetch(url, options = {}) {
    try {
        const response = await fetch(url, options);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return await response.json();
    } catch (error) {
        console.error(`API Error [${url}]:`, error);
        return { error: error.message };
    }
}

/**
 * POST JSON data to an endpoint
 * 
 * @param {string} url - URL to post to
 * @param {Object} data - Data to send
 * @returns {Promise<Object>} JSON response
 */
async function postJson(url, data) {
    return apiFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
}

// ========== CONFIG ==========

/**
 * Fetch configuration from server
 * Updates global state with config values
 */
export async function getConfig() {
    try {
        const response = await fetch('/config');
        const config = await response.json();
        setUpdateInterval(config.updateInterval);
        console.log(`Update interval set to: ${config.updateInterval}ms`);

        if (config.overlayOpacity !== undefined) {
            document.documentElement.style.setProperty('--overlay-opacity', config.overlayOpacity);
        }
        if (config.blurStrength !== undefined) {
            document.documentElement.style.setProperty('--blur-strength', config.blurStrength + 'px');
        }

        // Set soft album art mode from server config only if URL didn't explicitly set it
        const urlParams = new URLSearchParams(window.location.search);
        if (config.softAlbumArt !== undefined && !urlParams.has('softAlbumArt')) {
            displayConfig.softAlbumArt = config.softAlbumArt;
            if (displayConfig.softAlbumArt) {
                displayConfig.artBackground = false;
                displayConfig.sharpAlbumArt = false;
            }
        }

        // Set sharp album art mode from server config
        if (config.sharpAlbumArt !== undefined && !urlParams.has('sharpAlbumArt')) {
            displayConfig.sharpAlbumArt = config.sharpAlbumArt;
            if (displayConfig.sharpAlbumArt) {
                displayConfig.artBackground = false;
                displayConfig.softAlbumArt = false;
            }
        }

        // Load visual mode settings from server
        if (config.visualModeEnabled !== undefined) {
            visualModeConfig.enabled = config.visualModeEnabled;
        }
        if (config.visualModeDelaySeconds !== undefined) {
            visualModeConfig.delaySeconds = config.visualModeDelaySeconds;
        }
        if (config.visualModeAutoSharp !== undefined) {
            visualModeConfig.autoSharp = config.visualModeAutoSharp;
        }
        if (config.slideshowEnabled !== undefined) {
            visualModeConfig.slideshowEnabled = config.slideshowEnabled;
        }
        if (config.slideshowIntervalSeconds !== undefined) {
            visualModeConfig.slideshowIntervalSeconds = config.slideshowIntervalSeconds;
        }

        console.log(`Config loaded: Interval=${config.updateInterval}ms, Blur=${config.blurStrength}px, Opacity=${config.overlayOpacity}, Soft=${config.softAlbumArt}, Sharp=${config.sharpAlbumArt}`);

        return config;
    } catch (error) {
        console.error('Error fetching config:', error);
        return { error: error.message };
    }
}

// ========== TRACK & LYRICS ==========

/**
 * Fetch current track info from backend
 * 
 * @returns {Promise<Object>} Track info or error object
 */
export async function getCurrentTrack() {
    try {
        const response = await fetch('/current-track');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // Update word-sync interpolation anchor on each successful poll
        // This enables smooth 60-144fps animation between 100ms poll intervals
        if (data && data.position !== undefined) {
            setWordSyncAnchorPosition(data.position);
            setWordSyncAnchorTimestamp(performance.now());
            setWordSyncIsPlaying(data.is_playing !== false); // Default to true if not specified
        }
        
        // Update latency compensation for word-sync (source-dependent)
        if (data && data.latency_compensation !== undefined) {
            setWordSyncLatencyCompensation(data.latency_compensation);
        }
        
        // Update word-sync specific latency compensation (separate from line-sync)
        if (data && data.word_sync_latency_compensation !== undefined) {
            setWordSyncSpecificLatencyCompensation(data.word_sync_latency_compensation);
        }
        
        return data;
    } catch (error) {
        console.error('Error fetching current track:', error);
        return { error: error.message };
    }
}

/**
 * Fetch lyrics from backend
 * Also updates colors and provider info
 * 
 * @param {Function} updateBackgroundFn - Callback to update background
 * @param {Function} updateThemeColorFn - Callback to update theme color
 * @param {Function} updateProviderDisplayFn - Callback to update provider display
 * @returns {Promise<Object>} Lyrics data or null
 */
export async function getLyrics(updateBackgroundFn, updateThemeColorFn, updateProviderDisplayFn) {
    try {
        let response = await fetch('/lyrics');
        let data = await response.json();

        // Update background if colors are present
        if (data.colors) {
            if (data.colors[0] !== currentColors[0] || data.colors[1] !== currentColors[1]) {
                setCurrentColors(data.colors);
                if (updateBackgroundFn) updateBackgroundFn();
                if (updateThemeColorFn) updateThemeColorFn(data.colors[0]);
            }
        }

        // Update provider info
        if (data.provider && updateProviderDisplayFn) {
            updateProviderDisplayFn(data.provider);
        } else if (updateProviderDisplayFn) {
            updateProviderDisplayFn("None");
        }

        // Update word-sync state
        // Word-sync data is automatically used when available (ON by default)
        if (data.has_word_sync && data.word_synced_lyrics) {
            setWordSyncedLyrics(data.word_synced_lyrics);
            setHasWordSync(true);
            setWordSyncProvider(data.word_sync_provider || null);
        } else {
            setWordSyncedLyrics(null);
            setHasWordSync(false);
            setWordSyncProvider(null);
        }

        return data || data.lyrics;
    } catch (error) {
        console.error('Error fetching lyrics:', error);
        return null;
    }
}

// ========== ARTIST IMAGES ==========

/**
 * Fetch artist images from Spotify API
 * 
 * @param {string} artistId - Spotify artist ID
 * @returns {Promise<Array<string>>} Array of image URLs
 */
export async function fetchArtistImages(artistId) {
    if (!artistId) {
        console.warn('No artist ID provided for image fetch');
        return [];
    }

    try {
        const response = await fetch(`/api/artist/images?artist_id=${encodeURIComponent(artistId)}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        if (data.images && data.images.length > 0) {
            console.log(`Loaded ${data.images.length} artist images`);
            return data.images;
        }
    } catch (error) {
        console.error('Error fetching artist images:', error);
    }
    return [];
}

// ========== ALBUM ART ==========

/**
 * Fetch album art options for current track
 * 
 * @returns {Promise<Object>} Album art options or error
 */
export async function fetchAlbumArtOptions() {
    return apiFetch('/api/album-art/options');
}

/**
 * Save background style preference
 * 
 * @param {string} style - Style to save ('soft', 'sharp', 'blur', 'none')
 * @returns {Promise<Object>} Result
 */
export async function saveBackgroundStyle(style) {
    return postJson('/api/album-art/background-style', { style });
}

/**
 * Set album art preference
 * 
 * @param {string} provider - Provider name
 * @param {string} url - Image URL (optional)
 * @param {string} filename - Filename (optional)
 * @param {string} type - Type: 'album_art' or 'artist_image' (optional)
 * @returns {Promise<Object>} Result
 */
export async function setAlbumArtPreference(provider, url = null, filename = null, type = null) {
    const body = { provider };
    if (url) body.url = url;
    if (filename) body.filename = filename;
    if (type) body.type = type;
    return postJson('/api/album-art/preference', body);
}

/**
 * Clear album art preference
 * 
 * @returns {Promise<Object>} Result
 */
export async function clearAlbumArtPreference() {
    return apiFetch('/api/album-art/preference', { method: 'DELETE' });
}

// ========== PROVIDERS ==========

/**
 * Fetch available providers
 * 
 * @returns {Promise<Object>} Provider list
 */
export async function fetchProviders() {
    return apiFetch('/api/providers/available');
}

/**
 * Set provider preference
 * 
 * @param {string} provider - Provider name
 * @returns {Promise<Object>} Result with new lyrics
 */
export async function setProviderPreference(provider) {
    return postJson('/api/providers/preference', { provider });
}

/**
 * Clear provider preference (reset to auto)
 * 
 * @returns {Promise<Object>} Result
 */
export async function clearProviderPreference() {
    return apiFetch('/api/providers/preference', { method: 'DELETE' });
}

/**
 * Delete cached lyrics for current track
 * 
 * @returns {Promise<Object>} Result
 */
export async function deleteCachedLyrics() {
    return apiFetch('/api/lyrics/delete', { method: 'DELETE' });
}

// ========== INSTRUMENTAL ==========

/**
 * Toggle instrumental mark for current track
 * 
 * @param {boolean} isInstrumental - Whether to mark as instrumental
 * @returns {Promise<Object>} Result
 */
export async function toggleInstrumentalMark(isInstrumental) {
    return postJson('/api/instrumental/mark', { is_instrumental: isInstrumental });
}

// ========== PLAYBACK CONTROL ==========

/**
 * Send playback command
 * 
 * @param {string} action - 'previous', 'next', or 'play-pause'
 * @returns {Promise<Object>} Result
 */
export async function playbackCommand(action) {
    return apiFetch(`/api/playback/${action}`, { method: 'POST' });
}

// ========== QUEUE ==========

/**
 * Fetch playback queue
 * 
 * @returns {Promise<Object>} Queue data
 */
export async function fetchQueue() {
    return apiFetch('/api/playback/queue');
}

// ========== LIKE ==========

/**
 * Check if track is liked
 * 
 * @param {string} trackId - Spotify track ID
 * @returns {Promise<Object>} Liked status
 */
export async function checkLikedStatus(trackId) {
    return apiFetch(`/api/playback/liked?track_id=${trackId}`);
}

/**
 * Toggle like status for track
 * 
 * @param {string} trackId - Spotify track ID
 * @param {string} action - 'like' or 'unlike'
 * @returns {Promise<Object>} Result
 */
export async function toggleLikeStatus(trackId, action) {
    return postJson('/api/playback/liked', { track_id: trackId, action });
}

// ========== SLIDESHOW ==========

/**
 * Fetch random images for global slideshow
 * 
 * @param {number} limit - Number of images to fetch
 * @returns {Promise<Array<string>>} Array of image URLs
 */
export async function fetchRandomSlideshowImages(limit = 50) {
    try {
        const response = await fetch(`/api/slideshow/random-images?limit=${limit}`);
        if (!response.ok) throw new Error('Failed to fetch random images');

        const data = await response.json();
        if (data.images && data.images.length > 0) {
            console.log(`Loaded ${data.images.length} random images for global slideshow`);
            return data.images;
        }
    } catch (error) {
        console.error('Error fetching random slideshow images:', error);
    }
    return [];
}

// ========== AUDIO RECOGNITION API ==========

/**
 * Get audio recognition status
 * 
 * @returns {Promise<Object>} Status including active, state, mode, current_song
 */
export async function getAudioRecognitionStatus() {
    return apiFetch('/api/audio-recognition/status');
}

/**
 * Get audio recognition config with session overrides
 * 
 * @returns {Promise<Object>} Config object
 */
export async function getAudioRecognitionConfig() {
    return apiFetch('/api/audio-recognition/config');
}

/**
 * Set audio recognition session config
 * 
 * @param {Object} config - Config updates to apply
 * @returns {Promise<Object>} Updated config
 */
export async function setAudioRecognitionConfig(config) {
    return postJson('/api/audio-recognition/configure', config);
}

/**
 * Get available audio capture devices
 * 
 * @returns {Promise<Object>} Devices and recommended device
 */
export async function getAudioRecognitionDevices() {
    return apiFetch('/api/audio-recognition/devices');
}

/**
 * Start audio recognition
 * 
 * @param {boolean} manual - Whether this is a manual trigger
 * @returns {Promise<Object>} Result
 */
export async function startAudioRecognition(manual = true) {
    return postJson('/api/audio-recognition/start', { manual });
}

/**
 * Stop audio recognition
 * 
 * @returns {Promise<Object>} Result
 */
export async function stopAudioRecognition() {
    return postJson('/api/audio-recognition/stop', {});
}

