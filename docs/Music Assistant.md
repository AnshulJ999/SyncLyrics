# Music Assistant Integration

This guide explains how to connect SyncLyrics to [Music Assistant](https://music-assistant.io/) for real-time lyrics display.

## Prerequisites

- Music Assistant server running (standalone or Home Assistant add-on)
- API token (generated in MA web UI)

## Getting Your Credentials

### 1. Server URL

Your Music Assistant server URL is typically:
- `http://<IP_ADDRESS>:8095` (standalone)
- `http://<HA_IP>:8095` (Home Assistant add-on)

### 2. API Token

1. Open your Music Assistant web UI
2. Go to **Settings** → **Security**
3. Click **Create Token**
4. Give it a name (e.g., "SyncLyrics")
5. Copy the generated token

> ⚠️ **Security Note**: The token grants full access to your MA server. Keep it secure.

## Configuration

### Recommended: Environment Variables

Set these in your `.env` file or system environment:

```bash
SYSTEM_MUSIC_ASSISTANT_SERVER_URL=http://192.168.1.100:8095
SYSTEM_MUSIC_ASSISTANT_TOKEN=your_token_here
```

For Docker/Home Assistant, add as environment variables in your deployment config.

### Alternative: settings.json

> ⚠️ **Not Recommended**: `settings.json` is not secure for sensitive data like tokens.

```json
{
    "system.music_assistant.server_url": "http://192.168.1.100:8095",
    "system.music_assistant.token": "your_token_here",
    "system.music_assistant.player_id": ""
}
```

### Optional: Specific Player

If you have multiple players and want to target a specific one:

```bash
SYSTEM_MUSIC_ASSISTANT_PLAYER_ID=your_player_id
```

Leave empty to auto-detect (uses first playing/paused player).

## Features

| Feature | Supported |
|---------|-----------|
| Real-time metadata | ✅ |
| Album art | ✅ |
| Playback controls | ✅ |
| Seek | ✅ |
| Queue display | ✅ |
| Like/Favorites | ✅ |
| Multi-player auto-detect | ✅ |

## Latency Tuning

### The Problem

Music Assistant streams audio over your network to various players (Chromecast, Sonos, AirPlay, etc.). This introduces latency that varies by:
- Network conditions
- Player type and buffer settings
- MA server load

Without compensation, lyrics may appear **too early** because SyncLyrics receives position updates before the audio actually plays on your speaker.

### The Solution

Add a latency offset to delay lyrics display:

```json
"lyrics.display.music_assistant_latency_compensation": -0.5,
```

This value is in **seconds**. Start with `-0.5` and adjust:
- **Lyrics too early** → Increase the value (e.g., -0.2, -0.1)
- **Lyrics too late** → Decrease the value (e.g., -0.6, -0.7)

### Tuning Tips

1. Play a song you know well
2. Watch if lyrics appear before or after the vocals
3. Adjust in 0.1 increments until synced
4. Different players may need different values

## Troubleshooting

### "No Music Assistant player available"

- Ensure MA server is running and accessible
- Check that at least one player exists in MA
- Verify your server URL is correct

### Connection keeps dropping

- Check network stability between SyncLyrics and MA server
- MA server may be restarting or updating
- SyncLyrics auto-reconnects with exponential backoff

### Like button not working

- Ensure the track exists in your MA library
- Some streaming providers may not support favorites
- Check MA logs for errors

### Wrong player detected

Set `SYSTEM_MUSIC_ASSISTANT_PLAYER_ID` to your preferred player's ID.

To find your player ID:
1. Open MA web UI
2. Go to **Players**
3. Click on your player
4. The ID is in the URL or settings panel

## Connection Status

SyncLyrics maintains a persistent WebSocket connection to MA. When connected:
- Metadata updates in real-time
- No polling overhead
- Automatic reconnection on disconnect

Check your SyncLyrics logs for connection status:
```
INFO: Connected to Music Assistant
INFO: Music Assistant disconnected
DEBUG: Reconnecting to Music Assistant (attempt 2)
```
