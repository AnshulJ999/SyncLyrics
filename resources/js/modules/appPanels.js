/**
 * appPanels.js - What's New / Welcome panels and the update-available dot.
 *
 * Which panel to show comes from /api/app/info (see app_info.py). Preview either
 * panel without marking it seen: ?preview=whats-new or ?preview=welcome
 */

import { copyToClipboard, timeoutSignal } from './utils.js';

const AUTO_CLOSE_SECONDS = 45;
const BROWSER_SEEN_KEY = 'synclyrics.lastSeenVersion';
const WELCOME_ROWS = ['now_playing', 'spotify', 'music_assistant'];
// Any of these inside the panel means someone is reading it: stop the auto-close
const STOP_EVENTS = ['pointerenter', 'pointerdown', 'wheel', 'touchstart', 'keydown'];

function storageGet(key) {
    try { return localStorage.getItem(key); } catch { return null; }
}

function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch { /* storage blocked */ }
}

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'className') node.className = value;
        else if (key === 'html') node.innerHTML = value;
        else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of children) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
}

function icon(name) {
    return el('i', { className: `bi ${name}`, 'aria-hidden': 'true' });
}

function externalLink(href, text) {
    return el('a', { href, target: '_blank', rel: 'noopener' }, text);
}

// ---------- Update dot ----------

function showUpdateDot(info) {
    const update = info.update || {};
    if (!update.available) return;

    for (const id of ['settings-toggle', 'main-settings-link']) {
        const target = document.getElementById(id);
        if (!target || target.querySelector('.update-dot')) continue;
        target.append(el('span', { className: 'update-dot', 'aria-hidden': 'true' }));
        const label = target.getAttribute('title') || '';
        target.setAttribute('aria-label', `${label} (update available)`);
    }

    const header = document.querySelector('#settings-panel .settings-header');
    if (header && !document.getElementById('update-line')) {
        header.after(el('a', { id: 'update-line', className: 'update-line', href: '/settings' },
            icon('bi-arrow-up-circle'), `SyncLyrics ${update.latest} is available`));
    }
}

// ---------- Panel shell ----------

let openPanelState = null;

function closePanel() {
    if (!openPanelState) return;
    const { backdrop, onClose, previousFocus, keyHandler, timer } = openPanelState;
    openPanelState = null;
    clearInterval(timer);
    document.removeEventListener('keydown', keyHandler, true);
    backdrop.classList.remove('visible');
    setTimeout(() => backdrop.remove(), 250);
    if (previousFocus && typeof previousFocus.focus === 'function') previousFocus.focus();
    onClose?.();
}

