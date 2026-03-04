# SyncLyrics API Reference

SyncLyrics exposes a full HTTP REST API and two WebSocket endpoints. Any app that can make HTTP requests — a mobile client, Home Assistant dashboard, CLI tool, OBS overlay, or a custom front-end — can integrate with it.

> **Base URL:** `http://<host>:<port>` (default HTTP port: `9012`, HTTPS: `9013`)
>
> All JSON responses use UTF-8 encoding. All POST/DELETE endpoints expect `Content-Type: application/json` unless noted.

---

## Table of Contents

1. [Pages (HTML)](#pages-html)
2. [Core Data](#core-data)
3. [Settings](#settings)
4. [Provider Management](#provider-management)
5. [Album Art](#album-art)
6. [Playback Control](#playback-control)
7. [Artist Images & Slideshow](#artist-images--slideshow)
8. [Spotify-Specific](#spotify-specific)
9. [Audio Recognition](#audio-recognition)
10. [WebSockets](#websockets)
11. [System](#system)

---

## Pages (HTML)

These routes render the web UI. They return HTML, not JSON.

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Main lyrics display page |
| `/settings` | GET/POST | Settings panel |
| `/callback` | GET | Spotify OAuth redirect handler |
| `/media-browser/` | GET | Embedded Spotify browser or Music Assistant iframe |

---

## Core Data

### `GET /lyrics`

The primary polling endpoint. Returns current, previous, and next lyric lines, along with colors extracted from album art, the active provider, word-sync data, and instrumental flags.

**Response:**
```json
{
  "lyrics": ["prev line", "current line", "next line", "..."],
  "colors": ["#24273a", "#363b54"],
  "provider": "spotify",
  "has_lyrics": true,
  "is_instrumental": false,
  "is_instrumental_manual": false,
  "word_synced_lyrics": [ ... ],
  "has_word_sync": true,
  "word_sync_provider": "spotify",
  "any_provider_has_word_sync": true,
  "instrumental_markers": [12.5, 45.0]
}
```

- `lyrics` — a 6-element array: `[prev2, prev1, current, next1, next2, next3]`
- `colors` — two dominant colors from album art (for theming)
- `word_synced_lyrics` — karaoke word-by-word data, or `null` if unavailable
- `instrumental_markers` — timestamps (seconds) of `♪` markers in line-sync data, or `null`

---

### `GET /current-track`

Returns full metadata for the currently playing track. Polled by the frontend alongside `/lyrics`.

**Response:**
```json
{
  "title": "Song Title",
  "artist": "Artist Name",
  "album": "Album Name",
  "album_art_url": "https://...",
  "album_art_path": "/path/to/local/image.jpg",
  "background_image_path": "/path/to/background.jpg",
  "duration_ms": 210000,
  "position_ms": 45000,
  "is_playing": true,
  "source": "spicetify",
  "artist_id": "spotify:artist:...",
  "colors": ["#1a1a2e", "#16213e"],
  "background_style": "blur",
  "is_instrumental": false,
  "is_instrumental_manual": false,
  "latency_compensation": 0.0,
  "word_sync_latency_compensation": 0.0,
  "provider_word_sync_offset": 0.0,
  "word_sync_provider": "spotify",
  "word_sync_default_enabled": true,
  "song_word_sync_offset": 0.0
}
```

- `source` — one of: `spicetify`, `spotify`, `spotify_hybrid`, `windows_media`, `audio_recognition`, `music_assistant`
- `latency_compensation` — seconds to offset lyric display (source-dependent, can be negative)
- `background_style` — per-album saved preference: `blur`, `soft`, `sharp`, or `null`

**Error:**
```json
{ "error": "No track playing" }
```

---

### `GET /config`

Returns the frontend display configuration. Used once at startup to initialize the UI.

**Response:**
```json
{
  "updateInterval": 500,
  "blurStrength": 20,
  "overlayOpacity": 0.6,
  "sharpAlbumArt": false,
  "softAlbumArt": false,
  "visualModeEnabled": true,
  "visualModeDelaySeconds": 30,
  "slideshowEnabled": true,
  "lyricsFontFamily": "Inter",
  "lyricsGlowIntensity": 0.3,
  "customFonts": ["MyFont", "AnotherFont"],
  "word_sync_default_enabled": true,
  "wordSyncTransitionMs": 0
}
```

---

### `GET /cover-art`

Serves the current album art as a raw image file (JPEG, PNG, or WebP).

**Query params:**
- `?type=background` — serves the background image variant instead of album art (may differ when an artist image is set as background)

**Response:** Binary image data with appropriate `Content-Type` header.

**Notes:**
- Returns `404` if no track is playing or no art is available
- Sets `no-cache` headers — always fresh

---

### `GET /health`

Health check endpoint for Docker, Kubernetes, or monitoring.

**Response:**
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "spotify": "authenticated"
}
```

- `spotify` — `"authenticated"` or `"not_configured"`

---

## Settings

### `GET /api/settings`

Returns all current settings as a flat JSON object with dot-notation keys.

**Response:**
```json
{
  "ui.blur_strength": 20,
  "lyrics.font_family": "Inter",
  "visual_mode.enabled": true,
  ...
}
```

---

### `POST /api/settings/<key>`

Update a single setting by its dot-notation key.

**Body:**
```json
{ "value": true }
```

**Response:**
```json
{ "success": true, "requires_restart": false }
```

---

### `POST /api/settings`

Bulk-update multiple settings in one request.

**Body:**
```json
{
  "ui.blur_strength": 25,
  "lyrics.font_family": "Outfit"
}
```

**Response:**
```json
{ "success": true, "requires_restart": false }
```

---

### `POST /api/settings/reload`

Hot-reloads settings from `settings.json` without restarting the server.

**Response:**
```json
{ "success": true, "message": "Settings reloaded" }
```

---

## Provider Management

### `GET /api/providers/current`

Returns info about the provider currently serving lyrics for the playing song.

**Response:**
```json
{ "name": "spotify", "priority": 1, "enabled": true }
```

---

### `GET /api/providers/available`

Returns all providers that have cached lyrics for the current song.

**Response:**
```json
{
  "providers": ["spotify", "lrclib", "musixmatch"]
}
```

---

### `POST /api/providers/preference`

Set a preferred lyrics provider for the current song. Forces that provider even if a higher-priority one is available.

**Body:**
```json
{ "provider": "lrclib" }
```

**Response:**
```json
{ "status": "success", "provider": "lrclib" }
```

---

### `DELETE /api/providers/preference`

Clear the provider preference for the current song (revert to auto-priority).

**Response:**
```json
{ "status": "success", "message": "Preference cleared" }
```

---

### `POST /api/providers/word-sync-preference`

Set preferred word-sync provider for the current song.

**Body:**
```json
{ "provider": "musixmatch" }
```

---

### `DELETE /api/providers/word-sync-preference`

Clear the word-sync provider preference.

---

### `POST /api/instrumental/mark`

Manually mark or unmark the current song as instrumental. Overrides all automatic detection.

**Body:**
```json
{ "is_instrumental": true }
```

**Response:**
```json
{
  "success": true,
  "is_instrumental": true,
  "message": "Song marked as instrumental"
}
```

---

### `DELETE /api/lyrics/delete`

Delete all cached lyrics for the current song, forcing a fresh fetch from all providers on next poll.

**Response:**
```json
{ "status": "success", "message": "Lyrics deleted" }
```

---

### `POST /api/backfill/lyrics`

Manually trigger a re-fetch of lyrics from all enabled providers for the current song.

**Response:**
```json
{ "status": "success", "message": "Refetch triggered" }
```

---

### `POST /api/backfill/art`

Manually trigger a re-fetch of album art and artist images for the current song. Runs in the background.

**Response:**
```json
{ "status": "success", "message": "Refetching album art and artist images..." }
```

---

### `POST /api/word-sync-offset`

Save a per-song word-sync timing offset (in seconds). Used to fine-tune karaoke sync for a specific song.

**Body:**
```json
{ "artist": "Artist Name", "title": "Song Title", "offset": -0.2 }
```

- `offset` — float, clamped to `-10.0` to `+10.0` seconds

**Response:**
```json
{ "success": true, "offset": -0.2 }
```

---

## Album Art

### `GET /api/album-art/options`

Returns all available album art and artist image options cached for the current track. Used by the art picker UI.

**Response:**
```json
{
  "artist": "Artist Name",
  "album": "Album Name",
  "is_single": false,
  "preferred_provider": "spotify",
  "options": [
    {
      "provider": "spotify",
      "url": "https://...",
      "image_url": "/api/album-art/image/Artist%20-%20Album/spotify.jpg",
      "resolution": "640x640",
      "width": 640,
      "height": 640,
      "is_preferred": true,
      "type": "album_art"
    },
    {
      "provider": "FanArt.tv",
      "filename": "fanart_tv_0.jpg",
      "image_url": "/api/album-art/image/Artist/fanart_tv_0.jpg",
      "resolution": "1000x1000",
      "width": 1000,
      "height": 1000,
      "is_preferred": false,
      "type": "artist_image"
    }
  ]
}
```

- `type` — `"album_art"` or `"artist_image"` distinguishes the two categories

---

### `POST /api/album-art/preference`

Set the preferred album art or artist image for the current track. Changes take effect immediately.

**Body:**
```json
{
  "provider": "iTunes",
  "type": "album_art"
}
```

- `type` — `"album_art"` or `"artist_image"` (strongly recommended; avoids ambiguity)
- `filename` — optional, for uniquely identifying one of multiple artist images from the same source
- `url` — optional, alternative identifier

**Response:**
```json
{
  "status": "success",
  "provider": "iTunes",
  "cache_bust": 1709514987
}
```

---

### `DELETE /api/album-art/preference`

Clear both album art and artist image preferences for the current track (revert to auto-selection).

**Response:**
```json
{ "status": "success", "message": "Art preferences cleared" }
```

---

### `POST /api/album-art/background-style`

Save a per-album background blur style preference.

**Body:**
```json
{ "style": "sharp" }
```

- `style` — `"blur"` (default), `"soft"`, `"sharp"`, or `"none"` to clear

**Response:**
```json
{ "status": "success", "style": "sharp", "message": "Saved sharp preference" }
```

---

### `GET /api/album-art/image/<folder>/<file>`

Serve an image file directly from the art database. Used in `<img src="...">` tags by the frontend.

**Path params:**
- `folder` — URL-encoded folder name (e.g. `Artist%20-%20Album`)
- `file` — URL-encoded filename (e.g. `spotify.jpg`)

**Response:** Binary image data with `Cache-Control: public, max-age=86400`.

---

## Playback Control

All playback routes are `POST` (they trigger actions). They auto-detect the active source (Windows SMTC, Spicetify, Spotify API, Music Assistant, or plugin) and route accordingly.

### `POST /api/playback/play-pause`

Toggle play/pause for the current source.

**Response:**
```json
{ "status": "success", "message": "Toggled (Windows)" }
```

---

### `POST /api/playback/next`

Skip to next track.

**Response:**
```json
{ "status": "success", "message": "Skipped (Windows)" }
```

---

### `POST /api/playback/previous`

Skip to previous track.

**Response:**
```json
{ "status": "success", "message": "Previous (Windows)" }
```

---

### `POST /api/playback/seek`

Seek to a specific position.

**Body:**
```json
{ "position_ms": 45000 }
```

**Response:**
```json
{ "status": "success", "message": "Seeked to 45000ms (Windows)" }
```

---

### `GET /api/playback/volume`

Get current volume levels for all available sources.

**Response:**
```json
{
  "windows": 65,
  "spotify": 80,
  "music_assistant": 50
}
```

Only sources that are available/configured appear in the response.

---

### `POST /api/playback/volume`

Set volume for a specific source.

**Body:**
```json
{ "source": "windows", "volume": 70 }
```

- `source` — `"windows"`, `"spotify"`, or `"music_assistant"`
- `volume` — integer `0`–`100`

**Response:**
```json
{ "status": "success", "source": "windows", "volume": 70 }
```

---

### `POST /api/playback/shuffle`

Toggle or set shuffle mode.

**Body (optional):**
```json
{ "state": true }
```

Omit body to toggle based on current state.

**Response:**
```json
{ "status": "success", "shuffle": true, "source": "spotify" }
```

---

### `POST /api/playback/repeat`

Cycle or set repeat mode.

**Body (optional):**
```json
{ "mode": "context" }
```

- `mode` — `"off"`, `"context"` (repeat queue), or `"track"` (repeat one)
- Omit body to cycle: `off → context → track → off`

**Response:**
```json
{ "status": "success", "repeat": "context", "source": "spotify" }
```

---

### `GET /api/playback/queue`

Get the current playback queue.

**Response:**
```json
{
  "current": { "title": "Current Song", "artist": "Artist", ... },
  "queue": [
    { "title": "Next Song", "artist": "Artist", ... },
    ...
  ],
  "source": "spicetify"
}
```

- `source` — `"spicetify"` (most accurate, includes autoplay tracks) or `"spotify_api"`
- Returns up to 20 upcoming tracks

---

### `GET /api/playback/liked`

Check if a track is liked/saved.

**Query params:**
- `track_id` — Spotify track ID or Music Assistant item ID (required)
- `source` — `"music_assistant"` to check MA favorites instead of Spotify (optional)

**Response:**
```json
{ "liked": true }
```

---

### `POST /api/playback/liked`

Like or unlike a track.

**Body:**
```json
{ "track_id": "spotify:track:...", "action": "like", "source": "" }
```

- `action` — `"like"` or `"unlike"`
- `source` — `"music_assistant"` to target MA favorites (optional)

**Response:**
```json
{ "success": true }
```

---

### `GET /api/playback/devices`

List available playback devices.

**Query params:**
- `source` — force `"spotify"` or `"music_assistant"` instead of auto-detecting (optional)

**Response:**
```json
{
  "devices": [
    { "id": "abc123", "name": "Desktop", "is_active": true, "volume_percent": 80 }
  ],
  "source": "spotify"
}
```

---

### `POST /api/playback/transfer`

Transfer playback to a different device.

**Body:**
```json
{ "device_id": "abc123", "force_play": true }
```

**Response:**
```json
{ "status": "success", "message": "Transferred to abc123", "source": "spotify" }
```

---

### `GET /api/playback/audio-analysis`

Returns waveform, spectrum, and beat data for the current track. Sourced from Spicetify live data or the Spicetify cache (for any source that previously played the track via Spicetify).

**Response:**
```json
{
  "audio_analysis": { ... },
  "analysis_track_id": "artist-title-normalized",
  "waveform": [
    { "start": 0.0, "amp": 0.85 },
    { "start": 0.023, "amp": 0.72 }
  ],
  "segments": [
    { "start": 0.0, "duration": 0.3, "pitches": [...], "timbre": [...], "loudness": -5.2 }
  ],
  "beats": [
    { "start": 0.5, "duration": 0.5, "confidence": 0.9 }
  ],
  "sections": [ ... ],
  "duration": 210.4,
  "segment_count": 850
}
```

Returns `404` if no analysis is available (requires Spicetify to have played the track at least once).

---

## Artist Images & Slideshow

### `GET /api/artist/images`

Get artist images for the current song. Returns local database URLs. Will trigger a background download if not yet cached.

**Query params:**
- `artist_id` — Spotify artist ID (optional; used for fallback fetch)
- `include_metadata` — `"true"` to also include full metadata and slideshow preferences

**Response:**
```json
{
  "artist_id": "spotify:artist:...",
  "artist_name": "Artist Name",
  "images": [
    "/api/album-art/image/Artist%20Name/fanart_tv_0.jpg",
    "/api/album-art/image/Artist%20Name/deezer_0.jpg"
  ],
  "count": 2
}
```

With `include_metadata=true`, also includes:
```json
{
  "metadata": [
    { "source": "FanArt.tv", "filename": "fanart_tv_0.jpg", "width": 1000, "height": 562 }
  ],
  "preferences": { "excluded": [], "favorites": [], "auto_enable": null }
}
```

---

### `POST /api/artist/images/preferences`

Save slideshow preferences for an artist.

**Body:**
```json
{
  "artist": "Artist Name",
  "excluded": ["fanart_tv_1.jpg"],
  "favorites": ["deezer_0.jpg"],
  "auto_enable": true
}
```

- `excluded` — filenames to skip in the slideshow
- `favorites` — filenames to prioritize
- `auto_enable` — `true`/`false`/`null` (null = follow global setting)

**Response:**
```json
{ "status": "success", "message": "Preferences saved" }
```

---

### `GET /api/slideshow/random-images`

Get a random selection of images from the entire art database. Used for the idle dashboard/screensaver.

**Query params:**
- `limit` — number of images to return (default: `20`)

**Response:**
```json
{
  "images": [
    "/api/album-art/image/Artist%20-%20Album/spotify.jpg",
    "/api/album-art/image/Another%20Artist/fanart_tv_0.jpg"
  ],
  "total_available": 342
}
```

Results are cached for 1 hour; the list is randomly shuffled on each request from the cache.

---

## Spotify-Specific

### `GET /api/spotify/devices`

List Spotify Connect devices. Equivalent to `/api/playback/devices?source=spotify`.

**Response:**
```json
{
  "devices": [
    { "id": "abc123", "name": "Desktop", "is_active": true, "volume_percent": 80 }
  ]
}
```

---

### `POST /api/spotify/transfer`

Transfer Spotify playback to a specific device.

**Body:**
```json
{ "device_id": "abc123", "force_play": true }
```

---

### `GET /api/spotify/browser-token`

Get a fresh Spotify access token for use in the embedded media browser (Spotify React client).

**Response:**
```json
{ "access_token": "BQ...", "expires_in": 3600 }
```

---

## Audio Recognition

### `GET /api/audio-recognition/status`

Get the current audio recognition engine state.

**Response:**
```json
{
  "available": true,
  "enabled": true,
  "active": false,
  "mode": "idle",
  "reaper_detected": false,
  "auto_detect": false,
  "manual_mode": false,
  "capture_mode": null,
  "current_song": null
}
```

When recognition is disabled in config, returns `enabled: false` and `active: false` without initializing the audio subsystem.

---

### `POST /api/audio-recognition/start`

Start recognition manually.

**Body (optional):**
```json
{ "manual": true }
```

**Response:**
```json
{ "status": "started", "mode": "manual" }
```

---

### `POST /api/audio-recognition/stop`

Stop the recognition engine.

**Response:**
```json
{ "status": "stopped" }
```

---

### `GET /api/audio-recognition/devices`

List available audio capture devices, sorted with loopback devices first.

**Response:**
```json
{
  "devices": [
    { "id": 3, "name": "WASAPI Loopback", "is_loopback": true, "max_input_channels": 2 },
    { "id": 0, "name": "Microphone Array", "is_loopback": false, "max_input_channels": 2 }
  ],
  "recommended": { "id": 3, "name": "WASAPI Loopback" },
  "count": 2
}
```

---

### `GET /api/audio-recognition/config`

Get the effective audio recognition configuration (session overrides layered on top of `settings.json`).

**Response:**
```json
{
  "config": {
    "enabled": false,
    "device_id": null,
    "device_name": null,
    "mode": "backend",
    "reaper_auto_detect": false,
    "recognition_interval": 15.0,
    "capture_duration": 5.0,
    "latency_offset": 0.0
  },
  "status": { "active": false },
  "session_overrides_active": false,
  "active_overrides": {},
  "https_available": true
}
```

---

### `POST /api/audio-recognition/configure`

Apply session-level overrides to the audio recognition config. These are runtime-only and not persisted to `settings.json`.

**Body:**
```json
{
  "enabled": true,
  "device_id": 3,
  "device_name": "WASAPI Loopback",
  "mode": "backend",
  "reaper_auto_detect": false,
  "recognition_interval": 15.0,
  "capture_duration": 5.0,
  "latency_offset": 0.0,
  "silence_threshold": 500
}
```

All fields are optional. Starting recognition (`enabled: true`) automatically starts the engine.

**Response:**
```json
{
  "status": "configured",
  "config": { ... },
  "active_overrides": { "enabled": true, "device_id": 3 }
}
```

---

## WebSockets

### `WS /ws/spicetify`

Real-time bridge for the Spicetify extension running inside Spotify Desktop. This is not typically used by external clients — it is the channel through which the Spicetify browser extension pushes data into SyncLyrics.

**Incoming message types (from Spicetify):**
- `position` — playback position update (every ~100ms)
- `track_data` — full metadata + audio analysis on song change

**Outgoing commands (to Spicetify):**
- `play`, `pause`, `seek`, `get_queue`, etc.

---

### `WS /ws/audio-stream`

Frontend microphone audio streaming for audio recognition. Used by the browser-based mic capture mode.

**Protocol:**
1. Client connects
2. Server sends: `{ "type": "connected", "capture_duration": 5.0 }`
3. Client streams binary Int16 PCM chunks (44100 Hz, mono, little-endian)
4. Server responds with JSON messages:

| Type | Description |
|------|-------------|
| `connected` | Handshake — includes `capture_duration` |
| `recognition` | Match found — includes `artist`, `title`, `position` |
| `no_match` | No match found for this audio segment |
| `error` | Recognition error — includes `message` |
| `pong` | Response to client `{"type":"ping"}` keepalive |

A 10-second grace period applies on disconnect — the engine keeps running in case the client reconnects (e.g. browser refresh).

---

## System

### `GET /exit-application`

Gracefully shuts down the SyncLyrics process (2-second delay before force-exit).

**Response:**
```json
{ "status": "ok" }
```

---

### `POST /restart`

Restarts the server process.

**Response:**
```json
{ "status": "ok" }
```

---

### `GET /reset-defaults`

Resets all settings to their schema defaults and redirects to `/settings`.

---

## Building a Custom Client

The minimum polling loop for a custom lyrics client:

1. `GET /current-track` — get track identity and position
2. `GET /lyrics` — get lyric lines
3. Use `position_ms` + `latency_compensation` from `/current-track` to find the current line in the `lyrics` array
4. Display. Repeat at the `updateInterval` from `GET /config` (default: 500ms)

For album art, use the `album_art_url` from `/current-track` (direct Spotify CDN URL) or `GET /cover-art` for the locally cached version.

For playback controls, all `/api/playback/*` routes work regardless of the active source — routing to Windows SMTC, Spicetify, Spotify API, or Music Assistant is handled server-side automatically.
