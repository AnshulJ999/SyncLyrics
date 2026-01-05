# Configuration Reference

SyncLyrics has 100+ configurable settings organized by category. This reference covers the most important ones.

## Configuration Hierarchy

Settings are loaded in this priority order (highest wins):
1. **Environment Variables** (`SPOTIFY_CLIENT_ID`, etc.)
2. **settings.json** (edited via `/settings` page)
3. **Defaults** (built into the app)

## Where to Configure

- **Web UI**: Access `/settings` in your browser
- **Environment Variables**: Set in `.env` file or Docker/HASS config
- **settings.json**: Direct JSON editing (auto-created on first run)

---

## Server

| Setting | Default | Description |
|---------|---------|-------------|
| `server.port` | 9012 | HTTP port |
| `server.host` | 0.0.0.0 | Bind address |
| `server.https.enabled` | true | Enable HTTPS |
| `server.https.port` | 9013 | HTTPS port (0 = same as HTTP) |

## Media Sources

| Setting | Default | Description |
|---------|---------|-------------|
| `media_source.spicetify.enabled` | true | Spicetify WebSocket bridge |
| `media_source.spicetify.priority` | 0 | Priority (0 = highest) |
| `media_source.windows_media.enabled` | true | Windows SMTC |
| `media_source.spotify.enabled` | true | Spotify API polling |

## Lyrics

| Setting | Default | Description |
|---------|---------|-------------|
| `lyrics.display.latency_compensation` | -0.1 | Sync offset (seconds) |
| `lyrics.display.spotify_latency_compensation` | -0.5 | Spotify-specific offset |
| `lyrics.display.spicetify_latency_compensation` | 0.0 | Spicetify offset |
| `lyrics.display.word_sync_latency_compensation` | 0.0 | Word-sync offset |
| `lyrics.display.idle_interval` | 3.0 | Polling when idle (seconds) |
| `lyrics.display.smart_race_timeout` | 3.0 | Max wait for providers (seconds) |

## Providers

Each provider has: `enabled`, `priority` (lower = first), `timeout`, `retries`.

| Provider | Default Priority | Has Word-Sync |
|----------|-----------------|---------------|
| Spotify | 1 | ✅ |
| LRCLib | 2 | ❌ |
| NetEase | 3 | ✅ (YRC) |
| Musixmatch | 3 | ✅ (RichSync) |
| QQ | 4 | ❌ |

## Spotify API

| Setting | Default | Description |
|---------|---------|-------------|
| `spotify.redirect_uri` | http://127.0.0.1:9012/callback | OAuth callback |
| `spotify.polling.fast_interval` | 2.0 | Spotify-only polling (seconds) |
| `spotify.polling.slow_interval` | 6.0 | Idle polling (seconds) |

## Album Art

| Setting | Default | Description |
|---------|---------|-------------|
| `album_art.enable_itunes` | true | iTunes as art source |
| `album_art.enable_lastfm` | true | Last.fm as art source |
| `album_art.enable_spotify_enhanced` | true | Upgrade Spotify to 1400px |
| `album_art.min_resolution` | 3000 | Preferred resolution (px) |

## Artist Image

| Setting | Default | Description |
|---------|---------|-------------|
| `artist_image.enable_wikipedia` | false | Wikipedia/Wikimedia source |
| `artist_image.enable_fanart_albumcover` | true | FanArt.tv album covers |

## UI

| Setting | Default | Description |
|---------|---------|-------------|
| `ui.blur_strength` | 10 | Background blur (px) |
| `ui.overlay_opacity` | 0.4 | Background overlay |
| `ui.sharp_album_art` | false | Disable blur |
| `ui.soft_album_art` | false | Medium blur |

## Slideshow

| Setting | Default | Description |
|---------|---------|-------------|
| `slideshow.default_enabled` | false | Start with slideshow on |
| `slideshow.interval_seconds` | 6 | Seconds per image |
| `slideshow.ken_burns_enabled` | true | Zoom/pan animation |
| `slideshow.ken_burns_intensity` | subtle | subtle/medium/cinematic |
| `slideshow.shuffle` | false | Random order |

## Audio Recognition

| Setting | Default | Description |
|---------|---------|-------------|
| `audio_recognition.enabled` | false | Enable recognition |
| `audio_recognition.reaper_auto_detect` | false | Auto-start for Reaper |
| `audio_recognition.capture_duration` | 5.0 | Audio capture length (seconds) |
| `audio_recognition.recognition_interval` | 5.0 | Time between recognition attempts |
| `audio_recognition.silence_threshold` | 500 | Min amplitude to detect |

## Features

| Setting | Default | Description |
|---------|---------|-------------|
| `features.save_lyrics_locally` | true | Cache lyrics to disk |
| `features.parallel_provider_fetch` | true | Query providers concurrently |
| `features.album_art_db` | true | Cache album art |
| `features.word_sync_auto_switch` | true | Prefer providers with word-sync |
| `features.word_sync_default_enabled` | true | Enable word-sync by default |
| `features.spicetify_database` | true | Cache audio analysis |

## System

| Setting | Default | Description |
|---------|---------|-------------|
| `system.windows.app_blocklist` | [chrome, msedge, ...] | Apps to ignore |
| `system.windows.paused_timeout` | 600 | Accept paused media for N seconds |

---

## Environment Variables

Key environment variables for Docker/HASS:

| Variable | Description |
|----------|-------------|
| `SPOTIFY_CLIENT_ID` | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | Spotify app client secret |
| `SPOTIFY_REDIRECT_URI` | OAuth callback URL |
| `LASTFM_API_KEY` | Last.fm API key (album art) |
| `FANART_TV_API_KEY` | FanArt.tv API key (artist images) |
| `AUDIODB_API_KEY` | TheAudioDB key (free: `523532`) |
| `SERVER_PORT` | Override server port |
| `DEBUG_ENABLED` | Enable debug mode |
| `DEBUG_LOG_LEVEL` | Log level (DEBUG/INFO/WARNING/ERROR) |

See [docker/DOCKER.md](../docker/DOCKER.md) for complete Docker configuration.
