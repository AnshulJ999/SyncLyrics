"""
UDP Audio Capture Module

Receives PCM audio over UDP for fingerprinting, with optional RTP support.
Designed for Home Assistant integration where audio is streamed
from an external source (e.g., ESPHome, snapcast, or other HA audio pipeline).

Supported formats:
  - Raw PCM: 16kHz, 16-bit signed little-endian, mono (legacy)
  - RTP-encapsulated PCM: auto-detected via RTP version field

When RTP is detected, packets are reordered via a small jitter buffer and
lost packets are replaced with silence so the recogniser receives a coherent
stream with correct timing.
"""

import asyncio
import struct
import time
from typing import Optional

import numpy as np

from logging_config import get_logger
from .capture import AudioChunk

logger = get_logger(__name__)

# Rolling buffer limit (seconds of audio to retain)
MAX_BUFFER_SECONDS = 30

# RTP constants
RTP_VERSION = 2
RTP_HEADER_MIN_SIZE = 12  # bytes (V/P/X/CC, M/PT, seq, ts, SSRC)

# Jitter buffer defaults
DEFAULT_JITTER_BUFFER_MS = 60  # milliseconds of buffering for reorder
MAX_JITTER_BUFFER_PACKETS = 50  # safety cap


class RtpPacket:
    """Parsed RTP packet."""

    __slots__ = ('version', 'padding', 'extension', 'cc', 'marker',
                 'payload_type', 'sequence', 'timestamp', 'ssrc', 'payload')

    def __init__(self, data: bytes):
        if len(data) < RTP_HEADER_MIN_SIZE:
            raise ValueError("Packet too short for RTP header")

        byte0, byte1 = data[0], data[1]
        self.version = (byte0 >> 6) & 0x03
        if self.version != RTP_VERSION:
            raise ValueError(f"Not an RTP packet (version={self.version})")

        self.padding = bool((byte0 >> 5) & 0x01)
        self.extension = bool((byte0 >> 4) & 0x01)
        self.cc = byte0 & 0x0F
        self.marker = bool((byte1 >> 7) & 0x01)
        self.payload_type = byte1 & 0x7F

        self.sequence, self.timestamp, self.ssrc = struct.unpack_from(
            '!HII', data, 2
        )

        # Skip CSRC list (4 bytes each)
        header_len = RTP_HEADER_MIN_SIZE + self.cc * 4

        # Skip extension header if present
        if self.extension and len(data) >= header_len + 4:
            ext_len = struct.unpack_from('!HH', data, header_len)[1]
            header_len += 4 + ext_len * 4

        # Handle padding
        payload_end = len(data)
        if self.padding and payload_end > header_len:
            pad_len = data[-1]
            payload_end -= pad_len

        self.payload = data[header_len:payload_end]


def _seq_distance(a: int, b: int) -> int:
    """Signed distance from sequence *a* to *b*, handling 16-bit wrap."""
    diff = (b - a) & 0xFFFF
    if diff >= 0x8000:
        return diff - 0x10000  # negative: b is behind a
    return diff


