# SyncLyrics

A feature-rich desktop and web application that displays synchronized lyrics for your currently playing music. Supports Spotify, Windows Media, and audio recognition.

This started as a hobby project where I just wanted real-time lyrics on any of my tablet devices, but has grown to become what I believe is one of the best lyrics apps out there (and will continue to grow in that direction). SyncLyrics aims to be a visual companion to the music experience, built for tablet dashboards.

> **Note:** Forked for personal use. Primarily tested on Windows 10/11 with Spotify desktop client. Linux *may* work but is untested. Can be used with Home Assistant as an addon. 

![Main UI](<screenshots/SyncLyrics Main UI.png>)

_Main UI_

![Minimal Mode](<screenshots/Minimal Mode.png>) 

_Minimal Mode can be accessed by adding ?minimal=true to the URL_

https://github.com/user-attachments/assets/ddb9fd10-f082-44c3-ab36-563fca2cc75e

_Video demo showcasing the app's main features_

## ✨ Features

### 🎵 Lyrics
- **4 Providers:** Spotify, LRCLib, NetEase, QQ Music
- **Parallel Search:** Queries all providers simultaneously for fastest results
- **Local Caching:** Saves lyrics offline for instant future access
- **Provider Selection:** Manually choose your preferred provider per song
- **Instrumental Detection:** Automatically detects and marks instrumental tracks

### 🎨 Visual Modes
- **Background Styles:** Sharp, Soft, and Blur modes for album art display
- **Visual Mode:** Activates during instrumentals with artist image slideshow
- **Album Art Database:** Caches high-quality art from iTunes, Spotify and LastFM (requires API key)
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
4. (Optional) Configure `.env` for Spotify API credentials and other advanced features.

### Option 2: Home Assistant Addon
1. Add https://github.com/AnshulJ999/homeassistant-addons as a repository to your Home Assistant addon store
2. Install the SyncLyrics addon
3. Configure environment variables in addon settings
4. Start the addon and access via Ingress or direct URL

### Option 3: Run from Source

You can use the included run.bat or 'Run SyncLyrics Hidden.vbs' to run the app directly. Install the requirements first. 

```bash
git clone https://github.com/AnshulJ999/SyncLyrics.git
cd SyncLyrics
pip install -r requirements.txt
copy .env.example .env  # Edit with your credentials
python sync_lyrics.py
```

---

## ⚙️ Configuration

The app works best with a Spotify API connection, which requires you to create a custom app in your Spotify Developer Dashboard. 

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

These can easily be configured via the on-screen settings panel and the URL can be copied. 

### HTTPS (Required for Browser Microphone)

To use the browser microphone for audio recognition, HTTPS is required.

HTTPS is **enabled by default** for browser microphone access:

- **HTTP:** `http://localhost:9012` (for local use)
- **HTTPS:** `https://localhost:9013` (for mic access on tablets/phones)

The app auto-generates a self-signed certificate. You'll need to accept the browser's security warning on first use.

---

## 🛠️ Build

To create a standalone Windows executable yourself:

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
- Check that your media player supports Windows SMTC (System Media Transport Controls) (MusicBee requires a special plugin to support SMTC)
- Some apps (browsers, games) may be blocklisted - check settings and remove them from blocklist if needed. 

### Audio Recognition Not Working
- **Backend mode:** Ensure you have a loopback audio device (e.g., VB-Cable, WASAPI loopback)
- **Frontend mode:** HTTPS is required for browser microphone access

---

## 🤝 Contributing

Found a bug? Have an idea? PRs are super welcome! 🙌 Just give it a quick test on Windows or HASS before submitting. Even small fixes help!

---

## 📜 License

[MIT](LICENSE)

---

## ⚠️ Disclaimer (AI Usage)

This project was built with significant AI assistance (yes, vibe-coded 🤖). It works great for my use case, but if you find rough edges, PRs are always welcome!

---

## ❤️ Credits

Based on the original work by [Konstantinos Petrakis](https://github.com/konstantinospetrakis).

**Libraries & APIs:**
- [ShazamIO](https://github.com/shazamio/shazamio) - Audio recognition
- [Spotipy](https://github.com/spotipy-dev/spotipy) - Spotify API
- [LRCLib](https://lrclib.net/) - Lyrics database
- [Quart](https://github.com/pallets/quart) - Async web framework
- [Spotify Lyrics](https://github.com/akashrchandran/spotify-lyrics-api) - Spotify lyrics proxy
