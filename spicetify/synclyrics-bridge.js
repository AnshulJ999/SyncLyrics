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
        DEBUG: true                                  // Enable console logging
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
    let artistVisualsCache = null;      // Cache for artist images from GraphQL
    let artistVisualsCacheUri = null;   // Track which artist the cache is for

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
                
                // ======== PLAYBACK CONTROLS ========
                // All controls wrapped in try/catch for robust error handling
                
                case 'play':
                    try {
                        Spicetify.Player.play();
                        sendMessageTo(ws, { type: 'control_ack', command: 'play', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'play', success: false, error: e.message });
                    }
                    break;
                    
                case 'pause':
                    try {
                        Spicetify.Player.pause();
                        sendMessageTo(ws, { type: 'control_ack', command: 'pause', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'pause', success: false, error: e.message });
                    }
                    break;
                    
                case 'toggle_play':
                    try {
                        Spicetify.Player.togglePlay();
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_play', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_play', success: false, error: e.message });
                    }
                    break;
                    
                case 'skip_next':
                    try {
                        Spicetify.Player.next();
                        sendMessageTo(ws, { type: 'control_ack', command: 'skip_next', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'skip_next', success: false, error: e.message });
                    }
                    break;
                    
                case 'skip_prev':
                    try {
                        Spicetify.Player.back();
                        sendMessageTo(ws, { type: 'control_ack', command: 'skip_prev', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'skip_prev', success: false, error: e.message });
                    }
                    break;
                    
                case 'seek':
                    try {
                        if (typeof msg.position_ms === 'number') {
                            Spicetify.Player.seek(msg.position_ms);
                            sendMessageTo(ws, { type: 'control_ack', command: 'seek', success: true, position_ms: msg.position_ms });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'seek', success: false, error: 'position_ms required' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'seek', success: false, error: e.message });
                    }
                    break;
                
                case 'seek_by':
                    // Relative seek: positive = forward, negative = backward
                    try {
                        if (typeof msg.offset_ms === 'number') {
                            const currentPos = Spicetify.Player.getProgress();
                            const newPos = Math.max(0, currentPos + msg.offset_ms);
                            Spicetify.Player.seek(newPos);
                            sendMessageTo(ws, { type: 'control_ack', command: 'seek_by', success: true, offset_ms: msg.offset_ms, new_position: newPos });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'seek_by', success: false, error: 'offset_ms required' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'seek_by', success: false, error: e.message });
                    }
                    break;
                
                case 'play_uri':
                    // Play a specific track by URI
                    try {
                        if (msg.uri) {
                            Spicetify.Player.playUri(msg.uri, msg.context || {}, msg.options || {});
                            sendMessageTo(ws, { type: 'control_ack', command: 'play_uri', success: true, uri: msg.uri });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'play_uri', success: false, error: 'uri required' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'play_uri', success: false, error: e.message });
                    }
                    break;
                    
                case 'set_volume':
                    try {
                        if (typeof msg.volume === 'number') {
                            Spicetify.Player.setVolume(Math.max(0, Math.min(1, msg.volume)));
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_volume', success: true, volume: msg.volume });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_volume', success: false, error: 'volume required (0-1)' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'set_volume', success: false, error: e.message });
                    }
                    break;
                
                case 'increase_volume':
                    try {
                        Spicetify.Player.increaseVolume();
                        sendMessageTo(ws, { type: 'control_ack', command: 'increase_volume', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'increase_volume', success: false, error: e.message });
                    }
                    break;
                
                case 'decrease_volume':
                    try {
                        Spicetify.Player.decreaseVolume();
                        sendMessageTo(ws, { type: 'control_ack', command: 'decrease_volume', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'decrease_volume', success: false, error: e.message });
                    }
                    break;
                    
                case 'set_mute':
                    try {
                        if (typeof msg.muted === 'boolean') {
                            Spicetify.Player.setMute(msg.muted);
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_mute', success: true, muted: msg.muted });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_mute', success: false, error: 'muted required (boolean)' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'set_mute', success: false, error: e.message });
                    }
                    break;
                
                case 'toggle_mute':
                    try {
                        Spicetify.Player.toggleMute();
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_mute', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_mute', success: false, error: e.message });
                    }
                    break;
                    
                case 'set_shuffle':
                    try {
                        if (typeof msg.shuffle === 'boolean') {
                            Spicetify.Player.setShuffle(msg.shuffle);
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_shuffle', success: true, shuffle: msg.shuffle });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_shuffle', success: false, error: 'shuffle required (boolean)' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'set_shuffle', success: false, error: e.message });
                    }
                    break;
                
                case 'toggle_shuffle':
                    try {
                        Spicetify.Player.toggleShuffle();
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_shuffle', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_shuffle', success: false, error: e.message });
                    }
                    break;
                    
                case 'set_repeat':
                    // 0 = off, 1 = context (playlist/album), 2 = track
                    try {
                        if (typeof msg.repeat === 'number' && msg.repeat >= 0 && msg.repeat <= 2) {
                            Spicetify.Player.setRepeat(msg.repeat);
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_repeat', success: true, repeat: msg.repeat });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_repeat', success: false, error: 'repeat required (0/1/2)' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'set_repeat', success: false, error: e.message });
                    }
                    break;
                
                case 'toggle_repeat':
                    // Cycles through: off -> context -> track -> off
                    try {
                        Spicetify.Player.toggleRepeat();
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_repeat', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_repeat', success: false, error: e.message });
                    }
                    break;
                    
                case 'set_heart':
                    // Explicitly set like status (not toggle)
                    try {
                        if (typeof msg.liked === 'boolean') {
                            Spicetify.Player.setHeart(msg.liked);
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_heart', success: true, liked: msg.liked });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'set_heart', success: false, error: 'liked required (boolean)' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'set_heart', success: false, error: e.message });
                    }
                    break;
                
                case 'toggle_heart':
                    try {
                        Spicetify.Player.toggleHeart();
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_heart', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'toggle_heart', success: false, error: e.message });
                    }
                    break;
                
                case 'add_to_queue':
                    // Add track(s) to queue
                    try {
                        if (msg.uri || msg.uris) {
                            const uris = msg.uris || [msg.uri];
                            const tracks = uris.map(uri => ({ uri }));
                            Spicetify.Platform.PlayerAPI.addToQueue(tracks);
                            sendMessageTo(ws, { type: 'control_ack', command: 'add_to_queue', success: true, uris });
                        } else {
                            sendMessageTo(ws, { type: 'control_ack', command: 'add_to_queue', success: false, error: 'uri or uris required' });
                        }
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'add_to_queue', success: false, error: e.message });
                    }
                    break;
                
                case 'clear_queue':
                    try {
                        Spicetify.Platform.PlayerAPI.clearQueue();
                        sendMessageTo(ws, { type: 'control_ack', command: 'clear_queue', success: true });
                    } catch (e) {
                        sendMessageTo(ws, { type: 'control_ack', command: 'clear_queue', success: false, error: e.message });
                    }
                    break;
                
                case 'get_queue':
                    // Return the real queue including autoplay tracks
                    // Spicetify.Queue.nextTracks includes tracks the Web API doesn't expose
                    // log('get_queue request received, processing...');
                    try {
                        const queue = Spicetify.Queue;
                        const nextTracks = queue?.nextTracks || [];
                        
                        // Map to simplified format matching Spotify Web API structure
                        // IMPORTANT: Queue items have structure { contextTrack: { uri, metadata }, provider }
                        const queueItems = nextTracks.map(item => {
                            // Access the nested contextTrack structure
                            const track = item.contextTrack || {};
                            const metadata = track.metadata || {};
                            
                            // Extract album art URL (convert spotify:image: if needed)
                            let albumArtUrl = metadata.image_xlarge_url || 
                                              metadata.image_large_url || 
                                              metadata.image_url || null;
                            if (albumArtUrl && albumArtUrl.startsWith('spotify:image:')) {
                                const imageId = albumArtUrl.replace('spotify:image:', '');
                                albumArtUrl = `https://i.scdn.co/image/${imageId}`;
                            }
                            
                            // Build artist array (may have multiple artists)
                            const artists = [];
                            if (metadata.artist_name) {
                                artists.push({ name: metadata.artist_name });
                            }
                            
                            // Get album images in Web API format
                            const albumImages = albumArtUrl ? [{ url: albumArtUrl }] : [];
                            
                            return {
                                // Match Spotify Web API track structure for frontend compatibility
                                id: track.uri ? extractSpotifyId(track.uri) : null,
                                uri: track.uri,
                                name: metadata.title || null,
                                artists: artists,
                                album: {
                                    name: metadata.album_title || null,
                                    images: albumImages
                                },
                                duration_ms: parseInt(metadata.duration, 10) || null,
                                // Extra Spicetify-only fields
                                provider: item.provider || null,  // "context", "autoplay", "queue"
                                is_autoplay: item.provider === 'autoplay'
                            };
                        });
                        
                        // Get currently playing track info (same structure as queue items)
                        const currentItem_raw = queue?.track;
                        let currentItem = null;
                        if (currentItem_raw) {
                            const currentTrack = currentItem_raw.contextTrack || {};
                            const currentMeta = currentTrack.metadata || {};
                            
                            let currentArtUrl = currentMeta.image_xlarge_url || 
                                                currentMeta.image_large_url || 
                                                currentMeta.image_url || null;
                            if (currentArtUrl && currentArtUrl.startsWith('spotify:image:')) {
                                const imageId = currentArtUrl.replace('spotify:image:', '');
                                currentArtUrl = `https://i.scdn.co/image/${imageId}`;
                            }
                            
                            currentItem = {
                                id: currentTrack.uri ? extractSpotifyId(currentTrack.uri) : null,
                                uri: currentTrack.uri,
                                name: currentMeta.title || null,
                                artists: currentMeta.artist_name ? 
                                    [{ name: currentMeta.artist_name }] : [],
                                album: {
                                    name: currentMeta.album_title || null,
                                    images: currentArtUrl ? [{ url: currentArtUrl }] : []
                                }
                            };
                        }
                        
                        sendMessageTo(ws, { 
                            type: 'queue_data', 
                            success: true,
                            current: currentItem,
                            queue: queueItems,
                            count: queueItems.length,
                            timestamp: Date.now()
                        });
                        // log('Queue data sent:', queueItems.length, 'tracks');
                    } catch (e) {
                        sendMessageTo(ws, { type: 'queue_data', success: false, error: e.message });
                        log('Queue fetch error:', e.message);
                    }
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
            spotify_timestamp: playerData?.timestamp,
            
            // Player state (for real-time UI updates)
            shuffle: Spicetify.Player.getShuffle?.() ?? playerData?.options?.shuffling_context ?? null,
            repeat: Spicetify.Player.getRepeat?.() ?? null,
            volume: Spicetify.Player.getVolume?.() ?? null,
            is_muted: Spicetify.Player.getMute?.() ?? null,
            is_liked: Spicetify.Player.getHeart?.() ?? null
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

        // Fetch artist visuals from GraphQL (header image + gallery)
        const artistUri = item?.artists?.[0]?.uri;
        let artistVisuals = null;
        if (artistUri) {
            artistVisuals = await fetchArtistVisuals(artistUri);
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
                album_art_url: spotifyImageToUrl(item?.album?.images?.[0]?.url) || null,
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
            colors: colorCache,
            
            // ======== ARTIST VISUALS (from GraphQL) ========
            artist_visuals: artistVisuals
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
                album_art_url: spotifyImageToUrl(item?.album?.images?.[0]?.url) || null,
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
            colors: colorCache,
            
            // ======== ARTIST VISUALS (from GraphQL) ========
            artist_visuals: artistVisualsCache
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

            const track = data.track || {};
            
            // Extract only useful fields, excluding massive fingerprint strings
            // (codestring, echoprintstring, synchstring, rhythmstring are ~100KB+ and unused)
            return {
                // Core analysis
                tempo: track.tempo,
                tempo_confidence: track.tempo_confidence,
                key: track.key,
                key_confidence: track.key_confidence,
                mode: track.mode,  // 0=minor, 1=major
                mode_confidence: track.mode_confidence,
                time_signature: track.time_signature,
                time_signature_confidence: track.time_signature_confidence,
                loudness: track.loudness,
                duration: track.duration,
                
                // Fade info (for visualizations)
                end_of_fade_in: track.end_of_fade_in,
                start_of_fade_out: track.start_of_fade_out,
                
                // Analysis metadata
                num_samples: track.num_samples,
                analysis_sample_rate: track.analysis_sample_rate,
                analysis_channels: track.analysis_channels,
                
                // Audio features (mood-based visualizations)
                energy: track.energy,
                danceability: track.danceability,
                speechiness: track.speechiness,
                acousticness: track.acousticness,
                instrumentalness: track.instrumentalness,
                liveness: track.liveness,
                valence: track.valence,
                
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
     * Convert Spotify image URI to HTTPS URL
     * Handles both spotify:image:xxx format and https:// URLs
     * @param {string} uri - Image URI or URL
     * @returns {string|null} HTTPS URL or null
     */
    function spotifyImageToUrl(uri) {
        if (!uri) return null;
        
        // Already an HTTPS URL
        if (uri.startsWith('https://')) return uri;
        if (uri.startsWith('http://')) return uri;
        
        // Convert spotify:image:xxx to https://i.scdn.co/image/xxx
        if (uri.startsWith('spotify:image:')) {
            const imageId = uri.replace('spotify:image:', '');
            return `https://i.scdn.co/image/${imageId}`;
        }
        
        return null;
    }

    /**
     * Fetch colors using GraphQL (fixes 403 Forbidden from colorExtractor)
     * 
     * Tries multiple methods:
     * 1. GraphQL API with image URI (modern, what Spotify client uses)
     * 2. Local metadata (sometimes cached)
     * 3. Legacy colorExtractor (fallback for older versions)
     * 
     * @param {string} trackUri - Spotify track URI
     * @param {string} albumUri - Spotify album URI (unused, kept for API compatibility)
     * @returns {Object|null} Color palette or null on error
     */
    async function fetchColors(trackUri, albumUri) {
        const track = Spicetify.Player.data?.item;
        const metadata = track?.metadata || {};
        
        // Debug: Log available image sources
        log('Color extraction - track:', track?.name);
        log('Color extraction - album images:', track?.album?.images);
        log('Color extraction - metadata image_url:', metadata?.image_url);
        
        // Method 1: Try GraphQL API
        try {
            if (Spicetify.GraphQL?.Definitions?.fetchExtractedColors) {
                // Collect all possible image URIs
                const imageSources = [
                    track?.album?.images?.[0]?.url,           // HTTPS URL (most reliable)
                    track?.album?.images?.[0]?.uri,           // spotify:image:xxx format
                    metadata?.image_url,                       // Metadata image URL
                    metadata?.image_xlarge_url,               // Large image URL
                    metadata?.image_large_url,                // Medium image URL
                    metadata?.image_small_url,                // Small image URL
                ];
                
                log('Color extraction - raw image sources:', imageSources.filter(Boolean));
                
                // Convert all URIs to HTTPS URLs (GraphQL expects https://i.scdn.co/image/xxx format)
                const httpsUrls = imageSources
                    .map(uri => spotifyImageToUrl(uri))
                    .filter(Boolean);
                
                // Remove duplicates
                const uniqueUrls = [...new Set(httpsUrls)];
                log('Color extraction - HTTPS URLs to try:', uniqueUrls);
                
                // Try each HTTPS URL
                for (const imageUrl of uniqueUrls) {
                    try {
                        log('Color extraction - trying:', imageUrl);
                        const response = await Spicetify.GraphQL.Request(
                            Spicetify.GraphQL.Definitions.fetchExtractedColors,
                            { uris: [imageUrl] }
                        );
                        
                        log('Color extraction - response:', response);

                        if (response?.data?.extractedColors?.[0]) {
                            const c = response.data.extractedColors[0];
                            log('Colors extracted via GraphQL:', c);
                            return {
                                VIBRANT: c.colorRaw?.hex,
                                DARK_VIBRANT: c.colorDark?.hex,
                                LIGHT_VIBRANT: c.colorLight?.hex,
                                PROMINENT: c.colorRaw?.hex,
                                DESATURATED: c.colorDark?.hex,
                                VIBRANT_NON_ALARMING: c.colorLight?.hex
                            };
                        }
                    } catch (innerErr) {
                        log('Color extraction - failed for', imageUrl, ':', innerErr.message);
                        // Continue to next URL
                    }
                }
                
                log('Color extraction - GraphQL failed for all URLs');
            } else {
                log('Color extraction - GraphQL.Definitions.fetchExtractedColors not available');
            }
        } catch (e) {
            log('GraphQL color extraction failed:', e.message);
        }

        // Method 2: Check local metadata (sometimes cached by Spotify)
        try {
            if (metadata['extracted-color-dark']) {
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

    /**
     * Fetch artist visuals (header image + gallery) via GraphQL
     * Uses multiple fallback queries for maximum compatibility
     * @param {string} artistUri - Spotify artist URI (spotify:artist:xxx)
     * @returns {Object|null} Artist visuals or null on error
     */
    async function fetchArtistVisuals(artistUri) {
        if (!artistUri) return null;
        
        // Return cached data if same artist
        if (artistUri === artistVisualsCacheUri && artistVisualsCache) {
            log('Artist visuals: Using cached data for', artistUri);
            return artistVisualsCache;
        }
        
        const result = {
            header_image: null,
            gallery: [],
            source: null
        };
        
        // Method 1: Try fetchExtractedColorAndImageForArtistEntity (most reliable for images)
        try {
            if (Spicetify.GraphQL?.Definitions?.fetchExtractedColorAndImageForArtistEntity) {
                log('Artist visuals: Trying fetchExtractedColorAndImageForArtistEntity');
                const response = await Spicetify.GraphQL.Request(
                    Spicetify.GraphQL.Definitions.fetchExtractedColorAndImageForArtistEntity,
                    { uri: artistUri }
                );
                
                log('Artist visuals response (method 1):', response);
                
                if (response?.data?.artistUnion?.visuals) {
                    const visuals = response.data.artistUnion.visuals;
                    
                    // Header image (largest, artist-curated banner)
                    if (visuals.headerImage?.sources?.length > 0) {
                        // Get largest source
                        const largest = visuals.headerImage.sources.reduce((a, b) => 
                            ((a.width || 0) * (a.height || 0)) > ((b.width || 0) * (b.height || 0)) ? a : b
                        );
                        result.header_image = {
                            url: largest.url,
                            width: largest.width,
                            height: largest.height
                        };
                    }
                    
                    // Gallery images
                    if (visuals.gallery?.items?.length > 0) {
                        result.gallery = visuals.gallery.items
                            .filter(item => item?.sources?.length > 0)
                            .map(item => {
                                const src = item.sources[0];
                                return {
                                    url: src.url,
                                    width: src.width,
                                    height: src.height
                                };
                            });
                    }
                    
                    if (result.header_image || result.gallery.length > 0) {
                        result.source = 'fetchExtractedColorAndImageForArtistEntity';
                        log('Artist visuals: Found', result.gallery.length, 'gallery +', result.header_image ? 1 : 0, 'header');
                    }
                }
            }
        } catch (e) {
            log('Artist visuals: fetchExtractedColorAndImageForArtistEntity failed:', e.message);
        }
        
        // Method 2: Fallback to queryArtistOverview if Method 1 failed
        if (!result.source) {
            try {
                if (Spicetify.GraphQL?.Definitions?.queryArtistOverview) {
                    log('Artist visuals: Trying queryArtistOverview fallback');
                    const response = await Spicetify.GraphQL.Request(
                        Spicetify.GraphQL.Definitions.queryArtistOverview,
                        { uri: artistUri, locale: '', includePrerelease: false }
                    );
                    
                    log('Artist visuals response (method 2):', response);
                    
                    const artistUnion = response?.data?.artistUnion;
                    if (artistUnion) {
                        // Header image is at artistUnion.headerImage.data.sources (NOT inside visuals)
                        // Properties are maxWidth/maxHeight (NOT width/height)
                        if (artistUnion.headerImage?.data?.sources?.length > 0) {
                            const sources = artistUnion.headerImage.data.sources;
                            const largest = sources.reduce((a, b) => 
                                ((a.maxWidth || 0) * (a.maxHeight || 0)) > ((b.maxWidth || 0) * (b.maxHeight || 0)) ? a : b
                            );
                            result.header_image = {
                                url: largest.url,
                                width: largest.maxWidth,
                                height: largest.maxHeight
                            };
                            log('Artist visuals: Found header image', largest.maxWidth, 'x', largest.maxHeight);
                        }
                        
                        // Gallery is at artistUnion.visuals.gallery.items
                        if (artistUnion.visuals?.gallery?.items?.length > 0) {
                            result.gallery = artistUnion.visuals.gallery.items
                                .filter(item => item?.sources?.length > 0)
                                .map(item => {
                                    const src = item.sources[0];
                                    return { 
                                        url: src.url, 
                                        width: src.width || src.maxWidth, 
                                        height: src.height || src.maxHeight 
                                    };
                                });
                        }
                        
                        if (result.header_image || result.gallery.length > 0) {
                            result.source = 'queryArtistOverview';
                            log('Artist visuals: Found', result.gallery.length, 'gallery +', result.header_image ? 1 : 0, 'header via fallback');
                        }
                    }
                }
            } catch (e) {
                log('Artist visuals: queryArtistOverview failed:', e.message);
            }
        }
        
        // Log if no visuals found from any method
        if (!result.source) {
            log('Artist visuals: No visuals available from any method');
        }
        
        // Cache the result (even if null, to prevent repeated failed requests)
        artistVisualsCache = result.source ? result : null;
        artistVisualsCacheUri = artistUri;
        
        return artistVisualsCache;
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
            artistVisualsCache = null;
            artistVisualsCacheUri = null;
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
