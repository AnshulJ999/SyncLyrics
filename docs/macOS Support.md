# macOS Support

SyncLyrics natively supports macOS through the **MediaRemote** framework using `nowplaying-cli`, with an AppleScript fallback for Music.app and Spotify.

## Requirements

- macOS 13 (Ventura) or later
- `nowplaying-cli` installed (optional, but recommended)

## Installation

### With Homebrew (Recommended)

Install `nowplaying-cli` for universal media player support:

```bash
brew install nowplaying-cli
```

This enables SyncLyrics to detect media from **any app** that reports to Control Center.

### Without Homebrew

SyncLyrics will automatically fall back to AppleScript, which supports:
- ✅ Apple Music
- ✅ Spotify

Other apps (Firefox, VLC, etc.) require `nowplaying-cli`.

## Supported Players

### With nowplaying-cli (All Control Center apps)

- Apple Music
- Spotify
- Firefox / Safari (audio/video)
- VLC
- IINA
- YouTube (in browser)
- Any app that shows in Control Center's Now Playing widget

### Without nowplaying-cli (AppleScript only)

- Apple Music
- Spotify

## Features

| Feature | nowplaying-cli | AppleScript |
|---------|----------------|-------------|
| Track metadata (artist, title, album) | ✅ | ✅ |
| Playback position | ✅ | ✅ |
| Duration | ✅ | ✅ |
| Play/Pause control | ✅ | ✅ |
| Next/Previous track | ✅ | ✅ |
| Seek to position | ✅ | ✅ |
| Works with all players | ✅ | ❌ |
| Auto-enrichment (colors, artist images) | ✅ | ✅ |

## Configuration

The macOS source is **enabled by default** on macOS systems.

### Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `media_source.macos.enabled` | `true` | Enable/disable macOS source |
| `media_source.macos.priority` | `1` | Priority (lower = higher priority) |
| `system.macos.paused_timeout` | `600` | Seconds before paused source expires (0 = never) |
| `lyrics.display.macos_latency_compensation` | `0.0` | Sync offset in seconds (+early, -late) |

## Troubleshooting

### nowplaying-cli not found

If only Music.app and Spotify are detected:

```bash
# Install via Homebrew
brew install nowplaying-cli

# Verify installation
nowplaying-cli get title
```

### No metadata appearing

1. Ensure music is playing
2. Check that the app appears in Control Center's Now Playing widget
3. Try the command manually:
   ```bash
   nowplaying-cli get title artist album
   ```

### AppleScript permissions

On first run, macOS may ask for permission to control Music.app or Spotify. Click **OK** to allow.

If denied, go to **System Preferences → Privacy & Security → Automation** and enable access.

### Private Framework Warning

> **Note:** `nowplaying-cli` uses Apple's private `MediaRemote` framework. While tested on Ventura 13.x and Sonoma 14.x, it may break on future macOS versions. The AppleScript fallback provides a stable alternative for Music.app and Spotify.

## Running SyncLyrics

### Download Pre-built Binary

Download from [GitHub Releases](https://github.com/AnshulJ999/SyncLyrics/releases):
- `SyncLyrics-vX.X.X-macos-x64.zip` (Intel Macs)
- `SyncLyrics-vX.X.X-macos-arm64.zip` (Apple Silicon)

### First Run (Gatekeeper)

macOS builds are unsigned. On first run:

1. Right-click the app and select **Open**
2. Click **Open** in the dialog
3. Or run from Terminal:
   ```bash
   xattr -cr /path/to/SyncLyrics
   ./SyncLyrics/sync_lyrics
   ```

### From Source

```bash
# Clone and install dependencies
git clone https://github.com/AnshulJ999/SyncLyrics.git
cd SyncLyrics
pip install -r requirements.txt

# Run
python sync_lyrics.py
```

## Latency Tuning

If lyrics appear early or late, adjust the macOS latency compensation:

1. Open Settings in SyncLyrics web UI
2. Find **macOS Latency** under Lyrics section
3. Adjust value:
   - Positive values (+0.5) = lyrics appear earlier
   - Negative values (-0.5) = lyrics appear later
