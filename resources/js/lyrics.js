let lastLyrics = null;
let updateInProgress = false;
let currentColors = ["#24273a", "#363b54"];
let updateInterval = 100; // Default value, will be updated from config

async function getConfig() {
    try {
        const response = await fetch('/config');
        const config = await response.json();
        updateInterval = config.updateInterval;
        console.log(`Update interval set to: ${updateInterval}ms`);  // Debug log
    } catch (error) {
        console.error('Error fetching config:', error);
    }
}

async function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function getLyrics() {
    try {
        let response = await fetch('/lyrics');
        let data = await response.json();
        
        // Update background if colors are present
        if (data.colors && (data.colors[0] !== currentColors[0] || data.colors[1] !== currentColors[1])) {
            updateBackgroundColors(data.colors);
            currentColors = data.colors;
        }
        
        return data.lyrics || data;
    } catch (error) {
        console.error('Error fetching lyrics:', error);
        return null;
    }
}

function areLyricsDifferent(oldLyrics, newLyrics) {
    if (!oldLyrics || !newLyrics) return true;
    if (!Array.isArray(oldLyrics) || !Array.isArray(newLyrics)) return true;
    return JSON.stringify(oldLyrics) !== JSON.stringify(newLyrics);
}

function updateBackgroundColors(colors) {
    if (!colors || !Array.isArray(colors)) return;
    
    document.body.style.background = `linear-gradient(135deg, ${colors[0]} 0%, ${colors[1]} 100%)`;
    
    // Add subtle animation
    document.body.style.transition = 'background 1s ease-in-out';
}

function updateLyricElement(element, text) {
    if (element && element.textContent !== text) {
        element.textContent = text;
    }
}

function setLyricsInDom(lyrics) {
    if (updateInProgress) return;
    if (!Array.isArray(lyrics)) {
        lyrics = ['', '', lyrics.msg, '', '', ''];
    }

    // Only update if lyrics have changed
    if (!areLyricsDifferent(lastLyrics, lyrics)) {
        return;
    }

    updateInProgress = true;
    lastLyrics = [...lyrics];

    // Update all elements simultaneously
    updateLyricElement(document.getElementById('prev-2'), lyrics[0]);
    updateLyricElement(document.getElementById('prev-1'), lyrics[1]);
    updateLyricElement(document.getElementById('current'), lyrics[2]);
    updateLyricElement(document.getElementById('next-1'), lyrics[3]);
    updateLyricElement(document.getElementById('next-2'), lyrics[4]);
    updateLyricElement(document.getElementById('next-3'), lyrics[5]);

    // Add smooth scroll to current line
    const currentLine = document.getElementById('current');
    const lyricsContainer = document.getElementById('lyrics');
    if (currentLine && document.body.hasAttribute('data-scroll')) {
        currentLine.scrollIntoView({ 
            behavior: 'smooth', 
            block: 'center'
        });
    }

    setTimeout(() => {
        updateInProgress = false;
    }, 800);
}

async function main() {

    // Get configuration first
    await getConfig();

    // Check URL parameters for scroll setting
    const params = new URLSearchParams(window.location.search);
    const shouldScroll = params.get('scroll') === 'true';
    
    console.log('Scroll enabled:', shouldScroll); // Debug log

    // Enable scrolling if requested
    if (shouldScroll) {
        document.body.setAttribute('data-scroll', 'true');
        const lyricsContainer = document.getElementById('lyrics');
        if (lyricsContainer) {
            lyricsContainer.setAttribute('data-scroll', 'true');
            console.log('Scroll attributes set on container'); // Debug log
            // Also set it on all lyric lines
            document.querySelectorAll('.lyric-line').forEach(line => {
                line.setAttribute('data-scroll', 'true');
            });
        }
    }

    // Set initial background
    document.body.style.background = `linear-gradient(135deg, ${currentColors[0]} 0%, ${currentColors[1]} 100%)`;
    
    while(true) {
        let lyrics = await getLyrics();
        if (lyrics) {
            setLyricsInDom(lyrics);
        }
        await sleep(updateInterval); // Use the configured interval
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', main);