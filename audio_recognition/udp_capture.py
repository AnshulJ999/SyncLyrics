"""
UDP Audio Capture Module

Receives raw PCM audio over UDP for fingerprinting.
Designed for Home Assistant integration where audio is streamed
from an external source (e.g., ESPHome, snapcast, or other HA audio pipeline).

Expected format: 16kHz, 16-bit signed little-endian, mono PCM.
"""

import asyncio
import time
from typing import Optional

import numpy as np

from logging_config import get_logger
from .capture import AudioChunk

logger = get_logger(__name__)

# Rolling buffer limit (seconds of audio to retain)
MAX_BUFFER_SECONDS = 30


class UdpAudioProtocol(asyncio.DatagramProtocol):
    """asyncio datagram protocol that forwards received data to the capture buffer."""

    def __init__(self, capture: 'UdpAudioCapture'):
        self._capture = capture

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._capture.receive_data(data)

    def error_received(self, exc: Exception) -> None:
        logger.warning(f"UDP audio socket error: {exc}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            logger.warning(f"UDP audio connection lost: {exc}")


class UdpAudioCapture:
    """
    Receives 16kHz 16-bit mono PCM audio over UDP and provides AudioChunks
    for the recognition engine.
    """

    # Minimum fraction of fresh (unconsumed) audio required before get_audio()
    # will return a chunk.  0.5 means at least half the requested duration must
    # consist of audio that was NOT part of the previous chunk.  This prevents
    # near-duplicate recognition requests when the engine polls faster than
    # audio arrives (e.g. during verification at 0.75 s intervals).
    MIN_FRESH_RATIO = 0.5

    def __init__(self, port: int = 6056, sample_rate: int = 16000, channels: int = 1):
        self._port = port
        self._sample_rate = sample_rate
        self._channels = channels
        self._bytes_per_sample = 2  # int16
        self._frame_size = self._bytes_per_sample * self._channels

        # Rolling buffer
        self._buffer = bytearray()
        self._max_bytes = int(MAX_BUFFER_SECONDS * self._sample_rate * self._frame_size)

        self._transport: Optional[asyncio.DatagramTransport] = None
        self._running = False
        self._last_data_time: float = 0.0
        self._last_read_time: float = 0.0  # Tracks when get_audio() last returned data

        # Track how many bytes have been received in total (monotonically
        # increasing even as the rolling buffer is trimmed).  Used together
        # with _last_read_total to know how much *new* audio has arrived
        # since the previous get_audio() call.
        self._total_bytes_received: int = 0
        self._last_read_total: int = 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def has_data(self) -> bool:
        """True if any audio data has been received recently (within last 10s)."""
        return self._last_data_time > 0 and (time.time() - self._last_data_time) < 10.0

    @property
    def buffer_seconds(self) -> float:
        """Current amount of buffered audio in seconds."""
        return len(self._buffer) / (self._sample_rate * self._frame_size)

    async def start(self) -> None:
        """Start listening for UDP audio on the configured port."""
        if self._running:
            return

        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: UdpAudioProtocol(self),
            local_addr=('0.0.0.0', self._port)
        )
        self._running = True
        logger.info(f"UDP audio listener started on port {self._port} "
                     f"({self._sample_rate}Hz, {self._channels}ch, 16-bit)")

    async def stop(self) -> None:
        """Stop the UDP listener and clear the buffer."""
        if self._transport:
            self._transport.close()
            self._transport = None
        self._running = False
        self._buffer.clear()
        self._total_bytes_received = 0
        self._last_read_total = 0
        logger.info("UDP audio listener stopped")

    def receive_data(self, data: bytes) -> None:
        """Called by the protocol when a UDP packet is received."""
        self._buffer.extend(data)
        self._total_bytes_received += len(data)
        self._last_data_time = time.time()

        # Evict oldest data if buffer exceeds limit
        if len(self._buffer) > self._max_bytes:
            excess = len(self._buffer) - self._max_bytes
            del self._buffer[:excess]

    def get_audio(self, duration: float) -> Optional[AudioChunk]:
        """
        Get an AudioChunk of the requested duration from the buffer.

        Returns None if:
        - The buffer has insufficient data.
        - No new audio has been received since the last read (stream stopped).
        - Not enough *fresh* audio has arrived since the last read.  This
          prevents the recogniser from being fed near-duplicate overlapping
          chunks when the engine polls faster than audio arrives (e.g. during
          the 0.75 s verification interval).

        Args:
            duration: Desired audio duration in seconds.

        Returns:
            AudioChunk or None if insufficient, stale, or not-fresh-enough data.
        """
        # No new data since last read — stream has stopped
        if self._last_data_time <= self._last_read_time:
            return None

        needed_bytes = int(duration * self._sample_rate * self._frame_size)
        if len(self._buffer) < needed_bytes:
            return None

        # Require a minimum amount of *new* audio before returning a chunk.
        # Without this gate, rapid polling returns nearly identical audio and
        # Shazam produces duplicate offsets that confuse lyric synchronisation.
        new_bytes = self._total_bytes_received - self._last_read_total
        min_fresh_bytes = int(needed_bytes * self.MIN_FRESH_RATIO)
        if new_bytes < min_fresh_bytes:
            logger.debug(
                f"UDP get_audio: not enough fresh audio "
                f"({new_bytes}/{min_fresh_bytes} bytes), skipping"
            )
            return None

        now = time.time()
        self._last_read_time = now
        self._last_read_total = self._total_bytes_received

        audio_bytes = bytes(self._buffer[-needed_bytes:])
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16)

        if self._channels > 1:
            audio_data = audio_data.reshape(-1, self._channels)

        # Derive capture_start_time from when the *latest* byte in the
        # buffer was received, not from the current wall-clock time.
        # Using _last_data_time anchors the timestamp to actual audio
        # arrival, making it resilient to scheduling jitter and the
        # variable delay between packet arrival and this read call.
        capture_start = self._last_data_time - duration

        return AudioChunk(
            data=audio_data,
            sample_rate=self._sample_rate,
            channels=self._channels,
            duration=duration,
            capture_start_time=capture_start,
        )
