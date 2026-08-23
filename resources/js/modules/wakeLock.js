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
 * Modes:
 *   'always'   - hold the lock whenever the page is visible
 *   'playback' - hold only while a track is actually playing (default)
 *   'off'      - never hold
 *
 * Level 1 - Imports: none
 */

const MODES = ['always', 'playback', 'off'];

let wakeLock = null;
let mode = 'playback';
let isPlaying = false;
let modeLockedByUrl = false;
let listenersAttached = false;
let acquiring = false;

/**
 * Whether the browser supports the Screen Wake Lock API in this context.
 */
export function isWakeLockSupported() {
    return 'wakeLock' in navigator && window.isSecureContext;
}

/**
 * Whether the lock should be held right now, given mode, playback and
 * page visibility. Single source of truth for intent.
 */
function shouldHold() {
    if (!isWakeLockSupported()) return false;
    if (document.visibilityState !== 'visible') return false;
    if (mode === 'always') return true;
    if (mode === 'playback') return isPlaying;
    return false;
}

/**
 * Acquire the wake lock. Safe to call repeatedly.
 */
async function acquire() {
    if (!shouldHold()) return;
    if (wakeLock && !wakeLock.released) return;
    if (acquiring) return;

    acquiring = true;
    try {
        const sentinel = await navigator.wakeLock.request('screen');

        // Intent can change while the request is pending (mode switched,
        // playback stopped, page hidden). Drop it rather than hold a lock
        // nobody asked for any more.
        if (!shouldHold()) {
            try { await sentinel.release(); } catch (_) { /* already gone */ }
            return;
        }

        wakeLock = sentinel;
        console.log('[wakeLock] Screen wake lock acquired');

        wakeLock.addEventListener('release', () => {
            console.log('[wakeLock] Screen wake lock released');
        });
    } catch (err) {
        // NotAllowedError is expected when the document lacks user activation,
        // or when the OS refuses (low battery / power saving mode).
        console.warn(`[wakeLock] Could not acquire wake lock: ${err.name} - ${err.message}`);
    } finally {
        acquiring = false;
    }
}

/**
 * Release the wake lock, letting the screen sleep normally again.
 */
async function release() {
    if (!wakeLock) return;
    const sentinel = wakeLock;
    wakeLock = null;
    try {
        await sentinel.release();
    } catch (err) {
        console.warn('[wakeLock] Release failed:', err);
    }
}

/**
 * Re-evaluate intent and acquire or release to match it.
 */
function sync() {
    if (shouldHold()) acquire();
    else release();
}

/**
 * Set the wake lock mode, normally from /config.
 * Ignored when ?keepAwake= pinned the mode, so a per-display override is
 * never clobbered by the server setting.
 *
 * @param {string} value 'always' | 'playback' | 'off'
 */
export function setWakeLockMode(value) {
    if (modeLockedByUrl) return;
    const next = String(value);
    if (!MODES.includes(next)) {
        console.warn(`[wakeLock] Unknown mode "${value}", ignoring`);
        return;
    }
    if (next === mode) return;
    mode = next;
    sync();
}

/**
 * Report whether a track is currently playing. Only affects 'playback' mode.
 *
 * @param {boolean} value
 */
export function setWakeLockPlaying(value) {
    const next = Boolean(value);
    if (next === isPlaying) return;
    isPlaying = next;
    if (mode === 'playback') sync();
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

    // Per-display override for embeds and kiosk screens:
    //   ?keepAwake=false                 -> off
    //   ?keepAwake=always|playback|off   -> explicit mode
    // Pinned so a later /config load cannot override it.
    const param = new URLSearchParams(window.location.search).get('keepAwake');
    if (param !== null) {
        let normalized = param;
        if (param === 'false') normalized = 'off';
        else if (param === 'true') normalized = 'always';

        if (MODES.includes(normalized)) {
            mode = normalized;
            modeLockedByUrl = true;
        } else {
            console.warn(`[wakeLock] Ignoring unknown ?keepAwake=${param}`);
        }
    }

    if (!isWakeLockSupported()) {
        console.log('[wakeLock] Screen Wake Lock API unavailable (needs HTTPS or localhost) - screen may sleep');
        return;
    }

    document.addEventListener('visibilitychange', sync);

    // Fallback for browsers that require a user gesture on first request
    const onFirstInteraction = () => {
        sync();
        document.removeEventListener('click', onFirstInteraction);
        document.removeEventListener('touchend', onFirstInteraction);
        document.removeEventListener('keydown', onFirstInteraction);
    };
    document.addEventListener('click', onFirstInteraction);
    document.addEventListener('touchend', onFirstInteraction);
    document.addEventListener('keydown', onFirstInteraction);

    sync();
}
