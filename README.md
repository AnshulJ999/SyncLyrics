# 🎵 SyncLyrics

A lightweight, cross-platform desktop application that displays synchronized lyrics for your currently playing song on Spotify and Windows Media.

![Main UI](screenshots/SyncLyrics_Main_UI.png)

## ✨ Features
*   **Instant Sync:** Fetches time-synced lyrics from LRCLib, NetEase, Spotify, and QQ Music.
*   **Parallel Search:** Queries all providers simultaneously for zero lag.
*   **Cross-Platform:** Works on Windows (native media integration) and Linux (via playerctl).
*   **Customizable:** Dark/Light themes, transparency, and minimized "Overlay" mode.
*   **Resource Efficient:** Smart caching ensures <1% CPU usage.

## 🚀 Installation

### Option 1: Download Executable (Windows)
1.  Go to the **[Releases](../../releases)** page.
2.  Download `SyncLyrics.zip`.
3.  Extract and run `SyncLyrics.exe`.

### Option 2: Run from Source
1.  Install Python 3.10+.
2.  Clone the repo:
    ```bash
    git clone https://github.com/AnshulJ999/SyncLyrics.git
    cd SyncLyrics
    ```
3.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    # For Windows Media support (included in requirements.txt):
    # pip install winsdk
    ```
4.  Create your configuration:
    *   Rename `.env.example` to `.env`.
    *   (Optional) Add your Spotify Client ID/Secret for better reliability.
5.  Run it:
    ```bash
    python sync_lyrics.py
    ```

## 🛠️ Build
To create a standalone executable:

```bash
python build.py
```
This will generate the executable in `build_final/SyncLyrics/SyncLyrics.exe`.

## ⚙️ Configuration
You can configure the app via the System Tray icon -> Settings, or by editing `config.py` / `.env`.

| Setting | Description |
| :--- | :--- |
| `ENABLE_PARALLEL_FETCH` | Speed up search by asking all providers at once. |
| `CACHE_DURATION_DAYS` | How long to keep lyrics offline (Default: 30 days). |
| `SPOTIFY_LYRICS_SERVER` | Custom Spotify lyrics API server. |

## 🤝 Contributing
Pull requests are welcome! Please make sure to update tests as appropriate.

## 📜 License
[MIT](LICENSE)

## ❤️ Credits
Based on the original work by [Konstantinos Petrakis](https://github.com/konstantinospetrakis).

**Libraries used:**
*   [Lyricify](https://github.com/WXRIW/Lyricify-Lyrics-Helper)
*   [Syncedlyrics](https://github.com/moehmeni/syncedlyrics)
*   [spotify-lyrics-api](https://github.com/akashrchandran/spotify-lyrics-api)



