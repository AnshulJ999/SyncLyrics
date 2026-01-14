# Spicetify Integration

Spicetify is a powerful customization tool for Spotify Desktop. SyncLyrics includes a custom bridge extension that provides significant improvements over standard Spotify API polling.

> **Requirement**: Spicetify only works with the **Spotify Desktop Application** with **Spicetify already installed and setup + the SyncLyrics Bridge extension installed** (Windows/Linux/macOS). It does not work with the Web Player or mobile apps. See Spicetify docs for more info. 

> **Benefit:** The main benefit of Spicetify is that it enables access to the audio analysis data. As Spotify deprecated audio analysis API publicly, this remains the only way to reliably access that data (using internal endpoints). With that data, we can create an accurate waveform seekbar (similar to DAWs or MusicBee) and a spectrum visualizer. 

Position accuracy also improves, which is beneficial for karaoke/word-sync which requires accurate position data.

## What Spicetify Enables

| Feature | Without Spicetify | With Spicetify |
|---------|------------------|----------------|
| Position updates | 4-5 second polling | ~100ms real-time |
| Waveform seekbar | ❌ Not available | ✅ Full waveform |
| Spectrum visualizer | ❌ Not available | ✅ Full spectrum |
| Queue with autoplay | ❌ Partial (no autoplay) | ✅ Complete queue |
| Playback control | Via API (delayed) | Instant via WebSocket |
| Artist gallery images | ❌ | ✅ Via GraphQL |

## Installation

### Prerequisites
1. Install [Spicetify](https://spicetify.app/docs/getting-started)
2. Verify it's working: `spicetify -v`

### Install the Bridge Extension

1. **Copy the extension file**:
   ```
   %APPDATA%\spicetify\Extensions\synclyrics-bridge.js
   ```
   The file is located at `spicetify/synclyrics-bridge.js` in the SyncLyrics folder.

2. **Register the extension**:
   ```bash
   spicetify config extensions synclyrics-bridge.js
   ```

3. **Apply changes**:
   ```bash
   spicetify apply
   ```

4. **Restart Spotify** if it was running.

### Verify Installation
Open Spotify Desktop and check the DevTools console (Ctrl+Shift+I → Console). You should see:
```
[SyncLyrics] Connected to ws://127.0.0.1:9012/ws/spicetify
```

## Multi-Server Configuration

The bridge can connect to multiple SyncLyrics servers simultaneously (e.g., local machine + Home Assistant).

Edit the `CONFIG.WS_URLS` array in `synclyrics-bridge.js`:
```javascript
WS_URLS: [
    'ws://127.0.0.1:9012/ws/spicetify',      // Local
    'ws://192.168.1.100:9012/ws/spicetify',   // HASS/Tablet
],
```

Note: The Spotify desktop app does not allow insecure Websocket connections over the LAN. It only allows localhost. 

To allow this, please modify your Spotify shortcut with these flags: 

`--unsafely-treat-insecure-origin-as-secure=ws://192.168.1.100:9012` - This will bypass the security warning and allow the connection to be established. Make sure to change the IP to match your real LAN IP.

For example

```
C:\Users\[User Name]\AppData\Roaming\Spotify\Spotify.exe --disable-renderer-backgrounding --disable-background-timer-throttling --unsafely-treat-insecure-origin-as-secure=ws://192.168.1.100:9012
```

## Features Provided

### Real-Time Position
Position updates every ~100ms instead of 4-5 second polling intervals. This is essential for accurate word-sync timing.

### Audio Analysis
The extension fetches Spotify's audio analysis data, which powers:
- **Waveform seekbar**: Shows loudness over time
- **Spectrum visualizer**: Frequency visualization

Without Spicetify, these features are unavailable because Spotify's Web API requires track-by-track authorization for audio analysis.

### Complete Queue
Spicetify exposes the full queue including:
- User-added tracks
- **Autoplay tracks** (not available via Web API)
- Queue order and providers

### Artist Gallery Images
Fetches high-quality artist images from Spotify's GraphQL API, used in the slideshow.

## Troubleshooting

### Extension not connecting
- Check that SyncLyrics is running on port 9012
- Verify the WebSocket URL matches your server
- Look for errors in Spotify's console (Ctrl+Shift+I)

### Waveform still not showing
- Make sure the extension is installed and connected
- Enable "Waveform Seekbar" in display settings
- Audio analysis may take a moment to load on song change

### Uninstall
```bash
spicetify config extensions synclyrics-bridge.js-
spicetify apply
```
