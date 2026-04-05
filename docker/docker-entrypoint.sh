#!/bin/bash
set -e

echo "============================================"
echo "  SyncLyrics Docker Container Starting"
echo "============================================"

# Check for Spotify credentials (warn if missing, but don't exit)
if [ -z "$SPOTIFY_CLIENT_ID" ] || [ -z "$SPOTIFY_CLIENT_SECRET" ]; then
    echo ""
    echo "⚠️  WARNING: Spotify credentials not configured!"
    echo ""
    echo "   The app will start, but Spotify features won't work."
    echo "   Set these environment variables:"
    echo "     - SPOTIFY_CLIENT_ID"
    echo "     - SPOTIFY_CLIENT_SECRET"
    echo ""
    echo "   Get credentials at: https://developer.spotify.com/dashboard"
    echo ""
fi

# Create persistent storage directories
mkdir -p "$SYNCLYRICS_LYRICS_DB"
mkdir -p "$SYNCLYRICS_ALBUM_ART_DB"
mkdir -p "$SYNCLYRICS_SPICETIFY_DB"
mkdir -p "$SYNCLYRICS_CACHE_DIR"
mkdir -p "$SYNCLYRICS_LOGS_DIR"
mkdir -p "$SYNCLYRICS_CERTS_DIR"
mkdir -p "$(dirname "$SPOTIPY_CACHE_PATH")"

# Generate a random secret key for session security if not provided
if [ -z "$QUART_SECRET_KEY" ]; then
    export QUART_SECRET_KEY="docker-secret-$(date +%s)-$(head /dev/urandom | tr -dc A-Za-z0-9 | head -c 16)"
fi

# Get CPU model for logging
CPU_MODEL=""
if [ -f /proc/cpuinfo ]; then
    CPU_MODEL=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs)
fi

# Log configuration (show first 8 chars of secrets for debugging)
echo ""
echo "Configuration:"
echo "  Server Port: ${SERVER_PORT:-(not set)}"
echo "  Debug: ${DEBUG_ENABLED:-(not set)}"
echo "  Log Level: ${DEBUG_LOG_LEVEL:-(not set)}"
[ -n "$CPU_MODEL" ] && echo "  CPU: $CPU_MODEL"
echo "  Spotify Client ID: ${SPOTIFY_CLIENT_ID:0:8}${SPOTIFY_CLIENT_ID:+(...)}"
echo "  Spotify Client Secret: ${SPOTIFY_CLIENT_SECRET:+...${SPOTIFY_CLIENT_SECRET:3:5}... (${#SPOTIFY_CLIENT_SECRET} chars)}${SPOTIFY_CLIENT_SECRET:-(not set)}"
echo "  Spotify Redirect URI: ${SPOTIFY_REDIRECT_URI:-(not set)}"
echo "  Spotify Token Cache: ${SPOTIPY_CACHE_PATH:-(not set)}"
echo "  Polling Fast Interval: ${SPOTIFY_POLLING_FAST_INTERVAL:-(not set)}s"
echo "  Polling Slow Interval: ${SPOTIFY_POLLING_SLOW_INTERVAL:-(not set)}s"
echo "  Data Directory: /data"
echo ""
echo "UDP Audio:"
echo "  Enabled: ${UDP_AUDIO_ENABLED:-false}"
echo "  Port: ${UDP_AUDIO_PORT:-6056}"
echo "  Sample Rate: ${UDP_AUDIO_SAMPLE_RATE:-16000} Hz"
echo ""
echo "Optional APIs configured:"
[ -n "$LASTFM_API_KEY" ] && echo "  ✓ Last.fm"
[ -n "$FANART_TV_API_KEY" ] && echo "  ✓ FanArt.tv"
[ -n "$AUDIODB_API_KEY" ] && echo "  ✓ TheAudioDB"
[ -n "$SPOTIFY_BASE_URL" ] && echo "  ✓ Spotify Lyrics API: $SPOTIFY_BASE_URL"
[ -n "$SYSTEM_MUSIC_ASSISTANT_SERVER_URL" ] && echo "  ✓ Music Assistant: $SYSTEM_MUSIC_ASSISTANT_SERVER_URL"
echo ""
echo "============================================"
echo ""

# Set Linux defaults
export DESKTOP="Linux"

# Run SyncLyrics
exec python3 sync_lyrics.py
