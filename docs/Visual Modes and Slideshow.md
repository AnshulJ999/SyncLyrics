# Visual Modes and Slideshow

SyncLyrics offers several visual modes to enhance your lyrics display experience.

## Background Styles

Control how album art appears behind lyrics:

| Style | Effect | Best For |
|-------|--------|----------|
| **Sharp** | Full-res art, slight dim | Album art appreciation |
| **Soft** | Medium blur | Readability + aesthetics |
| **Blur** | Heavy blur | Maximum readability |
| **Auto** | Adapts to image | General use |

### Changing Background Style
1. Click the **provider badge** (lyrics source) in the header
2. Go to **Album Art & Images** tab
3. Select your preferred style

### Fill Modes
Control how images fit the screen:
- **Cover**: Fill screen, may crop edges
- **Contain**: Full image visible, may have bars
- **Stretch**: Distorts to fill (not recommended)
- **Original**: Centered at native size

---

## Visual Mode

Visual mode hides lyrics and shows the album art prominently. Useful during instrumentals or when you just want the visuals.

### Triggering Visual Mode
- **Automatic**: Enters during instrumental sections (detected by ♪ markers)
- **Manual**: Click the **music note icon** (🎵) in the bottom-left
- **Long-press**: Hold the album art to enter art-only mode

### Art-Only Mode
A variant where only the album art is shown:
- Triple-tap anywhere to exit
- Supports pinch-to-zoom on touch devices

---

## Slideshow

Cycles through artist images with subtle Ken Burns animation.

### Enabling Slideshow
1. Click the **film slate icon** (next to word-sync toggle)
2. Or long-press the slideshow icon to open the control center

### Slideshow Control Center
Long-press the slideshow button to access:

**Timing**: 3s, 6s, 9s, 15s, 30s, or custom intervals

**Effects**:
- **Shuffle**: Random image order
- **Ken Burns**: Subtle zoom/pan animation

**Intensity** (when Ken Burns is on):
- Subtle, Medium, or Cinematic

**Auto-Enable** (per-artist):
- Default: Use global setting
- Always: Auto-enable for this artist
- Never: Never auto-enable

**Image Selection**:
- View all available images in a grid
- Click to include/exclude from rotation
- Filter by provider or favorites

### Edge Tap Cycling
When slideshow is active, tap the left/right edges of the screen to manually cycle images.

### Image Sources
Images are fetched from:
- **Spicetify** (if connected): Artist gallery via GraphQL
- **Deezer**: Artist images
- **FanArt.tv**: High-quality fan art (requires API key)
- **TheAudioDB**: Backup source

---

## Album Art Database

Album art is cached locally for faster loading:
- Sources: iTunes, Spotify, Last.fm
- Enhanced resolution: Spotify images upgraded from 640px to 1400px when available
- Dominant colors extracted for background effects

### Adding Custom Images
Place images in the artist's folder:
```
album_art_database/[Artist Name]/custom_*.jpg
```

---

## Troubleshooting

### Background not changing
- Verify a background style is selected (not disabled)
- Check if "Use Album Colors" accidentally overriding

### Slideshow not showing images
- Artist may not have images available
- Check API keys for FanArt.tv/TheAudioDB in settings
- Enable Spicetify for additional image sources

### Ken Burns animation stuttering
- Normal on lower-end devices
- Try reducing intensity to "Subtle"
