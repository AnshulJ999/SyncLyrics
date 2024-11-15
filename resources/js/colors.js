// Color extraction and management
let lastAlbumArt = null;
let currentSwatch = "DarkVibrant";
const swatches = ["Vibrant", "DarkVibrant", "LightVibrant", "Muted", "DarkMuted", "LightMuted"];

// Add swatch cycler button (hidden in minimal mode)
function addSwatchCycler() {
    const cycler = document.createElement('button');
    cycler.id = 'swatch-cycler';
    cycler.style.cssText = `
        position: fixed;
        top: 10px;
        right: 10px;
        background: rgba(255,255,255,0.1);
        border: none;
        padding: 5px 10px;
        color: white;
        border-radius: 4px;
        cursor: pointer;
        z-index: 1000;
        opacity: ${document.body.hasAttribute('data-minimal') ? '0' : '1'};
    `;
    cycler.textContent = currentSwatch;
    
    cycler.onclick = async () => {
        const currentIndex = swatches.indexOf(currentSwatch);
        currentSwatch = swatches[(currentIndex + 1) % swatches.length];
        cycler.textContent = currentSwatch;
        
        // Re-extract colors if we have a last album art
        if (lastAlbumArt) {
            const colors = await extractColors(lastAlbumArt);
            if (colors) {
                updateBackgroundColors(colors);
            }
        }
    };
    
    document.body.appendChild(cycler);
    return cycler;
}

// Extract colors from album art
async function extractColors(albumArtUrl) {
    if (!albumArtUrl) return null;
    lastAlbumArt = albumArtUrl;
    
    try {
        const palette = await Vibrant.from(albumArtUrl).getPalette();
        console.log('Extracted palette:', palette);
        
        // Get primary and secondary colors based on current swatch
        const primary = palette[currentSwatch];
        const secondary = palette[currentSwatch === 'Vibrant' ? 'DarkVibrant' : 'Vibrant'];
        
        if (!primary || !secondary) {
            console.warn('Could not extract colors, using fallback');
            return ["#24273a", "#363b54"];  // Use hardcoded fallback
        }
        
        return [primary.getHex(), secondary.getHex()];
    } catch (error) {
        console.error('Color extraction failed:', error);
        return ["#24273a", "#363b54"];  // Use hardcoded fallback
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    // Only add cycler if not in minimal mode
    if (!document.body.hasAttribute('data-minimal')) {
        const cycler = addSwatchCycler();
    }
});