# 🐳 SyncLyrics Docker

Run SyncLyrics as a standalone Docker container for use with Home Assistant dashboards, media rooms, or any web-accessible display.

## Quick Start

### Using Docker Run

```bash
docker run -d \
  --name synclyrics \
  -p 9012:9012 \
  -e SPOTIFY_CLIENT_ID=your_client_id \
  -e SPOTIFY_CLIENT_SECRET=your_client_secret \
  -e SPOTIFY_REDIRECT_URI=http://localhost:9012/callback \
  -v synclyrics_data:/data \
  anshulj999/synclyrics:latest
```

### Using Docker Compose

1. Download the [docker-compose.yml](../docker/docker-compose.yml) file
2. Edit and add your Spotify credentials
3. Run:

```bash
docker-compose up -d
```

4. Open http://localhost:9012 in your browser
5. Complete Spotify authentication when prompted

## Getting Spotify Credentials

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Create a new app
3. Set the Redirect URI:
   - For localhost: `http://localhost:9012/callback` (HTTP works)
   - For remote/network access: `https://<YOUR_IP>:9013/callback` (HTTPS required)
4. Copy the Client ID and Client Secret

> **Note**: Spotify OAuth requires HTTPS for any address other than `127.0.0.1` or `localhost`.

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `SPOTIFY_CLIENT_ID` | Your Spotify app's Client ID |
| `SPOTIFY_CLIENT_SECRET` | Your Spotify app's Client Secret |

### Recommended

| Variable | Default | Description |
|----------|---------|-------------|
| `SPOTIFY_REDIRECT_URI` | `http://localhost:9012/callback` | OAuth callback URL - change to match your host |
| `SPOTIFY_BASE_URL` | - | Self-hosted [Spotify Lyrics API](https://github.com/akashrchandran/spotify-lyrics-api) URL |

### Optional API Keys

| Variable | Description |
|----------|-------------|
| `LASTFM_API_KEY` | Enhanced metadata from Last.fm |
| `FANART_TV_API_KEY` | High-quality artist images (get key at [fanart.tv](https://fanart.tv/)) |
| `AUDIODB_API_KEY` | Backup artist images (free key: `523532`) |

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SERVER_PORT` | `9012` | Web server port |
| `DEBUG_ENABLED` | `false` | Enable debug mode |
| `DEBUG_LOG_LEVEL` | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR |
| `SPOTIFY_POLLING_FAST_INTERVAL` | `2.0` | Seconds between polls (active) |
| `SPOTIFY_POLLING_SLOW_INTERVAL` | `6.0` | Seconds between polls (idle) |

## Persistent Data

Mount `/data` to persist:
- Lyrics database
- Album art cache
- Spotify tokens
- Settings and preferences

```bash
-v /path/to/your/data:/data
# or use a named volume:
-v synclyrics_data:/data
```

## Home Assistant Integration

### Iframe Card

Add SyncLyrics to your Home Assistant dashboard using an iframe card:

```yaml
type: iframe
url: http://YOUR_DOCKER_HOST:9012
aspect_ratio: 16:9
```

### Lovelace Example

```yaml
views:
  - title: Music
    cards:
      - type: iframe
        url: http://192.168.1.100:9012
        aspect_ratio: 16:9
        title: Now Playing
```

## Multi-Architecture Support

The Docker image supports:
- `linux/amd64` - Standard PCs and servers
- `linux/arm64` - Raspberry Pi 4/5, Apple Silicon
- `linux/arm/v7` - Raspberry Pi 3, older ARM devices

## Updating

```bash
# Docker Compose
docker-compose pull
docker-compose up -d

# Docker Run
docker pull anshulj999/synclyrics:latest
docker stop synclyrics
docker rm synclyrics
# Re-run your docker run command
```

## Troubleshooting

### Container won't start

Check logs:
```bash
docker logs synclyrics
```

Common issues:
- Missing Spotify credentials
- Port 9012 already in use (change with `-p 9013:9012`)

### Spotify authentication fails

Ensure your Redirect URI in Spotify Developer Dashboard matches `SPOTIFY_REDIRECT_URI` exactly.

### Lyrics not loading

- Check if you have a Spotify Premium account (required for some features)
- Consider hosting your own [Spotify Lyrics API](https://github.com/akashrchandran/spotify-lyrics-api)

## Image Registries

SyncLyrics is available from:

- **Docker Hub**: `anshulj999/synclyrics`
- **GitHub Container Registry**: `ghcr.io/anshulj999/synclyrics`

Both registries host identical images.

## Building Locally

```bash
git clone https://github.com/AnshulJ999/SyncLyrics.git
cd SyncLyrics
docker build -f docker/Dockerfile -t synclyrics:local .
```