function focusables(root) {
    return [...root.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')]
        .filter(node => node.offsetParent !== null);
}

function showPanel({ title, body, footerButton, autoClose, onClose }) {
    closePanel();

    const closeX = el('button', { type: 'button', className: 'app-panel-icon-btn', 'aria-label': 'Close' }, icon('bi-x-lg'));
    const footerBtn = el('button', { type: 'button', className: 'app-btn primary' }, footerButton);
    const countdown = el('span', { className: 'app-panel-countdown' });
    const timerBar = el('div', { className: 'app-panel-timer', 'aria-hidden': 'true' });

    const panel = el('div', { className: 'app-panel', role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': 'app-panel-title', tabindex: '-1' },
        el('div', { className: 'app-panel-header' }, el('h2', { id: 'app-panel-title' }, title), closeX),
        el('div', { className: 'app-panel-body' }, ...body),
        el('div', { className: 'app-panel-footer' }, countdown, footerBtn),
        autoClose ? timerBar : null
    );
    const backdrop = el('div', { className: 'app-panel-backdrop' }, panel);

    const state = { backdrop, onClose, previousFocus: document.activeElement, timer: null, keyHandler: null };

    const keyHandler = (event) => {
        if (event.key === 'Escape') {
            event.preventDefault();
            event.stopImmediatePropagation();
            closePanel();
        } else if (event.key === 'Tab') {
            const items = focusables(panel);
            if (!items.length) return;
            const first = items[0], last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    };
    state.keyHandler = keyHandler;
    // Keep global shortcuts (space, arrows) from firing while the panel has focus
    panel.addEventListener('keydown', (event) => event.stopPropagation());

    closeX.addEventListener('click', closePanel);
    footerBtn.addEventListener('click', closePanel);
    backdrop.addEventListener('click', (event) => { if (event.target === backdrop) closePanel(); });

    if (autoClose) {
        let remaining = AUTO_CLOSE_SECONDS;
        const render = () => {
            countdown.textContent = `Closes in ${remaining} s`;
            timerBar.style.transform = `scaleX(${remaining / AUTO_CLOSE_SECONDS})`;
        };
        render();
        state.timer = setInterval(() => {
            remaining -= 1;
            if (remaining <= 0) closePanel();
            else render();
        }, 1000);

        // Someone is reading or clicking: stop the countdown for good
        const stop = () => {
            clearInterval(state.timer);
            countdown.textContent = '';
            timerBar.remove();
            for (const type of STOP_EVENTS) panel.removeEventListener(type, stop);
        };
        for (const type of STOP_EVENTS) panel.addEventListener(type, stop);
    }

    openPanelState = state;
    document.addEventListener('keydown', keyHandler, true);
    document.body.append(backdrop);
    // Force a style flush so the fade-in runs; rAF never fires in background tabs
    void backdrop.offsetWidth;
    backdrop.classList.add('visible');
    panel.focus({ preventScroll: true });
}

// ---------- What's New ----------

function donationButtons(donations) {
    return el('div', { className: 'app-donate-grid' },
        ...donations.map(d => el('a', { className: 'app-btn', href: d.url, target: '_blank', rel: 'noopener' }, icon(d.icon), d.label)));
}

function whatsNewBody(info) {
    const body = [
        el('div', { className: 'app-panel-section' },
            el('div', { className: 'app-changelog', html: info.changelog_html }),
            el('a', { className: 'app-panel-more', href: info.links.changelog, target: '_blank', rel: 'noopener' },
                'Full changelog', icon('bi-box-arrow-up-right')))
    ];
    if (info.donations?.length) {
        body.push(el('div', { className: 'app-panel-section' },
            el('h3', {}, 'Support SyncLyrics'),
            el('p', {}, 'SyncLyrics is free and made by one person. If you enjoy it, you can support it here.'),
            donationButtons(info.donations)));
    }
    return body;
}

// ---------- Welcome ----------

function shareUrl(info) {
    const host = window.location.hostname;
    const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1';
    // Inside Docker the detected IP is the container's own, which other devices can't reach
    if (isLocal && info.install_type === 'docker') return null;
    if (isLocal && info.lan_ip && info.lan_ip !== '127.0.0.1') {
        const port = window.location.port ? `:${window.location.port}` : '';
        return `${window.location.protocol}//${info.lan_ip}${port}`;
    }
    return window.location.origin;
}

const STATUS_ICONS = { ok: 'bi-check-circle', warn: 'bi-exclamation-triangle', off: 'bi-dash-circle', idle: 'bi-pause-circle' };

function statusRow(row) {
    const action = row.state === 'ok' || row.state === 'idle' ? null
        : row.id === 'now_playing' ? 'Choose a source'
        : row.state === 'off' ? 'Set up' : 'Check';
    return el('li', {},
        el('i', { className: `bi ${STATUS_ICONS[row.state] || 'bi-circle'} status-${row.state}`, 'aria-hidden': 'true' }),
        el('span', { className: 'label' }, row.label),
        el('span', { className: 'value' }, row.value, row.detail && row.id === 'now_playing' ? el('em', {}, ` · ${row.detail}`) : null),
        action ? el('a', { href: `/settings#${row.tab}` }, action) : null
    );
}

function statsNotice(update) {
    if (update.checks_enabled && update.stats_enabled) return 'SyncLyrics checks for updates once a day and sends anonymous usage stats.';
    if (update.checks_enabled) return 'SyncLyrics checks for updates once a day.';
    if (update.stats_enabled) return 'SyncLyrics sends anonymous usage stats once a day.';
    return null;
}

function welcomeBody(info) {
    const statusList = el('ul', { className: 'app-status' }, el('li', {}, el('span', { className: 'value' }, 'Checking…')));
    fetch('/api/app/status', { signal: timeoutSignal(8000) })
        .then(r => r.json())
        .then(data => {
            const rows = (data.rows || []).filter(r => WELCOME_ROWS.includes(r.id));
            statusList.replaceChildren(...rows.map(statusRow));
        })
        .catch(() => statusList.replaceChildren(el('li', {}, el('span', { className: 'value' }, "Couldn't load status. Open Settings to check your sources."))));

    const url = shareUrl(info);
    const copyBtn = el('button', { type: 'button', className: 'app-btn' }, icon('bi-clipboard'), 'Copy link');
    copyBtn.addEventListener('click', () => {
        copyToClipboard(url)
            .then(() => { copyBtn.replaceChildren(icon('bi-check-lg'), 'Copied'); })
            .catch(() => { copyBtn.replaceChildren(icon('bi-x-lg'), "Couldn't copy"); })
            .finally(() => setTimeout(() => copyBtn.replaceChildren(icon('bi-clipboard'), 'Copy link'), 2000));
    });

    const body = [
        el('div', { className: 'app-panel-section' },
            el('h3', {}, 'Getting started'),
            statusList),
        el('div', { className: 'app-panel-section' },
            el('h3', {}, 'Use it on another screen'),
            ...(url
                ? [el('p', {}, 'Open this address on a tablet or phone on the same network.'),
                   el('div', { className: 'app-url-row' }, el('code', {}, url), copyBtn)]
                : [el('p', {}, "On a tablet or phone on the same network, open this computer's network address, for example ",
                      el('code', {}, `http://192.168.1.10:${window.location.port || '9012'}`), '.')])),
        el('div', { className: 'app-panel-section' },
            el('h3', {}, 'Help'),
            el('p', {}, externalLink(info.links.docs, 'Docs'), ' · ', externalLink(info.links.discussions, 'Ask on GitHub Discussions')))
    ];

    const notice = statsNotice(info.update || {});
    if (notice) {
        body.push(el('div', { className: 'app-panel-section' },
            el('p', {}, notice, ' ', externalLink(info.links.usage_stats, "What's sent"), ' · ', el('a', { href: '/settings#updates' }, 'Turn off'))));
    }

    if (info.donations?.length) {
        const links = [];
        info.donations.forEach((d, i) => {
            if (i) links.push(' · ');
            links.push(externalLink(d.url, d.label));
        });
        body.push(el('p', { className: 'app-quiet-line' }, 'Enjoying it? Support SyncLyrics: ', ...links));
    }
    return body;
}

// ---------- Entry ----------

function pickPanel(info) {
    const p = info.panel || {};
    if (p.welcome) return 'welcome';
    if (!info.changelog_html) return null;
    if (p.whats_new_scope === 'browser') {
        return storageGet(BROWSER_SEEN_KEY) !== info.version && p.updated_recently ? 'whats_new' : null;
    }
    return p.whats_new_unseen ? 'whats_new' : null;
}

function markSeen(panel, info) {
    storageSet(BROWSER_SEEN_KEY, info.version);
    fetch('/api/app/panel-seen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ panel })
    }).catch(() => { /* shows again next time; harmless */ });
}

function openAppPanel(panel, info, preview) {
    const onClose = preview ? null : () => markSeen(panel, info);
    if (panel === 'welcome') {
        showPanel({ title: 'Welcome to SyncLyrics', body: welcomeBody(info), footerButton: 'Got it', autoClose: false, onClose });
    } else if (info.changelog_html) {
        showPanel({ title: `What's new in ${info.version}`, body: whatsNewBody(info), footerButton: 'Close', autoClose: true, onClose });
    }
}

export async function initAppPanels() {
    let info;
    try {
        const response = await fetch('/api/app/info', { signal: timeoutSignal(8000) });
        info = await response.json();
    } catch (err) {
        console.warn('[AppPanels] Could not load app info:', err);
        return;
    }

    showUpdateDot(info);

    const params = new URLSearchParams(window.location.search);
    const preview = params.get('preview');
    if (preview === 'whats-new') return openAppPanel('whats_new', info, true);
    if (preview === 'welcome') return openAppPanel('welcome', info, true);

    // Dashboard cards and minimal mode wait for a full-app visit; nothing is marked seen
    if (params.get('minimal') === 'true' || window.self !== window.top) return;

    const panel = pickPanel(info);
    if (panel) openAppPanel(panel, info, false);
}
