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
        // Multiple SyncLyrics server endpoints (connects to all in parallel)
        // Add your server IPs here - extension broadcasts to all connected servers
        WS_URLS: [
            'ws://127.0.0.1:9012/ws/spicetify',      // Local machine
            'ws://192.168.1.99:9012/ws/spicetify', // HASS server (uncomment to enable)
            // 'ws://192.168.1.3:9012/ws/spicetify',  // Add more as needed
        ],
        RECONNECT_BASE_MS: 1000,                      // Initial reconnect delay
        RECONNECT_MAX_MS: 30000,                      // Max reconnect delay (30s)
        // No max attempts - keeps trying forever (caps at RECONNECT_MAX_MS delay)
        POSITION_THROTTLE_MS: 100,                    // Min time between position updates
        AUDIO_KEEPALIVE: true,                        // Enable silent audio to prevent Chrome throttling
        DEBUG: false                                  // Enable console logging
    };

    // ======== STATE ========
    
    // Multi-server connection state: Map<url, ConnectionState>
    // Each server has independent connection, reconnection, and state
    let connections = new Map();
    
    // Shared state (not per-connection)
    let lastPositionSend = 0;
    let currentTrackUri = null;
    let lastReportedPosition = 0;  // For seek detection while paused
    
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
    let fallbackIntervalId = null;  // setInterval ID for cleanup

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
     * Extract Spotify ID from URI (spotify:track:xxx -> xxx, spotify:artist:yyy -> yyy)
     * @param {string} uri - Spotify URI
     * @returns {string|null} - Extracted ID or null
     */
    function extractSpotifyId(uri) {
        if (!uri || typeof uri !== 'string') return null;
        const parts = uri.split(':');
        return parts.length >= 3 ? parts[2] : null;
    }

    /**
     * Calculate exponential backoff delay for reconnection
     * Always returns a delay (never gives up, caps at RECONNECT_MAX_MS)
     * @param {number} attempts - Current attempt count for this connection
     */
    function getReconnectDelay(attempts) {
        return Math.min(
            CONFIG.RECONNECT_BASE_MS * Math.pow(2, attempts),
            CONFIG.RECONNECT_MAX_MS
        );
    }
    
    /**
     * Check if ANY server is connected (for throttled updates)
     */
    function isAnyConnected() {
        for (const conn of connections.values()) {
            if (conn.connected) return true;
        }
        return false;
    }

    /**
     * Safely get player data, handling null/undefined
     */
    function getPlayerData() {
        try {
            return Spicetify?.Player?.data || null;
        } catch {
            return null;
        }
    }

    /**
     * Broadcast message to all connected servers
     * @returns {boolean} True if sent to at least one server
     */
    function broadcastMessage(msg) {
        let sent = false;
        const payload = JSON.stringify(msg);
        
        connections.forEach((conn, url) => {
            if (conn.connected && conn.ws && conn.ws.readyState === WebSocket.OPEN) {
                try {
                    conn.ws.send(payload);
                    sent = true;
                } catch (e) {
                    log('Send failed to', url);
                }
            }
        });
        
        return sent;
    }
    
    /**
     * Send message to a specific WebSocket connection
     */
    function sendMessageTo(ws, msg) {
        if (!ws || ws.readyState !== WebSocket.OPEN) return false;
        try {
            ws.send(JSON.stringify(msg));
            return true;
        } catch (e) {
            return false;
        }
    }

    // ======== WEBSOCKET CONNECTION ========

    /**
     * Initialize connections to all configured servers
     */
    function connectAll() {
        CONFIG.WS_URLS.forEach(url => {
            // Initialize connection state if not exists
            if (!connections.has(url)) {
                connections.set(url, {
                    ws: null,
                    connected: false,
                    reconnectAttempts: 0,
                    reconnectTimer: null
                });
            }
            connectTo(url);
        });
    }

    /**
     * Establish WebSocket connection to a specific SyncLyrics server
     * @param {string} url - WebSocket URL to connect to
     */
    function connectTo(url) {
        const conn = connections.get(url);
        if (!conn) return;
        
        if (conn.ws && (conn.ws.readyState === WebSocket.OPEN || conn.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        log('Connecting to', url);

        try {
            conn.ws = new WebSocket(url);

            conn.ws.onopen = () => {
                conn.connected = true;
                conn.reconnectAttempts = 0;
                log('Connected to', url);
                
                // Send initial state to THIS server
                sendPositionUpdateTo(conn.ws, 'connected');
                
                // Send track data to THIS server
                if (getPlayerData()?.item?.uri) {
                    sendTrackDataTo(conn.ws);
                }
            };

            conn.ws.onclose = (event) => {
                conn.connected = false;
                conn.ws = null;
                
                const delay = getReconnectDelay(conn.reconnectAttempts);
                conn.reconnectAttempts++;
                log(`Disconnected from ${url} (code: ${event.code}), reconnecting in ${delay}ms`);
                
                // Clear any existing timer before setting new one
                if (conn.reconnectTimer) {
                    clearTimeout(conn.reconnectTimer);
                }
                conn.reconnectTimer = setTimeout(() => connectTo(url), delay);
            };

            conn.ws.onerror = () => {
                log('Connection error:', url);
            };

            conn.ws.onmessage = (event) => {
                handleServerMessage(event.data, conn.ws);
            };

        } catch (e) {
            log('Connection failed:', url, e.message);
            const delay = getReconnectDelay(conn.reconnectAttempts);
            conn.reconnectAttempts++;
            conn.reconnectTimer = setTimeout(() => connectTo(url), delay);
        }
    }

    /**
     * Handle incoming messages from SyncLyrics server
     * @param {string} data - Message data
     * @param {WebSocket} ws - The WebSocket that received the message
     */
    function handleServerMessage(data, ws) {
        try {
            const msg = JSON.parse(data);
            
            switch (msg.type) {
                case 'ping':
                    sendMessageTo(ws, { type: 'pong', timestamp: Date.now() });
                    break;
                    
                case 'request_state':
                    sendPositionUpdateTo(ws, 'requested');
                    break;
                    
                case 'request_track_data':
                    sendTrackDataTo(ws);
                    break;
                    
                default:
                    log('Unknown message type:', msg.type);
            }
        } catch {
            // Ignore invalid JSON
        }
    }

    // ======== POSITION UPDATES ========

    /**
     * Build position update message
     * @param {string} trigger - What triggered this update
     * @returns {Object} Position message object
     */
    function buildPositionMessage(trigger = 'progress') {
        const playerData = getPlayerData();
        const item = playerData?.item;

        return {
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
    }
    
    /**
     * Broadcast position update to all connected servers
     * @param {string} trigger - What triggered this update
     */
    function sendPositionUpdate(trigger = 'progress') {
        broadcastMessage(buildPositionMessage(trigger));
    }
    
    /**
     * Send position update to a specific server
     * @param {WebSocket} ws - Target WebSocket connection
     * @param {string} trigger - What triggered this update
     */
    function sendPositionUpdateTo(ws, trigger = 'progress') {
        sendMessageTo(ws, buildPositionMessage(trigger));
    }

    /**
     * Send position update with throttling (to all servers)
     */
    function sendThrottledPositionUpdate() {
        const now = Date.now();
        if (now - lastPositionSend >= CONFIG.POSITION_THROTTLE_MS) {
            lastPositionSend = now;
            lastReportedPosition = Spicetify.Player.getProgress();  // Track for seek detection
            sendPositionUpdate('progress');
        }
    }
    
    /**
     * Check if position jumped significantly (seek detection while paused)
     * Returns true if position changed by more than 1 second since last report
     */
    function hasPositionJumped() {
        const currentPos = Spicetify.Player.getProgress();
        return Math.abs(currentPos - lastReportedPosition) > 1000;  // >1 second = seek
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

        // Build and send track data message with ALL available metadata
        const metadata = item?.metadata || {};
        
        const msg = {
            type: 'track_data',
            timestamp: Date.now(),
            track_uri: trackUri,
            
            // ======== TRACK METADATA ========
            track: {
                // Core identification
                name: item?.name || null,
                artist: item?.artists?.[0]?.name || null,
                artists: item?.artists?.map(a => a.name) || [],
                album: item?.album?.name || null,
                album_uri: item?.album?.uri || null,
                album_art_url: item?.album?.images?.[0]?.url || null,
                artist_uri: item?.artists?.[0]?.uri || null,
                artist_id: extractSpotifyId(item?.artists?.[0]?.uri),
                url: trackUri ? `https://open.spotify.com/track/${extractSpotifyId(trackUri)}` : null,
                
                // Track info
                duration_ms: item?.duration?.milliseconds || item?.duration_ms || Spicetify.Player.getDuration() || null,
                popularity: item?.popularity ?? null,
                is_explicit: (item?.explicit ?? (metadata?.is_explicit === 'true')) || null,
                is_local: item?.is_local ?? (trackUri?.startsWith('spotify:local:') || false),
                has_lyrics: (metadata?.has_lyrics === 'true') || null,
                isrc: item?.external_ids?.isrc || metadata?.isrc || null,
                
                // Album info
                album_type: item?.album?.album_type || metadata?.album_type || null,
                release_date: item?.album?.release_date || metadata?.release_date || null,
                disc_number: item?.disc_number ?? (parseInt(metadata?.album_disc_number, 10) || null),
                track_number: item?.track_number ?? (parseInt(metadata?.album_track_number, 10) || null),
                total_tracks: item?.album?.total_tracks ?? (parseInt(metadata?.album_track_count, 10) || null),
                total_discs: parseInt(metadata?.album_disc_count, 10) || null,
                
                // Linked track (for market-specific versions)
                linked_from_uri: item?.linked_from?.uri || null
            },
            
            // ======== CANVAS (Animated Video Loops) ========
            canvas: {
                url: metadata?.['canvas.url'] || null,
                type: metadata?.['canvas.type'] || null,  // VIDEO or IMAGE
                file_id: metadata?.['canvas.fileId'] || null,
                entity_uri: metadata?.['canvas.entityUri'] || null,
                artist_name: metadata?.['canvas.artist.name'] || null,
                artist_uri: metadata?.['canvas.artist.uri'] || null,
                explicit: metadata?.['canvas.explicit'] === 'true',
                uploaded_by: metadata?.['canvas.uploadedBy'] || null
            },
            
            // ======== PLAYER STATE ========
            player_state: {
                // Playback
                shuffle: Spicetify.Player.getShuffle?.() ?? playerData?.options?.shuffling_context ?? null,
                repeat: Spicetify.Player.getRepeat?.() ?? null,  // 0=off, 1=context, 2=track
                repeat_context: playerData?.options?.repeating_context ?? null,
                repeat_track: playerData?.options?.repeating_track ?? null,
                
                // Volume
                volume: Spicetify.Player.getVolume?.() ?? null,  // 0.0 - 1.0
                is_muted: Spicetify.Player.getMute?.() ?? null,
                
                // Track status
                is_liked: Spicetify.Player.getHeart?.() ?? null,
                progress_percent: Spicetify.Player.getProgressPercent?.() ?? null,
                
                // Session
                playback_id: playerData?.playback_id || null,
                session_id: playerData?.session_id || null,
                playback_speed: playerData?.playback_speed ?? null
            },
            
            // ======== PLAYBACK QUALITY ========
            playback_quality: playerData?.playback_quality ? {
                bitrate_level: playerData.playback_quality.bitrate_level || null,
                hifi_status: playerData.playback_quality.hifi_status || null,
                strategy: playerData.playback_quality.strategy || null,
                target_bitrate_level: playerData.playback_quality.target_bitrate_level || null
            } : null,
            
            // ======== CONTEXT (Playlist/Album/Radio) ========
            context: {
                uri: playerData?.context?.uri || playerData?.context_uri || null,
                url: playerData?.context?.url || playerData?.context_url || null,
                type: playerData?.context?.metadata?.context_description || null,
                
                // Queue position
                track_index: playerData?.index?.track ?? null,
                page_index: playerData?.index?.page ?? null,
                
                // Play origin (how playback started)
                origin_feature: playerData?.play_origin?.feature_identifier || null,
                origin_view: playerData?.play_origin?.view_uri || null,
                origin_referrer: playerData?.play_origin?.referrer_identifier || null
            },
            
            // ======== COLLECTION STATUS ========
            collection: {
                can_add: metadata?.['collection.can_add'] === 'true',
                can_ban: metadata?.['collection.can_ban'] === 'true',
                in_collection: metadata?.['collection.in_collection'] === 'true',
                is_banned: metadata?.['collection.is_banned'] === 'true'
            },
            
            // ======== RAW METADATA (for future use) ========
            // Forward full metadata objects for any fields we might have missed
            raw_metadata: metadata,
            context_metadata: playerData?.context?.metadata || null,
            page_metadata: playerData?.page_metadata || null,
            
            // ======== AUDIO ANALYSIS ========
            audio_analysis: audioDataCache,
            
            // ======== COLORS ========
            colors: colorCache
        };

        broadcastMessage(msg);
        log('Track data broadcast for:', item?.name);
    }
    
    /**
     * Send current track data to a specific server
     * Uses cached data if available (doesn't re-fetch)
     * @param {WebSocket} ws - Target WebSocket connection
     */
    async function sendTrackDataTo(ws) {
        const playerData = getPlayerData();
        const item = playerData?.item;
        const trackUri = item?.uri;
        
        if (!trackUri) return;
        
        // Build message with ALL available metadata
        const metadata = item?.metadata || {};
        
        const msg = {
            type: 'track_data',
            timestamp: Date.now(),
            track_uri: trackUri,
            
            // ======== TRACK METADATA ========
            track: {
                // Core identification
                name: item?.name || null,
                artist: item?.artists?.[0]?.name || null,
                artists: item?.artists?.map(a => a.name) || [],
                album: item?.album?.name || null,
                album_uri: item?.album?.uri || null,
                album_art_url: item?.album?.images?.[0]?.url || null,
                artist_uri: item?.artists?.[0]?.uri || null,
                artist_id: extractSpotifyId(item?.artists?.[0]?.uri),
                url: trackUri ? `https://open.spotify.com/track/${extractSpotifyId(trackUri)}` : null,
                
                // Track info
                duration_ms: item?.duration?.milliseconds || item?.duration_ms || Spicetify.Player.getDuration() || null,
                popularity: item?.popularity ?? null,
                is_explicit: (item?.explicit ?? (metadata?.is_explicit === 'true')) || null,
                is_local: item?.is_local ?? (trackUri?.startsWith('spotify:local:') || false),
                has_lyrics: (metadata?.has_lyrics === 'true') || null,
                isrc: item?.external_ids?.isrc || metadata?.isrc || null,
                
                // Album info
                album_type: item?.album?.album_type || metadata?.album_type || null,
                release_date: item?.album?.release_date || metadata?.release_date || null,
                disc_number: item?.disc_number ?? (parseInt(metadata?.album_disc_number, 10) || null),
                track_number: item?.track_number ?? (parseInt(metadata?.album_track_number, 10) || null),
                total_tracks: item?.album?.total_tracks ?? (parseInt(metadata?.album_track_count, 10) || null),
                total_discs: parseInt(metadata?.album_disc_count, 10) || null,
                
                // Linked track (for market-specific versions)
                linked_from_uri: item?.linked_from?.uri || null
            },
            
            // ======== CANVAS (Animated Video Loops) ========
            canvas: {
                url: metadata?.['canvas.url'] || null,
                type: metadata?.['canvas.type'] || null,
                file_id: metadata?.['canvas.fileId'] || null,
                entity_uri: metadata?.['canvas.entityUri'] || null,
                artist_name: metadata?.['canvas.artist.name'] || null,
                artist_uri: metadata?.['canvas.artist.uri'] || null,
                explicit: metadata?.['canvas.explicit'] === 'true',
                uploaded_by: metadata?.['canvas.uploadedBy'] || null
            },
            
            // ======== PLAYER STATE ========
            player_state: {
                shuffle: Spicetify.Player.getShuffle?.() ?? playerData?.options?.shuffling_context ?? null,
                repeat: Spicetify.Player.getRepeat?.() ?? null,
                repeat_context: playerData?.options?.repeating_context ?? null,
                repeat_track: playerData?.options?.repeating_track ?? null,
                volume: Spicetify.Player.getVolume?.() ?? null,
                is_muted: Spicetify.Player.getMute?.() ?? null,
                is_liked: Spicetify.Player.getHeart?.() ?? null,
                progress_percent: Spicetify.Player.getProgressPercent?.() ?? null,
                playback_id: playerData?.playback_id || null,
                session_id: playerData?.session_id || null,
                playback_speed: playerData?.playback_speed ?? null
            },
            
            // ======== PLAYBACK QUALITY ========
            playback_quality: playerData?.playback_quality ? {
                bitrate_level: playerData.playback_quality.bitrate_level || null,
                hifi_status: playerData.playback_quality.hifi_status || null,
                strategy: playerData.playback_quality.strategy || null,
                target_bitrate_level: playerData.playback_quality.target_bitrate_level || null
            } : null,
            
            // ======== CONTEXT (Playlist/Album/Radio) ========
            context: {
                uri: playerData?.context?.uri || playerData?.context_uri || null,
                url: playerData?.context?.url || playerData?.context_url || null,
                type: playerData?.context?.metadata?.context_description || null,
                track_index: playerData?.index?.track ?? null,
                page_index: playerData?.index?.page ?? null,
                origin_feature: playerData?.play_origin?.feature_identifier || null,
                origin_view: playerData?.play_origin?.view_uri || null,
                origin_referrer: playerData?.play_origin?.referrer_identifier || null
            },
            
            // ======== COLLECTION STATUS ========
            collection: {
                can_add: metadata?.['collection.can_add'] === 'true',
                can_ban: metadata?.['collection.can_ban'] === 'true',
                in_collection: metadata?.['collection.in_collection'] === 'true',
                is_banned: metadata?.['collection.is_banned'] === 'true'
            },
            
            // ======== RAW METADATA ========
            raw_metadata: metadata,
            context_metadata: playerData?.context?.metadata || null,
            page_metadata: playerData?.page_metadata || null,
            
            // ======== AUDIO & COLORS ========
            audio_analysis: audioDataCache,
            colors: colorCache
        };
        
        sendMessageTo(ws, msg);
        log('Track data sent to specific server for:', item?.name);
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
        } catch {
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
        // Also detects seeks while paused (position jump > 1 second)
        fallbackIntervalId = setInterval(() => {
            if (isAnyConnected() && (Spicetify?.Player?.isPlaying() || hasPositionJumped())) {
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
                    // Worker tick received - send position if connected and playing (or seek detected)
                    if (isAnyConnected() && (Spicetify?.Player?.isPlaying() || hasPositionJumped())) {
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
                // Send position if connected and playing (or seek detected while paused)
                if (isAnyConnected() && (Spicetify?.Player?.isPlaying() || hasPositionJumped())) {
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
                gain.gain.value = 0.0001;  // 0.01% volume - inaudible
                
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
        
        // Close all WebSocket connections
        connections.forEach((conn, _url) => {
            if (conn.ws) {
                conn.ws.close(1000, 'Extension cleanup');
            }
            if (conn.reconnectTimer) {
                clearTimeout(conn.reconnectTimer);
            }
        });
        connections.clear();
        log('All WebSocket connections closed');
        
        // Clear fallback interval
        if (fallbackIntervalId) {
            clearInterval(fallbackIntervalId);
            fallbackIntervalId = null;
            log('Fallback interval cleared');
        }
        
        // Reset state
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
            } catch {
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
        log('Configured servers:', CONFIG.WS_URLS);
        
        // Register cleanup on page unload
        window.addEventListener('beforeunload', cleanup);
        
        initEventListeners();
        connectAll();  // Connect to all configured servers
        
        log('SyncLyrics Bridge ready!');
    }

    // Start initialization
    waitForSpicetify();

})();
