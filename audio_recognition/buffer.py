"""
Audio Stream Buffer

Manages buffered audio data from frontend WebSocket stream.
Accumulates audio chunks and provides data for recognition when enough is collected.

Design Note (R10):
    Implements MAX_BUFFER_SECONDS limit to prevent unbounded memory growth.
    Oldest data is discarded when limit is reached.
"""

import asyncio
import numpy as np
from dataclasses import dataclass
from typing import Optional
from logging_config import get_logger

logger = get_logger(__name__)

# Constants
SAMPLE_RATE = 44100
BYTES_PER_SAMPLE = 2  # Int16
MAX_BUFFER_SECONDS = 30  # R10: Maximum buffer size in seconds


@dataclass
class AudioStreamBuffer:
    """
    Buffer for accumulating audio chunks from WebSocket stream.
    
    Features:
    - Accumulates Int16 PCM chunks
    - Memory limit with oldest-first eviction (R10)
    - Provides audio data for recognition when enough is collected
    """
    
    sample_rate: int = SAMPLE_RATE
    max_seconds: float = MAX_BUFFER_SECONDS
    
    def __post_init__(self):
        """Initialize buffer storage."""
        self._buffer = bytearray()
        self._lock = asyncio.Lock()
        self._max_bytes = int(self.max_seconds * self.sample_rate * BYTES_PER_SAMPLE)
    
    @property
    def duration_seconds(self) -> float:
        """Get current buffer duration in seconds."""
        return len(self._buffer) / (self.sample_rate * BYTES_PER_SAMPLE)
    
    @property
    def is_empty(self) -> bool:
        """Check if buffer is empty."""
        return len(self._buffer) == 0
    
    async def append(self, data: bytes) -> None:
        """
        Append audio data to buffer.
        
        Args:
            data: Raw Int16 PCM bytes (little-endian)
        """
        async with self._lock:
            self._buffer.extend(data)
            
            # R10: Enforce memory limit by removing oldest data
            if len(self._buffer) > self._max_bytes:
                excess = len(self._buffer) - self._max_bytes
                del self._buffer[:excess]  # In-place deletion, avoids full copy
                # logger.debug(f"Buffer limit reached, discarded {excess} bytes")
    
    async def get_audio_for_recognition(self, duration_seconds: float) -> Optional[np.ndarray]:
        """
        Get audio data for recognition if enough is available.
        
        Args:
            duration_seconds: Required duration for recognition
            
        Returns:
            NumPy array of Int16 samples, or None if not enough data
        """
        required_bytes = int(duration_seconds * self.sample_rate * BYTES_PER_SAMPLE)
        
        async with self._lock:
            if len(self._buffer) < required_bytes:
                return None
            
            # Take the most recent audio (last 'required_bytes')
            audio_bytes = bytes(self._buffer[-required_bytes:])
            
            # Convert to numpy array (Int16, little-endian per R3)
            audio_data = np.frombuffer(audio_bytes, dtype='<i2')
            
            return audio_data
    
    async def consume_for_recognition(self, duration_seconds: float) -> Optional[np.ndarray]:
        """
        Get and remove audio data for recognition.
        
        Used when consuming audio leaves the buffer in a known state.
        
        Args:
            duration_seconds: Required duration for recognition
            
        Returns:
            NumPy array of Int16 samples, or None if not enough data
        """
        required_bytes = int(duration_seconds * self.sample_rate * BYTES_PER_SAMPLE)
        
        async with self._lock:
            if len(self._buffer) < required_bytes:
                return None
            
            # Take the most recent audio and remove it
            audio_bytes = bytes(self._buffer[-required_bytes:])
            self._buffer = self._buffer[:-required_bytes]
            
            # Convert to numpy array (Int16, little-endian per R3)
            audio_data = np.frombuffer(audio_bytes, dtype='<i2')
            
            return audio_data
    
    async def clear(self) -> None:
        """Clear all buffered data."""
        async with self._lock:
            self._buffer = bytearray()
            logger.debug("Audio buffer cleared")
    
    def get_level(self) -> float:
        """
        Get current audio level (RMS) from recent data.
        
        Returns:
            Level from 0.0 to 1.0
        """
        if len(self._buffer) < 1024:
            return 0.0
        
        # Use last 1024 bytes (~11ms at 44100 Hz)
        recent = bytes(self._buffer[-1024:])
        samples = np.frombuffer(recent, dtype='<i2')
        
        # Calculate RMS
        rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
        normalized = rms / 32768.0  # Normalize to 0-1
        
        return min(1.0, normalized * 3)  # Amplify for visibility


class FrontendAudioQueue:
    """
    Queue wrapper for frontend audio data.
    
    Provides async queue interface for WebSocket handler to push audio
    and RecognitionEngine to pull audio.
    
    Design Note (R11):
        WebSocket handler pushes raw bytes to this queue.
        RecognitionEngine pulls from queue in _run_loop when in frontend mode.
        This keeps the engine's state machine consistent for both modes.
    """
    
    def __init__(self, maxsize: int = 100):
        """
        Initialize queue.
        
        Args:
            maxsize: Maximum number of chunks in queue
        """
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._buffer = AudioStreamBuffer()
        self._enabled = False
    
    @property
    def enabled(self) -> bool:
        """Check if frontend mode is enabled."""
        return self._enabled
    
    @property
    def buffer(self) -> AudioStreamBuffer:
        """Get the audio buffer."""
        return self._buffer
    
    def enable(self) -> None:
        """Enable frontend audio mode."""
        self._enabled = True
        logger.info("Frontend audio mode enabled")
    
    def disable(self) -> None:
        """Disable frontend audio mode."""
        self._enabled = False
        # Clear any pending data
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        logger.info("Frontend audio mode disabled")
    
    async def push(self, data: bytes) -> bool:
        """
        Push audio data to queue.
        
        Args:
            data: Raw Int16 PCM bytes
            
        Returns:
            True if pushed, False if queue full (oldest dropped)
        """
        if not self._enabled:
            return False
        
        # Also append to buffer for recognition
        await self._buffer.append(data)
        
        # Push to queue (non-blocking, drop oldest if full)
        try:
            self._queue.put_nowait(data)
            return True
        except asyncio.QueueFull:
            # Drop oldest and retry
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(data)
                return True
            except asyncio.QueueEmpty:
                return False
    
    async def get_recognition_audio(self, duration_seconds: float) -> Optional[np.ndarray]:
        """
        Get audio from buffer for recognition.
        
        Args:
            duration_seconds: Required duration
            
        Returns:
            NumPy array or None if not enough data
        """
        return await self._buffer.get_audio_for_recognition(duration_seconds)
    
    async def clear(self) -> None:
        """Clear queue and buffer."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self._buffer.clear()
