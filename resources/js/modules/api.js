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
    slideshowConfig,
    currentColors,
    setUpdateInterval,
    setCurrentColors,
    setWordSyncedLyrics,
    setHasWordSync,
    setWordSyncProvider,
    setWordSyncEnabled,
    setWordSyncAnchorPosition,
    setWordSyncAnchorTimestamp,
    setWordSyncIsPlaying,
    setWordSyncLatencyCompensation,
    setWordSyncSpecificLatencyCompensation,
    setProviderWordSyncOffset,
    setSongWordSyncOffset,
    setAnyProviderHasWordSync,
    setInstrumentalMarkers,
    setDebugRtt,
    setDebugRttSmoothed,
    setDebugRttJitter,
    setDebugServerPosition,
    setDebugPollTimestamp,
    setDebugLastPollTimestamp,
    setDebugPollInterval,
    setDebugSource,
    setDebugBadSamples,
    debugRttSmoothed,
    debugRttJitter,
    debugPollTimestamp,
    debugBadSamples
} from './state.js';
import { isLatencyBeingAdjusted } from './latency.js';

// RTT smoothing constant (EMA factor)
const RTT_SMOOTHING = 0.3;

// Bad sample detection thresholds
const BAD_POLL_INTERVAL_THRESHOLD = 180;  // ms - polls taking longer are suspicious
const BAD_RTT_MULTIPLIER = 2.5;           // RTT > avg * 2.5 is suspicious

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
        // Always try to parse JSON (even on non-2xx) to get server error messages
        const data = await response.json().catch(() => null);
        
        if (!response.ok) {
            // Return parsed JSON if available (contains status/message), else generic error
            if (data && (data.status || data.message || data.error)) {
                return data;
            }
            return { status: 'error', message: `HTTP error! status: ${response.status}` };
        }
        return data || {};
    } catch (error) {
        console.error(`API Error [${url}]:`, error);
        return { status: 'error', message: error.message, error: error.message };
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
        // IMPORTANT: Frontend default is now TRUE, so only apply server config if it's enabling
        // (server returning false should NOT override frontend default of true)
        const urlParams = new URLSearchParams(window.location.search);
        if (config.softAlbumArt === true && !urlParams.has('softAlbumArt')) {
            displayConfig.softAlbumArt = true;
            displayConfig.artBackground = false;
            displayConfig.sharpAlbumArt = false;
        }
        // Note: If server says false, we respect the frontend default (which is true)

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

        // Load slideshow (art cycling) settings from server AS DEFAULTS ONLY
        // User settings in localStorage take priority and are loaded later via loadSettingsFromLocalStorage()
        // Only set these if NO user settings exist (first time load)
        const hasUserSettings = localStorage.getItem('slideshowSettings') !== null;
        
        if (config.slideshowDefaultEnabled !== undefined) {
            slideshowConfig.defaultEnabled = config.slideshowDefaultEnabled;
        }
        // Only override these if user hasn't saved custom settings
        if (!hasUserSettings) {
            if (config.slideshowConfigIntervalSeconds !== undefined) {
                slideshowConfig.intervalSeconds = config.slideshowConfigIntervalSeconds;
            }
            if (config.slideshowKenBurnsEnabled !== undefined) {
                slideshowConfig.kenBurnsEnabled = config.slideshowKenBurnsEnabled;
            }
            if (config.slideshowKenBurnsIntensity !== undefined) {
                slideshowConfig.kenBurnsIntensity = config.slideshowKenBurnsIntensity;
            }
            if (config.slideshowShuffle !== undefined) {
                slideshowConfig.shuffle = config.slideshowShuffle;
            }
            if (config.slideshowTransitionDuration !== undefined) {
                slideshowConfig.transitionDuration = config.slideshowTransitionDuration;
            }
        }

        // Apply word_sync_default_enabled setting (only if URL doesn't override)
        // URL param takes priority over server config
        if (config.word_sync_default_enabled !== undefined && !urlParams.has('wordSync')) {
            setWordSyncEnabled(config.word_sync_default_enabled);
            console.log(`Word-sync default: ${config.word_sync_default_enabled}`);
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
        // RTT MEASUREMENT: Record time before request for position time correction
        const startTime = performance.now();
        
        const response = await fetch('/current-track');
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        // RTT MEASUREMENT: Record time after response
        const endTime = performance.now();
        
        // Update word-sync interpolation anchor on each successful poll
        // This enables smooth 60-144fps animation between 100ms poll intervals
        if (data && data.position !== undefined) {
            // RTT MIDPOINT ANCHORING: Correct position for network latency
            // The position was measured ~(RTT/2) ago on the server
            // By adding half the RTT, we estimate where the audio actually is NOW
            // Use SMOOTHED RTT to reduce position jitter from RTT spikes
            const rtt = endTime - startTime;  // Total round trip in ms (raw)
            const smoothedRttForCorrection = debugRttSmoothed > 0 ? debugRttSmoothed : rtt;
            const networkLatency = smoothedRttForCorrection / 2 / 1000;  // Half smoothed RTT in seconds
            const correctedPosition = data.position + networkLatency;
            
            // Update RTT tracking for debug overlay (always, even for bad samples)
            setDebugRtt(rtt);
            const smoothed = debugRttSmoothed === 0 
                ? rtt 
                : debugRttSmoothed * (1 - RTT_SMOOTHING) + rtt * RTT_SMOOTHING;
            setDebugRttSmoothed(smoothed);
            
            // Track RTT jitter (EMA of absolute deviation from average)
            const rttDeviation = Math.abs(rtt - smoothed);
            const jitter = debugRttJitter === 0
                ? rttDeviation
                : debugRttJitter * (1 - RTT_SMOOTHING) + rttDeviation * RTT_SMOOTHING;
            setDebugRttJitter(jitter);
            
            // Track poll interval (time between polls)
            let pollInterval = 0;
            if (debugPollTimestamp > 0) {
                pollInterval = endTime - debugPollTimestamp;
                setDebugPollInterval(pollInterval);
                setDebugLastPollTimestamp(debugPollTimestamp);
            }
            
            // Always update these (for debug overlay display)
            setDebugServerPosition(correctedPosition);
            setDebugPollTimestamp(endTime);
            if (data.source) {
                setDebugSource(data.source);
            }
            
            // BAD SAMPLE DETECTION: Skip anchor update if poll/RTT spiked
            // This prevents the flywheel from chasing noisy measurements
            const isRttSpike = debugRttSmoothed > 0 && rtt > debugRttSmoothed * BAD_RTT_MULTIPLIER;
            const isPollSpike = pollInterval > BAD_POLL_INTERVAL_THRESHOLD;
            
            if (isRttSpike || isPollSpike) {
                // Bad sample - don't update anchor, let flywheel coast
                setDebugBadSamples(debugBadSamples + 1);
                // Still update playing state (important for pause detection)
                setWordSyncIsPlaying(data.is_playing !== false);
            } else {
                // Good sample - update anchor normally
                setWordSyncAnchorPosition(correctedPosition);
                setWordSyncAnchorTimestamp(endTime);
                setWordSyncIsPlaying(data.is_playing !== false);
            }
        }
        
        // Update latency compensation for word-sync (source-dependent)
        if (data && data.latency_compensation !== undefined) {
            setWordSyncLatencyCompensation(data.latency_compensation);
        }
        
        // Update word-sync specific latency compensation (separate from line-sync)
        if (data && data.word_sync_latency_compensation !== undefined) {
            setWordSyncSpecificLatencyCompensation(data.word_sync_latency_compensation);
        }
        
        // Update provider-specific word-sync offset (Musixmatch/NetEase timing adjustments)
        if (data && data.provider_word_sync_offset !== undefined) {
            setProviderWordSyncOffset(data.provider_word_sync_offset);
        }
        
        // Update per-song word-sync offset (user adjustment)
        // Skip if user is actively adjusting (prevents polling from overwriting local changes)
        if (data && data.song_word_sync_offset !== undefined && !isLatencyBeingAdjusted()) {
            setSongWordSyncOffset(data.song_word_sync_offset);
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

        // Update word-sync state FIRST (before provider display)
        // This ensures the provider badge shows the correct provider
        if (data.has_word_sync && data.word_synced_lyrics) {
            setWordSyncedLyrics(data.word_synced_lyrics);
            setHasWordSync(true);
            setWordSyncProvider(data.word_sync_provider || null);
        } else {
            setWordSyncedLyrics(null);
            setHasWordSync(false);
            setWordSyncProvider(null);
        }
        
        // Update toggle availability flag (true if ANY cached provider has word-sync)
        // This allows toggle to be enabled even if current provider doesn't have word-sync
        setAnyProviderHasWordSync(data.any_provider_has_word_sync || false);
        
        // Update instrumental markers (timestamps where ♪ appears in line-sync)
        // Used for accurate gap detection during word-sync playback
        setInstrumentalMarkers(data.instrumental_markers);

        // Update provider info (now uses word-sync provider when enabled)
        if (data.provider && updateProviderDisplayFn) {
            updateProviderDisplayFn(data.provider);
        } else if (updateProviderDisplayFn) {
            updateProviderDisplayFn("None");
        }

        return data || data.lyrics;
    } catch (error) {
        console.error('Error fetching lyrics:', error);
        return null;
    }
}

// ========== ARTIST IMAGES ==========

/**
 * Fetch artist images from backend
 * 
 * @param {string} artistId - Spotify artist ID (optional - backend uses artist name from current track)
 * @param {boolean} includeMetadata - If true, return full object with metadata and preferences
 * @returns {Promise<Array<string>|Object>} URL array (default) or full response object (if includeMetadata)
 */
export async function fetchArtistImages(artistId, includeMetadata = false) {
    // Note: artistId is optional - backend gets artist name from current track metadata
    // artistId is only used for Spotify API fallback, other sources use artist name

    try {
        // Build URL with optional artist_id param
        const params = new URLSearchParams();
        if (artistId) {
            params.set('artist_id', artistId);
        }
        if (includeMetadata) {
            params.set('include_metadata', 'true');
        }
        const queryString = params.toString();
        const url = `/api/artist/images${queryString ? '?' + queryString : ''}`;
        
        const response = await fetch(url);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        
        if (includeMetadata) {
            // Return full response for control center (includes metadata and preferences)
            console.log(`Loaded ${data.images?.length || 0} artist images with metadata`);
            return data;
        }
        
        // Existing behavior: return URL array only
        if (data.images && data.images.length > 0) {
            console.log(`Loaded ${data.images.length} artist images`);
            return data.images;
        }
    } catch (error) {
        console.error('Error fetching artist images:', error);
    }
    return includeMetadata ? { images: [], metadata: [], preferences: {} } : [];
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
 * Set word-sync provider preference
 * 
 * @param {string} provider - Provider name
 * @returns {Promise<Object>} Result
 */
export async function setWordSyncProviderPreference(provider) {
    return postJson('/api/providers/word-sync-preference', { provider });
}

/**
 * Clear word-sync provider preference (reset to auto)
 * 
 * @returns {Promise<Object>} Result
 */
export async function clearWordSyncProviderPreference() {
    return apiFetch('/api/providers/word-sync-preference', { method: 'DELETE' });
}

/**
 * Delete cached lyrics for current track
 * 
 * @returns {Promise<Object>} Result
 */
export async function deleteCachedLyrics() {
    return apiFetch('/api/lyrics/delete', { method: 'DELETE' });
}

/**
 * Refetch lyrics from all providers for current track
 * 
 * @returns {Promise<Object>} Result
 */
export async function refetchLyrics() {
    return apiFetch('/api/backfill/lyrics', { method: 'POST' });
}

/**
 * Refetch album art and artist images for current track
 * 
 * @returns {Promise<Object>} Result
 */
export async function refetchArt() {
    return apiFetch('/api/backfill/art', { method: 'POST' });
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

/**
 * Seek to position in current playback
 * 
 * @param {number} positionMs - Position in milliseconds
 * @returns {Promise<Object>} Result
 */
export async function seekToPosition(positionMs) {
    return postJson('/api/playback/seek', { position_ms: positionMs });
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

/**
 * Save slideshow preferences for an artist
 * 
 * @param {string} artist - Artist name
 * @param {Array<string>} excluded - Filenames to exclude from slideshow
 * @param {boolean|null} autoEnable - Tri-state: true (always on), false (always off), null (use global)
 * @param {Array<string>} favorites - Filenames marked as favorites
 * @returns {Promise<Object>} Result
 */
export async function saveArtistSlideshowPreferences(artist, excluded, autoEnable = null, favorites = []) {
    return postJson('/api/artist/images/preferences', {
        artist,
        excluded,
        auto_enable: autoEnable,
        favorites
    });
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

