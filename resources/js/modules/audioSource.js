/**
 * Audio Source Module
 * 
 * Manages the audio source selection modal and state.
 * Handles device enumeration, recognition control, and status updates.
 */

import {
    getAudioRecognitionConfig,
    setAudioRecognitionConfig,
    getAudioRecognitionDevices,
    startAudioRecognition,
    stopAudioRecognition,
    getAudioRecognitionStatus
} from './api.js';

import { showToast } from './dom.js';
import audioCapture from './audioCapture.js';

// =============================================================================
// State
// =============================================================================

let isModalOpen = false;
let pollInterval = null;
let currentConfig = null;
let isActive = false;
let isFrontendCapture = false; // True if currently using frontend mic capture
let currentTrackSource = null; // Default: no source (shows Idle)
let lastKnownProvider = null; // Last known recognition provider (prevents flashing)

// DOM Elements (cached on init)
let elements = {};

// =============================================================================
// DOM Cache
// =============================================================================

function cacheElements() {
    elements = {
        // Button
        sourceToggle: document.getElementById('source-toggle'),
        sourceName: document.getElementById('source-name'),

        // Modal
        modal: document.getElementById('audio-source-modal'),
        closeBtn: document.getElementById('audio-source-close'),

        // Status
        recognitionStatus: document.getElementById('recognition-status'),
        recognitionMode: document.getElementById('recognition-mode'),
        attemptRow: document.getElementById('attempt-row'),
        recognitionAttempts: document.getElementById('recognition-attempts'),
        lastMatchRow: document.getElementById('last-match-row'),
        lastMatchInfo: document.getElementById('last-match-info'),
        enrichmentRow: document.getElementById('enrichment-row'),
        enrichmentStatus: document.getElementById('enrichment-status'),

        // Quick start
        quickStartBackend: document.getElementById('quick-start-backend'),
        quickStartBackendBtn: document.getElementById('quick-start-backend-btn'),
        backendDeviceName: document.getElementById('backend-device-name'),
        quickStartFrontend: document.getElementById('quick-start-frontend'),
        quickStartFrontendBtn: document.getElementById('quick-start-frontend-btn'),

        // Device selection
        deviceSelect: document.getElementById('device-select'),
        sampleRateInfo: document.getElementById('sample-rate-info'),
        httpsWarning: document.getElementById('https-warning'),

        // Audio level meter (large)
        audioLevelContainer: document.getElementById('audio-level-container'),
        audioLevelFill: document.getElementById('audio-level-fill'),
        audioLevelValue: document.getElementById('audio-level-value'),

        // Control button (single toggle)
        toggleBtn: document.getElementById('recognition-toggle'),

        // Current song
        currentSongInfo: document.getElementById('current-song-info'),
        currentSongTitle: document.getElementById('current-song-title'),
        currentSongArtist: document.getElementById('current-song-artist'),

        // Advanced settings
        advancedToggle: document.getElementById('advanced-toggle'),
        advancedContent: document.getElementById('advanced-content'),
        recognitionInterval: document.getElementById('recognition-interval'),
        recognitionIntervalValue: document.getElementById('recognition-interval-value'),
        captureDuration: document.getElementById('capture-duration'),
        captureDurationValue: document.getElementById('capture-duration-value'),
        latencyOffset: document.getElementById('latency-offset'),
        latencyOffsetValue: document.getElementById('latency-offset-value'),
        silenceThreshold: document.getElementById('silence-threshold'),
        silenceThresholdValue: document.getElementById('silence-threshold-value'),
    };
}

// =============================================================================
// Modal Control
// =============================================================================

function openModal() {
    if (!elements.modal) return;

    isModalOpen = true;
    elements.modal.classList.add('visible');

    // Load data
    loadDevices();
    loadConfig();
    refreshStatus();

    // Start polling (faster when modal open)
    startPolling(2000);
}

function closeModal() {
    if (!elements.modal) return;

    isModalOpen = false;
    elements.modal.classList.remove('visible');

    // Slow down polling
    startPolling(5000);
}

function toggleAdvanced() {
    const toggle = elements.advancedToggle;
    const content = elements.advancedContent;

    if (toggle && content) {
        toggle.classList.toggle('open');
        content.classList.toggle('open');
    }
}

// =============================================================================
// Device Loading
// =============================================================================

