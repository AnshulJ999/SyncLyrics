#!/bin/bash
set -e

echo "============================================"
echo "  SyncLyrics Docker Container Starting"
echo "============================================"

# Validate required environment variables
if [ -z "$SPOTIFY_CLIENT_ID" ] || [ -z "$SPOTIFY_CLIENT_SECRET" ]; then
    echo ""
    echo "ERROR: Spotify credentials not configured!"
    echo ""
    echo "Required environment variables:"
    echo "  - SPOTIFY_CLIENT_ID"
    echo "  - SPOTIFY_CLIENT_SECRET"
    echo ""
    echo "Get your credentials at: https://developer.spotify.com/dashboard"
    echo ""
    exit 1
fi

# Create persistent storage directories
mkdir -p "$SYNCLYRICS_LYRICS_DB"
mkdir -p "$SYNCLYRICS_ALBUM_ART_DB"
mkdir -p "$SYNCLYRICS_CACHE_DIR"
mkdir -p "$SYNCLYRICS_LOGS_DIR"
mkdir -p "$(dirname "$SPOTIPY_CACHE_PATH")"

# Log configuration (redact secrets)
echo ""
echo "Configuration:"
echo "  Server Port: ${SERVER_PORT:-9012}"
echo "  Debug: ${DEBUG_ENABLED:-false}"
echo "  Log Level: ${DEBUG_LOG_LEVEL:-INFO}"
echo "  Spotify Client ID: ${SPOTIFY_CLIENT_ID:0:8}..."
echo "  Data Directory: /data"
echo ""
echo "Optional APIs configured:"
[ -n "$LASTFM_API_KEY" ] && echo "  ✓ Last.fm"
[ -n "$FANART_TV_API_KEY" ] && echo "  ✓ FanArt.tv"
[ -n "$AUDIODB_API_KEY" ] && echo "  ✓ TheAudioDB"
[ -n "$SPOTIFY_BASE_URL" ] && echo "  ✓ Spotify Lyrics API: $SPOTIFY_BASE_URL"
echo ""
echo "============================================"
echo ""

# Set Linux defaults
export DESKTOP="Linux"

# Run SyncLyrics
exec python3 sync_lyrics.py
