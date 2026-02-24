---
trigger: always_on
---

# Basic Code Guide

## Project Overview

SyncLyrics is a Python desktop/web application that displays synchronized lyrics for currently playing music.

## Development Commands

### Run from Source
```bash
pip install -r requirements.txt
python sync_lyrics.py
```

### Run with Audio Recognition Mode
```bash
python sync_lyrics.py --reaper
```

### Build Windows Executable
```bash
python build.py           # Release build (no console)
python build.py --debug   # Debug build (with console)
python build.py clean     # Remove build artifacts
```
Output: `build_final/SyncLyrics/SyncLyrics.exe`

### Run Tests

Tests are not structured; they are individual. 

```bash
python tests/run_all_tests.py
# Or directly with pytest:
pytest tests/ -v
```

## Architecture

```
sync_lyrics.py          <- Entry point, main async loop, tray icon
├── server.py           <- Quart web server (50+ REST/WebSocket endpoints)
├── lyrics.py           <- Lyrics fetching, caching, multi-provider coordination
├── config.py           <- Configuration loader (env vars > settings.json > defaults)
├── settings.py         <- Settings schema and manager
├── state_manager.py    <- Thread-safe application state (state.json)
│
├── providers/          <- Lyrics providers (all inherit from LyricsProvider base)
│   ├── base.py         <- Abstract base class
│   ├── spotify_api.py  <- Spotify API singleton (get_shared_spotify_client())
│   ├── spotify_lyrics.py, lrclib.py, musixmatch.py, netease.py, qq.py
│   ├── album_art.py    <- Album art from iTunes/Spotify/LastFM
│   └── artist_image.py <- Artist images from Deezer/FanArt/Spotify/Wikipedia
│
├── system_utils/       <- Platform integrations and metadata orchestration
│   ├── metadata.py     <- Main orchestrator: Spicetify > Windows SMTC > Spotify API
│   ├── windows.py      <- Windows SMTC integration
│   ├── spotify.py      <- Spotify playback source
│   ├── spicetify.py    <- WebSocket bridge for real-time updates (~100ms)
│   ├── reaper.py       <- Audio recognition engine (ShazamIO)
│   └── album_art.py, artist_image.py <- Image database managers
│
├── resources/
│   ├── js/main.js      <- Frontend entry point
│   └── js/modules/     <- 17 JS modules (wordSync.js has flywheel clock for smooth interpolation)
│
└── spicetify/
    └── synclyrics-bridge.js <- Spicetify extension (1600+ lines)
```

## Key Design Patterns

### Singleton Spotify Client
`providers/spotify_api.py` uses `get_shared_spotify_client()` for consolidated API stats, token caching, and single auth flow.

### Provider System
All lyrics providers inherit from `LyricsProvider` base class with `get_lyrics(artist, title, album, duration)`. Priority-based parallel fetching where first result wins.

### Configuration Priority
1. Environment variables (Docker-friendly)
2. `settings.json` (user preferences)
3. Schema defaults in `settings.py`

### Threading Model
- Main loop: asyncio event loop
- File I/O: Thread pool executors
- State: `threading.RLock` for thread-safe access
- Async locks for concurrent API access

Always use best-practice coding: Safe, non-blocking, low-risk. 

## Data Storage

| Directory | Contents |
|-----------|----------|
| `lyrics_database/` | Cached lyrics JSON per song |
| `album_art_database/` | Album art + artist images |
| `spicetify_database/` | Audio analysis cache |
| `cache/` | Temporary files |
| `certs/` | Auto-generated SSL certificates |

## Important Guidelines

- Do NOT delete large code sections without confirmation
- Use LF line endings
- Do not use Git commands (they don't work well in this environment)
- Never use any deletion commands (rm, git rm, etc.). Anything dangerous - don't ever run it.
- Never assume app behavior - ask for clarification
- Always re-read files to check current state before making changes. Don't assume or guess.
- Your search tool / function is broken. If search gives you 0 results, please use alternative methods to access the real code. Don't assume code is missing just because your search gave 0 results.