async function loadDevices() {
    const select = elements.deviceSelect;
    if (!select) return;

    try {
        const result = await getAudioRecognitionDevices();

        if (result.error) {
            console.warn('Failed to load devices:', result.error);
            return;
        }

        // Build options
        const backendOptgroup = document.createElement('optgroup');
        backendOptgroup.label = 'System Audio (Backend)';

        const devices = result.devices || [];
        const recommended = result.recommended;

        // Add "Auto" option first if there's a recommended device
        // Fix 3.2: recommended is an integer (device ID), not an object
        if (recommended !== null && recommended !== undefined) {
            const recommendedDevice = devices.find(d => d.id === recommended);
            const deviceName = recommendedDevice ? recommendedDevice.name : `Device ${recommended}`;
            const apiLabel = recommendedDevice?.api ? ` [${recommendedDevice.api}]` : '';
            const autoOpt = document.createElement('option');
            autoOpt.value = 'backend:auto';
            autoOpt.textContent = `Auto (${deviceName})${apiLabel}`;
            backendOptgroup.appendChild(autoOpt);

            // Update quick-start backend device name
            if (elements.backendDeviceName) {
                elements.backendDeviceName.textContent = `Auto (${deviceName})`;
            }
        }

        if (devices.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'No devices available';
            opt.disabled = true;
            backendOptgroup.appendChild(opt);
        } else {
            devices.forEach(device => {
                const opt = document.createElement('option');
                opt.value = `backend:${device.id}`;
                // Show API name for clarity (e.g., "Loopback (MOTU M Series) [MME]")
                const apiLabel = device.api ? ` [${device.api}]` : '';
                opt.textContent = `${device.name}${apiLabel}`;
                backendOptgroup.appendChild(opt);
            });
        }

        // Frontend (browser mic) options
        const frontendOptgroup = document.createElement('optgroup');
        frontendOptgroup.label = 'Browser Microphone (Frontend)';

        // Try to enumerate browser mics
        try {
            if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
                const mediaDevices = await navigator.mediaDevices.enumerateDevices();
                const audioInputs = mediaDevices.filter(d => d.kind === 'audioinput');

                audioInputs.forEach(device => {
                    const opt = document.createElement('option');
                    opt.value = `frontend:${device.deviceId || 'default'}`;
                    opt.textContent = device.label || 'Microphone';
                    frontendOptgroup.appendChild(opt);
                });

                if (audioInputs.length === 0) {
                    const opt = document.createElement('option');
                    opt.value = 'frontend:default';
                    opt.textContent = 'Default Microphone';
                    frontendOptgroup.appendChild(opt);
                }
            }
        } catch (e) {
            // Browser mic enumeration failed, add default option
            const opt = document.createElement('option');
            opt.value = 'frontend:default';
            opt.textContent = 'Default Microphone';
            frontendOptgroup.appendChild(opt);
        }

        // Clear and rebuild select
        select.innerHTML = '';
        select.appendChild(backendOptgroup);
        select.appendChild(frontendOptgroup);

        // Detect if running on mobile/tablet (no backend devices available)
        const isMobile = /Android|iPhone|iPad|iPod|Mobile|Tablet/i.test(navigator.userAgent);
        const hasBackendDevices = devices.length > 0;

        // Select current device based on config, or smart default
        if (currentConfig) {
            const mode = currentConfig.mode || 'backend';
            const deviceId = currentConfig.device_id;

            if (mode === 'frontend') {
                select.value = 'frontend:default';
            } else if (deviceId !== null && deviceId !== undefined) {
                select.value = `backend:${deviceId}`;
            } else {
                // Default to Auto if no specific device configured
                select.value = 'backend:auto';
            }
        } else {
            // Smart default: frontend mic on mobile, backend auto on desktop
            if (isMobile || !hasBackendDevices) {
                select.value = 'frontend:default';
            } else {
                select.value = 'backend:auto';
            }
        }

    } catch (error) {
        console.error('Error loading devices:', error);
    }
}

// =============================================================================
// Config Loading
// =============================================================================

