/**
 * provider.js - Provider Modal & Album Art Selection
 * 
 * This module handles the provider selection modal, album art selection,
 * and instrumental marking functionality.
 * 
 * Level 3 - Imports: state, dom, api, background
 */

import {
    displayConfig,
    lastTrackInfo,
    providerDisplayNames,
    currentArtistImages,
    manualStyleOverride,
    setLastTrackInfo,
    setManualStyleOverride,
    wordSyncEnabled,
    wordSyncProvider,
    hasWordSync
} from './state.js';
import { showToast, setLyricsInDom } from './dom.js';
import { normalizeTrackId } from './utils.js';
import {
    fetchProviders,
    fetchAlbumArtOptions,
    setProviderPreference,
    clearProviderPreference as apiClearProviderPreference,
    setAlbumArtPreference,
    clearAlbumArtPreference as apiClearAlbumArtPreference,
    deleteCachedLyrics as apiDeleteCachedLyrics,
    toggleInstrumentalMark as apiToggleInstrumental,
    saveBackgroundStyle,
    getCurrentTrack
} from './api.js';
import {
    getCurrentBackgroundStyle,
    applyBackgroundStyle,
    updateBackground,
    checkForVisualMode
} from './background.js';
import { updateLatencyDisplay } from './latency.js';
import { songWordSyncOffset } from './state.js';

// ========== PROVIDER DISPLAY ==========

/**
 * Update the provider display badge
 * 
 * Shows the word-sync provider when word-sync is enabled and available,
 * otherwise shows the line-sync provider.
 * 
 * @param {string} providerName - Line-sync provider name (fallback)
 */
export function updateProviderDisplay(providerName) {
    if (!displayConfig.showProvider) return;

    const providerInfo = document.getElementById('provider-info');
    const providerNameEl = document.getElementById('provider-name');

    if (providerInfo && providerNameEl) {
        // Show word-sync provider when word-sync is enabled and available
        // Otherwise fall back to line-sync provider
        let effectiveProvider = providerName;
        if (wordSyncEnabled && hasWordSync && wordSyncProvider) {
            effectiveProvider = wordSyncProvider;
        }
        
        const displayName = providerDisplayNames[effectiveProvider] ||
            effectiveProvider.charAt(0).toUpperCase() + effectiveProvider.slice(1);
        providerNameEl.textContent = displayName;
        providerInfo.classList.remove('hidden');
    }
}

// ========== PROVIDER MODAL ==========

/**
 * Show the provider selection modal
 */
export async function showProviderModal() {
    try {
        const data = await fetchProviders();

        if (data.error) {
            console.error('Cannot show providers:', data.error);
            return;
        }

        const modal = document.getElementById('provider-modal');
        const providerList = document.getElementById('provider-list');

        providerList.innerHTML = '';

        // Build provider list
        data.providers.forEach(provider => {
            const providerItem = document.createElement('div');
            providerItem.className = 'provider-item';
            
            // Determine if this provider is the "effective" current provider
            // When word-sync is enabled, the word-sync provider is the effective one
            const isEffectiveCurrent = wordSyncEnabled && hasWordSync 
                ? provider.is_word_sync_current 
                : provider.is_current;
            
            if (isEffectiveCurrent) {
                providerItem.classList.add('current-provider');
            }

            const displayName = providerDisplayNames[provider.name] ||
                provider.name.charAt(0).toUpperCase() + provider.name.slice(1);

            // Build badge HTML
            let badgeHtml = '';
            if (provider.is_word_sync_current && wordSyncEnabled && hasWordSync) {
                badgeHtml = '<span class="current-badge">Word Source</span>';
            } else if (provider.is_current && !provider.is_word_sync_current) {
                badgeHtml = '<span class="current-badge" style="background: rgba(100, 100, 255, 0.3);">Lyrics Source</span>';
            } else if (provider.is_current) {
                badgeHtml = '<span class="current-badge">Current</span>';
            }

            providerItem.innerHTML = `
                <div class="provider-item-content">
                    <div class="provider-item-header">
                        <span class="provider-item-name">${displayName}${provider.has_word_sync ? ' 🎤' : ''}</span>
                        ${badgeHtml}
                        ${provider.cached ? '<span class="cached-badge">Cached</span>' : ''}
                    </div>
                    <div class="provider-item-meta">
                        Priority: ${provider.priority}${provider.has_word_sync ? ' • Word Sync' : ''}
                    </div>
                </div>
                <button class="provider-select-btn" data-provider="${provider.name}">
                    ${isEffectiveCurrent ? 'Selected' : 'Use This'}
                </button>
            `;

            providerList.appendChild(providerItem);
        });

        // Load album art tab in parallel
        loadAlbumArtTab();

        // Update instrumental button state
        updateInstrumentalButtonState();
        
        // Update latency display with current per-song offset
        updateLatencyDisplay(songWordSyncOffset);

        modal.classList.remove('hidden');

        // Lock body scroll to prevent pull-to-refresh
        document.body.style.overflow = 'hidden';
        document.documentElement.style.overflow = 'hidden';

    } catch (error) {
        console.error('Error loading providers:', error);
    }
}

