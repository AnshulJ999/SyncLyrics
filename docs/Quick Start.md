# Quick Start

Get SyncLyrics running in 5 minutes.

## 1. Install

Follow installation instructions from main README to get up and running quickly. 

**Windows**: Download from [Releases](../../releases), extract, run `SyncLyrics.exe`

**Docker** (choose one):
```bash
# Docker Hub
docker run -d -p 9012:9012 -v synclyrics_data:/data anshulj99/synclyrics:latest

# GitHub Container Registry
docker run -d -p 9012:9012 -v synclyrics_data:/data ghcr.io/anshulj999/synclyrics:latest
```

**Home Assistant**: Add `https://github.com/AnshulJ999/homeassistant-addons` as a repository

## 2. Get Spotify Credentials (optional)

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Add Redirect URI:
   - Local: `http://127.0.0.1:9012/callback`
   - Remote: `https://<YOUR_IP>:9013/callback`
4. Copy Client ID and Client Secret

## 3. Configure

**Windows**: Edit `.env` file with your credentials

**Docker/HASS**: Set environment variables:
- `SPOTIFY_CLIENT_ID`
- `SPOTIFY_CLIENT_SECRET`
- `SPOTIFY_REDIRECT_URI`

## 4. Launch & Authenticate

1. Open `http://localhost:9012` (or `https://<IP>:9013` for remote)
2. A **Welcome** panel shows on first launch: what's already working, what still needs setting up, and the address to open on your tablet or phone
3. If you added Spotify credentials, click "Login with Spotify" and authorize the app

The full settings page (the gear icon, then the sliders button, or `/settings`) opens on an **Overview** page with the same status check, so you can come back to it anytime.

## 5. Play Music

Start playing on Spotify or any supported source and watch the lyrics appear! Music on a phone or another device can be sent in with [Now Playing Input](Now%20Playing%20Input.md).

---

## Next Steps

- [Features Overview](Features%20Overview.md) - See what's available
- [Spicetify Integration](Spicetify%20Integration.md) - Get real-time updates
- [Configuration Reference](Configuration%20Reference.md) - Customize everything