async function loadConfig() {
    try {
        const result = await getAudioRecognitionConfig();

        if (result.error) {
            console.warn('Failed to load config:', result.error);
            return;
        }

        currentConfig = result.config || {};

        // Update slider values from backend config
        if (elements.recognitionInterval) {
            elements.recognitionInterval.value = currentConfig.recognition_interval || 5;
        }
        if (elements.captureDuration) {
            elements.captureDuration.value = currentConfig.capture_duration || 5;
        }
        if (elements.latencyOffset) {
            elements.latencyOffset.value = currentConfig.latency_offset || 0;
        }
        if (elements.silenceThreshold) {
            elements.silenceThreshold.value = currentConfig.silence_threshold || 500;
        }

        // Update slider value displays by dispatching input events
        elements.recognitionInterval?.dispatchEvent(new Event('input'));
        elements.captureDuration?.dispatchEvent(new Event('input'));
        elements.latencyOffset?.dispatchEvent(new Event('input'));
        elements.silenceThreshold?.dispatchEvent(new Event('input'));

    } catch (error) {
        console.error('Error loading config:', error);
    }
}

// =============================================================================
// Status Polling
// =============================================================================

async function refreshStatus() {
    try {
        const result = await getAudioRecognitionStatus();

        if (result.error) {
            updateStatusDisplay({ active: false, state: 'error' });
            return;
        }

        isActive = result.active || false;

        // Also fetch current track to get the actual source if audio rec is inactive
        // Fix 3.1: Correct endpoint is /current-track, not /api/track/current
        if (!isActive) {
            try {
                const response = await fetch('/current-track');
                const trackData = await response.json();
                if (trackData && trackData.source) {
                    currentTrackSource = trackData.source;
                }
            } catch (e) {
                // Ignore errors fetching track
            }
        }

        updateStatusDisplay(result);
        updateButtonState();

        // Audio level is now updated inline in updateStatusDisplay via audioLevelRow

    } catch (error) {
        console.error('Error refreshing status:', error);
    }
}

function updateStatusDisplay(status) {
    // Update status text
    if (elements.recognitionStatus) {
        const state = status.state || (status.active ? 'active' : 'idle');
        let displayState = capitalizeFirst(state);

        // Add attempt count if searching
        if (status.consecutive_no_match > 0 && state !== 'idle') {
            displayState = `Searching (${status.consecutive_no_match})`;
        }

        elements.recognitionStatus.textContent = displayState;
        elements.recognitionStatus.className = 'status-value ' + state;
    }

    // Update mode
    if (elements.recognitionMode) {
        const mode = status.mode || '—';
        elements.recognitionMode.textContent = capitalizeFirst(mode);
    }

    // Update attempt count row
    if (elements.attemptRow && elements.recognitionAttempts) {
        if (status.active && status.consecutive_no_match !== undefined) {
            elements.attemptRow.style.display = 'flex';
            const result = status.last_attempt_result || 'idle';
            if (result === 'matched') {
                elements.recognitionAttempts.textContent = '✓ Matched';
                elements.recognitionAttempts.className = 'status-value enriched';
            } else if (result === 'no_match') {
                elements.recognitionAttempts.textContent = `No match (${status.consecutive_no_match})`;
                elements.recognitionAttempts.className = 'status-value no-match';
            } else {
                elements.recognitionAttempts.textContent = capitalizeFirst(result);
                elements.recognitionAttempts.className = 'status-value';
            }
        } else {
            elements.attemptRow.style.display = 'none';
        }
    }

    // Audio level is now handled by large meter in updateButtonState
    // Update amplitude display if active
    if (status.active && status.audio_level !== undefined) {
        updateAudioLevel(status.audio_level);
    }

    // Update last match info
    if (elements.lastMatchRow && elements.lastMatchInfo) {
        if (status.current_song && status.current_song.title) {
            elements.lastMatchRow.style.display = 'flex';
            const song = status.current_song;
            elements.lastMatchInfo.textContent = `${song.artist} - ${song.title}`;
        } else {
            elements.lastMatchRow.style.display = 'none';
        }
    }

    // Update enrichment status
    if (elements.enrichmentRow && elements.enrichmentStatus) {
        if (status.current_song && status.current_song.album_art_url) {
            elements.enrichmentRow.style.display = 'flex';
            // Check if enriched (has Spotify URL or proper album art)
            const isEnriched = status.current_song.album_art_url.includes('scdn.co') ||
                status.current_song.spotify_url;
            elements.enrichmentStatus.textContent = isEnriched ? '☑ Spotify' : '☐ Shazam only';
            elements.enrichmentStatus.className = isEnriched ? 'status-value enriched' : 'status-value';
        } else {
            elements.enrichmentRow.style.display = 'none';
        }
    }

    // Update button text - show current source
    if (elements.sourceName) {
        if (status.active) {
            // Audio recognition is active - show actual recognition provider
            if (status.capture_mode === 'frontend') {
                elements.sourceName.textContent = 'Mic';
            } else {
                // Use recognition_provider from current_song
                // IMPORTANT: Always use current provider, don't cache stale values
                const provider = status.current_song?.recognition_provider;
                if (provider === 'acrcloud') {
                    elements.sourceName.textContent = 'ACRCloud';
                } else if (provider === 'local_fingerprint') {
                    elements.sourceName.textContent = 'Local FP';
                } else if (provider === 'shazam') {
                    elements.sourceName.textContent = 'Shazam';
                } else if (status.current_song) {
                    // Have a song but no provider - default to Shazam (the primary recognizer)
                    elements.sourceName.textContent = 'Shazam';
                } else {
                    // No song yet - show generic
                    elements.sourceName.textContent = 'Audio';
                }
            }
        } else {
            // Audio recognition not active - reset provider tracking
            lastKnownProvider = null;
            
            // Show current track source
            const sourceMap = {
                'spotify': 'Spotify',
                'spotify_hybrid': 'Hybrid',
                'spotifyhybrid': 'Hybrid',
                'spicetify': 'Spicetify',
                'windows': 'Windows',
                'windows_media': 'Windows',
                'windowsmedia': 'Windows',
                'audio_recognition': 'Shazam',
                'audiorecognition': 'Shazam',
                'shazam': 'Shazam',
                'acrcloud': 'ACRCloud',
                'local_fingerprint': 'Local',
                'reaper': 'Reaper',
                'music_assistant': 'Music Assistant',
                'linux': 'Linux',
                'macos': 'Mac'
            };
            const displaySource = sourceMap[currentTrackSource] || 'Idle';
            elements.sourceName.textContent = displaySource;
        }
    }

    // Update current song (if active)
    if (status.current_song && status.current_song.title) {
        if (elements.currentSongInfo) {
            elements.currentSongInfo.style.display = 'block';
        }
        if (elements.currentSongTitle) {
            elements.currentSongTitle.textContent = status.current_song.title;
        }
        if (elements.currentSongArtist) {
            elements.currentSongArtist.textContent = status.current_song.artist || '—';
        }
    } else {
        if (elements.currentSongInfo) {
            elements.currentSongInfo.style.display = 'none';
        }
    }
}

