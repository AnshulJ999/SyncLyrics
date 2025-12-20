/**
 * synclyrics-bridge.js - Spicetify Bridge Extension for SyncLyrics
 * 
 * This extension provides real-time playback data from Spotify Desktop
 * to the SyncLyrics application via WebSocket for improved word-sync timing.
 * 
 * FEATURES:
 * - Real-time position updates (~100-200ms vs 4-5s from SMTC)
 * - Instant play/pause/seek detection  
 * - Audio analysis data (tempo, beats, sections)
 * - Album art color extraction
 * - Buffering state detection
 * 
 * INSTALLATION:
 *   1. Copy this file to: %APPDATA%\spicetify\Extensions\
 *   2. Run: spicetify config extensions synclyrics-bridge.js
 *   3. Run: spicetify apply
 * 
 * UNINSTALL:
 *   spicetify config extensions synclyrics-bridge.js-
 *   spicetify apply
 * 
 * @version 1.0.0
 * @author SyncLyrics
 * @see https://spicetify.app/docs/development/api-wrapper
 */

(function SyncLyricsBridge() {
    'use strict';

    // ======== CONFIGURATION ========
    const CONFIG = {
        WS_URL: 'ws://127.0.0.1:9012/ws/spicetify',  // SyncLyrics WebSocket endpoint
        WS_TEST_URL: 'ws://127.0.0.1:9099/',         // Test server (for development)
        RECONNECT_BASE_MS: 1000,                      // Initial reconnect delay
        RECONNECT_MAX_MS: 30000,                      // Max reconnect delay (30s)
        POSITION_THROTTLE_MS: 100,                    // Min time between position updates
        DEBUG: false                                  // Enable console logging
    };

    // ======== STATE ========
    let ws = null;
    let connected = false;
    let reconnectAttempts = 0;
    let lastPositionSend = 0;
    let currentTrackUri = null;
    
    // Caches (cleared on song change)
    let audioDataCache = null;
    let colorCache = null;

    // ======== UTILITIES ========
    
    /**
     * Log message to console (only if DEBUG enabled)
     */
    function log(msg, data = null) {
        if (!CONFIG.DEBUG) return;
        const prefix = '[SyncLyrics]';
        if (data !== null) {
            console.log(prefix, msg, data);
        } else {
            console.log(prefix, msg);
        }
    }

    /**
     * Calculate exponential backoff delay for reconnection
     */
    function getReconnectDelay() {
        const delay = Math.min(
            CONFIG.RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts),
            CONFIG.RECONNECT_MAX_MS
        );
        return delay;
    }

    /**
     * Safely get player data, handling null/undefined
     */
    function getPlayerData() {
        try {
            return Spicetify?.Player?.data || null;
        } catch (e) {
            return null;
        }
    }

    /**
     * Safely send message via WebSocket
     */
    function sendMessage(msg) {
        if (!connected || !ws || ws.readyState !== WebSocket.OPEN) {
            return false;
        }
        try {
            ws.send(JSON.stringify(msg));
            return true;
        } catch (e) {
            log('Send failed:', e.message);
            return false;
        }
    }

    // ======== WEBSOCKET CONNECTION ========

    /**
     * Establish WebSocket connection to SyncLyrics server
     */
    function connect() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        // Try main URL, fall back to test URL if configured
        const url = CONFIG.WS_URL;
        log('Connecting to', url);

        try {
            ws = new WebSocket(url);

            ws.onopen = () => {
                connected = true;
                reconnectAttempts = 0;
                log('Connected!');
                
                // Send initial state
                sendPositionUpdate('connected');
                
                // Fetch track data for current song
                if (getPlayerData()?.item?.uri) {
                    fetchAndSendTrackData();
                }
            };

            ws.onclose = (event) => {
                connected = false;
                ws = null;
                
                const delay = getReconnectDelay();
                reconnectAttempts++;
                log(`Disconnected (code: ${event.code}), reconnecting in ${delay}ms`);
                
                setTimeout(connect, delay);
            };

            ws.onerror = () => {
                // Error handling done in onclose
                log('Connection error');
            };

            ws.onmessage = (event) => {
                handleServerMessage(event.data);
            };

        } catch (e) {
            log('Connection failed:', e.message);
            const delay = getReconnectDelay();
            reconnectAttempts++;
            setTimeout(connect, delay);
        }
    }

    /**
     * Handle incoming messages from SyncLyrics server
     */
    function handleServerMessage(data) {
        try {
            const msg = JSON.parse(data);
            
            switch (msg.type) {
                case 'ping':
                    sendMessage({ type: 'pong', timestamp: Date.now() });
                    break;
                    
                case 'request_state':
                    sendPositionUpdate('requested');
                    break;
                    
                case 'request_track_data':
                    fetchAndSendTrackData();
                    break;
                    
                default:
                    log('Unknown message type:', msg.type);
            }
        } catch (e) {
            // Ignore invalid JSON
        }
    }

    // ======== POSITION UPDATES ========

    /**
     * Send current playback position and state to SyncLyrics
     * @param {string} trigger - What triggered this update
     */
    function sendPositionUpdate(trigger = 'progress') {
        const playerData = getPlayerData();
        const item = playerData?.item;

        const msg = {
            type: 'position',
            trigger: trigger,
            timestamp: Date.now(),
            
            // Core position data
            position_ms: Spicetify.Player.getProgress(),
            duration_ms: Spicetify.Player.getDuration(),
            is_playing: Spicetify.Player.isPlaying(),
            
            // Playback state
            is_buffering: playerData?.is_buffering || false,
            is_paused: playerData?.is_paused || false,
            
            // Track identification
            track_uri: item?.uri || null,
            
            // Internal timing (for advanced sync)
            position_as_of_timestamp: playerData?.position_as_of_timestamp,
            spotify_timestamp: playerData?.timestamp
        };

        sendMessage(msg);
    }

    /**
     * Send position update with throttling
     */
    function sendThrottledPositionUpdate() {
        const now = Date.now();
        if (now - lastPositionSend >= CONFIG.POSITION_THROTTLE_MS) {
            lastPositionSend = now;
            sendPositionUpdate('progress');
        }
    }

    // ======== TRACK DATA (Audio Analysis + Colors) ========

    /**
     * Fetch and send audio analysis and colors for current track
     */
    async function fetchAndSendTrackData() {
        const playerData = getPlayerData();
        const item = playerData?.item;
        const trackUri = item?.uri;
        
        if (!trackUri) {
            log('No track playing');
            return;
        }

        // Check if this is a new track
        if (trackUri !== currentTrackUri) {
            currentTrackUri = trackUri;
            audioDataCache = null;
            colorCache = null;
        }

        // Fetch audio analysis (if not cached)
        if (!audioDataCache) {
            audioDataCache = await fetchAudioAnalysis(trackUri);
        }

        // Fetch colors (if not cached)
        if (!colorCache) {
            colorCache = await fetchColors(trackUri, item?.album?.uri);
        }

        // Build and send track data message
        const msg = {
            type: 'track_data',
            timestamp: Date.now(),
            track_uri: trackUri,
            
            // Track metadata
            track: {
                name: item?.name || null,
                artist: item?.artists?.[0]?.name || null,
                artists: item?.artists?.map(a => a.name) || [],
                album: item?.album?.name || null,
                album_uri: item?.album?.uri || null
            },
            
            // Audio analysis
            audio_analysis: audioDataCache,
            
            // Colors
            colors: colorCache
        };

        sendMessage(msg);
        log('Track data sent for:', item?.name);
    }

    /**
     * Fetch audio analysis for a track
     * Uses Spicetify.getAudioData() which accesses internal Spotify endpoint
     * 
     * @param {string} trackUri - Spotify track URI
     * @returns {Object|null} Audio analysis data or null on error
     */
    async function fetchAudioAnalysis(trackUri) {
        try {
            // getAudioData() can be called without args for current track
            // or with specific URI
            const data = await Spicetify.getAudioData(trackUri);
            
            if (!data) {
                log('No audio data available for track');
                return null;
            }

            // Extract key information
            return {
                // Track-level analysis
                tempo: data.track?.tempo,
                key: data.track?.key,
                mode: data.track?.mode,  // 0=minor, 1=major
                time_signature: data.track?.time_signature,
                loudness: data.track?.loudness,
                duration: data.track?.duration,
                
                // Timing arrays (for beat-sync features)
                beats: data.beats || [],
                bars: data.bars || [],
                sections: data.sections || [],
                segments: data.segments || [],
                tatums: data.tatums || []
            };
        } catch (e) {
            log('Audio analysis error:', e.message);
            return null;
        }
    }

    /**
     * Fetch colors for a track or album
     * Uses Spicetify.colorExtractor() which extracts from artwork
     * 
     * @param {string} trackUri - Spotify track URI
     * @param {string} albumUri - Spotify album URI (fallback)
     * @returns {Object|null} Color palette or null on error
     */
    async function fetchColors(trackUri, albumUri) {
        // Try track URI first (per Spicetify docs)
        try {
            const colors = await Spicetify.colorExtractor(trackUri);
            if (colors && Object.keys(colors).length > 0) {
                log('Colors extracted from track');
                return colors;
            }
        } catch (e) {
            log('Track color extraction failed:', e.message);
        }

        // Fallback to album URI
        if (albumUri) {
            try {
                const colors = await Spicetify.colorExtractor(albumUri);
                if (colors && Object.keys(colors).length > 0) {
                    log('Colors extracted from album');
                    return colors;
                }
            } catch (e) {
                log('Album color extraction failed:', e.message);
            }
        }

        return null;
    }

    // ======== EVENT LISTENERS ========

    /**
     * Initialize all Spicetify Player event listeners
     */
    function initEventListeners() {
        // Position progress (throttled)
        Spicetify.Player.addEventListener('onprogress', () => {
            sendThrottledPositionUpdate();
        });

        // Play/Pause (immediate)
        Spicetify.Player.addEventListener('onplaypause', () => {
            sendPositionUpdate('playpause');
        });

        // Song change (fetch new track data)
        Spicetify.Player.addEventListener('songchange', () => {
            log('Song changed');
            
            // Clear caches
            audioDataCache = null;
            colorCache = null;
            currentTrackUri = null;
            
            // Send position update immediately
            sendPositionUpdate('songchange');
            
            // Fetch track data after short delay (let Spotify update state)
            setTimeout(() => {
                fetchAndSendTrackData();
            }, 300);
        });
    }

    // ======== INITIALIZATION ========

    /**
     * Wait for Spicetify to be fully loaded before initializing
     */
    function waitForSpicetify() {
        if (
            typeof Spicetify === 'undefined' ||
            !Spicetify.Player ||
            !Spicetify.Player.data
        ) {
            setTimeout(waitForSpicetify, 100);
            return;
        }
        
        init();
    }

    /**
     * Initialize the bridge
     */
    function init() {
        log('SyncLyrics Bridge initializing...');
        
        initEventListeners();
        connect();
        
        log('SyncLyrics Bridge ready!');
    }

    // Start initialization
    waitForSpicetify();

})();
