// State management
let lastLyrics = null;
let updateInProgress = false;
let currentColors = ["#24273a", "#363b54"];
let isMinimalMode = false;
let currentTheme = null;

// Utility functions
async function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function applyTheme(uiSettings, albumArtUrl = null) {
    // Don't reapply the same theme
    const themeString = JSON.stringify({ uiSettings, albumArtUrl });
    if (themeString === currentTheme) return;
    currentTheme = themeString;

    const root = document.documentElement;
    
    try {
        // Apply background style
        switch (uiSettings.backgroundStyle) {
            case 'albumart':
                if (albumArtUrl) {
                    const opacity = Math.max(0, Math.min(100, uiSettings.albumArt.opacity));
                    const blur = Math.max(0, Math.min(20, uiSettings.albumArt.blur));
                    
                    document.body.style.background = `
                        linear-gradient(
                            rgba(0, 0, 0, ${1 - opacity / 100}),
                            rgba(0, 0, 0, ${1 - opacity / 100})
                        ),
                        url('${albumArtUrl}')
                    `;
                    document.body.style.backgroundSize = 'cover';
                    document.body.style.backgroundPosition = 'center';
                    document.body.style.backdropFilter = `blur(${blur}px)`;
                }
                break;
                
            case 'gradient':
                const { bgStart, bgEnd } = uiSettings.customColors;
                if (bgStart && bgEnd) {
                    document.body.style.background = `linear-gradient(135deg, ${bgStart} 0%, ${bgEnd} 100%)`;
                }
                break;
                
            case 'solid':
                if (uiSettings.customColors.bgStart) {
                    document.body.style.background = uiSettings.customColors.bgStart;
                }
                break;
        }

        // Apply text color
        if (uiSettings.customColors.text) {
            root.style.setProperty('--text-color', uiSettings.customColors.text);
        }
        
        // Apply animation style
        const lyrics = document.querySelectorAll('.lyric-line');
        const validAnimations = ['wave', 'fade', 'slide'];
        
        lyrics.forEach(line => {
            validAnimations.forEach(anim => line.classList.remove(anim));
            if (uiSettings.animationStyle !== 'none' && 
                validAnimations.includes(uiSettings.animationStyle)) {
                line.classList.add(uiSettings.animationStyle);
            }
        });
    } catch (error) {
        console.error('Error applying theme:', error);
    }
}

async function getLyrics() {
    try {
        const params = new URLSearchParams(window.location.search);
        const minimal = params.get('minimal') === 'true';
        
        const response = await fetch(`/lyrics?minimal=${minimal}`);
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        
        const data = await response.json();
        
        // Apply theme based on settings and album art
        if (data.uiSettings) {
            await applyTheme(data.uiSettings, data.albumArt);
        } else {
            // Fallback to basic colors if no settings
            updateBackgroundColors(data.colors);
        }
        
        return {
            lyrics: data.lyrics || data,
            provider: data.provider,
            minimal: minimal
        };
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
    if (!colors || !Array.isArray(colors) || colors.length < 2) return;
    
    const [start, end] = colors;
    if (!start || !end) return;
    
    document.body.style.background = `linear-gradient(135deg, ${start} 0%, ${end} 100%)`;
    document.body.style.transition = 'background 1s ease-in-out';
}

function updateLyricElement(element, text) {
    if (!element) return;
    if (element.textContent !== text) {
        element.textContent = text || '-';
    }
}

function setLyricsInDom(lyrics, providerInfo, isMinimal) {
    if (updateInProgress) return;
    
    // Ensure lyrics is an array
    if (!Array.isArray(lyrics)) {
        lyrics = ['', '', lyrics.msg || 'No lyrics available', '', '', ''];
    }

    // Only update if lyrics have changed
    if (!areLyricsDifferent(lastLyrics, lyrics)) {
        return;
    }

    updateInProgress = true;
    lastLyrics = [...lyrics];

    // Update all elements simultaneously
    const elements = ['prev-2', 'prev-1', 'current', 'next-1', 'next-2', 'next-3'];
    elements.forEach((id, index) => {
        updateLyricElement(document.getElementById(id), lyrics[index]);
    });

    // Update provider info and minimal mode elements
    updateUIElements(providerInfo, isMinimal);

    setTimeout(() => {
        updateInProgress = false;
    }, 800);
}

function updateUIElements(providerInfo, isMinimal) {
    const providerEl = document.getElementById('provider-info');
    const minimalToggle = document.getElementById('minimal-toggle');

    if (!providerEl || !minimalToggle) return;

    if (isMinimal) {
        providerEl.style.display = 'none';
        minimalToggle.style.display = 'none';
    } else {
        if (providerInfo) {
            const providerName = providerEl.querySelector('.provider-name');
            const providerStats = providerEl.querySelector('.provider-stats');
            
            if (providerName) {
                providerName.textContent = `Lyrics by ${providerInfo.name}`;
            }
            
            if (providerStats && providerInfo.stats) {
                const responseTime = Math.round(providerInfo.stats.avg_response_time || 0);
                providerStats.textContent = responseTime ? `${responseTime}ms` : '';
            }
            
            providerEl.style.display = 'flex';
        }
        
        minimalToggle.style.display = 'block';
    }
}

function toggleMinimalMode() {
    const urlParams = new URLSearchParams(window.location.search);
    const currentMinimal = urlParams.get('minimal') === 'true';
    
    // Toggle the minimal parameter
    urlParams.set('minimal', (!currentMinimal).toString());
    
    // Update URL without reloading
    window.history.replaceState({}, '', `${window.location.pathname}?${urlParams}`);
    
    // Update body attribute and UI elements
    document.body.setAttribute('data-minimal', (!currentMinimal).toString());
    updateUIElements(null, !currentMinimal);
}

async function main() {
    try {
        // Set initial minimal mode from URL
        const urlParams = new URLSearchParams(window.location.search);
        isMinimalMode = urlParams.get('minimal') === 'true';
        document.body.setAttribute('data-minimal', isMinimalMode.toString());
        
        // Initial visibility update
        updateUIElements(null, isMinimalMode);
        
        // Main loop
        while (true) {
            const response = await getLyrics();
            if (response) {
                setLyricsInDom(response.lyrics, response.provider, response.minimal);
            }
            await sleep(400);
        }
    } catch (error) {
        console.error('Error in main loop:', error);
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', main);