#!/bin/bash
# SyncLyrics (UDP) - Home Assistant Addon Entrypoint
# Reads options from /data/options.json and maps them to environment variables.

set -e

OPTIONS_FILE="/data/options.json"

if [ ! -f "$OPTIONS_FILE" ]; then
    echo "ERROR: $OPTIONS_FILE not found - is this running as an HA addon?"
    exit 1
fi

echo "============================================"
echo "  SyncLyrics (UDP) - HA Addon Starting"
echo "============================================"

# Read options from HA config
SPOTIFY_CLIENT_ID=$(jq -r '.spotify_client_id // empty' "$OPTIONS_FILE")
SPOTIFY_CLIENT_SECRET=$(jq -r '.spotify_client_secret // empty' "$OPTIONS_FILE")
SPOTIFY_REDIRECT_URI=$(jq -r '.spotify_redirect_uri // empty' "$OPTIONS_FILE")
SPOTIFY_BASE_URL=$(jq -r '.spotify_lyrics_api_url // empty' "$OPTIONS_FILE")
LASTFM_API_KEY=$(jq -r '.lastfm_api_key // empty' "$OPTIONS_FILE")
FANART_TV_API_KEY=$(jq -r '.fanart_tv_api_key // empty' "$OPTIONS_FILE")
AUDIODB_API_KEY=$(jq -r '.audiodb_api_key // empty' "$OPTIONS_FILE")
SERVER_PORT=$(jq -r '.server_port // 9012' "$OPTIONS_FILE")
SPOTIFY_TOKEN_CACHE=$(jq -r '.spotify_token_cache // "/config/.spotify_cache"' "$OPTIONS_FILE")
DEBUG_ENABLED=$(jq -r '.debug_mode // false' "$OPTIONS_FILE")
DEBUG_LOG_LEVEL=$(jq -r '.log_level // "INFO"' "$OPTIONS_FILE")
SPOTIFY_POLLING_FAST_INTERVAL=$(jq -r '.spotify_polling_fast // 2' "$OPTIONS_FILE")
SPOTIFY_POLLING_SLOW_INTERVAL=$(jq -r '.spotify_polling_slow // 6' "$OPTIONS_FILE")
HTTPS_ENABLED=$(jq -r '.https_enabled // true' "$OPTIONS_FILE")
HTTPS_PORT=$(jq -r '.https_port // 9013' "$OPTIONS_FILE")
SAVE_LYRICS=$(jq -r '.save_lyrics_to_db // true' "$OPTIONS_FILE")
ALBUM_ART_DB=$(jq -r '.album_art_db // true' "$OPTIONS_FILE")
MUSIC_ASSISTANT_URL=$(jq -r '.music_assistant_url // empty' "$OPTIONS_FILE")
MUSIC_ASSISTANT_TOKEN=$(jq -r '.music_assistant_token // empty' "$OPTIONS_FILE")
MUSIC_ASSISTANT_PLAYER_ID=$(jq -r '.music_assistant_player_id // empty' "$OPTIONS_FILE")
CPU_COMPAT=$(jq -r '.cpu_compat_mode // false' "$OPTIONS_FILE")
OPENBLAS_TYPE=$(jq -r '.openblas_cpu_type // empty' "$OPTIONS_FILE")
UDP_AUDIO_ENABLED=$(jq -r '.udp_audio_enabled // true' "$OPTIONS_FILE")
UDP_AUDIO_PORT=$(jq -r '.udp_audio_port // 6056' "$OPTIONS_FILE")
UDP_AUDIO_SAMPLE_RATE=$(jq -r '.udp_audio_sample_rate // 16000' "$OPTIONS_FILE")

# Export environment variables for the application
export SPOTIFY_CLIENT_ID
export SPOTIFY_CLIENT_SECRET
export SERVER_PORT
export DEBUG_ENABLED
export DEBUG_LOG_LEVEL
export SPOTIFY_POLLING_FAST_INTERVAL
export SPOTIFY_POLLING_SLOW_INTERVAL
export UDP_AUDIO_ENABLED
export UDP_AUDIO_PORT
export UDP_AUDIO_SAMPLE_RATE
export AUDIO_RECOGNITION_ENABLED=true

