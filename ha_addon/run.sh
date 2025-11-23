#!/usr/bin/env bashio

echo "Starting SyncLyrics..."

# Read config from HA options
export SPOTIFY_CLIENT_ID=$(bashio::config 'spotify_client_id')
export SPOTIFY_CLIENT_SECRET=$(bashio::config 'spotify_client_secret')
export SPOTIFY_REDIRECT_URI=$(bashio::config 'spotify_redirect_uri')
export SERVER_PORT=$(bashio::config 'server_port')

# Generate a random secret key for the session
export QUART_SECRET_KEY="ha-secret-$(date +%s)"

# Set other defaults for Linux/Docker
export DESKTOP="Linux"
export PYTHONUNBUFFERED=1

# Run the app
python3 sync_lyrics.py
