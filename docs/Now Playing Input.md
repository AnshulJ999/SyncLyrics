# Now Playing Input

SyncLyrics normally finds out what's playing on the machine it runs on. Now Playing Input lets **other devices tell it** instead: your phone, a Home Assistant automation, a script, or any app that can send an HTTP request. SyncLyrics then shows the lyrics, album art and progress as usual.

This is how a Docker or Home Assistant install can show lyrics for music playing somewhere else, and how players with no API of their own (TIDAL on a phone, Apple Music, Sonos started from its own app) can be supported.

There are two ways it works:

- **Push:** the device sends SyncLyrics what's playing, whenever it changes. Works with anything that can make an HTTP request. Covered first below.
- **Pull:** some music apps run a small local API. SyncLyrics asks them what's playing, about once a second. You just switch the source on. See [Pull sources](#pull-sources).

---

## Push

### 1. Turn it on

**Settings > Media > Now Playing Input** (off by default). Or with an environment variable:

```bash
MEDIA_SOURCE_NOW_PLAYING_INPUT_ENABLED=true
```

While it's off, SyncLyrics answers every push with `403`.

**Priority:** Now Playing Input has priority 9, the lowest of the built-in sources. If something else on the SyncLyrics machine is also playing (Spotify, Windows media, Music Assistant), that source wins. To make pushed devices win, give Now Playing Input a lower number in **Settings > Media** (lower = first).

### 2. Optional: set a token

If SyncLyrics is reachable from the internet (a reverse proxy, Cloudflare Tunnel, Home Assistant remote access), set a token so strangers can't push fake songs:

**Settings > Media > Now Playing Input Token**, or `MEDIA_SOURCE_NOW_PLAYING_INPUT_TOKEN=some-long-random-string`.

Senders then include this header:

```text
Authorization: Bearer some-long-random-string
```

On a home network you can leave it empty.

### 3. Send what's playing

```text
POST http://<synclyrics-address>:9012/api/now-playing
Content-Type: application/json
```

| Field | Required | Description |
|---|---|---|
| `device_id` | Yes | Any stable name for the sending device, like `pixel-8` or `living-room`. Each device keeps its own slot. |
| `device_name` | No | Shown as the source name, like `Anshul's Phone`. Without it, the source shows as **Remote**. |
| `title` | Yes | Song title. |
| `artist` | No | Artist. Strongly recommended: lyrics are found by artist + title. |
| `album` | No | Album. |
| `album_art_url` | No | An `http://` or `https://` image URL. Without it, SyncLyrics uses album art it has saved before for that album or artist, if any. |
| `position` | No | Seconds into the song. Strongly recommended: without it, lyrics can't be synced. |
| `duration` | No | Song length in seconds. |
| `position_ms`, `duration_ms` | No | The same, in milliseconds. Use these instead of `position` / `duration` if your app gives milliseconds. |
| `is_playing` | Yes | `true` or `false`. |

Example:

```json
{
  "device_id": "pixel-8",
  "device_name": "Anshul's Phone",
  "title": "Blinding Lights",
  "artist": "The Weeknd",
  "album": "After Hours",
  "position": 42.5,
  "duration": 200,
  "is_playing": true
}
```

Fields SyncLyrics doesn't know are ignored, so it's safe to send extra data.

**When the player closes**, remove the device right away:

```json
{ "device_id": "pixel-8", "stopped": true }
```

### Replies

| Status | Meaning |
|---|---|
| `200` | `{"ok": true}` |
| `400` | Something's wrong with the data, for example `{"error": "title is required."}` |
| `401` | Missing or wrong token |
| `403` | Now Playing Input is switched off |

### How often to send

Send an update **whenever something changes** (new song, play/pause, seek), plus **about every 5 seconds while playing**. Between updates, SyncLyrics moves the position forward by itself, so lyrics stay smooth.

- A device marked playing that stops sending for **30 seconds** is treated as paused (the phone died, the app was killed).
- A paused device drops out after **10 minutes**. Change this with `SYSTEM_NOW_PLAYING_INPUT_PAUSED_TIMEOUT` (seconds, `0` = never).

### Several devices

Each `device_id` is tracked separately. The **most recent device that's playing** wins. If none are playing, the one that was playing most recently shows as paused.

---

## Examples

These are starting points; adjust the address, names and entity IDs to your setup.

### curl

Linux, macOS or Git Bash:

```bash
curl -X POST http://192.168.1.50:9012/api/now-playing \
  -H "Content-Type: application/json" \
  -d '{"device_id":"test","device_name":"Test PC","title":"Blinding Lights","artist":"The Weeknd","position":30,"duration":200,"is_playing":true}'
```

