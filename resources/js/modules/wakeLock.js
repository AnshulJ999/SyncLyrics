/**
 * wakeLock.js - Screen Wake Lock Module
 *
 * Keeps the device screen on while SyncLyrics is displayed, so phones and
 * tablets used as a lyrics display don't dim or lock mid-song.
 *
 * Uses the Screen Wake Lock API (Chrome/Edge/Android, Safari 16.4+).
 * Requires a secure context (https:// or localhost) - silently no-ops
 * otherwise instead of throwing.
 *
 * Level 1 - Imports: none
 */

let wakeLock = null;
let enabled = true;
let listenersAttached = false;

/**
 * Whether the browser supports the Screen Wake Lock API in this context.
 */
export function isWakeLockSupported() {
    return 'wakeLock' in navigator && window.isSecureContext;
}

/**
 * Acquire the wake lock. Safe to call repeatedly.
 */
async function acquire() {
    if (!enabled || !isWakeLockSupported()) return;
    if (wakeLock && !wakeLock.released) return;
    if (document.visibilityState !== 'visible') return;

    try {
        wakeLock = await navigator.wakeLock.request('screen');
        console.log('[wakeLock] Screen wake lock acquired');

        wakeLock.addEventListener('release', () => {
            console.log('[wakeLock] Screen wake lock released');
        });
    } catch (err) {
        // NotAllowedError is expected when the document lacks user activation,
        // or when the OS refuses (low battery / power saving mode).
        console.warn(`[wakeLock] Could not acquire wake lock: ${err.name} - ${err.message}`);
    }
}

/**
 * Release the wake lock, letting the screen sleep normally again.
 */
async function release() {
    if (!wakeLock) return;
    try {
        await wakeLock.release();
    } catch (err) {
        console.warn('[wakeLock] Release failed:', err);
    }
    wakeLock = null;
}

/**
 * Enable or disable the wake lock at runtime (called once /config is loaded).
 * @param {boolean} value
 */
export function setWakeLockEnabled(value) {
    const next = Boolean(value);
    if (next === enabled) return;
    enabled = next;
    if (enabled) {
        acquire();
    } else {
        release();
    }
}

/**
 * Initialize wake lock handling.
 *
 * The browser drops the lock whenever the page is hidden (tab switch, screen
 * lock, app backgrounded), so it must be re-acquired on visibilitychange.
 * Some browsers also require user activation for the first request, hence the
 * one-shot interaction listeners as a fallback.
 */
export function initWakeLock() {
    if (listenersAttached) return;
    listenersAttached = true;

    // URL param override for embeds/kiosk displays: ?keepAwake=false
    const keepAwakeParam = new URLSearchParams(window.location.search).get('keepAwake');
    if (keepAwakeParam !== null) {
        enabled = keepAwakeParam !== 'false';
    }

    if (!isWakeLockSupported()) {
        console.log('[wakeLock] Screen Wake Lock API unavailable (needs HTTPS or localhost) - screen may sleep');
        return;
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') acquire();
    });

    // Fallback for browsers that require a user gesture on first request
    const onFirstInteraction = () => {
        acquire();
        document.removeEventListener('click', onFirstInteraction);
        document.removeEventListener('touchend', onFirstInteraction);
        document.removeEventListener('keydown', onFirstInteraction);
    };
    document.addEventListener('click', onFirstInteraction);
    document.addEventListener('touchend', onFirstInteraction);
    document.addEventListener('keydown', onFirstInteraction);

    acquire();
}
