# SyncLyrics

A real-time synced lyrics server that can run on multiple platforms and serves beautiful lyrics to any device with a web-browser.

The app's philosophy is simple: configure it once and let it run in the background all the time. When you're not listening to music; it does nothing. When you are; it activates and shows you lyrics + album art + rich metadata.

This started as a hobby project where I just wanted real-time lyrics on any of my tablet devices, but has grown to be a feature-rich self-hosted lyrics server. SyncLyrics is a visual companion to all music, anywhere. 

**Supported Platforms:** Windows, Home Assistant, Docker, Linux, macOS (coming soon)

**Supported Audio Sources:** Spotify, Windows Media (SMTC), Music Assistant, Audio Recognition (Shazam), Linux, macOS, and Spicetify. 

![Main UI](<screenshots/SyncLyrics Main UI.png>)

_Main UI_

![Minimal Mode](<screenshots/Minimal Mode.png>) 

_Minimal Mode can be accessed by adding ?minimal=true to the URL_

https://github.com/user-attachments/assets/ddb9fd10-f082-44c3-ab36-563fca2cc75e

_Video demo showcasing the app's main features_

[More Screenshots](<screenshots/>)

## ✨ Features

### 🎵 Lyrics
- **5 Providers:** Spotify, LRCLib, Musixmatch, NetEase, QQ Music
- **Word-Sync (Karaoke):** Highlights each word as it's sung
- **Parallel Search:** Queries all providers simultaneously for fastest results
- **Local Caching:** Saves lyrics offline for instant future access
- **Provider Selection:** Manually choose your preferred provider per song
- **Instrumental Detection:** Automatically detects and marks instrumental tracks

### 🎨 Visual Modes
- **Background Styles:** Sharp, Soft, and Blur modes for album art display
- **Visual Mode:** Activates during instrumentals with optional artist image slideshow
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
- Waveform seekbar with audio analysis visualization (Spicetify Required)
- Spectrum visualizer (Spicetify Required)
- **Keyboard shortcuts:** Space, arrows, and letter keys for quick control

### ⚡ Spicetify Integration
- **Real-time Updates:** ~100ms position updates via WebSocket
- **Audio Analysis:** Enables waveform and spectrum features
- **Queue with Autoplay:** Full queue including suggested tracks
- See [Spicetify Integration](docs/Spicetify%20Integration.md) for setup

### ⚙️ Configuration
- **Web Settings Page:** Full configuration UI at `/settings`
- **URL Parameters:** Customize display for embedding/OBS
- **Environment Variables:** Docker/HASS-friendly configuration
- **Modular Settings:** See [Configuration Reference](docs/Configuration%20Reference.md)

---

## Quick Start: 

1) Install the app using your preferred method. 
2) Visit `http://synclyrics.local:9012` or `http://localhost:9012` in your browser.
3) Login to Spotify if needed (optional)
4) Play music from a supported source and watch the lyrics on screen!

**Tip:** Embed it as an iFrame in any existing dashboard or run it standalone inside Fully Kiosk Browser. Make sure the app is fullscreen for the best experience; and keep your device plugged-in as running it continuously can be a battery drain.

## 🚀 Installation

### Option 1: Windows Executable
1. Go to **[Releases](../../releases)**
2. Download and extract `SyncLyrics-vX.X.X-windows-x64.zip` anywhere on your computer. Ensure all files are within a dedicated folder.
3. Run `SyncLyrics.exe`
4. (Optional) Configure `.env.example` for Spotify API credentials and other advanced features then rename it to `.env`.

#### **Updating:** When updating the app, please delete these 2 folders: 

`_internal`

`resources`

You can also delete `SyncLyrics.exe` for safety.

Then extract the new version and replace any old files. This should maintain your existing database and settings (including Spotify cache) while avoiding any conflict from previous versions.

### Option 2: Linux (AppImage or Tarball)
1. Go to **[Releases](../../releases)**
2. Download either:
   - `SyncLyrics-vX.X.X-linux-x64.AppImage` (recommended - single file, no install)
   - `SyncLyrics-vX.X.X-linux-x64.tar.gz` (for developers)
3. For AppImage:
   ```bash
   chmod +x SyncLyrics-*.AppImage
   ./SyncLyrics-*.AppImage
   ```
4. For Tarball:
   ```bash
   tar -xzf SyncLyrics-*.tar.gz
   cd SyncLyrics
   ./SyncLyrics
   ```

> **Note:** Linux builds require `playerctl` for media detection. Install via your package manager.

### Option 3: macOS (Unsigned)
1. Go to **[Releases](../../releases)**
2. Download:
   - `SyncLyrics-vX.X.X-macos-x64.zip` (Intel Macs)
   - `SyncLyrics-vX.X.X-macos-arm64.zip` (Apple Silicon)
3. Extract the zip - you'll get a `SyncLyrics` folder
4. **First launch** (bypass Gatekeeper):
   ```bash
   cd /path/to/SyncLyrics
   xattr -cr .  # Remove quarantine attributes
   ./sync_lyrics
   ```
   Or right-click the executable → Open → click "Open" in the warning dialog.

> **Note:** macOS builds are unsigned, so you'll need to allow them through Gatekeeper on first run.