/**
 * Hide the provider modal
 */
export function hideProviderModal() {
    const modal = document.getElementById('provider-modal');
    if (modal) {
        modal.classList.add('hidden');
    }

    document.body.style.overflow = '';
    document.documentElement.style.overflow = '';
}

/**
 * Select a provider for lyrics
 * 
 * @param {string} providerName - Provider name to select
 */
export async function selectProvider(providerName) {
    try {
        const result = await setProviderPreference(providerName);

        if (result.status === 'success') {
            if (result.lyrics) {
                setLyricsInDom(result.lyrics);
            }
            updateProviderDisplay(result.provider);
            hideProviderModal();

            const displayName = providerDisplayNames[result.provider] || result.provider;
            showToast(`Switched to ${displayName}`);
        } else {
            showToast(`Error: ${result.message}`, 'error');
        }
    } catch (error) {
        console.error('Error selecting provider:', error);
        showToast('Failed to switch provider', 'error');
    }
}

/**
 * Clear provider preference (reset to auto)
 */
export async function clearProviderPreference() {
    try {
        const result = await apiClearProviderPreference();

        if (result.status === 'success') {
            hideProviderModal();
            showToast('Reset to automatic provider selection');
        } else {
            showToast('Failed to reset preference', 'error');
        }
    } catch (error) {
        console.error('Error clearing preference:', error);
        showToast('Failed to reset preference', 'error');
    }
}

// ========== ALBUM ART TAB ==========

/**
 * Load album art options tab
 */
export async function loadAlbumArtTab() {
    try {
        const data = await fetchAlbumArtOptions();

        if (data.error) {
            const grid = document.getElementById('album-art-grid');
            if (grid) {
                grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: rgba(255, 255, 255, 0.5); padding: 40px;">No album art options available yet.</div>';
            }
            return;
        }

        const grid = document.getElementById('album-art-grid');
        if (!grid) return;

        grid.innerHTML = '';

        // Update style buttons
        const styleBtns = document.querySelectorAll('.style-btn');
        const currentStyle = getCurrentBackgroundStyle();
        const isAutoMode = !lastTrackInfo || !lastTrackInfo.background_style;

        styleBtns.forEach(btn => {
            btn.classList.remove('active');
            btn.style.background = '';
            btn.style.borderColor = '';

            if (btn.dataset.style === 'auto' && isAutoMode) {
                btn.classList.add('active');
                btn.style.background = 'rgba(29, 185, 84, 0.3)';
                btn.style.borderColor = 'rgba(29, 185, 84, 0.6)';
            } else if (btn.dataset.style !== 'auto' && btn.dataset.style === currentStyle) {
                btn.classList.add('active');
                btn.style.background = 'rgba(29, 185, 84, 0.3)';
                btn.style.borderColor = 'rgba(29, 185, 84, 0.6)';
            } else {
                btn.style.background = 'rgba(255,255,255,0.1)';
                btn.style.borderColor = 'rgba(255,255,255,0.2)';
            }
        });

        // Build art grid
        data.options.forEach(option => {
            const card = document.createElement('div');
            card.className = 'art-card';
            if (option.is_preferred) {
                card.classList.add('selected');
            }
            card.dataset.provider = option.provider;

            card.innerHTML = `
                <img src="${option.image_url}" alt="${option.provider}" class="art-card-image" loading="lazy" onerror="this.parentElement.classList.add('loading')">
                <div class="art-card-overlay">
                    <div class="art-card-provider">${option.provider}</div>
                    <div class="art-card-resolution">${option.resolution}</div>
                </div>
                ${option.is_preferred ? '<div class="art-card-badge">Selected</div>' : ''}
            `;

            card.addEventListener('click', () => selectAlbumArt(
                option.provider,
                option.url || null,
                option.filename || null,
                option.type || null
            ));

            grid.appendChild(card);
        });

    } catch (error) {
        console.error('Error loading album art options:', error);
        const grid = document.getElementById('album-art-grid');
        if (grid) {
            grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: rgba(255, 255, 255, 0.5); padding: 40px;">Error loading album art options.</div>';
        }
    }
}

