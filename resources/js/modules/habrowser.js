/**
 * habrowser.js — Home Assistant iframe embed
 *
 * Mirrors the mediabrowser.js pattern: opens a full-screen modal containing
 * an iframe pointed at /ha/ (which proxies to the configured HA instance).
 * Login persistence is automatic via the browser's cookie storage for the
 * HA origin. A 30-minute inactivity timer destroys the iframe to free memory;
 * it reloads transparently on next open.
 *
 * Import hierarchy: Level 2 (no imports from higher-level modules).
 */

// ── Constants ────────────────────────────────────────────────────────────────

const HA_IFRAME_SRC = '/ha/';
const HA_INACTIVITY_MS = 30 * 60 * 1000; // 30 minutes

// ── Module state ─────────────────────────────────────────────────────────────

let haModalOpen = false;
let haInactivityTimer = null;

// ── DOM helpers ──────────────────────────────────────────────────────────────

function getModal()  { return document.getElementById('ha-modal'); }
function getFrame()  { return document.getElementById('ha-frame'); }
function getBtn()    { return document.getElementById('btn-home-assistant'); }

// ── Inactivity timer ─────────────────────────────────────────────────────────

function resetInactivityTimer() {
    clearTimeout(haInactivityTimer);
    haInactivityTimer = setTimeout(() => {
        // Only destroy when modal is closed (don't disrupt an open session)
        if (!haModalOpen) {
            destroyFrame();
        }
    }, HA_INACTIVITY_MS);
}

function destroyFrame() {
    const frame = getFrame();
    if (frame) {
        frame.src = '';
    }
}

// ── Modal open / close ───────────────────────────────────────────────────────

function openModal() {
    const modal = getModal();
    const frame = getFrame();
    const btn   = getBtn();
    if (!modal || !frame) return;

    // Load iframe if not already loaded
    if (!frame.src || frame.src === window.location.href) {
        frame.src = HA_IFRAME_SRC;
    }

    modal.classList.remove('hidden');
    if (btn) btn.classList.add('active');
    haModalOpen = true;

    // Pause inactivity destruction while modal is open
    clearTimeout(haInactivityTimer);
}

function closeModal() {
    const modal = getModal();
    const btn   = getBtn();
    if (!modal) return;

    modal.classList.add('hidden');
    if (btn) btn.classList.remove('active');
    haModalOpen = false;

    // Restart inactivity timer — destroy iframe after 30 min of disuse
    resetInactivityTimer();
}

// ── Refresh ──────────────────────────────────────────────────────────────────

function refreshFrame() {
    const frame = getFrame();
    if (!frame) return;
    // Force reload by briefly clearing src then restoring
    frame.src = '';
    requestAnimationFrame(() => { frame.src = HA_IFRAME_SRC; });
}

// ── Setup ────────────────────────────────────────────────────────────────────

/**
 * setupHomeAssistant()
 * Called from main.js when config.haEnabled is true.
 * Shows the HA button and wires up all event listeners.
 */
export function setupHomeAssistant() {
    const btn    = getBtn();
    const modal  = getModal();

    if (!btn || !modal) {
        console.warn('[habrowser] Required DOM elements not found.');
        return;
    }

    // Show the button (hidden by default in HTML)
    btn.style.display = '';

    // Button → open modal
    btn.addEventListener('click', () => {
        if (haModalOpen) {
            closeModal();
        } else {
            openModal();
        }
    });

    // Close button
    const closeBtn = document.getElementById('ha-close');
    if (closeBtn) closeBtn.addEventListener('click', closeModal);

    // Refresh button
    const refreshBtn = document.getElementById('ha-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', refreshFrame);

    // "Open in new tab" button
    const openTabBtn = document.getElementById('ha-open-tab');
    if (openTabBtn) {
        openTabBtn.addEventListener('click', () => {
            window.open(HA_IFRAME_SRC, '_blank', 'noopener,noreferrer');
        });
    }

    // Backdrop click (clicking the modal backdrop closes it)
    modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });

    // Escape key closes modal
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && haModalOpen) closeModal();
    });

    // Start inactivity timer
    resetInactivityTimer();
}
