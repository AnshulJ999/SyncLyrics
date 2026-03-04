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
│   │   └── modules/    ← 19 JS modules
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

> See [`API Reference.md`](API%20Reference.md) for full documentation with request/response details.

### Pages (return HTML)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main lyrics UI |
| `/settings` | GET/POST | Settings management page |
| `/callback` | GET | Spotify OAuth callback |
| `/media-browser/` | GET | Embedded Spotify/Music Assistant browser |

### Lyrics & Track Data
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/lyrics` | GET | Current lyrics, colors, provider, word-sync data |
| `/current-track` | GET | Full track metadata, progress, source, latency info |
| `/config` | GET | All frontend display config (update interval, fonts, etc.) |
| `/cover-art` | GET | Current album art image file (`?type=background` for background variant) |
| `/health` | GET | Server health check (uptime, Spotify status) |

### Settings
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/settings` | GET | All current settings as JSON |
| `/api/settings/<key>` | POST | Update a single setting |
| `/api/settings` | POST | Bulk-update multiple settings |
| `/api/settings/reload` | POST | Hot-reload settings from disk |

### Provider Management
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/providers/current` | GET | Active lyrics provider for current song |
| `/api/providers/available` | GET | All providers with cached lyrics for current song |
| `/api/providers/preference` | POST/DELETE | Set or clear preferred lyrics provider |
| `/api/providers/word-sync-preference` | POST/DELETE | Set or clear preferred word-sync provider |
| `/api/instrumental/mark` | POST | Manually mark/unmark song as instrumental |
| `/api/lyrics/delete` | DELETE | Delete cached lyrics (force re-fetch) |
| `/api/backfill/lyrics` | POST | Trigger re-fetch from all providers |
| `/api/backfill/art` | POST | Trigger re-fetch of album art + artist images |
| `/api/word-sync-offset` | POST | Save per-song word-sync timing offset |

### Album Art
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/cover-art` | GET | Current album art image file |
| `/api/album-art/options` | GET | All art + artist image options for current song |
| `/api/album-art/preference` | POST/DELETE | Set or clear preferred art/artist image |
| `/api/album-art/background-style` | POST | Set per-album background style (sharp/soft/blur/none) |
| `/api/album-art/image/<folder>/<file>` | GET | Serve image file from art database |

### Playback Control
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/playback/play-pause` | POST | Toggle play/pause |
| `/api/playback/next` | POST | Skip to next track |
| `/api/playback/previous` | POST | Skip to previous track |
| `/api/playback/seek` | POST | Seek to position `{position_ms}` |
| `/api/playback/volume` | GET/POST | Get or set volume |
| `/api/playback/shuffle` | POST | Toggle or set shuffle |
| `/api/playback/repeat` | POST | Cycle or set repeat mode |
| `/api/playback/queue` | GET | Get playback queue |
| `/api/playback/liked` | GET/POST | Get or toggle track like status |
| `/api/playback/devices` | GET | List available playback devices |
| `/api/playback/transfer` | POST | Transfer playback to device |
| `/api/playback/audio-analysis` | GET | Waveform, spectrum, and beat data |

### Artist Images & Slideshow
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/artist/images` | GET | Artist images for current song |
| `/api/artist/images/preferences` | POST | Save slideshow preferences (excludes, favorites) |
| `/api/slideshow/random-images` | GET | Random images from art DB for idle screen |

### Spotify-Specific
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/spotify/devices` | GET | List Spotify Connect devices |
| `/api/spotify/transfer` | POST | Transfer Spotify playback to device |
| `/api/spotify/browser-token` | GET | Get fresh Spotify access token for media browser |

### Audio Recognition
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/audio-recognition/status` | GET | Current recognition state and song info |
| `/api/audio-recognition/start` | POST | Start recognition manually |
| `/api/audio-recognition/stop` | POST | Stop recognition |
| `/api/audio-recognition/devices` | GET | List available audio capture devices |
| `/api/audio-recognition/config` | GET | Current recognition config with session overrides |
| `/api/audio-recognition/configure` | POST | Set session-level config overrides |

## WebSocket Endpoints

### `/ws/spicetify`
Spicetify bridge for real-time updates from Spotify Desktop:
- Receives position updates every ~100ms
- Receives track metadata, audio analysis, and color data on song change
- Supports commands: `play`, `pause`, `seek`, `get_queue`, etc.

### `/ws/audio-stream`
Frontend microphone audio streaming for audio recognition.
- Client sends binary Int16 PCM chunks (44100 Hz, mono)
- Server responds with JSON: `connected`, `recognition`, `no_match`, `error`

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