function updateButtonState() {
    // Update toggle button text and style
    if (elements.toggleBtn) {
        if (isActive) {
            elements.toggleBtn.textContent = '⏹ Stop Recognition';
            elements.toggleBtn.classList.remove('start');
            elements.toggleBtn.classList.add('stop');
        } else {
            elements.toggleBtn.textContent = '▶ Start Recognition';
            elements.toggleBtn.classList.remove('stop');
            elements.toggleBtn.classList.add('start');
        }
    }

    // Show/hide audio level container
    if (elements.audioLevelContainer) {
        elements.audioLevelContainer.style.display = isActive ? 'block' : 'none';
    }

    // Disable quick-start buttons when active
    if (elements.quickStartBackendBtn) {
        elements.quickStartBackendBtn.disabled = isActive;
        elements.quickStartBackendBtn.textContent = isActive ? 'Running' : '▶ Start';
    }
    if (elements.quickStartFrontendBtn) {
        elements.quickStartFrontendBtn.disabled = isActive;
        elements.quickStartFrontendBtn.textContent = isActive ? 'Running' : '▶ Start';
    }

    // Toggle recording indicator on source button
    if (elements.sourceToggle) {
        if (isActive) {
            elements.sourceToggle.classList.add('recording');
        } else {
            elements.sourceToggle.classList.remove('recording');
        }
    }
}