/**
 * Load artist images tab
 */
export function loadArtistImagesTab() {
    const grid = document.getElementById('artist-images-grid');
    if (!grid) return;

    grid.innerHTML = '';

    if (!currentArtistImages || currentArtistImages.length === 0) {
        grid.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; color: rgba(255, 255, 255, 0.5); padding: 40px;">No artist images available.</div>';
        return;
    }

    currentArtistImages.forEach((url, index) => {
        const card = document.createElement('div');
        card.className = 'art-card';

        card.innerHTML = `
            <img src="${url}" class="art-card-image" loading="lazy">
            <div class="art-card-overlay">
                <div class="art-card-provider">Image ${index + 1}</div>
            </div>
        `;

        grid.appendChild(card);
    });
}

/**
 * Select album art from provider
 */
export async function selectAlbumArt(providerName, imageUrl = null, filename = null, type = null) {
    try {
        const result = await setAlbumArtPreference(providerName, imageUrl, filename, type);

        if (result.status === 'success') {
            // Force refresh metadata
            const freshTrack = await getCurrentTrack();
            if (freshTrack && !freshTrack.error) {
                setLastTrackInfo(freshTrack);
            }

            // Update UI
            const cards = document.querySelectorAll('.art-card');
            cards.forEach(card => {
                card.classList.remove('selected');
                const badge = card.querySelector('.art-card-badge');
                if (badge) badge.remove();
            });

            const selectedCard = document.querySelector(`.art-card[data-provider="${providerName}"]`);
            if (selectedCard) {
                selectedCard.classList.add('selected');
                if (!selectedCard.querySelector('.art-card-badge')) {
                    selectedCard.insertAdjacentHTML('afterbegin', '<div class="art-card-badge">Selected</div>');
                }
            }

            // Force art refresh
            const albumArt = document.getElementById('album-art');
            if (albumArt) {
                if (result.cache_bust) {
                    const currentSrc = albumArt.src;
                    const baseUrl = currentSrc.split('?')[0];
                    albumArt.src = `${baseUrl}?t=${result.cache_bust}`;
                } else {
                    const currentSrc = albumArt.src;
                    const baseUrl = currentSrc.split('?')[0];
                    albumArt.src = `${baseUrl}?t=${Date.now()}`;
                }

                if (displayConfig.artBackground || displayConfig.softAlbumArt || displayConfig.sharpAlbumArt) {
                    updateBackground();
                }
            }

            const imageType = (type === 'artist_image') ? 'artist image' : 'album art';
            showToast(`Switched to ${providerName} ${imageType}`);

            setTimeout(() => hideProviderModal(), 1000);
        } else {
            showToast(`Error: ${result.error || result.message}`, 'error');
        }
    } catch (error) {
        console.error('Error selecting album art:', error);
        showToast('Failed to switch album art', 'error');
    }
}

/**
 * Clear album art preference
 */
export async function clearAlbumArtPreference() {
    try {
        const result = await apiClearAlbumArtPreference();

        if (result.status === 'success') {
            const freshTrack = await getCurrentTrack();
            if (freshTrack && !freshTrack.error) {
                setLastTrackInfo(freshTrack);
            }

            hideProviderModal();
            showToast('Reset album art preference');

            const albumArt = document.getElementById('album-art');
            if (albumArt) {
                const currentSrc = albumArt.src;
                const baseUrl = currentSrc.split('?')[0];
                albumArt.src = `${baseUrl}?t=${Date.now()}`;
            }
            updateBackground();
        } else {
            showToast('Failed to reset preference', 'error');
        }
    } catch (error) {
        console.error('Error clearing art preference:', error);
        showToast('Failed to reset preference', 'error');
    }
}

