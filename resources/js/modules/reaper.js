/**
 * reaper.js - REAPER DAW Integration UI
 *
 * Manages the REAPER Control Panel modal, offset nudging, auto-calibration triggers,
 * and transport controls.
 *
 * Also exports isReaperConnected() for use by main.js to show/hide the toggle button
 * whenever the companion script is running (heartbeat active), regardless of whether
 * reaper_daw is the currently winning metadata source.
 */

// ─── Connection State ───────────────────────────────────────────────────────
let _reaper_connected = false;
let _connectionMonitorId = null;

/**
 * Returns true when the companion script is actively sending heartbeats.
 * Used by main.js to decide whether to show the REAPER button.
 */
export function isReaperConnected() {
    return _reaper_connected;
}

/**
 * Lightweight background poll (~every 3s) to track whether REAPER is reachable.
 * Runs independently of the modal so the button appears the moment the companion
 * script starts, even if another source (Spotify) has higher priority.
 */
async function _checkConnection() {
    try {
        const res = await fetch('/api/reaper/status');
        if (!res.ok) {
            _reaper_connected = false;
            return;
        }
        const data = await res.json();
        _reaper_connected = !!data.connected;
    } catch {
        _reaper_connected = false;
    }
}

function _startConnectionMonitor() {
    if (_connectionMonitorId) return;
    _checkConnection(); // Immediate check on init
    _connectionMonitorId = setInterval(_checkConnection, 3000);
}

// ─── Modal State ────────────────────────────────────────────────────────────
let updateIntervalId = null;

/**
 * Initialize REAPER integration UI.
 * Called once at app startup from main.js.
 */
export function setupReaperUI() {
    const btnOpen = document.getElementById('btn-reaper-daw');
    const btnClose = document.getElementById('reaper-daw-close');
    const modal = document.getElementById('reaper-daw-modal');

    if (!btnOpen || !btnClose || !modal) return;

    btnOpen.addEventListener('click', () => {
        modal.classList.remove('hidden');
        // Small delay lets display:flex apply before the opacity transition fires.
        setTimeout(() => modal.classList.add('visible'), 10);
        startPolling();
    });

    btnClose.addEventListener('click', closeModal);

    // Close on backdrop click
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });

    function closeModal() {
        modal.classList.remove('visible');
        setTimeout(() => modal.classList.add('hidden'), 300); // match CSS transition
        stopPolling();
    }

    // Transport buttons
    document.getElementById('reaper-btn-prev')?.addEventListener('click', () => sendCommand('prev'));
    document.getElementById('reaper-btn-play')?.addEventListener('click', () => sendCommand('play_pause'));
    document.getElementById('reaper-btn-next')?.addEventListener('click', () => sendCommand('next'));

    // Offset nudging
    document.getElementById('reaper-offset-minus')?.addEventListener('click', () => adjustOffset(-0.5));
    document.getElementById('reaper-offset-plus')?.addEventListener('click', () => adjustOffset(0.5));

    // Auto-calibration
    document.getElementById('reaper-btn-calibrate')?.addEventListener('click', triggerCalibration);

    // Start background connection monitor (runs independently of modal open state)
    _startConnectionMonitor();
}

// ─── Modal Polling ──────────────────────────────────────────────────────────

function startPolling() {
    if (updateIntervalId) return;
    updateStatus(); // Immediate fetch
    updateIntervalId = setInterval(updateStatus, 1000);
}

function stopPolling() {
    if (updateIntervalId) {
        clearInterval(updateIntervalId);
        updateIntervalId = null;
    }
}

async function updateStatus() {
    try {
        const response = await fetch('/api/reaper/status');
        if (!response.ok) {
            document.getElementById('reaper-project-name').textContent = 'Not Connected';
            document.getElementById('reaper-song-name').textContent = '—';
            return;
        }
        const data = await response.json();

        // Update connection state from the authoritative backend field
        _reaper_connected = !!data.connected;

        document.getElementById('reaper-project-name').textContent = data.project || 'None';
        document.getElementById('reaper-song-name').textContent = data.song || 'Unknown';
        document.getElementById('reaper-offset-value').textContent = `${(data.offset ?? 0).toFixed(2)}s`;

        const calibratingRow = document.getElementById('reaper-calibrating-row');
        if (calibratingRow) {
            calibratingRow.style.display = data.calibrating ? 'flex' : 'none';
        }
    } catch (e) {
        console.error('REAPER UI Error:', e);
    }
}

// ─── Actions ────────────────────────────────────────────────────────────────

async function adjustOffset(delta) {
    try {
        const res = await fetch('/api/reaper/offset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ delta })
        });
        const data = await res.json();
        if (data.success) {
            document.getElementById('reaper-offset-value').textContent = `${data.new_offset.toFixed(2)}s`;
        } else {
            console.warn('REAPER offset nudge failed:', data.error);
        }
    } catch (e) {
        console.error(e);
    }
}

async function triggerCalibration() {
    try {
        await fetch('/api/reaper/calibrate', { method: 'POST' });
        updateStatus();
    } catch (e) {
        console.error(e);
    }
}

async function sendCommand(command) {
    try {
        await fetch('/api/reaper/command', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ command })
        });
    } catch (e) {
        console.error(e);
    }
}
