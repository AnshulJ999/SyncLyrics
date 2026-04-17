/**
 * reaper.js - REAPER DAW Integration UI
 * 
 * Manages the REAPER Control Panel modal, offset nudging, auto-calibration triggers,
 * and transport controls.
 */

let updateIntervalId = null;

/**
 * Initialize REAPER integration UI
 */
export function setupReaperUI() {
    const btnOpen = document.getElementById('btn-reaper-daw');
    const btnClose = document.getElementById('reaper-daw-close');
    const modal = document.getElementById('reaper-daw-modal');
    
    if (!btnOpen || !btnClose || !modal) return;
    
    btnOpen.addEventListener('click', () => {
        modal.classList.remove('hidden');
        // Add a small delay to allow display: flex to apply before opacity transition
        setTimeout(() => modal.classList.add('visible'), 10);
        startPolling();
    });
    
    btnClose.addEventListener('click', () => {
        modal.classList.remove('visible');
        setTimeout(() => modal.classList.add('hidden'), 300); // match transition duration
        stopPolling();
    });
    
    // Close on click outside
    modal.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.remove('visible');
            setTimeout(() => modal.classList.add('hidden'), 300);
            stopPolling();
        }
    });

    // Transport buttons
    document.getElementById('reaper-btn-prev')?.addEventListener('click', () => sendCommand('prev'));
    document.getElementById('reaper-btn-play')?.addEventListener('click', () => sendCommand('play_pause'));
    document.getElementById('reaper-btn-next')?.addEventListener('click', () => sendCommand('next'));

    // Offset nudging
    document.getElementById('reaper-offset-minus')?.addEventListener('click', () => adjustOffset(-0.5));
    document.getElementById('reaper-offset-plus')?.addEventListener('click', () => adjustOffset(0.5));

    // Auto-calibration
    document.getElementById('reaper-btn-calibrate')?.addEventListener('click', triggerCalibration);
}

/**
 * Start polling status while modal is open
 */
function startPolling() {
    if (updateIntervalId) return;
    updateStatus(); // Initial fetch
    updateIntervalId = setInterval(updateStatus, 1000);
}

/**
 * Stop polling
 */
function stopPolling() {
    if (updateIntervalId) {
        clearInterval(updateIntervalId);
        updateIntervalId = null;
    }
}

/**
 * Fetch and update status UI
 */
async function updateStatus() {
    try {
        const response = await fetch('/api/reaper/status');
        if (!response.ok) {
            document.getElementById('reaper-project-name').textContent = "Not Connected";
            document.getElementById('reaper-song-name').textContent = "—";
            return;
        }
        const data = await response.json();
        
        document.getElementById('reaper-project-name').textContent = data.project || "None";
        document.getElementById('reaper-song-name').textContent = data.song || "Unknown";
        document.getElementById('reaper-offset-value').textContent = `${data.offset.toFixed(2)}s`;
        
        const calibratingRow = document.getElementById('reaper-calibrating-row');
        if (data.calibrating) {
            calibratingRow.style.display = 'flex';
        } else {
            calibratingRow.style.display = 'none';
        }
    } catch (e) {
        console.error("REAPER UI Error:", e);
    }
}

/**
 * Adjust current offset
 */
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
        }
    } catch (e) {
        console.error(e);
    }
}

/**
 * Trigger auto calibration
 */
async function triggerCalibration() {
    try {
        await fetch('/api/reaper/calibrate', { method: 'POST' });
        updateStatus();
    } catch (e) {
        console.error(e);
    }
}

/**
 * Send transport command
 */
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
