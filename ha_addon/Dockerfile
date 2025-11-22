ARG BUILD_FROM
FROM $BUILD_FROM

# Install requirements for add-on
RUN \
  apk add --no-cache \
    python3 \
    py3-pip \
    jq

# Copy root filesystem
COPY . /app
WORKDIR /app

# Install Python dependencies
# We use --break-system-packages because HA Alpine uses managed python
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

# Copy run script
COPY ha_addon/run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