Windows Command Prompt:

```bat
curl -X POST http://192.168.1.50:9012/api/now-playing -H "Content-Type: application/json" -d "{\"device_id\":\"test\",\"device_name\":\"Test PC\",\"title\":\"Blinding Lights\",\"artist\":\"The Weeknd\",\"position\":30,\"duration\":200,\"is_playing\":true}"
```

A single request only shows for about 30 seconds, since nothing keeps sending.

### Home Assistant

Any `media_player` in Home Assistant can feed SyncLyrics, including Sonos speakers playing from the Sonos app. Add a REST command to `configuration.yaml`:

```yaml
rest_command:
  synclyrics_now_playing:
    url: "http://192.168.1.50:9012/api/now-playing"
    method: POST
    content_type: "application/json"
    # headers:
    #   Authorization: "Bearer some-long-random-string"   # only if you set a token
    payload: >
      {% set e = 'media_player.living_room' %}
      {% set playing = is_state(e, 'playing') %}
      {% set pos = state_attr(e, 'media_position') %}
      {% set updated = state_attr(e, 'media_position_updated_at') %}
      {% if pos is not none and updated and playing %}
        {% set pos = pos + (now() - updated).total_seconds() %}
      {% endif %}
      {{ {
        "device_id": "living_room",
        "device_name": "Living Room",
        "title": state_attr(e, 'media_title'),
        "artist": state_attr(e, 'media_artist'),
        "album": state_attr(e, 'media_album_name'),
        "position": pos,
        "duration": state_attr(e, 'media_duration'),
        "is_playing": playing
      } | to_json }}
```

Then an automation that sends it on every change, and every 10 seconds as a heartbeat:

```yaml
automation:
  - alias: "SyncLyrics: Living Room now playing"
    mode: restart
    triggers:
      - trigger: state
        entity_id: media_player.living_room
      - trigger: time_pattern
        seconds: "/10"
    conditions:
      - condition: template
        value_template: "{{ state_attr('media_player.living_room', 'media_title') is not none }}"
    actions:
      - action: rest_command.synclyrics_now_playing
```

Home Assistant reports the position as of its last update, so the template adds the time since then. When the speaker turns off, updates stop and SyncLyrics drops it after the paused timeout.

### Android (Tasker, MacroDroid, Automate)

Use your automation app's **HTTP Request** action:

- **Method:** POST
- **URL:** `http://192.168.1.50:9012/api/now-playing`
- **Headers:** `Content-Type: application/json` (plus `Authorization: Bearer ...` if you set a token)
- **Body:** the JSON above, filled in from the app's music variables

Trigger it when the song or play state changes, and repeat every 5-10 seconds while music plays. Send the position if your app can get it; without it, lyrics show but can't follow the song.

### Your own app or script

Anything that can send JSON works. Keep one `device_id` per device, send on every change plus a heartbeat while playing, and send `"stopped": true` when the player closes. To read lyrics back out of SyncLyrics, see [Use SyncLyrics as a lyrics server](API%20Reference.md#use-synclyrics-as-a-lyrics-server).

---

## Pull sources

These read a music app's own local API. Switch them on in **Settings > Media**; all are off by default. If the app runs on a different computer from SyncLyrics, put that computer's address in the source's URL setting.

| Source | App | Default address | Notes |
|---|---|---|---|
| Pear Desktop | [Pear Desktop](https://github.com/pear-devs/pear-desktop) (YouTube Music) | `http://127.0.0.1:26538` | Enable its API Server plugin. |
| tidal-hifi | [tidal-hifi](https://github.com/Mastermindzh/tidal-hifi), a TIDAL desktop app | `http://127.0.0.1:47836` | Enable its API in tidal-hifi's settings. For another computer, set its API hostname to `0.0.0.0`. |
| smtc-now-playing | [smtc-now-playing](https://github.com/soarqin/smtc-now-playing) | `http://127.0.0.1:11451` | Follows **any** player on a Windows PC. See the note below. |

**About smtc-now-playing:** it's a small third-party Windows app, not part of SyncLyrics. It lets a Docker or Home Assistant install follow whatever is playing on a Windows PC. Things to know before installing it:

- It's a one-person project with few users, and its `.exe` isn't code-signed.
- While it runs, other devices on your network can read what's playing on that PC, and can also play, pause and skip through its WebSocket.

If you'd rather not run it, a Windows machine can push to SyncLyrics instead (see [Push](#push)).
