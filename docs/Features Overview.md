# Features Overview

SyncLyrics is a synchronized lyrics display application with multiple advanced features. This guide provides an overview - see linked docs for details.

## Core Features

### 🎵 Lyrics Display
Displays synchronized lyrics in a 6-line view with smooth scrolling:
- **Line-sync**: Standard timed lyrics from multiple providers
- **Word-sync**: Karaoke-style word highlighting (see [Word Sync and Karaoke](Word%20Sync%20and%20Karaoke.md))

### 📡 Media Sources
SyncLyrics can get track info from:
- **Spotify API**: Direct Spotify polling (requires API credentials)
- **Windows Media**: System Media Transport Controls (SMTC)
- **Spicetify**: Real-time WebSocket bridge (see [Spicetify Integration](Spicetify%20Integration.md))

### 🎤 Lyrics Providers
Queries multiple providers in parallel for fastest results:
| Provider | Type | Word-Sync |
|----------|------|-----------|
| Spotify | Hosted proxy | ✅ |
| LRCLIB | Community | ❌ |
| Musixmatch | Desktop API | ✅ (RichSync) |
| NetEase | Chinese | ✅ (YRC) |
| QQ Music | Chinese | ❌ |

---

## Visual Features

### 🎨 Background Styles
Four background modes for album art:
- **Sharp**: Full-res album art behind lyrics
- **Soft**: Medium blur for readability
- **Blur**: Heavy blur (classic style)
- **Auto**: Automatically selects based on URL params

See [Visual Modes and Slideshow](Visual%20Modes%20and%20Slideshow.md) for details.

### 🖼️ Slideshow
Artist image cycling with Ken Burns effect:
- Fetches images from Deezer, FanArt.tv, TheAudioDB, Spicetify
- Configurable timing (3-30s)
- Per-artist auto-enable preferences

### 📊 Waveform Seekbar
Visual waveform showing audio loudness over time:
- Requires Spicetify for audio analysis data
- Click/drag to seek

### 🌈 Spectrum Visualizer
Frequency spectrum display:
- Also requires Spicetify audio analysis

---

## ⌨️ Keyboard Shortcuts

Quick controls for desktop users:

| Key | Action |
|---|---|
| `Space` | Play/Pause |
| `←` / `→` | Previous/Next image (slideshow) |
| `Ctrl+←` / `Ctrl+→` | Previous/Next track |
| `S` | Toggle slideshow |
| `F` | Toggle fullscreen |
| `V` | Toggle visual mode |
| `W` | Toggle word-sync |
| `M` | Toggle minimal mode |
| `A` | Toggle art-only mode |
| `Escape` | Exit art-only mode |
| `[` / `]` | Adjust timing ±50ms |

> **Note:** Shortcuts are disabled when typing in input fields.

---

## Advanced Features

### 🎤 Audio Recognition
Shazam-powered song identification for non-Spotify sources:
- **Backend mode**: Captures system audio via loopback
- **Frontend mode**: Uses browser microphone (HTTPS required)
- Reaper DAW integration

See [Audio Recognition](Audio%20Recognition.md) for setup.

### ⚡ Spicetify Integration
Custom extension providing:
- Real-time position updates (~100ms vs 4-5s polling)
- Audio analysis for waveform/spectrum
- Queue including autoplay tracks
- Artist visual images

See [Spicetify Integration](Spicetify%20Integration.md) for installation.

---

## Quick Links

- [Quick Start](Quick%20Start.md) - Get running in 5 minutes
- [FAQ](FAQ.md) - Common questions
- [Configuration Reference](Configuration%20Reference.md) - All settings
- [Docker Reference](Docker%20Reference.md) - Docker/HASS setup
- [Troubleshooting](Troubleshooting.md) - Common issues
- [Development Reference](Development%20Reference.md) - API and architecture
