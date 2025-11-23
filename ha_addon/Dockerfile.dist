ARG BUILD_FROM
FROM $BUILD_FROM

# Build Argument for Branch (Default: main)
ARG BRANCH=main

# Install requirements for add-on + git to clone the app
RUN \
    apk add --no-cache \
    python3 \
    py3-pip \
    jq \
    git

WORKDIR /app

# Clone the latest Stable code from GitHub
RUN git clone -b ${BRANCH} https://github.com/AnshulJ999/SyncLyrics.git .

# Install Python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

# Copy run script (Local override if needed, or use the one from repo if you commit it)
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
