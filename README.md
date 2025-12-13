# SyncLyrics

A feature-rich desktop and web application that displays synchronized lyrics for your currently playing music. Supports Spotify, Windows Media, and audio recognition (Shazam).

> **Note:** Forked for personal use. Primarily tested on Windows 10/11 with Spotify desktop client. Linux *may* work but is untested.

![Main UI](screenshots/SyncLyrics%20Main%20UI.png)

## ✨ Features

### 🎵 Lyrics
- **5 Providers:** Spotify, LRCLib, NetEase, QQ Music, Musicxmatch
- **Parallel Search:** Queries all providers simultaneously for fastest results
- **Local Caching:** Saves lyrics offline for instant future access
- **Provider Selection:** Manually choose your preferred provider per song
- **Instrumental Detection:** Automatically detects and marks instrumental tracks

### 🎨 Visual Modes
- **Background Styles:** Sharp, Soft, and Blur modes for album art display
- **Visual Mode:** Activates during instrumentals with artist image slideshow
- **Album Art Database:** Caches high-quality art from iTunes, Deezer, Spotify, MusicBrainz
- **Artist Images:** Fetches from Deezer, FanArt.tv, TheAudioDB, Spotify

### 🎤 Audio Recognition
- **Shazam-Powered:** Identify any song playing through your speakers or microphone
- **Two Capture Modes:**
  - Backend: Captures system audio via loopback device
  - Frontend: Uses browser microphone (requires HTTPS)
- **Reaper DAW Integration:** Auto-detects Reaper and starts recognition

### 🎛️ Playback Controls
- Play/Pause, Next, Previous track controls
- Like/Unlike tracks (Spotify)
- View playback queue
- Seek bar with progress display

### ⚙️ Configuration
- **Web Settings Page:** Full configuration UI at `/settings`
- **URL Parameters:** Customize display for embedding/OBS
- **Environment Variables:** Docker/HASS-friendly configuration

---

## 🚀 Installation

### Option 1: Windows Executable
1. Go to **[Releases](../../releases)**
2. Download and extract `SyncLyrics.zip`
3. Run `SyncLyrics.exe`
4. (Optional) Configure `.env` for Spotify API credentials

### Option 2: Home Assistant Addon
1. Add this repository to your Home Assistant addon store
2. Install the SyncLyrics addon
3. Configure environment variables in addon settings
4. Start the addon and access via Ingress or direct URL

### Option 3: Docker
```bash
docker run -d \
  -p 9012:9012 \
  -v /path/to/config:/config \
  -e SPOTIFY_CLIENT_ID=your_id \
  -e SPOTIFY_CLIENT_SECRET=your_secret \
  synclyrics
```

### Option 4: Run from Source
```bash
git clone https://github.com/AnshulJ999/SyncLyrics.git
cd SyncLyrics
pip install -r requirements.txt
cp .env.example .env  # Edit with your credentials
python sync_lyrics.py
```

---

## ⚙️ Configuration

### Key Environment Variables

| Variable | Description |
|----------|-------------|
| `SPOTIFY_CLIENT_ID` | Spotify API client ID |
| `SPOTIFY_CLIENT_SECRET` | Spotify API client secret |
| `SPOTIFY_REDIRECT_URI` | OAuth callback URL (default: `http://127.0.0.1:9012/callback`) |
| `SERVER_PORT` | Web server port (default: 9012) |
| `FANART_TV_API_KEY` | FanArt.tv API key for artist images |
| `LASTFM_API_KEY` | Last.fm API key for album art |

### URL Parameters

Append these to the URL for custom displays (e.g., `http://localhost:9012/?minimal=true`):

| Parameter | Values | Description |
|-----------|--------|-------------|
| `minimal` | `true/false` | Hide all UI except lyrics |
| `sharpAlbumArt` | `true/false` | Sharp album art background |
| `softAlbumArt` | `true/false` | Soft (medium blur) background |
| `artBackground` | `true/false` | Blurred album art background |
| `hideControls` | `true/false` | Hide playback controls |
| `hideProgress` | `true/false` | Hide progress bar |

### HASS/Docker Paths

For persistent storage in containers, set these environment variables:

```env
SYNCLYRICS_SETTINGS_FILE=/config/settings.json
SYNCLYRICS_LYRICS_DB=/config/lyrics_database
SYNCLYRICS_ALBUM_ART_DB=/config/album_art_database
SYNCLYRICS_CACHE_DIR=/config/cache
SPOTIPY_CACHE_PATH=/config/.spotify_cache
```

---

## 🛠️ Build

To create a standalone Windows executable:

```bash
python build.py
```

Output: `build_final/SyncLyrics/SyncLyrics.exe`

---

## 🐛 Troubleshooting

### Spotify Authentication
- Ensure `SPOTIFY_REDIRECT_URI` matches exactly what's registered in your Spotify Developer Dashboard
- For HASS, use your actual access URL (not `127.0.0.1`)

### Windows Media Not Detected
- Check that your media player supports Windows SMTC (System Media Transport Controls)
- Some apps (browsers, games) may be blocklisted - check settings

### Audio Recognition Not Working
- **Backend mode:** Ensure you have a loopback audio device (e.g., VB-Cable, WASAPI loopback)
- **Frontend mode:** HTTPS is required for browser microphone access

---

## 🤝 Contributing

Pull requests are welcome! Please test your changes on Windows before submitting.

---

## 📜 License

[MIT](LICENSE)

---

## ⚠️ Disclaimer

This application uses AI-assisted development. Much of the code has been written with AI assistance.

---

## ❤️ Credits

Based on the original work by [Konstantinos Petrakis](https://github.com/konstantinospetrakis).

**Libraries & APIs:**
- [ShazamIO](https://github.com/shazamio/shazamio) - Audio recognition
- [Spotipy](https://github.com/spotipy-dev/spotipy) - Spotify API
- [LRCLib](https://lrclib.net/) - Lyrics database
- [syncedlyrics](https://github.com/moehmeni/syncedlyrics) - Lyrics fetching