class JitterBuffer:
    """
    Small reorder buffer that emits packets in sequence-number order.

    Lost packets are detected when their slot is skipped and the buffer
    deadline expires.  The caller is told how many packets were lost so
    it can insert equivalent silence.
    """

    def __init__(self, max_packets: int, sample_rate: int,
                 frame_size: int):
        self._max_packets = max_packets
        self._sample_rate = sample_rate
        self._frame_size = frame_size  # bytes per sample-frame

        # Packets waiting to be emitted, keyed by sequence number
        self._pending: dict[int, RtpPacket] = {}
        self._next_seq: Optional[int] = None
        self._samples_per_packet: Optional[int] = None
        self._initialized = False

    def reset(self) -> None:
        self._pending.clear()
        self._next_seq = None
        self._samples_per_packet = None
        self._initialized = False

    def push(self, pkt: RtpPacket) -> list[tuple[Optional[bytes], int]]:
        """
        Insert a packet and return a (possibly empty) list of
        ``(payload_or_None, lost_count)`` tuples ready for consumption.

        * ``payload`` is the PCM bytes for one packet, or ``None`` if
          that slot was lost (in which case ``lost_count`` tells how
          many consecutive packets were lost *before* this payload).
        * Results are returned in strict sequence order.
        """
        if not self._initialized:
            self._next_seq = pkt.sequence
            self._samples_per_packet = len(pkt.payload) // self._frame_size
            self._initialized = True
            logger.info(
                f"RTP jitter buffer initialised: seq={pkt.sequence}, "
                f"samples/pkt={self._samples_per_packet}, "
                f"PT={pkt.payload_type}, SSRC=0x{pkt.ssrc:08X}"
            )

        dist = _seq_distance(self._next_seq, pkt.sequence)

        # Too old — already emitted
        if dist < 0:
            return []

        # Duplicate
        if pkt.sequence in self._pending:
            return []

        self._pending[pkt.sequence] = pkt

        # Emit as many consecutive packets as possible
        return self._drain()

    def flush_stale(self, max_gap: int) -> list[tuple[Optional[bytes], int]]:
        """
        Force-emit if the buffer has grown too large (sender burst or
        sustained loss).  ``max_gap`` is the maximum number of missing
        packets we'll tolerate before flushing.
        """
        if not self._initialized or not self._pending:
            return []

        # Find the smallest seq in pending
        min_seq = min(self._pending, key=lambda s: _seq_distance(self._next_seq, s))
        gap = _seq_distance(self._next_seq, min_seq)
        if gap > max_gap:
            # Skip ahead — declare everything in between as lost
            return self._skip_to(min_seq)
        return []

    # ------------------------------------------------------------------

    def _drain(self) -> list[tuple[Optional[bytes], int]]:
        results: list[tuple[Optional[bytes], int]] = []

        while True:
            seq = self._next_seq & 0xFFFF
            if seq in self._pending:
                pkt = self._pending.pop(seq)
                results.append((pkt.payload, 0))
                self._next_seq = (self._next_seq + 1) & 0xFFFF
            elif len(self._pending) >= self._max_packets:
                # Buffer full — skip missing packet (declare lost)
                results.extend(self._skip_missing())
            else:
                break

        return results

    def _skip_missing(self) -> list[tuple[Optional[bytes], int]]:
        """Skip past missing packets until we hit one we have."""
        if not self._pending:
            return []

        # Find next packet we actually have
        target = min(self._pending,
                     key=lambda s: _seq_distance(self._next_seq, s))
        return self._skip_to(target)

    def _skip_to(self, target_seq: int) -> list[tuple[Optional[bytes], int]]:
        """Advance _next_seq to target_seq, emitting loss + found packet."""
        lost = _seq_distance(self._next_seq, target_seq)
        if lost <= 0:
            return []

        results: list[tuple[Optional[bytes], int]] = []

        # Report the lost packets
        logger.debug(f"RTP: {lost} packet(s) lost (seq {self._next_seq}-"
                     f"{(self._next_seq + lost - 1) & 0xFFFF})")
        results.append((None, lost))

        # Advance past the gap
        self._next_seq = target_seq & 0xFFFF

        # Now drain any consecutive packets starting at target
        while True:
            seq = self._next_seq & 0xFFFF
            if seq in self._pending:
                pkt = self._pending.pop(seq)
                results.append((pkt.payload, 0))
                self._next_seq = (self._next_seq + 1) & 0xFFFF
            else:
                break

        return results

    @property
    def samples_per_packet(self) -> Optional[int]:
        return self._samples_per_packet


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
    Receives PCM audio over UDP (raw or RTP-encapsulated) and provides
    AudioChunks for the recognition engine.

    RTP packets are automatically detected and processed through a jitter
    buffer that reorders packets and inserts silence for any that are lost,
    keeping the audio stream coherent and correctly timed.
    """

    def __init__(self, port: int = 6056, sample_rate: int = 16000,
                 channels: int = 1, jitter_buffer_ms: int = DEFAULT_JITTER_BUFFER_MS):
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

        # Track how many bytes have been appended in total (monotonically
        # increasing even as the rolling buffer is trimmed).  Used together
        # with _last_read_total to know how much *new* audio has arrived
        # since the previous get_audio() call.
        self._total_bytes_received: int = 0
        self._last_read_total: int = 0

        # Event signalled whenever new data arrives, so get_audio() can
        # async-wait instead of polling.
        self._data_event: asyncio.Event = asyncio.Event()

        # RTP state
        self._rtp_detected: Optional[bool] = None  # None = not yet determined
        self._jitter_buffer_ms = jitter_buffer_ms
        self._jitter_buffer: Optional[JitterBuffer] = None

        # Stats
        self._packets_received: int = 0
        self._packets_lost: int = 0

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

    @property
    def rtp_active(self) -> bool:
        """True if RTP encapsulation was detected on the incoming stream."""
        return self._rtp_detected is True

    @property
    def packet_loss_rate(self) -> float:
        """Fraction of packets lost (0.0-1.0), or 0 if no packets yet."""
        total = self._packets_received + self._packets_lost
        if total == 0:
            return 0.0
        return self._packets_lost / total

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
                     f"({self._sample_rate}Hz, {self._channels}ch, 16-bit, "
                     f"RTP auto-detect enabled, jitter buffer {self._jitter_buffer_ms}ms)")

    async def stop(self) -> None:
        """Stop the UDP listener and clear the buffer."""
        if self._transport:
            self._transport.close()
            self._transport = None
        self._running = False
        self._buffer.clear()
        self._total_bytes_received = 0
        self._last_read_total = 0
        self._rtp_detected = None
        if self._jitter_buffer:
            self._jitter_buffer.reset()
            self._jitter_buffer = None
        self._packets_received = 0
        self._packets_lost = 0
        self._data_event.set()  # Unblock any waiting get_audio() call
        logger.info("UDP audio listener stopped")

    def receive_data(self, data: bytes) -> None:
        """Called by the protocol when a UDP packet is received."""
        # Auto-detect RTP on first packet
        if self._rtp_detected is None:
            self._rtp_detected = self._detect_rtp(data)
            if self._rtp_detected:
                # Calculate jitter buffer size in packets based on first packet
                try:
                    pkt = RtpPacket(data)
                    payload_samples = len(pkt.payload) // self._frame_size
                    if payload_samples > 0:
                        packet_duration_ms = (payload_samples / self._sample_rate) * 1000
                        max_pkts = max(2, int(self._jitter_buffer_ms / packet_duration_ms))
                        max_pkts = min(max_pkts, MAX_JITTER_BUFFER_PACKETS)
                    else:
                        max_pkts = 5
                except (ValueError, ZeroDivisionError):
                    max_pkts = 5

                self._jitter_buffer = JitterBuffer(
                    max_packets=max_pkts,
                    sample_rate=self._sample_rate,
                    frame_size=self._frame_size,
                )
                logger.info(f"RTP encapsulation detected — jitter buffer "
                            f"holds up to {max_pkts} packets")
            else:
                logger.info("Raw PCM detected on UDP stream (no RTP headers)")

        if self._rtp_detected:
            self._handle_rtp_packet(data)
        else:
            self._handle_raw_pcm(data)

    def _detect_rtp(self, data: bytes) -> bool:
        """Heuristic: check if the packet looks like a valid RTP packet."""
        if len(data) < RTP_HEADER_MIN_SIZE:
            return False
        version = (data[0] >> 6) & 0x03
        if version != RTP_VERSION:
            return False
        # Payload type should be in a reasonable range for audio
        pt = data[1] & 0x7F
        # L16 mono = 11, L16 stereo = 10, dynamic = 96-127
        # Accept any valid PT — senders may use dynamic types
        if pt > 127:
            return False
        # The payload after the header should be non-empty
        cc = data[0] & 0x0F
        header_len = RTP_HEADER_MIN_SIZE + cc * 4
        if len(data) <= header_len:
            return False
        return True

    def _handle_rtp_packet(self, data: bytes) -> None:
        """Parse RTP, feed jitter buffer, append ordered audio to rolling buffer."""
        try:
            pkt = RtpPacket(data)
        except ValueError as exc:
            logger.debug(f"RTP parse error: {exc}")
            return

        self._packets_received += 1
        results = self._jitter_buffer.push(pkt)

        # Also flush if buffer has grown stale
        if not results:
            results = self._jitter_buffer.flush_stale(
                max_gap=self._jitter_buffer._max_packets * 2
            )

        for payload, lost_count in results:
            if lost_count > 0:
                # Insert silence for lost packets
                self._packets_lost += lost_count
                samples_per_pkt = self._jitter_buffer.samples_per_packet or 160
                silence_bytes = lost_count * samples_per_pkt * self._frame_size
                self._append_audio(b'\x00' * silence_bytes)
                logger.debug(f"RTP: inserted {lost_count * samples_per_pkt} "
                             f"silence samples for {lost_count} lost packet(s)")

            if payload is not None:
                self._append_audio(payload)

    def _handle_raw_pcm(self, data: bytes) -> None:
        """Legacy path: append raw PCM bytes directly."""
        self._packets_received += 1
        self._append_audio(data)

    def _append_audio(self, data: bytes) -> None:
        """Append audio bytes to the rolling buffer and update bookkeeping."""
        self._buffer.extend(data)
        self._total_bytes_received += len(data)
        self._last_data_time = time.time()

        # Evict oldest data if buffer exceeds limit
        if len(self._buffer) > self._max_bytes:
            excess = len(self._buffer) - self._max_bytes
            del self._buffer[:excess]

        # Wake up any waiting get_audio() call
        self._data_event.set()

    async def get_audio(self, duration: float) -> Optional[AudioChunk]:
        """
        Wait for a full ``duration`` of *fresh* audio, then return it.

        Behaves like the blocking mic/loopback capture: the caller is
        suspended until enough new real-time audio has been received via
        UDP.  This prevents the recogniser from being fed overlapping
        near-duplicate chunks when the engine polls faster than audio
        arrives.

        Returns None if the listener is stopped while waiting or the
        stream goes dead (no data for 10 s).

        Args:
            duration: Desired audio duration in seconds.

        Returns:
            AudioChunk, or None if the stream stopped.
        """
        needed_bytes = int(duration * self._sample_rate * self._frame_size)

        # Wait until a full duration of *new* audio has arrived since the
        # last chunk was returned — just like the mic blocks on hardware.
        while self._running:
            new_bytes = self._total_bytes_received - self._last_read_total
            if new_bytes >= needed_bytes and len(self._buffer) >= needed_bytes:
                break

            # Wait for more data to arrive
            self._data_event.clear()
            try:
                await asyncio.wait_for(self._data_event.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                # Check if stream is still alive
                if not self._running:
                    return None
                if self._last_data_time > 0 and (time.time() - self._last_data_time) > 10.0:
                    logger.debug("UDP stream appears dead (no data for 10s)")
                    return None

        if not self._running:
            return None

        self._last_read_total = self._total_bytes_received

        audio_bytes = bytes(self._buffer[-needed_bytes:])
        audio_data = np.frombuffer(audio_bytes, dtype=np.int16)

        if self._channels > 1:
            audio_data = audio_data.reshape(-1, self._channels)

        # Anchor capture_start_time to when the audio actually arrived,
        # matching mic behaviour where capture_start = time.time() before
        # the blocking read.
        capture_start = self._last_data_time - duration

        return AudioChunk(
            data=audio_data,
            sample_rate=self._sample_rate,
            channels=self._channels,
            duration=duration,
            capture_start_time=capture_start,
        )
