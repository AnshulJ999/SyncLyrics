/**
 * Audio Capture Module
 * 
 * Handles browser microphone capture using AudioWorklet for real-time
 * audio streaming to the backend via WebSocket.
 * 
 * Features:
 * - AudioWorklet-based capture for low latency
 * - WebSocket streaming with auto-reconnection (R6)
 * - Audio level metering
 * - Proper resource cleanup
 */

// =============================================================================
// Constants
// =============================================================================

const SAMPLE_RATE = 44100;
const CHUNK_SIZE = 4096; // Samples per chunk
const TARGET_SAMPLE_RATE = 44100; // Target for backend

// WebSocket reconnection settings (R6: exponential backoff)
const WS_RECONNECT_BASE_DELAY = 1000;
const WS_RECONNECT_MAX_DELAY = 30000;
const WS_RECONNECT_MAX_ATTEMPTS = 10;

// =============================================================================
// State
// =============================================================================

let audioContext = null;
let mediaStream = null;
let audioWorkletNode = null;
let analyserNode = null;
let websocket = null;

let isCapturing = false;
let reconnectAttempts = 0;
let reconnectTimeout = null;

// Callbacks
let onLevelUpdate = null;
let onStatusChange = null;
let onRecognition = null;

// =============================================================================
// WebSocket Connection
// =============================================================================

