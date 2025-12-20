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
 * @version 1.1.0
 * @author SyncLyrics
 * @see https://spicetify.app/docs/development/api-wrapper
 */

(function SyncLyricsBridge() {
    'use strict';

    // ======== DUPLICATE INSTANCE PROTECTION ========
    // Prevents multiple instances when extension is reloaded
    if (window._SyncLyricsBridgeActive) {
        console.log('[SyncLyrics] Bridge already running, skipping initialization');
        return;
    }
    window._SyncLyricsBridgeActive = true;

    // ======== CONFIGURATION ========
    const CONFIG = {
        WS_URL: 'ws://127.0.0.1:9012/ws/spicetify',  // SyncLyrics WebSocket endpoint
        RECONNECT_BASE_MS: 1000,                      // Initial reconnect delay
        RECONNECT_MAX_MS: 30000,                      // Max reconnect delay (30s)
        MAX_RECONNECT_ATTEMPTS: 50,                   // Stop after 50 attempts (~15 min)
        POSITION_THROTTLE_MS: 100,                    // Min time between position updates
        AUDIO_KEEPALIVE: true,                        // Enable silent audio to prevent Chrome throttling
        DEBUG: false                                  // Enable console logging
    };

    // ======== STATE ========
    let ws = null;
    let connected = false;
    let reconnectAttempts = 0;
    let reconnectTimer = null;
    let lastPositionSend = 0;
    let currentTrackUri = null;
    
    // Caches (cleared on song change)
    let audioDataCache = null;
    let colorCache = null;

    // Named listener references (for cleanup)
    let listeners = {
        onprogress: null,
        onplaypause: null,
        songchange: null
    };
    
    // Fallback timer references (for cleanup)
    let heartbeatWorker = null;
    let messageChannel = null;
    let audioKeepAlive = null;  // Silent audio context to prevent throttling

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
     * Returns null if max attempts reached (signals to stop trying)
     */
    function getReconnectDelay() {
        if (reconnectAttempts >= CONFIG.MAX_RECONNECT_ATTEMPTS) {
            log('Max reconnection attempts reached, stopping');
            return null;
        }
        return Math.min(
            CONFIG.RECONNECT_BASE_MS * Math.pow(2, reconnectAttempts),
            CONFIG.RECONNECT_MAX_MS
        );
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

        log('Connecting to', CONFIG.WS_URL);

        try {
            ws = new WebSocket(CONFIG.WS_URL);

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
                if (delay === null) {
                    // Max attempts reached, stop trying
                    return;
                }
                
                reconnectAttempts++;
                log(`Disconnected (code: ${event.code}), reconnecting in ${delay}ms`);
                
                // Clear any existing timer before setting new one
                if (reconnectTimer) {
                    clearTimeout(reconnectTimer);
                }
                reconnectTimer = setTimeout(connect, delay);
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
            if (delay !== null) {
                reconnectAttempts++;
                reconnectTimer = setTimeout(connect, delay);
            }
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

        // Fetch colors (if not cached) - pass album URI as fallback
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
                album_uri: item?.album?.uri || null,
                album_art_url: item?.album?.images?.[0]?.url || null
            },
            
            // Audio analysis
            audio_analysis: audioDataCache,
            
            // Colors (property names match Spicetify API exactly)
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
        // Check if getAudioData function exists
        if (typeof Spicetify.getAudioData !== 'function') {
            log('getAudioData not available');
            return null;
        }

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
     * Fetch colors using GraphQL (fixes 403 Forbidden from colorExtractor)
     * 
     * Tries multiple methods:
     * 1. GraphQL API (modern, what Spotify client uses)
     * 2. Local metadata (sometimes cached)
     * 3. Legacy colorExtractor (fallback for older versions)
     * 
     * @param {string} trackUri - Spotify track URI
     * @param {string} albumUri - Spotify album URI (unused, kept for API compatibility)
     * @returns {Object|null} Color palette or null on error
     */
    async function fetchColors(trackUri, albumUri) {
        // Method 1: Try GraphQL API (Official modern way)
        try {
            const track = Spicetify.Player.data?.item;
            const imageUri = track?.album?.images?.[0]?.uri;
            
            if (imageUri && Spicetify.GraphQL?.Definitions?.fetchExtractedColors) {
                const response = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.fetchExtractedColors,
                    { uris: [imageUri] }
                );

                if (response?.data?.extractedColors?.[0]) {
                    const c = response.data.extractedColors[0];
                    log('Colors extracted via GraphQL');
                    return {
                        VIBRANT: c.colorRaw?.hex,
                        DARK_VIBRANT: c.colorDark?.hex,
                        LIGHT_VIBRANT: c.colorLight?.hex,
                        PROMINENT: c.colorRaw?.hex,
                        DESATURATED: c.colorDark?.hex,
                        VIBRANT_NON_ALARMING: c.colorLight?.hex
                    };
                }
            }
        } catch (e) {
            log('GraphQL color extraction failed:', e.message);
        }

        // Method 2: Check local metadata (sometimes cached by Spotify)
        try {
            const metadata = Spicetify.Player.data?.item?.metadata;
            if (metadata && metadata['extracted-color-dark']) {
                log('Colors found in local metadata');
                return {
                    VIBRANT: metadata['extracted-color-raw'] || null,
                    DARK_VIBRANT: metadata['extracted-color-dark'] || null,
                    LIGHT_VIBRANT: metadata['extracted-color-light'] || null,
                    PROMINENT: metadata['extracted-color-raw'] || null,
                    DESATURATED: metadata['extracted-color-dark'] || null,
                    VIBRANT_NON_ALARMING: metadata['extracted-color-light'] || null
                };
            }
        } catch (e) {
            log('Metadata fallback failed:', e.message);
        }

        // Method 3: Legacy colorExtractor (may still work for some users)
        try {
            if (typeof Spicetify.colorExtractor === 'function') {
                const colors = await Spicetify.colorExtractor(trackUri);
                if (colors && Object.keys(colors).length > 0) {
                    log('Colors extracted via legacy colorExtractor');
                    return colors;
                }
            }
        } catch (e) {
            // Silently fail - 403 is expected
        }

        log('No colors available from any method');
        return null;
    }

    // ======== EVENT LISTENERS ========

    /**
     * Initialize all Spicetify Player event listeners
     * Uses named functions for proper cleanup
     */
    function initEventListeners() {
        // Position progress (throttled)
        listeners.onprogress = function() {
            sendThrottledPositionUpdate();
        };

        // Play/Pause (immediate)
        listeners.onplaypause = function() {
            sendPositionUpdate('playpause');
        };

        // Song change - use event.data when available
        listeners.songchange = function(event) {
            log('Song changed');
            
            // Clear caches
            audioDataCache = null;
            colorCache = null;
            currentTrackUri = null;
            
            // Send position update immediately
            sendPositionUpdate('songchange');
            
            // Fetch track data
            // Use event.data if available (guaranteed ready by Spotify),
            // otherwise use short delay as fallback
            if (event?.data?.item?.uri) {
                fetchAndSendTrackData();
            } else {
                setTimeout(fetchAndSendTrackData, 300);
            }
        };

        // Register listeners
        Spicetify.Player.addEventListener('onprogress', listeners.onprogress);
        Spicetify.Player.addEventListener('onplaypause', listeners.onplaypause);
        Spicetify.Player.addEventListener('songchange', listeners.songchange);
        
        // FALLBACK 1: setInterval (throttled when minimized, but still helps)
        // Some Spicetify versions don't fire onprogress reliably
        setInterval(() => {
            if (connected && Spicetify?.Player?.isPlaying()) {
                sendThrottledPositionUpdate();
            }
        }, 500);  // Every 500ms as fallback
        
        // FALLBACK 2: Web Worker timer (runs in separate thread, less throttled)
        // When Spotify is minimized, normal timers get throttled heavily.
        // Web Workers are less affected by background throttling.
        try {
            const workerCode = `
                // Web Worker: sends tick every 500ms
                // This runs in a separate thread, less affected by throttling
                let timerId = setInterval(() => {
                    self.postMessage({ type: 'tick' });
                }, 500);
                
                // Allow stopping the worker
                self.onmessage = (e) => {
                    if (e.data === 'stop') {
                        clearInterval(timerId);
                        self.close();
                    }
                };
            `;
            const blob = new Blob([workerCode], { type: 'application/javascript' });
            const workerUrl = URL.createObjectURL(blob);
            heartbeatWorker = new Worker(workerUrl);
            
            // Clean up the URL object to free memory
            URL.revokeObjectURL(workerUrl);
            
            heartbeatWorker.onmessage = (e) => {
                if (e.data?.type === 'tick') {
                    // Worker tick received - send position if connected and playing
                    if (connected && Spicetify?.Player?.isPlaying()) {
                        sendThrottledPositionUpdate();
                    }
                }
            };
            
            heartbeatWorker.onerror = (e) => {
                log('Web Worker error:', e.message);
                heartbeatWorker = null;
            };
            
            log('Web Worker timer initialized');
        } catch (e) {
            // Web Workers might not be available in all environments
            log('Web Worker not available:', e.message);
            heartbeatWorker = null;
        }
        
        // FALLBACK 3: MessageChannel (potentially unthrottled by Chrome)
        // Uses message passing loop which may bypass timer throttling
        try {
            messageChannel = new MessageChannel();
            
            messageChannel.port1.onmessage = () => {
                // Send position if connected and playing
                if (connected && Spicetify?.Player?.isPlaying()) {
                    sendThrottledPositionUpdate();
                }
                
                // Schedule next tick (500ms)
                setTimeout(() => {
                    if (messageChannel) {
                        messageChannel.port2.postMessage(null);
                    }
                }, 500);
            };
            
            // Start the loop
            messageChannel.port2.postMessage(null);
            log('MessageChannel fallback initialized');
        } catch (e) {
            log('MessageChannel not available:', e.message);
            messageChannel = null;
        }
        
        // ANTI-THROTTLE: Audio Keep-Alive (silent audio prevents Chrome background throttling)
        // Chrome doesn't throttle tabs playing audio. We create an inaudible 1Hz oscillator
        // to trick Chrome into keeping our timers running at full speed when minimized.
        if (CONFIG.AUDIO_KEEPALIVE) {
            try {
                const ctx = new (window.AudioContext || window.webkitAudioContext)();
                
                // Create 1Hz oscillator (below human hearing range of ~20Hz)
                const oscillator = ctx.createOscillator();
                oscillator.frequency.value = 1;
                oscillator.type = 'sine';
                
                // Create gain node with very low volume (practically silent)
                const gain = ctx.createGain();
                gain.gain.value = 0.001;  // 0.1% volume - inaudible
                
                // Connect: oscillator -> gain -> speakers
                oscillator.connect(gain);
                gain.connect(ctx.destination);
                
                // Start the oscillator
                oscillator.start();
                
                // Store references for cleanup
                audioKeepAlive = { ctx, oscillator, gain };
                log('Audio keep-alive initialized (anti-throttle)');
            } catch (e) {
                log('Audio keep-alive failed:', e.message);
                audioKeepAlive = null;
            }
        }
    }

    /**
     * Cleanup function - remove event listeners and close connection
     * Called on page unload to prevent memory leaks
     */
    function cleanup() {
        log('Cleaning up...');
        
        // Remove event listeners
        if (Spicetify?.Player?.removeEventListener) {
            if (listeners.onprogress) {
                Spicetify.Player.removeEventListener('onprogress', listeners.onprogress);
            }
            if (listeners.onplaypause) {
                Spicetify.Player.removeEventListener('onplaypause', listeners.onplaypause);
            }
            if (listeners.songchange) {
                Spicetify.Player.removeEventListener('songchange', listeners.songchange);
            }
        }
        
        // Close WebSocket
        if (ws) {
            ws.close(1000, 'Extension cleanup');
            ws = null;
        }
        
        // Clear reconnect timer
        if (reconnectTimer) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
        
        // Reset state
        connected = false;
        window._SyncLyricsBridgeActive = false;
        
        // Terminate Web Worker
        if (heartbeatWorker) {
            heartbeatWorker.postMessage('stop');
            heartbeatWorker.terminate();
            heartbeatWorker = null;
            log('Web Worker terminated');
        }
        
        // Close MessageChannel
        if (messageChannel) {
            messageChannel.port1.close();
            messageChannel.port2.close();
            messageChannel = null;
            log('MessageChannel closed');
        }
        
        // Stop audio keep-alive
        if (audioKeepAlive) {
            try {
                audioKeepAlive.oscillator.stop();
                audioKeepAlive.ctx.close();
            } catch (e) {
                // Ignore errors during cleanup
            }
            audioKeepAlive = null;
            log('Audio keep-alive stopped');
        }
    }

    // ======== INITIALIZATION ========

    /**
     * Wait for Spicetify to be fully loaded before initializing
     * Checks for both Player and Platform per official docs
     */
    function waitForSpicetify() {
        if (
            typeof Spicetify === 'undefined' ||
            !Spicetify.Platform ||  // Required per official docs
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
        
        // Register cleanup on page unload
        window.addEventListener('beforeunload', cleanup);
        
        initEventListeners();
        connect();
        
        log('SyncLyrics Bridge ready!');
    }

    // Start initialization
    waitForSpicetify();

})();
