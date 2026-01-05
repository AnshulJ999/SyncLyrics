# Development Reference

Technical reference for developers and AI assistants working on SyncLyrics.

## Architecture Overview

```
sync_lyrics.py          ← Entry point, main loop
├── server.py           ← Quart web server (50+ endpoints)
├── lyrics.py           ← Lyrics fetching, caching, multi-provider
├── config.py           ← Configuration loader
├── settings.py         ← Settings schema and manager
├── state_manager.py    ← Thread-safe application state
│
├── providers/          ← Lyrics providers
│   ├── base.py         ← Abstract base class
│   ├── spotify_api.py  ← Spotify API singleton
│   ├── spotify_lyrics.py
│   ├── lrclib.py
│   ├── musixmatch.py   ← RichSync word-sync
│   ├── netease.py      ← YRC word-sync
│   └── qq.py
│
├── system_utils/       ← Platform integrations
│   ├── metadata.py     ← Main orchestrator
│   ├── windows.py      ← Windows SMTC
│   ├── spotify.py      ← Spotify source
│   ├── spicetify.py    ← WebSocket bridge
│   ├── album_art.py    ← Album art database
│   ├── artist_image.py ← Artist image database
│   ├── reaper.py       ← Audio recognition (Shazam)
│   └── session_config.py ← Runtime overrides
│
├── resources/
│   ├── js/
│   │   ├── main.js     ← Frontend entry point
│   │   └── modules/    ← 17 JS modules
│   ├── css/
│   └── templates/
│
└── spicetify/
    └── synclyrics-bridge.js  ← Spicetify extension (1600+ lines)
```

## Key Design Patterns

### Singleton Spotify Client
`providers/spotify_api.py` uses singleton pattern via `get_shared_spotify_client()` for:
- Consolidated API statistics
- Efficient token caching
- Single auth flow

### Provider System
All providers inherit from `LyricsProvider` base class:
- `get_lyrics(artist, title, album, duration)` → returns dict with lyrics
- Priority-based parallel fetching
- First result wins, background saves others

### Metadata Orchestration
`system_utils/metadata.py` coordinates sources:
1. Check Spicetify (if connected)
2. Check Windows SMTC
3. Fallback to Spotify API

### Frontend Flywheel Clock
`wordSync.js` implements smooth position interpolation:
- Monotonic time that never goes backwards
- Handles seek, pause, speed adjustments
- Snaps when drift exceeds threshold

## REST API Endpoints

### Lyrics & Metadata
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/lyrics` | GET | Current lyrics + metadata |
| `/current-track` | GET | Track info + position |
| `/api/providers` | GET | Available providers for song |
| `/api/provider/preference` | POST | Set preferred provider |

### Playback Control
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/play-pause` | GET | Toggle playback |
| `/next` | GET | Skip to next track |
| `/previous` | GET | Skip to previous |
| `/seek/{position}` | GET | Seek to position (ms) |

### Album Art
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cover-art` | GET | Current album art image |
| `/api/album-art/options` | GET | All album art options |
| `/api/album-art/preference` | POST | Set preferred image |

### Audio Recognition
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/audio-recognition/status` | GET | Recognition status |
| `/api/audio-recognition/start` | POST | Start recognition |
| `/api/audio-recognition/stop` | POST | Stop recognition |

## WebSocket Endpoints

### `/ws/spicetify`
Spicetify bridge for real-time updates:
- `position`: Position updates every ~100ms
- `track_data`: Full metadata + audio analysis
- Commands: `play`, `pause`, `seek`, `get_queue`, etc.

### `/ws/audio-stream`
Frontend microphone audio streaming for recognition.

## Data Storage

| Directory | Contents |
|-----------|----------|
| `lyrics_database/` | Cached lyrics JSON per song |
| `album_art_database/` | Album art + artist images |
| `spicetify_database/` | Audio analysis cache |
| `cache/` | Temporary files |
| `certs/` | SSL certificates |

## Configuration Priority

1. Environment variables (Docker-friendly)
2. `settings.json` (user preferences)
3. Schema defaults (`settings.py`)

## Threading Model

- Main loop: `asyncio` event loop
- File I/O: Thread pool executors
- State: `threading.RLock` for thread-safe access
- Locks: Async locks for concurrent API access
