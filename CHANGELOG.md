# Changelog

## [Unreleased]

### ✨ New Features

#### Pear Desktop (YouTube Music) Source

SyncLyrics can now read the currently playing track from [Pear Desktop](https://github.com/pear-devs/pear-desktop), giving YouTube Music users synced lyrics. Enable it under Media settings (off by default) and make sure Pear Desktop's API server plugin is running - the default URL is `http://localhost:26538`.

Thanks to [@webbrain-one](https://github.com/webbrain-one) for the implementation in [#24](https://github.com/AnshulJ999/SyncLyrics/pull/24), and [@1upbyte](https://github.com/1upbyte) for the original request and API research in [#20](https://github.com/AnshulJ999/SyncLyrics/issues/20).

#### Keep Screen Awake

Phones and tablets used as a lyrics display no longer dim and lock mid-song. A new **Keep Screen Awake** setting (Settings > UI) uses the Screen Wake Lock API to hold the display on - `always`, only during `playback` (default), or `off`. Override it per display with `?keepAwake=always|playback|off`.

Requires a secure context (HTTPS or localhost). In an iframe the parent must set `allow="screen-wake-lock"`, which the Home Assistant Lovelace iframe card does not - those users still need the direct URL or Fully Kiosk.

Thanks to [@Ayce45](https://github.com/Ayce45) for contributing this feature in [#25](https://github.com/AnshulJ999/SyncLyrics/pull/25).

## [2.3.0] - 2026-07-23

### ⚠️ Important

Spotify has begun enforcing a 6-month expiration on refresh tokens for existing apps (rolling out from 2026-07-20). If you were logged into Spotify before this release, you may have hit a "Refresh token revoked" login error that couldn't be cleared no matter how many times you re-authorized. This release fixes that.

### 🐛 Bug Fixes

- **Fixed Spotify re-login being permanently stuck after a refresh token expires/is revoked.** A fresh login was being silently discarded in favor of retrying the old, already-dead cached token, so re-authorizing never actually worked. Logging in now always uses the fresh authorization code.
- Fixed the app silently looping on backoff forever (instead of prompting re-login) when a Spotify refresh token is revoked mid-session.

### ✨ New Features

#### Spotify Connection Monitor
- New status card in **Settings → Spotify API** showing live connection health (connected / degraded / needs reconnect / not configured)
- **Test connection** button for an on-demand real check against Spotify
- **Disconnect** button to remove the saved local Spotify login (does not revoke access on Spotify's side - remove SyncLyrics from your Spotify account's connected apps for that)

## [2.0.0] - 2026-01-17

### ⚠️ Breaking Changes

**Note:** Due to Spotify OAuth scope changes, you will have to re-login to Spotify and accept the new permissions. This is for the new enhanced features including device picker UI and volume/shuffle/repeat controls.

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
