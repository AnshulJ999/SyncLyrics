# Changelog

All notable changes to SyncLyrics will be documented in this file.

## [2.0.0] - 2026-01-17

### ✨ New Features

#### Media Browser
- **Embedded library browser** for Spotify and Music Assistant directly in the app
- Browse playlists, albums, and artists without leaving the lyrics view
- Toggle between Spotify and Music Assistant libraries with a single click
- Auto-authentication for Music Assistant browser

#### Playback Controls
- **Volume control slider** with system integration
- **Device picker** - switch playback between devices (Spotify Connect, MA players)
- **Shuffle and repeat controls** with state sync across all sources
- Shuffle/repeat state now properly propagates from all backends (Spotify, MA, Windows, Linux, macOS)

#### Music Assistant Integration
- Full Music Assistant support as an audio source
- Device picker integration for MA players
- WebSocket connection for real-time updates
- Configurable latency compensation for network streaming

#### Visual Enhancements
- **Album name display** - optionally show album name on the main UI
- Improved art mode and visual mode styling
- Better slideshow controls and preferences

#### Audio Source Improvements
- **Idle state display** - shows "Idle" instead of last source when no music playing
- Source stickiness via `paused_timeout: 0` for preferred default source
- Spicetify paused heartbeat - returns cached data with `playing=false` instead of nothing

#### Platform Support
- **macOS full support** - Intel (x64) and Apple Silicon (ARM64) builds
- Linux AppImage and tarball builds
- Improved signal handling for graceful Ctrl+C exit on Linux

#### Custom Fonts
- Support for custom font files in the fonts directory
- Variable font detection with proper weight ranges

### 🐛 Bug Fixes

- Fixed mobile playback controls layout and sizing
- Fixed device picker modal visibility over media browser
- Fixed first-time page load issues with media browser caching
- Fixed settings gear icon hover alignment
- Fixed event listener accumulation (memory leak)
- Fixed copy URL button overflow on certain screens
- Resolved Intel Xeon segfault in Home Assistant add-on (OpenBLAS compatibility)
- Fixed Spotify data refresh for top tracks and recently played

### 🏠 Home Assistant Add-on
- Added `compatibility_mode` option for Intel Xeon processors
- Auto-detection of CPU type for OpenBLAS settings
- New Debian-based add-on variant for maximum compatibility

### 📝 Documentation
- Added Music Assistant integration guide
- Added Custom Fonts documentation
- Updated macOS support status (no longer "coming soon")
- Added media browser documentation
- Credited Spotify React Web Client

### 🔧 Technical Improvements

- Automated version numbering from Git tags in CI/CD
- Multi-stage Docker builds with non-root user
- Smoke tests for all release artifacts (Windows, Linux, macOS, Docker)
- React client caching improvements
- Spicetify extension timeout handling

---

## [1.9.0] - Previous Release

See [GitHub Releases](https://github.com/AnshulJ999/SyncLyrics/releases) for earlier versions.