For full support on macOS, it is recommended to install `nowplaying-cli` via Homebrew. Simply run this command: 

```bash
brew install nowplaying-cli
```

Without it, only Apple Music and Spotify are detected. With it, any app that shows in Control Center works (Firefox, VLC, etc.).

### Option 4: Home Assistant Addon

1. Add https://github.com/AnshulJ999/homeassistant-addons as a repository to your Home Assistant addon store
2. Install the SyncLyrics addon
3. Configure environment variables in addon settings
4. Start the addon and access via direct URL

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Addon-blue)](https://github.com/AnshulJ999/homeassistant-addons)

### Option 5: Run from Source

You can use the included run.bat or 'Run SyncLyrics Hidden.vbs' to run the app directly. Install the requirements first. 

Clone the repo and run directly with Python:

```bash
git clone https://github.com/AnshulJ999/SyncLyrics.git

cd SyncLyrics

pip install -r requirements.txt

# Edit with your credentials
copy .env.example .env  

python sync_lyrics.py
```

### Option 6: Docker

Docker images are available from:
- **Docker Hub**: `anshulj99/synclyrics`
- **GitHub Container Registry**: `ghcr.io/anshulj999/synclyrics`

1. Download [docker-compose.yml](docker/docker-compose.yml)
2. Edit with your Spotify credentials
3. Run: `docker-compose up -d`
4. Open: http://localhost:9012

➡️ [Docker Reference](docs/Docker%20Reference.md) for all configuration options.

---

## UI UX Shortcuts

The UI supports many gestures and shortcuts, such as: 

1) **Long-press:** Several buttons can be long-pressed to access more features. For example: 
a) Visual Mode Toggle: Holding this will lead to 'Art Mode', which hides all on-screen elements to focus on the album art. Combine with Slideshow to turn your device into an art slideshow using artist images. Long-press on a screen corner or press ESC to exit it. 
b) Slideshow Icon: Holding this will show the Slideshow Menu, where you can configure all slideshow settings and exclude images you don't want. 

2) **Three-finger tap** will play/pause the music. This is for touchsceen devices. 

3) **Four-finger tap** will start/stop the slideshow.

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

> **Note**: Spotify OAuth works with `localhost`/`127.0.0.1` over HTTP, but requires HTTPS for any other address. For remote access, use `https://<YOUR_IP>:9013/callback`.

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

To create a standalone Windows/Linux/macOS executable yourself:

Clone the repo and then run this command: 

```bash
python build.py
```

Output: `build_final/SyncLyrics/`

---

## 📚 Documentation

Detailed guides for all features:
- [Quick Start](docs/Quick%20Start.md) - Get running in 5 minutes
- [FAQ](docs/FAQ.md) - Common questions answered
- [Features Overview](docs/Features%20Overview.md)
- [Linux Support](docs/Linux%20Support.md) - MPRIS via playerctl
- [macOS Support](docs/macOS%20Support.md) - Now Playing via nowplaying-cli
- [Word Sync and Karaoke](docs/Word%20Sync%20and%20Karaoke.md)
- [Visual Modes and Slideshow](docs/Visual%20Modes%20and%20Slideshow.md)
- [Audio Recognition](docs/Audio%20Recognition.md)
- [Spicetify Integration](docs/Spicetify%20Integration.md)
- [Docker Reference](docs/Docker%20Reference.md)
- [Configuration Reference](docs/Configuration%20Reference.md)
- [Latency Tuning Guide](docs/Latency%20Tuning%20Guide.md) - Calibrate lyrics timing
- [Troubleshooting](docs/Troubleshooting.md)
- [Development Reference](docs/Development%20Reference.md)

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

See [Troubleshooting Guide](docs/Troubleshooting.md) for detailed solutions.

---

## 🤝 Contributing

Found a bug? Have an idea? PRs are super welcome! 🙌 Just give it a quick test on Windows or HASS before submitting. Even small fixes help!

The extensible plugin system makes it easy to add new metadata sources, so I welcome any requests and contributions.

---

## 📜 License

[MIT + Commons Clause](LICENSE) — Free for personal and non-commercial use. Commercial use (selling, paid hosting, paid services) is not permitted.

---

## ⚠️ Disclaimer (AI Usage)

This project was built with AI assistance (I spent over 300+ hours on it myself). It works great for my use case, but if you find rough edges, PRs and feedback are always welcome!

---

## ☕ Support This Project

If this project has been useful to you, consider supporting its development: 

[![PayPal](https://img.shields.io/badge/PayPal-Donate-blue?logo=paypal)](https://paypal.me/AnshulJain99)

## ❤️ Credits

Based on the original work by [Konstantinos Petrakis](https://github.com/konstantinospetrakis).

**Libraries & APIs:**
- [ShazamIO](https://github.com/shazamio/shazamio) - Audio recognition
- [Spotipy](https://github.com/spotipy-dev/spotipy) - Spotify API
- [LRCLib](https://lrclib.net/) - Lyrics database
- [Quart](https://github.com/pallets/quart) - Async web framework
- [Spotify Lyrics](https://github.com/akashrchandran/spotify-lyrics-api) - Spotify lyrics proxy
