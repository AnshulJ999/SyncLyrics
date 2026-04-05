# SyncLyrics (UDP) - Home Assistant Addon Dockerfile
# Reads config from /data/options.json via run.sh

FROM python:3.12-slim-bookworm AS builder

# Install build dependencies (including g++ for NumPy on ARM)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libc-dev \
    libjpeg-dev \
    zlib1g-dev \
    libffi-dev \
    libasound2-dev \
    libportaudio2 \
    portaudio19-dev \
    ffmpeg \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better layer caching
COPY requirements.txt .

# Remove Windows-only and heavy optional dependencies
RUN sed -i '/winsdk/d' requirements.txt && \
    sed -i '/pywin32/d' requirements.txt && \
    sed -i '/pystray/d' requirements.txt && \
    sed -i '/desktop-notifier/d' requirements.txt && \
    sed -i '/pyinstaller/d' requirements.txt && \
    sed -i '/matplotlib/d' requirements.txt && \
    sed -i '/scipy/d' requirements.txt && \
    sed -i '/psutil/d' requirements.txt && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# -----------------------------------
# Final stage - slim runtime image
# -----------------------------------
FROM python:3.12-slim-bookworm

# Install runtime dependencies (jq needed to read HA options.json)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo \
    zlib1g \
    libffi8 \
    libasound2 \
    libportaudio2 \
    ffmpeg \
    jq \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY . .

# Copy and set entrypoint
COPY run.sh /run.sh
RUN chmod +x /run.sh

# Create data and config directories for HA persistent storage
RUN mkdir -p /data /config

CMD ["/run.sh"]