/**
 * Delete cached lyrics
 */
export async function deleteCachedLyrics() {
    if (!confirm('Delete all cached lyrics for this song?\n\nThis will remove lyrics from all providers and re-fetch them fresh.')) {
        return;
    }

    try {
        const result = await apiDeleteCachedLyrics();

        if (result.status === 'success') {
            hideProviderModal();
            showToast('Cached lyrics deleted. Re-fetching...');
        } else {
            showToast(result.message || 'Failed to delete lyrics', 'error');
        }
    } catch (error) {
        console.error('Error deleting cached lyrics:', error);
        showToast('Failed to delete cached lyrics', 'error');
    }
}

// ========== INSTRUMENTAL MARKING ==========

/**
 * Update instrumental button state
 */
export async function updateInstrumentalButtonState() {
    const btn = document.getElementById('mark-instrumental-btn');
    if (!btn) return;

    try {
        const trackData = await getCurrentTrack();

        if (trackData.error) {
            btn.disabled = true;
            btn.textContent = '🎵 Instrumental';
            btn.classList.remove('active');
            return;
        }

        const isManual = trackData.is_instrumental_manual === true;

        if (isManual) {
            btn.textContent = '✓ Marked as Instrumental';
            btn.classList.add('active');
        } else {
            btn.textContent = '🎵 Instrumental';
            btn.classList.remove('active');
        }

        btn.disabled = false;
    } catch (error) {
        console.error('Error updating instrumental button state:', error);
    }
}

/**
 * Toggle instrumental mark for current track
 */
export async function toggleInstrumentalMark() {
    const btn = document.getElementById('mark-instrumental-btn');
    if (!btn || btn.disabled) return;

    try {
        const trackData = await getCurrentTrack();

        if (trackData.error || !trackData.artist || !trackData.title) {
            console.error('No track playing or missing info');
            return;
        }

        const currentlyMarked = trackData.is_instrumental_manual === true;
        const newState = !currentlyMarked;

        const result = await apiToggleInstrumental(newState);

        if (result.success) {
            if (newState) {
                btn.textContent = '✓ Marked as Instrumental';
                btn.classList.add('active');
            } else {
                btn.textContent = '🎵 Instrumental';
                btn.classList.remove('active');
            }

            // Force refresh lyrics
            const lyricsResponse = await fetch('/lyrics');
            const lyricsData = await lyricsResponse.json();

            const updatedTrackData = await getCurrentTrack();

            if (updatedTrackData && !updatedTrackData.error) {
                setLastTrackInfo(updatedTrackData);
            }

            if (lyricsData.lyrics && lyricsData.lyrics.length > 0) {
                setLyricsInDom(lyricsData.lyrics);
            } else {
                setLyricsInDom(lyricsData);
            }

            lyricsData.is_instrumental_manual = updatedTrackData.is_instrumental_manual === true;
            lyricsData.is_instrumental = updatedTrackData.is_instrumental_manual === true || lyricsData.is_instrumental;

            const trackId = updatedTrackData.track_id || normalizeTrackId(
                updatedTrackData.artist || "",
                updatedTrackData.title || ""
            );
            checkForVisualMode(lyricsData, trackId);
        } else {
            console.error('Failed to mark instrumental:', result.error);
        }
    } catch (error) {
        console.error('Error toggling instrumental mark:', error);
    }
}

// ========== PROVIDER UI SETUP ==========

/**
 * Setup provider UI event handlers
 */
