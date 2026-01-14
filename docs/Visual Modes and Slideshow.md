# Visual Modes and Slideshow

SyncLyrics offers several visual modes to enhance your lyrics display experience.

## Background Styles

Control how album art appears behind lyrics:

| Style | Effect | Best For |
|-------|--------|----------|
| **Sharp** | Full-res art | Album art appreciation |
| **Soft** | Medium blur | Readability + aesthetics |
| **Blur** | Heavy blur | Maximum readability |
| **Auto** | Soft by default, uses URL params | General use |

### Changing Background Style

## URL Params: 

You can use URL parameters to change the background style. But the easier way is to use the settings icon in the top right which shows you 'Display Options'. Simply select your preferred style and it will be applied to all tracks.

The URL will auto-update, but you can copy it and save it for reuse. 

## Album-wise Preferences

It is possible to save the background style as an album-wise preference. This is useful if you have a favorite style for a specific album art. For example, a certain album can always be 'Sharp' or always be 'Blurry' if you so prefer.

1. Click the **provider badge** (lyrics source) in the header
2. Go to **Album Art & Images** tab
3. Select your preferred style

It will automatically be saved. The next time you listen to that album; the saved style will auto-apply. 

### Fill Modes

Control how images fit the screen:
- **Cover**: Fill screen, may crop edges
- **Contain**: Full image visible, may have bars
- **Stretch**: Distorts to fill (not recommended)
- **Original**: Centered at native size (can be very zoomed in for 4K images)

This preference is saved to the local storage on your device; it will not be synced to other devices. Hence you can use different settings for each device. For example, Cover on a tablet, and Contain on a mobile. 

---

## Visual Mode

Visual mode hides lyrics and shows the album art prominently. Useful during instrumentals or when you just want the visuals.

### Triggering Visual Mode

- **Automatic**: Enters during instrumental sections (detected by ♪ markers)
- **Manual**: Click the **visual mode button** (♩) in the bottom-left corner
- **Long-press**: Hold the visual mode button (♩) to enter art-only mode

### Art-Only Mode

A variant where only the album art is shown:

- Long-press corners to exit. 
- Supports pinch-to-zoom on touch devices. You can pan and zoom images.

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
Place images directly in the artist's folder:
```
album_art_database/[Artist Name]/custom_*.jpg
or
album_art_database/[Artist Name]/your_image.jpg
```

Any `.jpg`, `.png`, `.webp`, or `.gif` file is auto-discovered and added to the rotation. Names don't matter, but simple short names will be better to avoid issues. 

Standard naming like 'Custom1' and 'Custom2' is recommended.

> **Tip**: The filename (without extension) becomes the label in the image selection UI. Use descriptive names like `Concert_2024.jpg` or `Album_Promo.jpg` rather than `image1.jpg`.

### Album-Specific Art
For specific album covers (not slideshow images), use the album folder:
```
album_art_database/[Artist Name] - [Album Name]/cover.jpg
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