function getWebSocketUrl() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws/audio-stream`;
}

async function connectWebSocket() {
    if (websocket && (websocket.readyState === WebSocket.CONNECTING ||
        websocket.readyState === WebSocket.OPEN)) {
        return;
    }

    const url = getWebSocketUrl();
    console.log(`[AudioCapture] Connecting to WebSocket: ${url}`);

    try {
        websocket = new WebSocket(url);
        websocket.binaryType = 'arraybuffer';

        websocket.onopen = () => {
            console.log('[AudioCapture] WebSocket connected');
            reconnectAttempts = 0;
            if (onStatusChange) onStatusChange('connected');
        };

        websocket.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                handleWebSocketMessage(data);
            } catch (e) {
                console.error('[AudioCapture] Failed to parse message:', e);
            }
        };

        websocket.onerror = (error) => {
            console.error('[AudioCapture] WebSocket error:', error);
            if (onStatusChange) onStatusChange('error');
        };

        websocket.onclose = (event) => {
            console.log(`[AudioCapture] WebSocket closed: ${event.code} ${event.reason}`);
            websocket = null;

            if (isCapturing) {
                // Attempt reconnection with exponential backoff (R6)
                scheduleReconnect();
            }
        };

    } catch (error) {
        console.error('[AudioCapture] WebSocket connection failed:', error);
        scheduleReconnect();
    }
}

function scheduleReconnect() {
    if (reconnectAttempts >= WS_RECONNECT_MAX_ATTEMPTS) {
        console.error('[AudioCapture] Max reconnection attempts reached');
        if (onStatusChange) onStatusChange('disconnected');
        return;
    }

    // Exponential backoff with jitter (R6)
    const delay = Math.min(
        WS_RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts) + Math.random() * 1000,
        WS_RECONNECT_MAX_DELAY
    );

    reconnectAttempts++;
    console.log(`[AudioCapture] Reconnecting in ${delay}ms (attempt ${reconnectAttempts})`);

    if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
    }

    reconnectTimeout = setTimeout(() => {
        if (isCapturing) {
            connectWebSocket();
        }
    }, delay);
}

function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'connected':
            console.log('[AudioCapture] Server confirmed connection');
            if (data.capture_duration) {
                console.log(`[AudioCapture] Capture duration: ${data.capture_duration}s`);
            }
            break;

        case 'recognition':
            console.log(`[AudioCapture] Recognition: ${data.artist} - ${data.title}`);
            if (onRecognition) {
                onRecognition({
                    artist: data.artist,
                    title: data.title,
                    position: data.position
                });
            }
            break;

        case 'no_match':
            console.log('[AudioCapture] No match found');
            break;

        case 'error':
            console.error('[AudioCapture] Server error:', data.message);
            break;

        case 'pong':
            // Heartbeat response
            break;

        default:
            console.log('[AudioCapture] Unknown message type:', data.type);
    }
}

function disconnectWebSocket() {
    if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
        reconnectTimeout = null;
    }

    if (websocket) {
        websocket.close(1000, 'User stopped capture');
        websocket = null;
    }
}

// =============================================================================
// Audio Capture
// =============================================================================

async function initAudioContext() {
    if (audioContext) return;

    audioContext = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: SAMPLE_RATE
    });

    // Load audio worklet processor
    try {
        await audioContext.audioWorklet.addModule('/resources/js/audio-processor.js');
        console.log('[AudioCapture] AudioWorklet loaded');
    } catch (error) {
        console.error('[AudioCapture] Failed to load AudioWorklet:', error);
        throw new Error('AudioWorklet not supported or failed to load');
    }
}

async function requestMicrophone(deviceId = null) {
    const constraints = {
        audio: {
            echoCancellation: false,
            noiseSuppression: false,
            autoGainControl: false,
            sampleRate: SAMPLE_RATE
        }
    };

    if (deviceId && deviceId !== 'default') {
        constraints.audio.deviceId = { exact: deviceId };
    }

    try {
        mediaStream = await navigator.mediaDevices.getUserMedia(constraints);
        console.log('[AudioCapture] Microphone access granted');
        return true;
    } catch (error) {
        console.error('[AudioCapture] Microphone access denied:', error);
        throw error;
    }
}

function setupAudioPipeline() {
    if (!audioContext || !mediaStream) return;

    // Create source node from microphone
    const sourceNode = audioContext.createMediaStreamSource(mediaStream);

    // Create analyser for level metering
    analyserNode = audioContext.createAnalyser();
    analyserNode.fftSize = 256;
    analyserNode.smoothingTimeConstant = 0.8;

    // Create AudioWorklet node for processing
    audioWorkletNode = new AudioWorkletNode(audioContext, 'audio-processor', {
        processorOptions: {
            chunkSize: CHUNK_SIZE
        }
    });

    // Handle audio chunks from worklet
    audioWorkletNode.port.onmessage = (event) => {
        const { type, data, level } = event.data;

        if (type === 'audio') {
            // Send to WebSocket as Int16 PCM (little-endian per R3)
            if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(data.buffer);
            }

            // Update level meter (R7: send level with audio data)
            if (level !== undefined && onLevelUpdate) {
                onLevelUpdate(level);
            }
        }
    };

    // Connect pipeline
    sourceNode.connect(analyserNode);
    analyserNode.connect(audioWorkletNode);
    // Don't connect to destination - we don't want to hear the microphone

    console.log('[AudioCapture] Audio pipeline ready');
}

function startLevelMeter() {
    if (!analyserNode) return;

    const dataArray = new Uint8Array(analyserNode.frequencyBinCount);

    function updateLevel() {
        if (!isCapturing || !analyserNode) return;

        analyserNode.getByteFrequencyData(dataArray);

        // Calculate RMS level
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
            sum += dataArray[i] * dataArray[i];
        }
        const rms = Math.sqrt(sum / dataArray.length);
        const level = rms / 255; // Normalize to 0-1

        if (onLevelUpdate) {
            onLevelUpdate(level);
        }

        requestAnimationFrame(updateLevel);
    }

    updateLevel();
}

// =============================================================================
// Public API
// =============================================================================

/**
 * Start audio capture
 * 
 * @param {string} deviceId - Optional device ID
 * @param {Object} callbacks - Callback functions
 * @returns {Promise<boolean>} Success
 */
export async function startCapture(deviceId = null, callbacks = {}) {
    if (isCapturing) {
        console.warn('[AudioCapture] Already capturing');
        return false;
    }

    // Store callbacks
    onLevelUpdate = callbacks.onLevel;
    onStatusChange = callbacks.onStatus;
    onRecognition = callbacks.onRecognition;

    try {
        // Initialize audio context
        await initAudioContext();

        // Request microphone
        await requestMicrophone(deviceId);

        // Resume audio context if suspended
        if (audioContext.state === 'suspended') {
            await audioContext.resume();
        }

        // Setup audio pipeline
        setupAudioPipeline();

        // Connect WebSocket
        await connectWebSocket();

        // Start level meter
        startLevelMeter();

        isCapturing = true;
        if (onStatusChange) onStatusChange('capturing');

        console.log('[AudioCapture] Capture started');
        return true;

    } catch (error) {
        console.error('[AudioCapture] Failed to start capture:', error);
        await stopCapture();
        throw error;
    }
}

/**
 * Stop audio capture
 */
export async function stopCapture() {
    console.log('[AudioCapture] Stopping capture...');

    isCapturing = false;

    // Disconnect WebSocket
    disconnectWebSocket();

    // Stop AudioWorklet
    if (audioWorkletNode) {
        audioWorkletNode.disconnect();
        audioWorkletNode = null;
    }

    // Stop analyser
    if (analyserNode) {
        analyserNode.disconnect();
        analyserNode = null;
    }

    // Stop media stream tracks
    if (mediaStream) {
        mediaStream.getTracks().forEach(track => track.stop());
        mediaStream = null;
    }

    // Close audio context
    if (audioContext) {
        await audioContext.close();
        audioContext = null;
    }

    // Clear callbacks
    onLevelUpdate = null;
    onStatusChange = null;
    onRecognition = null;

    console.log('[AudioCapture] Capture stopped');
}

/**
 * Check if capture is active
 */
export function isActive() {
    return isCapturing;
}

/**
 * Check if browser supports audio capture
 */
export function isSupported() {
    return !!(
        navigator.mediaDevices &&
        navigator.mediaDevices.getUserMedia &&
        window.AudioContext || window.webkitAudioContext &&
        window.AudioWorklet
    );
}

/**
 * Check if running in secure context (required for getUserMedia)
 */
export function isSecureContext() {
    return window.isSecureContext ||
        location.protocol === 'https:' ||
        location.hostname === 'localhost' ||
        location.hostname === '127.0.0.1';
}

export default {
    startCapture,
    stopCapture,
    isActive,
    isSupported,
    isSecureContext
};