# Optional variables (only export if set)
[ -n "$SPOTIFY_REDIRECT_URI" ] && export SPOTIFY_REDIRECT_URI
[ -n "$SPOTIFY_BASE_URL" ] && export SPOTIFY_BASE_URL
[ -n "$LASTFM_API_KEY" ] && export LASTFM_API_KEY
[ -n "$FANART_TV_API_KEY" ] && export FANART_TV_API_KEY
[ -n "$AUDIODB_API_KEY" ] && export AUDIODB_API_KEY
[ -n "$MUSIC_ASSISTANT_URL" ] && export SYSTEM_MUSIC_ASSISTANT_SERVER_URL="$MUSIC_ASSISTANT_URL"
[ -n "$MUSIC_ASSISTANT_TOKEN" ] && export SYSTEM_MUSIC_ASSISTANT_TOKEN="$MUSIC_ASSISTANT_TOKEN"
[ -n "$MUSIC_ASSISTANT_PLAYER_ID" ] && export SYSTEM_MUSIC_ASSISTANT_PLAYER_ID="$MUSIC_ASSISTANT_PLAYER_ID"

# HTTPS config
export SERVER_HTTPS_ENABLED="$HTTPS_ENABLED"
export SERVER_HTTPS_PORT="$HTTPS_PORT"

# Feature flags
export FEATURES_SAVE_LYRICS_LOCALLY="$SAVE_LYRICS"
export FEATURES_ALBUM_ART_DB="$ALBUM_ART_DB"

# CPU compatibility (OpenBLAS for Intel Xeon)
if [ "$CPU_COMPAT" = "true" ]; then
    export OPENBLAS_NUM_THREADS=1
    [ -n "$OPENBLAS_TYPE" ] && export OPENBLAS_CORETYPE="$OPENBLAS_TYPE"
fi

# Persistent storage paths (use /config for addon_config mount)
export SYNCLYRICS_SETTINGS_FILE="/config/settings.json"
export SYNCLYRICS_STATE_FILE="/config/state.json"
export SYNCLYRICS_LYRICS_DB="/config/lyrics_database"
export SYNCLYRICS_ALBUM_ART_DB="/config/album_art_database"
export SYNCLYRICS_SPICETIFY_DB="/config/spicetify_database"
export SYNCLYRICS_CACHE_DIR="/config/cache"
export SYNCLYRICS_LOGS_DIR="/config/logs"
export SYNCLYRICS_CERTS_DIR="/config/certs"
export SPOTIPY_CACHE_PATH="$SPOTIFY_TOKEN_CACHE"
export DESKTOP="Linux"
export PYTHONUNBUFFERED=1

# Create persistent storage directories
mkdir -p "$SYNCLYRICS_LYRICS_DB" \
         "$SYNCLYRICS_ALBUM_ART_DB" \
         "$SYNCLYRICS_SPICETIFY_DB" \
         "$SYNCLYRICS_CACHE_DIR" \
         "$SYNCLYRICS_LOGS_DIR" \
         "$SYNCLYRICS_CERTS_DIR" \
         "$(dirname "$SPOTIPY_CACHE_PATH")"

# Log configuration
echo ""
echo "Configuration:"
echo "  Server Port: $SERVER_PORT"
echo "  HTTPS: $HTTPS_ENABLED (port $HTTPS_PORT)"
echo "  Debug: $DEBUG_ENABLED"
echo "  Log Level: $DEBUG_LOG_LEVEL"
echo "  Spotify Client ID: ${SPOTIFY_CLIENT_ID:0:8}${SPOTIFY_CLIENT_ID:+(...)}"
echo "  Polling: fast=${SPOTIFY_POLLING_FAST_INTERVAL}s slow=${SPOTIFY_POLLING_SLOW_INTERVAL}s"
echo ""
echo "UDP Audio:"
echo "  Enabled: $UDP_AUDIO_ENABLED"
echo "  Port: $UDP_AUDIO_PORT"
echo "  Sample Rate: ${UDP_AUDIO_SAMPLE_RATE} Hz"
echo ""
echo "Data: /config"
echo ""
[ -n "$LASTFM_API_KEY" ] && echo "  Last.fm: configured"
[ -n "$FANART_TV_API_KEY" ] && echo "  FanArt.tv: configured"
[ -n "$AUDIODB_API_KEY" ] && echo "  TheAudioDB: configured"
[ -n "$MUSIC_ASSISTANT_URL" ] && echo "  Music Assistant: $MUSIC_ASSISTANT_URL"
echo "============================================"
echo ""

# Run SyncLyrics
exec python3 sync_lyrics.py
