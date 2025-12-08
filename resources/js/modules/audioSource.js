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

import audioCapture from './audioCapture.js';

// =============================================================================
// State
// =============================================================================

let isModalOpen = false;
let pollInterval = null;
let currentConfig = null;
let isActive = false;
let isFrontendCapture = false; // True if currently using frontend mic capture
let currentTrackSource = 'Spotify'; // Default display

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
        // statusDot removed - using minimal design

        // Modal
        modal: document.getElementById('audio-source-modal'),
        closeBtn: document.getElementById('audio-source-close'),

        // Status
        recognitionStatus: document.getElementById('recognition-status'),
        recognitionMode: document.getElementById('recognition-mode'),
        lastRecognitionRow: document.getElementById('last-recognition-row'),
        lastRecognitionTime: document.getElementById('last-recognition-time'),

        // Device selection
        deviceSelect: document.getElementById('device-select'),
        httpsWarning: document.getElementById('https-warning'),

        // Controls
        startBtn: document.getElementById('recognition-start'),
        stopBtn: document.getElementById('recognition-stop'),

        // Audio level
        audioLevelContainer: document.getElementById('audio-level-container'),
        audioLevelFill: document.getElementById('audio-level-fill'),

        // Current song
        currentSongInfo: document.getElementById('current-song-info'),
        currentSongTitle: document.getElementById('current-song-title'),
        currentSongArtist: document.getElementById('current-song-artist'),

        // Advanced settings
        advancedToggle: document.getElementById('advanced-toggle'),
        advancedContent: document.getElementById('advanced-content'),
        reaperAutoDetect: document.getElementById('reaper-auto-detect'),
        recognitionInterval: document.getElementById('recognition-interval'),
        captureDuration: document.getElementById('capture-duration'),
        latencyOffset: document.getElementById('latency-offset'),
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

        // Update advanced settings
        if (elements.reaperAutoDetect) {
            elements.reaperAutoDetect.checked = currentConfig.reaper_auto_detect || false;
        }
        if (elements.recognitionInterval) {
            elements.recognitionInterval.value = currentConfig.recognition_interval || 5;
        }
        if (elements.captureDuration) {
            elements.captureDuration.value = currentConfig.capture_duration || 4;
        }
        if (elements.latencyOffset) {
            elements.latencyOffset.value = currentConfig.latency_offset || 0;
        }

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

        // Update audio level from backend if not in frontend capture mode
        // Frontend mode calculates its own level via AudioWorklet
        if (!isFrontendCapture && result.audio_level !== undefined) {
            if (isActive && elements.audioLevelContainer) {
                elements.audioLevelContainer.style.display = 'block';
                updateAudioLevel(result.audio_level);
            }
        }

        // Hide level meter when not active
        if (!isActive && !isFrontendCapture && elements.audioLevelContainer) {
            elements.audioLevelContainer.style.display = 'none';
        }

    } catch (error) {
        console.error('Error refreshing status:', error);
    }
}

function updateStatusDisplay(status) {
    // Update status text
    if (elements.recognitionStatus) {
        const state = status.state || (status.active ? 'active' : 'idle');
        elements.recognitionStatus.textContent = capitalizeFirst(state);
        elements.recognitionStatus.className = 'status-value ' + state;
    }

    // Update mode
    if (elements.recognitionMode) {
        const mode = status.mode || '—';
        elements.recognitionMode.textContent = capitalizeFirst(mode);
    }

    // Update button text - show current source
    if (elements.sourceName) {
        if (status.active) {
            // Audio recognition is active
            const sourceName = status.capture_mode === 'frontend' ? 'Mic' : 'Shazam';
            elements.sourceName.textContent = sourceName;
        } else {
            // Audio recognition not active - show current track source
            // Fix 3.3: Complete source mapping with all variations
            const sourceMap = {
                'spotify': 'Spotify',
                'spotify_hybrid': 'Hybrid',
                'spotifyhybrid': 'Hybrid',
                'windows': 'Windows',
                'windows_media': 'Windows',
                'windowsmedia': 'Windows',
                'audio_recognition': 'Shazam',
                'audiorecognition': 'Shazam',
                'shazam': 'Shazam',
                'reaper': 'Reaper'
            };
            const displaySource = sourceMap[currentTrackSource] || 'Spotify';
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
    if (elements.startBtn && elements.stopBtn) {
        if (isActive) {
            elements.startBtn.style.display = 'none';
            elements.stopBtn.style.display = 'flex';
        } else {
            elements.startBtn.style.display = 'flex';
            elements.stopBtn.style.display = 'none';
        }
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

async function handleStart() {
    const select = elements.deviceSelect;
    if (!select) return;

    const value = select.value;
    const [mode, deviceId] = value.split(':');

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
    if (elements.reaperAutoDetect) {
        configUpdate.reaper_auto_detect = elements.reaperAutoDetect.checked;
    }
    if (elements.recognitionInterval) {
        configUpdate.recognition_interval = parseFloat(elements.recognitionInterval.value);
    }
    if (elements.captureDuration) {
        configUpdate.capture_duration = parseFloat(elements.captureDuration.value);
    }
    if (elements.latencyOffset) {
        configUpdate.latency_offset = parseFloat(elements.latencyOffset.value);
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

            // Show audio level container
            if (elements.audioLevelContainer) {
                elements.audioLevelContainer.style.display = 'block';
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

            // Hide audio level container
            if (elements.audioLevelContainer) {
                elements.audioLevelContainer.style.display = 'none';
            }

            console.log('[AudioSource] Frontend capture stopped');
        }

        // Always notify backend to stop
        const result = await stopAudioRecognition();

        if (result.error) {
            console.error('Failed to stop backend recognition:', result.error);
        }

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

    // Show/hide audio level meter
    if (elements.audioLevelContainer) {
        elements.audioLevelContainer.style.display =
            mode === 'frontend' ? 'block' : 'none';
    }

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
    if (elements.httpsWarning) {
        elements.httpsWarning.classList.add('visible');
    }
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
    // Level should be 0-1
    if (elements.audioLevelFill) {
        const percent = Math.min(100, Math.max(0, level * 100));
        elements.audioLevelFill.style.width = `${percent}%`;
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

    // Control buttons
    if (elements.startBtn) {
        elements.startBtn.addEventListener('click', handleStart);
    }
    if (elements.stopBtn) {
        elements.stopBtn.addEventListener('click', handleStop);
    }

    // Device selection change
    if (elements.deviceSelect) {
        elements.deviceSelect.addEventListener('change', handleDeviceChange);
    }

    // Advanced toggle
    if (elements.advancedToggle) {
        elements.advancedToggle.addEventListener('click', toggleAdvanced);
    }

    // Reaper auto-detect toggle - immediately update when changed
    if (elements.reaperAutoDetect) {
        elements.reaperAutoDetect.addEventListener('change', async () => {
            try {
                await setAudioRecognitionConfig({
                    reaper_auto_detect: elements.reaperAutoDetect.checked
                });
                console.log(`[AudioSource] Reaper auto-detect: ${elements.reaperAutoDetect.checked}`);
            } catch (error) {
                console.error('Failed to update Reaper auto-detect:', error);
            }
        });
    }

    // Start background polling (slower when modal closed)
    startPolling(5000);

    // Initial status check
    refreshStatus();

    console.log('Audio source module initialized');
}

export default {
    init,
    updateAudioLevel,
    refreshStatus
};
