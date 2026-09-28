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
- **Spicetify**: Real-time WebSocket bridge (see [Spicetify Integration](Spicetify%20Integration.md))
- **Windows Media**: System Media Transport Controls (SMTC)
- **Linux / macOS**: MPRIS and Now Playing (see [Linux Support](Linux%20Support.md), [macOS Support](macOS%20Support.md))
- **Music Assistant**: Any Music Assistant player, including music started from other apps like the Sonos app or AirPlay (see [Music Assistant](Music%20Assistant.md))
- **Pear Desktop** (YouTube Music), **tidal-hifi** (TIDAL) and **smtc-now-playing** (any Windows player over the network)
- **Now Playing Input**: phones, Home Assistant or your own scripts send what's playing (see [Now Playing Input](Now%20Playing%20Input.md))
- **Audio Recognition**: Shazam identifies whatever is playing nearby (see [Audio Recognition](Audio%20Recognition.md))

### 🎤 Lyrics Providers
Queries multiple providers in parallel for fastest results:
| Provider | Type | Word-Sync |
|----------|------|-----------|
| Spotify | Hosted proxy | ❌ |
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

### 📚 Media Browser
Embedded library browser accessible via the Spotify button:
- Browse Spotify playlists, albums, and artists
- Music Assistant library support (if configured)
- Toggle between sources without leaving the lyrics view
- Selected tracks play on your active device

### 🔔 Updates and What's New
- Settings opens on an **Overview** page: your version, what's new, a quick status check and how to update
- A small dot on the settings icon means a new version is out
- After an update, a **What's New** panel shows once
- The daily update check and anonymous usage stats can be turned off (see [Usage Stats](Usage%20Stats.md))

---

## Quick Links

- [Quick Start](Quick%20Start.md) - Get running in 5 minutes
- [FAQ](FAQ.md) - Common questions
- [Configuration Reference](Configuration%20Reference.md) - All settings
- [Docker Reference](Docker%20Reference.md) - Docker/HASS setup
- [Troubleshooting](Troubleshooting.md) - Common issues
- [Development Reference](Development%20Reference.md) - API and architecture
