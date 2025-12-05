/**
 * slideshow.js - Slideshow Functionality
 * 
 * This module handles the slideshow logic for visual mode and idle mode.
 * Kept separate for easy future development.
 * 
 * NOTE: Slideshow is currently DISABLED per user request (Dec 1, 2025).
 * The start function returns early to disable slideshow behavior.
 * 
 * Level 2 - Imports: state
 */

import {
    visualModeConfig,
    lastTrackInfo,
    currentArtistImages,
    dashboardImages,
    slideshowInterval,
    currentSlideIndex,
    setSlideshowInterval,
    setCurrentSlideIndex
} from './state.js';

// ========== SLIDESHOW CONTROL ==========

/**
 * Start slideshow - cycle through images
 * 
 * @param {string} source - 'artist' (for Visual Mode) or 'dashboard' (for Idle Mode)
 */
export function startSlideshow(source = 'artist') {
    // SLIDESHOW DISABLED (User Request: Dec 1, 2025)
    // Completely disabled for now as it was "utterly broken"
    // Comment out this return to re-enable
    return;

    if (slideshowInterval) {
        clearInterval(slideshowInterval);
    }

    let images = [];
    let includeAlbumArt = false;

    if (source === 'artist') {
        images = currentArtistImages;
        includeAlbumArt = (lastTrackInfo && lastTrackInfo.album_art_url);
    } else {
        images = dashboardImages;
        includeAlbumArt = false;
    }

    const totalSlides = images.length + (includeAlbumArt ? 1 : 0);

    if (totalSlides === 0) {
        console.log(`Slideshow: No images available for ${source} source.`);
        return;
    }

    setCurrentSlideIndex(0);

    const renderCurrentSlide = () => {
        let imageUrl;
        if (includeAlbumArt && currentSlideIndex === images.length) {
            imageUrl = lastTrackInfo.album_art_url;
        } else {
            const safeIndex = currentSlideIndex % images.length;
            imageUrl = images[safeIndex];
        }

        if (imageUrl) {
            showSlide(imageUrl);
        }
    };

    // Show first image immediately
    renderCurrentSlide();

    // Then cycle through images
    const intervalMs = visualModeConfig.slideshowIntervalSeconds * 1000;
    const interval = setInterval(() => {
        const currentTotal = images.length + (includeAlbumArt ? 1 : 0);
        if (currentTotal > 0) {
            setCurrentSlideIndex((currentSlideIndex + 1) % currentTotal);
            renderCurrentSlide();
        }
    }, intervalMs);

    setSlideshowInterval(interval);
}

/**
 * Stop slideshow
 */
export function stopSlideshow() {
    if (slideshowInterval) {
        clearInterval(slideshowInterval);
        setSlideshowInterval(null);
    }

    // Clear slideshow images
    const bgContainer = document.getElementById('background-layer');
    if (bgContainer) {
        const slideshowImages = bgContainer.querySelectorAll('.slideshow-image');
        slideshowImages.forEach(img => img.remove());
    }
}

/**
 * Show a specific slide in the slideshow
 * 
 * @param {string} imageUrl - URL of the image to show
 */
export function showSlide(imageUrl) {
    const bgContainer = document.getElementById('background-layer');
    if (!bgContainer || !imageUrl) return;

    // Create new image element for crossfade
    const newImg = document.createElement('div');
    newImg.className = 'slideshow-image';
    newImg.style.backgroundImage = `url("${imageUrl}")`;

    // Add Ken Burns animation class
    newImg.classList.add('ken-burns-effect');

    bgContainer.appendChild(newImg);

    // Fade in new image
    setTimeout(() => {
        newImg.classList.add('active');
    }, 50);

    // DOM CLEANUP: Remove old images after transition
    setTimeout(() => {
        const oldImages = bgContainer.querySelectorAll('.slideshow-image:not(.active)');
        oldImages.forEach(img => {
            if (img !== newImg) {
                img.remove();
            }
        });
    }, 2000);
}
