# Quick Start

Get SyncLyrics running in 5 minutes.

## 1. Install

**Windows**: Download from [Releases](../../releases), extract, run `SyncLyrics.exe`

**Docker** (choose one):
```bash
# Docker Hub
docker run -d -p 9012:9012 -v synclyrics_data:/data anshulj99/synclyrics:latest

# GitHub Container Registry
docker run -d -p 9012:9012 -v synclyrics_data:/data ghcr.io/anshulj999/synclyrics:latest
```

**Home Assistant**: Add `https://github.com/AnshulJ999/homeassistant-addons` as a repository

## 2. Get Spotify Credentials

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
2. Click "Login with Spotify"
3. Authorize the app

## 5. Play Music

Start playing on Spotify and watch the lyrics appear!

---

## Next Steps

- [Features Overview](Features%20Overview.md) - See what's available
- [Spicetify Integration](Spicetify%20Integration.md) - Get real-time updates
- [Configuration Reference](Configuration%20Reference.md) - Customize everything