export function setupProviderUI() {
    // Provider badge click handler
    const providerBadge = document.getElementById('provider-badge');
    if (providerBadge) {
        providerBadge.addEventListener('click', showProviderModal);
    }

    // Modal close handlers
    const modalClose = document.getElementById('provider-modal-close');
    if (modalClose) {
        modalClose.addEventListener('click', hideProviderModal);
    }

    // Tab switching
    const tabs = document.querySelectorAll('.provider-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const tabName = tab.dataset.tab;

            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            const contents = document.querySelectorAll('.provider-tab-content');
            contents.forEach(content => content.classList.remove('active'));

            const activeContent = document.getElementById(`provider-tab-content-${tabName}`);
            if (activeContent) {
                activeContent.classList.add('active');
            }

            if (tabName === 'album-art') {
                loadAlbumArtTab();
            } else if (tabName === 'artist-images') {
                loadArtistImagesTab();
            }
        });
    });

    const modal = document.getElementById('provider-modal');
    if (modal) {
        // Modal backdrop click handler
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                hideProviderModal();
            }
        });

        // Style button click handler (event delegation)
        modal.addEventListener('click', async (e) => {
            const styleBtn = e.target.closest('.style-btn');
            if (!styleBtn) return;

            const style = styleBtn.dataset.style;

            if (style === 'auto') {
                try {
                    const response = await saveBackgroundStyle('none');
                    if (response.status === 'success') {
                        showToast('Cleared saved preference - using URL parameters');
                        if (lastTrackInfo) {
                            delete lastTrackInfo.background_style;
                        }
                    }
                } catch (err) {
                    console.error('Error clearing style:', err);
                }

                setManualStyleOverride(false);

                const urlParams = new URLSearchParams(window.location.search);
                if (urlParams.has('sharpAlbumArt') && urlParams.get('sharpAlbumArt') === 'true') {
                    applyBackgroundStyle('sharp');
                } else if (urlParams.has('softAlbumArt') && urlParams.get('softAlbumArt') === 'true') {
                    applyBackgroundStyle('soft');
                } else if (urlParams.has('artBackground') && urlParams.get('artBackground') === 'true') {
                    applyBackgroundStyle('blur');
                } else {
                    applyBackgroundStyle('none');
                }
            } else {
                applyBackgroundStyle(style);
                setManualStyleOverride(true);

                try {
                    const response = await saveBackgroundStyle(style);
                    if (response.status === 'success') {
                        showToast(`Saved preference: ${style}`);
                    } else {
                        showToast(`Error: ${response.error || 'Failed to save'}`, 'error');
                    }
                } catch (err) {
                    console.error('Error saving style:', err);
                    showToast('Failed to save style preference', 'error');
                }
            }

            // Update UI
            const styleBtns = document.querySelectorAll('.style-btn');
            styleBtns.forEach(b => {
                b.style.background = 'rgba(255,255,255,0.1)';
                b.style.borderColor = 'rgba(255,255,255,0.2)';
                b.classList.remove('active');
            });
            styleBtn.style.background = 'rgba(29, 185, 84, 0.3)';
            styleBtn.style.borderColor = 'rgba(29, 185, 84, 0.6)';
            styleBtn.classList.add('active');
        });
    }

    // Clear preference button
    const clearBtn = document.getElementById('provider-clear-preference');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearProviderPreference);
    }

    // Clear album art preference button
    const clearArtBtn = document.getElementById('album-art-clear-preference');
    if (clearArtBtn) {
        clearArtBtn.addEventListener('click', clearAlbumArtPreference);
    }

    // Delete cached lyrics button
    const deleteBtn = document.getElementById('lyrics-delete-cache');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', deleteCachedLyrics);
    }

    // Mark as Instrumental button
    const instrumentalBtn = document.getElementById('mark-instrumental-btn');
    if (instrumentalBtn) {
        instrumentalBtn.addEventListener('click', toggleInstrumentalMark);
    }
    
    // Reload Settings button
    const reloadBtn = document.getElementById('reload-settings-btn');
    if (reloadBtn) {
        reloadBtn.addEventListener('click', async () => {
            try {
                const response = await fetch('/api/settings/reload', { method: 'POST' });
                const result = await response.json();
                if (result.success) {
                    showToast('Settings reloaded');
                } else {
                    showToast('Failed to reload settings', 'error');
                }
            } catch (error) {
                console.error('Error reloading settings:', error);
                showToast('Error reloading settings', 'error');
            }
        });
    }

    // Provider selection (event delegation)
    const providerList = document.getElementById('provider-list');
    if (providerList) {
        providerList.addEventListener('click', (e) => {
            if (e.target.classList.contains('provider-select-btn')) {
                const providerName = e.target.getAttribute('data-provider');
                selectProvider(providerName);
            }
        });
    }
}

// ========== STYLE BUTTONS IN MODAL ==========

/**
 * Update style buttons in modal to show current selection
 * 
 * @param {string} currentStyle - Current style
 */
export function updateStyleButtonsInModal(currentStyle) {
    document.querySelectorAll('.style-btn').forEach(btn => {
        if (btn.dataset.style === currentStyle) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
}
