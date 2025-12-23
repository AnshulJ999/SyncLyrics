# SyncLyrics Standalone Docker Image
# https://github.com/AnshulJ999/SyncLyrics

FROM python:3.12-slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    libjpeg-dev \
    zlib1g-dev \
    libffi-dev \
    libasound2-dev \
    libportaudio2 \
    portaudio19-dev \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Remove Windows-only dependencies and install Python packages
RUN sed -i '/winsdk/d' requirements.txt && \
    sed -i '/pywin32/d' requirements.txt && \
    sed -i '/pystray/d' requirements.txt && \
    sed -i '/desktop-notifier/d' requirements.txt && \
    sed -i '/pyinstaller/d' requirements.txt && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# -----------------------------------
# Final stage - slim runtime image
# -----------------------------------
FROM python:3.12-slim-bookworm

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    libffi8 \
    libasound2 \
    libportaudio2 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash synclyrics

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY --chown=synclyrics:synclyrics . .

# Copy entrypoint script
COPY docker/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Create data directory for persistent storage
RUN mkdir -p /data && chown synclyrics:synclyrics /data

# Switch to non-root user
USER synclyrics

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SERVER_PORT=9012 \
    DEBUG_ENABLED=false \
    DEBUG_LOG_LEVEL=INFO \
    SYNCLYRICS_SETTINGS_FILE=/data/settings.json \
    SYNCLYRICS_STATE_FILE=/data/state.json \
    SYNCLYRICS_LYRICS_DB=/data/lyrics_database \
    SYNCLYRICS_ALBUM_ART_DB=/data/album_art_database \
    SYNCLYRICS_CACHE_DIR=/data/cache \
    SYNCLYRICS_LOGS_DIR=/data/logs \
    SPOTIPY_CACHE_PATH=/data/.spotify_cache

EXPOSE 9012

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9012/health')" || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