function startPolling(intervalMs) {
    if (pollInterval) {
        clearInterval(pollInterval);
    }
    pollInterval = setInterval(refreshStatus, intervalMs);
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

// =============================================================================
// Recognition Control
// =============================================================================

async function handleStart(overrideMode = null) {
    const select = elements.deviceSelect;
    if (!select) return;

    const value = select.value;
    let [mode, deviceId] = value.split(':');
    
    // Use override mode if provided (from Quick Start)
    if (overrideMode) {
        mode = overrideMode;
        deviceId = overrideMode === 'frontend' ? 'default' : 'auto';
    }

    // Check HTTPS for frontend mode
    if (mode === 'frontend' && !isSecureContext()) {
        showHttpsWarning();
        return;
    }

    // Build config update
    const configUpdate = {
        mode: mode,
        enabled: true
    };

    // Only set device_id for specific device selection, not for 'auto'
    if (mode === 'backend' && deviceId && deviceId !== 'auto') {
        configUpdate.device_id = parseInt(deviceId, 10);
    } else if (mode === 'backend' && deviceId === 'auto') {
        // Auto mode - explicitly set to null so backend uses auto-detection
        configUpdate.device_id = null;
    }

    // Apply advanced settings
    if (elements.recognitionInterval) {
        configUpdate.recognition_interval = parseFloat(elements.recognitionInterval.value);
    }
    if (elements.captureDuration) {
        configUpdate.capture_duration = parseFloat(elements.captureDuration.value);
    }
    if (elements.latencyOffset) {
        configUpdate.latency_offset = parseFloat(elements.latencyOffset.value);
    }
    if (elements.silenceThreshold) {
        configUpdate.silence_threshold = parseInt(elements.silenceThreshold.value, 10);
    }

    try {
        // Apply config to backend
        await setAudioRecognitionConfig(configUpdate);

        if (mode === 'frontend') {
            // FRONTEND MODE: Start browser mic capture
            isFrontendCapture = true;

            // CRITICAL: Start the recognition engine FIRST, before WebSocket connects
            // The WebSocket handler checks if engine is running and disconnects if not
            const startResult = await startAudioRecognition();
            if (startResult.error) {
                console.error('Failed to start recognition engine:', startResult.error);
                isFrontendCapture = false;
                return;
            }

            // Now start capture - this connects WebSocket which switches engine to frontend mode
            await audioCapture.startCapture(deviceId, {
                onLevel: (level) => updateAudioLevel(level),
                onStatus: (status) => console.log('[AudioSource] Capture status:', status),
                onRecognition: (result) => {
                    console.log('[AudioSource] Recognition:', result);
                    // Status will be updated via polling
                }
            });

            console.log('[AudioSource] Frontend capture started');
        } else {
            // BACKEND MODE: Use backend audio capture
            isFrontendCapture = false;

            const result = await startAudioRecognition();
            if (result.error) {
                console.error('Failed to start backend recognition:', result.error);
                return;
            }
        }

        // Refresh status
        await refreshStatus();

    } catch (error) {
        console.error('Error starting recognition:', error);
        // Stop any partial capture
        if (isFrontendCapture) {
            await audioCapture.stopCapture();
            isFrontendCapture = false;
        }
    }
}

async function handleStop() {
    try {
        // Stop frontend capture if active
        if (isFrontendCapture) {
            await audioCapture.stopCapture();
            isFrontendCapture = false;

            console.log('[AudioSource] Frontend capture stopped');
        }

        // Always notify backend to stop
        const result = await stopAudioRecognition();

        if (result.error) {
            console.error('Failed to stop backend recognition:', result.error);
        }

        // Reset provider tracking for next session
        lastKnownProvider = null;

        await refreshStatus();

    } catch (error) {
        console.error('Error stopping recognition:', error);
    }
}

// =============================================================================
// Device Selection
// =============================================================================

function handleDeviceChange() {
    const select = elements.deviceSelect;
    if (!select) return;

    const value = select.value;
    const [mode] = value.split(':');

    // Show HTTPS warning if needed
    if (mode === 'frontend' && !isSecureContext()) {
        showHttpsWarning();
    } else {
        hideHttpsWarning();
    }
}

// =============================================================================
// Utilities
// =============================================================================

function isSecureContext() {
    return window.isSecureContext ||
        location.protocol === 'https:' ||
        location.hostname === 'localhost' ||
        location.hostname === '127.0.0.1';
}

function showHttpsWarning() {
    // Show inline warning in modal (if visible)
    if (elements.httpsWarning) {
        elements.httpsWarning.classList.add('visible');
    }
    // Also show a toast so user definitely sees the message
    showToast('🎤 Browser microphone requires HTTPS. Use System Audio instead, or switch to HTTPS.', 'error', 5000);
}

function hideHttpsWarning() {
    if (elements.httpsWarning) {
        elements.httpsWarning.classList.remove('visible');
    }
}

function capitalizeFirst(str) {
    if (!str) return '';
    return str.charAt(0).toUpperCase() + str.slice(1).toLowerCase();
}

// =============================================================================
// Audio Level Meter
// =============================================================================

export function updateAudioLevel(level) {
    // Level is normalized 0-1, update fill bar
    if (elements.audioLevelFill) {
        const percent = Math.min(100, Math.max(0, level * 100));
        elements.audioLevelFill.style.width = `${percent}%`;
    }

    // Show raw amplitude value (reverse the normalization: level * 32768 / 2)
    // This matches what silence threshold expects (50-500 range typical)
    if (elements.audioLevelValue) {
        const rawAmplitude = Math.round(level * 32768 / 2);
        elements.audioLevelValue.textContent = `Amp: ${rawAmplitude}`;
    }
}

// =============================================================================
// Initialization
// =============================================================================

export function init() {
    cacheElements();

    if (!elements.sourceToggle) {
        console.log('Audio source UI not found, skipping init');
        return;
    }

    // Button click -> open modal
    elements.sourceToggle.addEventListener('click', openModal);

    // Close modal
    if (elements.closeBtn) {
        elements.closeBtn.addEventListener('click', closeModal);
    }

    // Click outside to close
    if (elements.modal) {
        elements.modal.addEventListener('click', (e) => {
            if (e.target === elements.modal) {
                closeModal();
            }
        });
    }

    // Escape key to close
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && isModalOpen) {
            closeModal();
        }
    });

    // Toggle button (Start/Stop)
    if (elements.toggleBtn) {
        elements.toggleBtn.addEventListener('click', () => {
            if (isActive) {
                handleStop();
            } else {
                handleStart();
            }
        });
    }

    // Device selection change
    if (elements.deviceSelect) {
        elements.deviceSelect.addEventListener('change', handleDeviceChange);
    }

    // Advanced toggle
    if (elements.advancedToggle) {
        elements.advancedToggle.addEventListener('click', toggleAdvanced);
    }

    // Quick-start buttons
    if (elements.quickStartBackendBtn) {
        elements.quickStartBackendBtn.addEventListener('click', () => handleQuickStart('backend'));
    }
    if (elements.quickStartFrontendBtn) {
        elements.quickStartFrontendBtn.addEventListener('click', () => handleQuickStart('frontend'));
    }

    // Slider change handlers - update value display + immediate apply when active
    setupSlider('recognitionInterval', 's', 'recognition_interval');
    setupSlider('captureDuration', 's', 'capture_duration');
    setupSlider('latencyOffset', 's', 'latency_offset');
    setupSlider('silenceThreshold', '', 'silence_threshold');

    // Reset button handlers - use loaded config values from settings.json
    document.querySelectorAll('.reset-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.dataset.target;
            // Convert kebab-case ID to snake_case config key
            const configKey = targetId.replace(/-/g, '_');
            // Use loaded config value, fallback to HTML default if config not loaded
            const defaultValue = currentConfig?.[configKey] ?? btn.dataset.default;
            const input = document.getElementById(targetId);
            if (input) {
                input.value = defaultValue;
                input.dispatchEvent(new Event('input'));
            }
        });
    });

    // Start background polling (slower when modal closed)
    startPolling(5000);

    // Initial status check
    refreshStatus();

    console.log('Audio source module initialized');
}

