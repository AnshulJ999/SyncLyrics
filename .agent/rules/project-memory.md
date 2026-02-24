---
trigger: manual
---

# SyncLyrics Project Memory

This file provides persistent context for AI assistants working on SyncLyrics. It is automatically loaded for all conversations in this workspace.

---

## Architecture Overview

- **Entry point:** `sync_lyrics.py` → `server.py` (Quart web server)
- **Frontend:** ES modules in `resources/js/modules/` with strict import hierarchy
- **Backend:** `system_utils/` package with tiered dependency structure
- **Providers:** Lyrics from Spotify, LRCLib, Musixmatch, NetEase, QQ Music
- **Metadata sources:** Spicetify > Windows SMTC > Spotify API (priority order)

---

## Recent Development History

### Build & Distribution (Jan 2026)
- **Automated versioning:** Git tags are the single source of truth; `version.py` is injected at build time via GitHub Actions
- **AppImage fix:** Resolved read-only filesystem error by using XDG directories (`~/.local/share/SyncLyrics`)
- **Linux Ctrl+C:** Implemented graceful signal handling for proper exit
- **macOS Gatekeeper:** Launcher script workaround for "damaged app" warnings

### Home Assistant Add-on (Jan 2026)
- **Alpine segfault fix:** Added OpenBLAS compatibility mode for Intel Xeon processors
  - Environment variables: `OPENBLAS_NUM_THREADS=1`, `OPENBLAS_CORETYPE=SANDYBRIDGE`
  - Auto-detection of Xeon CPUs in `run.sh`
  - Added `compatibility_mode` option in add-on config
- **Debian variant:** Created `synclyrics-debian` as fallback for musl/numpy issues
- **Native ARM builds:** GitHub Actions uses native ARM64 runners (no QEMU) for faster builds
- **Smoke tests:** Added to verify images before publishing

### Frontend Enhancements (Jan 2026)
- **Media Browser persistence:** Iframe stays loaded (hidden) for 30 minutes after modal close
- **Smart refresh:** Tap refreshes current URL, long-press navigates to base URL
- **Event listener leak:** Investigated accumulation in `controls.js` and `slideshow.js`
- **Shuffle/repeat state:** Propagated from all backend sources to frontend

### UI/UX Improvements (Jan 2026)
- **Custom font support:** Variable font weight detection, italic handling, fontTools logging silenced
- **Settings panel:** Per-setting reset buttons, mobile layout fixes, improved compactness
- **Album name styling:** CSS properties tuned for consistency with title/artist
- **Visualizer options:** Hidden when Spicetify unavailable or audio analysis blank

### Configuration System
- **Hot-reload:** Settings accessed via `settings.get()` are hot-reloaded
- **Per-song line-sync adjustment:** Under consideration for all audio sources
- **Background customization:** `bg_start` and `bg_end` theme settings available

---

## Key Patterns & Gotchas

### Backend
- All locks are singletons in `state.py` — never duplicate `asyncio.Lock()`
- Import from `system_utils` package, not submodules directly
- `ACTIVE_INTERVAL` and constants are in `state.py`, not `config.py`
- Lazy imports in `helpers.py` to avoid circular dependencies

### Frontend
- ES module live bindings: `setLastTrackInfo()` updates are visible to all importers
- Always call `updateBackground()` after changing display config
- Settings checkbox changes require: `applyDisplayConfig()` + `applySoftMode()` + `applySharpMode()` + `updateBackground()` + `updateUrlDisplay()`
- `slideshow.js` is currently disabled (has early return)

### General
- **LF line endings:** Project uses LF format; report any CRLF files for conversion
- **No Git commands in Antigravity:** They don't work well; ask user instead
- **No destructive commands:** Never delete files/folders/directories
- **Always ask approval:** Before making any code changes

---

## API Integrations

### Spotify
- Singleton client via `get_shared_spotify_client()` for token caching and consolidated stats
- Web API exploration done: client can connect to SyncLyrics for metadata

### Music Assistant
- URL redirection: Spotify URLs can redirect to Music Assistant when configured
- Multi-iframe approach discussed for instant switching between sources

---

## Infrastructure

### GitHub Actions Workflows
- `release.yml` — Main release process
- `docker-publish.yml` — Docker image builds (uses native ARM runners)
- `hass-addon-publish.yml` — HA add-on builds (Alpine + Debian variants)
- `latest` tag issue: Lower versions tagged as latest due to workflow logic

### Docker
- Base: Alpine Linux (primary), Debian (fallback)
- ARM builds: Native runners, not QEMU emulation
- OpenBLAS threading disabled for compatibility

---

## File Reference

| Path | Purpose |
|------|---------|
| `.agent/rules/code-guide.md` | Architecture and development commands |
| `.agent/workflows/codebase-guide.md` | Navigation guide for frontend/backend |
| `version.py` | Injected version from Git tags |
| `settings.py` | Settings schema and manager |
| `config.py` | Configuration loader (env > settings.json > defaults) |