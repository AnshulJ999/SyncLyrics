/**
 * synclyrics-test.js - Spicetify Test Extension for SyncLyrics
 * 
 * This extension extracts ALL available data from Spicetify and sends it
 * to a local Python WebSocket server for testing purposes.
 * 
 * INSTALLATION:
 * 1. Copy this file to: %APPDATA%\spicetify\Extensions\
 * 2. Run: spicetify config extensions synclyrics-test.js
 * 3. Run: spicetify apply
 * 4. Restart Spotify
 * 
 * USAGE:
 * 1. Run the Python test server: python tests/test_spicetify_data.py
 * 2. Play songs in Spotify
 * 3. Watch the Python console for extracted data
 */

(function SyncLyricsTestBridge() {
    'use strict';
    
    const WS_URL = 'ws://127.0.0.1:9099/';  // Test WebSocket server
    const RECONNECT_INTERVAL = 3000;  // 3 seconds
    
    let ws = null;
    let connected = false;
    let audioDataCache = {};
    let colorCache = {};
    
    // Logging helper
    function log(msg, data = null) {
        const prefix = '[SyncLyrics-Test]';
        if (data) {
            console.log(prefix, msg, data);
        } else {
            console.log(prefix, msg);
        }
    }
    
    // Connect to WebSocket server
    function connect() {
        if (ws && ws.readyState === WebSocket.OPEN) return;
        
        try {
            ws = new WebSocket(WS_URL);
            
            ws.onopen = () => {
                connected = true;
                log('Connected to test server!');
                // Send initial state
                sendFullState('connected');
                // IMMEDIATELY fetch and send track data for current song
                setTimeout(() => {
                    log('Fetching data for currently playing track...');
                    sendTrackData();
                }, 500);
            };
            
            ws.onclose = () => {
                connected = false;
                log('Disconnected, reconnecting in 3s...');
                setTimeout(connect, RECONNECT_INTERVAL);
            };
            
            ws.onerror = (err) => {
                log('WebSocket error (server not running?)');
            };
            
            ws.onmessage = (event) => {
                // Handle any commands from test server
                try {
                    const cmd = JSON.parse(event.data);
                    if (cmd.type === 'request_full_state') {
                        sendFullState('requested');
                        sendTrackData();
                    }
                } catch (e) {}
            };
        } catch (e) {
            log('Failed to connect:', e.message);
            setTimeout(connect, RECONNECT_INTERVAL);
        }
    }
    
    // Send current playback state
    function sendFullState(trigger = 'event') {
        if (!connected || !ws || ws.readyState !== WebSocket.OPEN) return;
        
        const playerData = Spicetify.Player.data;
        const item = playerData?.item;
        
        const state = {
            type: 'state',
            trigger: trigger,
            timestamp: Date.now(),
            
            // Position data
            position_ms: Spicetify.Player.getProgress(),
            position_percent: Spicetify.Player.getProgressPercent(),
            duration_ms: Spicetify.Player.getDuration(),
            
            // Playback state
            is_playing: Spicetify.Player.isPlaying(),
            is_buffering: playerData?.is_buffering || false,
            is_paused: playerData?.is_paused || false,
            playback_speed: playerData?.playback_speed || 1.0,
            
            // Track info
            track_uri: item?.uri || null,
            track_name: item?.name || null,
            track_artists: item?.artists?.map(a => a.name) || [],
            track_album: item?.album?.name || null,
            track_album_uri: item?.album?.uri || null,
            
            // Player settings
            volume: Spicetify.Player.getVolume(),
            is_muted: Spicetify.Player.getMute(),
            repeat: Spicetify.Player.getRepeat(),  // 0=off, 1=all, 2=one
            shuffle: Spicetify.Player.getShuffle(),
            heart: Spicetify.Player.getHeart(),  // Liked status
            
            // Queue info (basic)
            has_next: playerData?.next_tracks?.length > 0,
            has_prev: playerData?.prev_tracks?.length > 0,
            queue_length: playerData?.next_tracks?.length || 0,
            
            // Raw position data for timing analysis
            position_as_of_timestamp: playerData?.position_as_of_timestamp,
            internal_timestamp: playerData?.timestamp
        };
        
        try {
            ws.send(JSON.stringify(state));
        } catch (e) {
            log('Failed to send state:', e.message);
        }
    }
    
    // Fetch and send audio analysis + colors for current track
    async function sendTrackData() {
        if (!connected || !ws || ws.readyState !== WebSocket.OPEN) return;
        
        const trackUri = Spicetify.Player.data?.item?.uri;
        if (!trackUri) {
            log('No track playing');
            return;
        }
        
        log('Fetching track data for:', trackUri);
        
        // Audio Analysis
        let audioAnalysis = null;
        if (!audioDataCache[trackUri]) {
            try {
                log('Fetching audio analysis...');
                audioDataCache[trackUri] = await Spicetify.getAudioData(trackUri);
                log('Audio analysis received!');
            } catch (e) {
                log('Audio analysis failed:', e.message);
                audioDataCache[trackUri] = { error: e.message };
            }
        }
        audioAnalysis = audioDataCache[trackUri];
        
        // Color Extraction - try track URI first, then album URI
        let colors = null;
        const albumUri = Spicetify.Player.data?.item?.album?.uri;
        const cacheKey = trackUri;  // Still cache by track
        
        if (!colorCache[cacheKey]) {
            // Try track URI first
            try {
                log('Fetching colors for track URI...');
                const trackColors = await Spicetify.colorExtractor(trackUri);
                if (trackColors && Object.keys(trackColors).length > 0) {
                    log('Colors from track URI received!', trackColors);
                    colorCache[cacheKey] = trackColors;
                } else {
                    log('Track URI returned empty colors, trying album URI...');
                }
            } catch (e) {
                log('Track color extraction failed:', e.message);
            }
            
            // Fallback to album URI if track didn't work
            if (!colorCache[cacheKey] && albumUri) {
                try {
                    log('Fetching colors for album URI:', albumUri);
                    const albumColors = await Spicetify.colorExtractor(albumUri);
                    if (albumColors && Object.keys(albumColors).length > 0) {
                        log('Colors from album URI received!', albumColors);
                        colorCache[cacheKey] = albumColors;
                    } else {
                        log('Album URI also returned empty colors');
                        colorCache[cacheKey] = { error: 'Both track and album URIs returned empty' };
                    }
                } catch (e) {
                    log('Album color extraction failed:', e.message);
                    colorCache[cacheKey] = { error: e.message };
                }
            }
            
            // If still nothing, mark as error
            if (!colorCache[cacheKey]) {
                colorCache[cacheKey] = { error: 'colorExtractor returned null/empty' };
            }
        }
        colors = colorCache[cacheKey];
        
        // Send track data
        const trackData = {
            type: 'track_data',
            timestamp: Date.now(),
            track_uri: trackUri,
            track_name: Spicetify.Player.data?.item?.name,
            track_artist: Spicetify.Player.data?.item?.artists?.[0]?.name,
            
            // Audio Analysis Summary
            audio_analysis: audioAnalysis ? {
                // Check if we got actual data or error
                success: !audioAnalysis.error,
                error: audioAnalysis.error || null,
                
                // Basic track info
                duration: audioAnalysis.track?.duration,
                tempo: audioAnalysis.track?.tempo,
                time_signature: audioAnalysis.track?.time_signature,
                key: audioAnalysis.track?.key,
                mode: audioAnalysis.track?.mode,  // 0=minor, 1=major
                loudness: audioAnalysis.track?.loudness,
                
                // Counts
                beats_count: audioAnalysis.beats?.length || 0,
                bars_count: audioAnalysis.bars?.length || 0,
                sections_count: audioAnalysis.sections?.length || 0,
                segments_count: audioAnalysis.segments?.length || 0,
                tatums_count: audioAnalysis.tatums?.length || 0,
                
                // Sample data (first few items)
                first_beats: audioAnalysis.beats?.slice(0, 5),
                first_sections: audioAnalysis.sections?.slice(0, 3),
                
                // Raw data available
                has_beats: !!audioAnalysis.beats,
                has_bars: !!audioAnalysis.bars,
                has_sections: !!audioAnalysis.sections,
                has_segments: !!audioAnalysis.segments,
                has_tatums: !!audioAnalysis.tatums
            } : null,
            
            // Colors
            colors: colors ? {
                success: !colors.error,
                error: colors.error || null,
                VIBRANT: colors.VIBRANT,
                DARK_VIBRANT: colors.DARK_VIBRANT,
                LIGHT_VIBRANT: colors.LIGHT_VIBRANT,
                PROMINENT: colors.PROMINENT,
                DESATURATED: colors.DESATURATED,
                VIBRANT_NON_ALARMING: colors.VIBRANT_NON_ALARMING
            } : null,
            
            // Full raw audio analysis (for detailed inspection)
            audio_analysis_raw: audioAnalysis
        };
        
        try {
            ws.send(JSON.stringify(trackData));
            log('Track data sent!');
        } catch (e) {
            log('Failed to send track data:', e.message);
        }
    }
    
    // Event listeners
    Spicetify.Player.addEventListener("onprogress", () => {
        // Send position updates every ~500ms (throttle to avoid spam)
        if (!window._lastProgressSend || Date.now() - window._lastProgressSend > 500) {
            window._lastProgressSend = Date.now();
            sendFullState('onprogress');
        }
    });
    
    Spicetify.Player.addEventListener("onplaypause", () => {
        log('Play/Pause event');
        sendFullState('onplaypause');
    });
    
    Spicetify.Player.addEventListener("songchange", () => {
        log('Song changed!');
        // Clear caches for new song
        sendFullState('songchange');
        // Delay slightly to let Spotify update internal state
        setTimeout(() => {
            sendTrackData();
        }, 500);
    });
    
    // Initial connection
    log('SyncLyrics Test Extension loaded!');
    log('Waiting for test server on ' + WS_URL);
    
    // Wait for Spicetify to be fully ready
    if (Spicetify.Player && Spicetify.Player.data) {
        connect();
    } else {
        // Retry after a short delay
        setTimeout(() => {
            connect();
        }, 1000);
    }
    
})();
