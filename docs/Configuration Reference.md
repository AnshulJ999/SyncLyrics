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

Below list may be outdated. Use the built-in settings menu for updated reference. 

## Server

| Setting | Default | Description |
|---------|---------|-------------|
| `server.port` | 9012 | HTTP port |
| `server.host` | 0.0.0.0 | Bind address |
| `server.https.enabled` | true | Enable HTTPS |
| `server.https.port` | 9013 | HTTPS port (0 = same as HTTP) |

## Media Sources

Each source has `media_source.<name>.enabled` and `media_source.<name>.priority` (lower = checked first). The first source that's playing wins; if none are, the most recently active paused one shows.

| Source (`<name>`) | Enabled by default | Priority | Notes |
|---|---|---|---|
| `spicetify` | true | 0 | Spicetify WebSocket bridge |
| `windows_media` | true | 1 | Windows SMTC |
| `linux` | true | 1 | MPRIS via playerctl |
| `macos` | true | 1 | Now Playing (nowplaying-cli recommended) |
| `music_assistant` | true | 1 | Needs a server URL and token, see [Music Assistant](Music%20Assistant.md) |
| `spotify` | true | 2 | Spotify API polling |
| `pear_desktop` | false | 6 | `base_url` default `http://127.0.0.1:26538` |
| `tidal_hifi` | false | 7 | `base_url` default `http://127.0.0.1:47836` |
| `smtc_now_playing` | false | 8 | `base_url` default `http://127.0.0.1:11451` |
| `now_playing_input` | false | 9 | Push endpoint; optional `token`. See [Now Playing Input](Now%20Playing%20Input.md) |

Most sources also have `system.<name>.paused_timeout` (default 600 seconds, `0` = forever): how long a paused source can still be shown.

## Updates

| Setting | Default | Description |
|---------|---------|-------------|
| `updates.check_enabled` | true | Once a day, check for a newer version |
| `updates.usage_stats` | true | Include anonymous usage stats in that check. See [Usage Stats](Usage%20Stats.md) |

## Lyrics

| Setting | Default | Description |
|---------|---------|-------------|
| `lyrics.display.latency_compensation` | -0.1 | Sync offset (seconds) |
| `lyrics.display.spotify_latency_compensation` | -0.5 | Spotify-specific offset |
| `lyrics.display.spicetify_latency_compensation` | 0.0 | Spicetify offset |
| `lyrics.display.word_sync_latency_compensation` | -0.1 | Word-sync offset |
| `lyrics.display.music_assistant_latency_compensation` | 0.0 | Music Assistant offset |
| `lyrics.display.idle_interval` | 3.0 | Polling when idle (seconds) |
| `lyrics.display.smart_race_timeout` | 4.0 | Max wait for providers (seconds) |

## Providers

Each provider has: `enabled`, `priority` (lower = first), `timeout`, `retries`.

| Provider | Default Priority | Has Word-Sync |
|----------|-----------------|---------------|
| Spotify | 1 | ❌ |
| LRCLib | 2 | ❌ |
| Musixmatch | 3 | ✅ (RichSync) |
| NetEase | 4 | ✅ (YRC) |
| QQ | 5 | ❌ |

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
| `slideshow.shuffle` | true | Random order |

## Audio Recognition

| Setting | Default | Description |
|---------|---------|-------------|
| `audio_recognition.enabled` | false | Enable recognition |
| `audio_recognition.reaper_auto_detect` | false | Auto-start for Reaper |
| `audio_recognition.capture_duration` | 6.0 | Audio capture length (seconds) |
| `audio_recognition.recognition_interval` | 4.0 | Time between recognition attempts |
| `audio_recognition.silence_threshold` | 350 | Min amplitude to detect |
| `audio_recognition.verification_cycles` | 2 | Matches needed to accept song |

## Features

| Setting | Default | Description |
|---------|---------|-------------|
| `features.save_lyrics_locally` | true | Cache lyrics to disk |
| `features.parallel_provider_fetch` | true | Query providers concurrently |
| `features.album_art_db` | true | Cache album art |
| `features.word_sync_auto_switch` | false | Prefer providers with word-sync |
| `features.word_sync_default_enabled` | true | Enable word-sync by default |
| `features.spicetify_database` | true | Cache audio analysis |

## System

| Setting | Default | Description |
|---------|---------|-------------|
| `system.windows.app_blocklist` | [] | Apps to ignore (empty by default) |
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
| `UPDATES_CHECK_ENABLED` | `false` turns off the daily update check |
| `UPDATES_USAGE_STATS` | `false` turns off anonymous usage stats |

Any setting can be set this way: uppercase the key and replace dots with underscores, e.g. `media_source.now_playing_input.enabled` becomes `MEDIA_SOURCE_NOW_PLAYING_INPUT_ENABLED`.

See [Docker Reference](Docker%20Reference.md) for complete Docker configuration.