// Quick-start handler
async function handleQuickStart(mode) {
    // Set device select to appropriate value (for visual consistency)
    if (elements.deviceSelect) {
        if (mode === 'backend') {
            elements.deviceSelect.value = 'backend:auto';
        } else {
            elements.deviceSelect.value = 'frontend:default';
        }
    }
    // Pass mode directly to avoid race condition with dropdown options
    await handleStart(mode);
}

// Setup slider with value display and immediate apply
function setupSlider(baseName, suffix, configKey) {
    const slider = elements[baseName];
    const valueDisplay = elements[baseName + 'Value'];

    if (slider && valueDisplay) {
        // Update display on input
        slider.addEventListener('input', () => {
            valueDisplay.textContent = slider.value + suffix;
        });

        // Apply to backend on change (when user releases slider)
        slider.addEventListener('change', async () => {
            if (isActive && configKey) {
                const value = configKey === 'silence_threshold'
                    ? parseInt(slider.value, 10)
                    : parseFloat(slider.value);
                try {
                    await setAudioRecognitionConfig({ [configKey]: value });
                    console.log(`[AudioSource] Applied ${configKey}: ${value}`);
                } catch (error) {
                    console.error(`Failed to apply ${configKey}:`, error);
                }
            }
        });

        // Initial value
        valueDisplay.textContent = slider.value + suffix;
    }
}

export default {
    init,
    updateAudioLevel,
    refreshStatus
};
