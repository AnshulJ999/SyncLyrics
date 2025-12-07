"""
Audio Capture Module

Handles audio capture from system devices using sounddevice.
Supports loopback devices (MOTU M4, VB-Cable, Voicemeeter) for capturing system audio.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

import numpy as np

try:
    import sounddevice as sd
except ImportError:
    sd = None

from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class AudioChunk:
    """
    Raw audio data captured from a device.
    
    Attributes:
        data: Audio samples as numpy array (int16, stereo)
        sample_rate: Sample rate in Hz
        channels: Number of audio channels
        duration: Duration of captured audio in seconds
        capture_start_time: Unix timestamp when capture started (for latency compensation)
    """
    data: np.ndarray
    sample_rate: int
    channels: int
    duration: float
    capture_start_time: float
    
    def get_max_amplitude(self) -> int:
        """Get the maximum amplitude in the audio (for silence detection)."""
        return int(np.max(np.abs(self.data)))
    
    def is_silent(self, threshold: int = 100) -> bool:
        """Check if the audio is silent (below amplitude threshold)."""
        return self.get_max_amplitude() < threshold


class AudioCaptureManager:
    """
    Manages audio capture from system devices.
    Thread-safe and async-compatible via executor pattern.
    """
    
    DEFAULT_SAMPLE_RATE = 44100
    DEFAULT_CHANNELS = 2
    DEFAULT_DURATION = 4.0
    MIN_AMPLITUDE = 50  # Minimum amplitude to consider valid audio
    
    # Known loopback device name patterns (PRIORITY ORDER - most specific first!)
    # CRITICAL: "loopback" must come BEFORE generic "motu" to avoid matching physical inputs
    LOOPBACK_PATTERNS = [
        "loopback",      # Priority 1: Explicit "loopback" in name (e.g., "Loopback (MOTU M Series)")
        "stereo mix",    # Priority 2: Windows default loopback
        "what u hear",   # Priority 3: Creative Sound Blaster loopback
        "vb-cable",      # Priority 4: Virtual audio cable
        "vb-audio",      # Priority 5: VB-Audio virtual devices
        "voicemeeter",   # Priority 6: Voicemeeter
        "wave out",      # Priority 7: Generic wave out
        "motu",          # Priority 8: Generic MOTU (LAST - too broad, matches physical inputs!)
    ]
    
    def __init__(
        self, 
        device_id: Optional[int] = None,
        device_name: Optional[str] = None,
        sample_rate: Optional[int] = None
    ):
        """
        Initialize capture manager.
        
        Args:
            device_id: Specific device ID to use (None or -1 = auto-detect)
            device_name: Device name to find (overrides device_id if provided)
            sample_rate: Sample rate in Hz (None = auto-detect from device, default: 44100)
        """
        # Normalize -1 to None (backward compatibility)
        if device_id == -1:
            device_id = None
            
        self._device_id = device_id
        self._device_name = device_name
        self._requested_sample_rate = sample_rate
        self.sample_rate = sample_rate or self.DEFAULT_SAMPLE_RATE
        self.channels = self.DEFAULT_CHANNELS
        
        # Cache for device resolution (avoid repeated lookups)
        self._resolved_device_id: Optional[int] = None
        self._resolved_sample_rate: Optional[int] = None
        
        # Flag to abort ongoing capture
        self._abort_capture = False
        
        if not sd:
            logger.error("sounddevice not installed. Audio capture unavailable.")
            
    @property
    def device_id(self) -> Optional[int]:
        """Get current device ID, resolving by name if needed. Cached to avoid repeated lookups."""
        # Return cached value if available
        if self._resolved_device_id is not None:
            return self._resolved_device_id
        
        # Resolve device ID (priority: name > explicit ID > auto-detect)
        if self._device_name:
            self._resolved_device_id = self.find_device_by_name(self._device_name)
            if self._resolved_device_id is not None:
                logger.info(f"Resolved device by name '{self._device_name}': ID {self._resolved_device_id}")
                return self._resolved_device_id
        
        if self._device_id is not None:
            self._resolved_device_id = self._device_id
        else:
            self._resolved_device_id = self.find_loopback_device()
            if self._resolved_device_id is not None:
                logger.info(f"Auto-detected loopback device: ID {self._resolved_device_id}")
        
        return self._resolved_device_id
    
    def _get_device_sample_rate(self, device_id: int) -> int:
        """Get the native sample rate of a device."""
        try:
            device_info = sd.query_devices(device_id, 'input')
            native_rate = int(device_info.get('default_samplerate', self.DEFAULT_SAMPLE_RATE))
            logger.debug(f"Device {device_id} native sample rate: {native_rate} Hz")
            return native_rate
        except Exception as e:
            logger.warning(f"Failed to get sample rate for device {device_id}: {e}")
            return self.DEFAULT_SAMPLE_RATE
    
    @staticmethod
    def is_available() -> bool:
        """Check if audio capture is available (sounddevice installed)."""
        return sd is not None
    
    @staticmethod
    def list_devices() -> List[Dict[str, Any]]:
        """
        List all available audio input devices.
        
        Returns:
            List of device info dicts with:
            - index: Device ID
            - name: Device name
            - channels: Max input channels
            - sample_rate: Default sample rate
            - api: Audio API name
            - is_loopback: True if likely a loopback device
        """
        if not sd:
            return []
            
        devices = []
        try:
            all_devices = sd.query_devices()
            
            for i, device in enumerate(all_devices):
                # Only include input devices (>0 input channels)
                if device.get('max_input_channels', 0) > 0:
                    name = device.get('name', f'Device {i}')
                    
                    # Detect if likely a loopback device
                    name_lower = name.lower()
                    is_loopback = any(
                        pattern in name_lower 
                        for pattern in AudioCaptureManager.LOOPBACK_PATTERNS
                    )
                    
                    devices.append({
                        'index': i,
                        'name': name,
                        'channels': device.get('max_input_channels', 0),
                        'sample_rate': device.get('default_samplerate', 44100),
                        'api': device.get('hostapi', 0),
                        'is_loopback': is_loopback
                    })
                    
        except Exception as e:
            logger.error(f"Failed to list audio devices: {e}")
            
        return devices
    
    @classmethod
    def find_loopback_device(cls) -> Optional[int]:
        """
        Auto-detect a loopback device (MOTU, VB-Cable, etc.).
        
        Returns:
            Device index or None if not found.
            Priority: MOTU > VB-Cable > Voicemeeter > any loopback
        """
        devices = cls.list_devices()
        loopback_devices = [d for d in devices if d['is_loopback']]
        
        if not loopback_devices:
            logger.debug("No loopback devices found")
            return None
            
        # Sort by pattern priority (earlier patterns = higher priority)
        def priority_key(device):
            name_lower = device['name'].lower()
            for i, pattern in enumerate(cls.LOOPBACK_PATTERNS):
                if pattern in name_lower:
                    return i
            return len(cls.LOOPBACK_PATTERNS)
            
        loopback_devices.sort(key=priority_key)
        
        best_device = loopback_devices[0]
        logger.info(f"Auto-detected loopback device: {best_device['name']} (ID: {best_device['index']})")
        return best_device['index']
    
    @classmethod
    def find_device_by_name(cls, name: str) -> Optional[int]:
        """
        Find a device by name (partial match, case-insensitive).
        
        Args:
            name: Device name to search for
            
        Returns:
            Device index or None if not found
        """
        devices = cls.list_devices()
        name_lower = name.lower()
        
        for device in devices:
            if name_lower in device['name'].lower():
                return device['index']
                
        logger.warning(f"Device not found by name: {name}")
        return None
    
    def set_device(self, device_id: Optional[int] = None, device_name: Optional[str] = None):
        """
        Set the capture device.
        
        Args:
            device_id: Device ID to use
            device_name: Device name to use (takes precedence)
        """
        self._device_id = device_id
        self._device_name = device_name
        # FIX: Clear cached device resolution so new device takes effect immediately
        self._resolved_device_id = None
        self._resolved_sample_rate = None
        
        if device_name:
            logger.info(f"Set capture device by name: {device_name}")
        elif device_id is not None:
            logger.info(f"Set capture device by ID: {device_id}")
    
    def is_device_available(self) -> bool:
        """Check if the configured device is currently available."""
        if not sd:
            return False
            
        device_id = self.device_id
        if device_id is None:
            return False
            
        try:
            devices = sd.query_devices()
            return 0 <= device_id < len(devices)
        except Exception:
            return False
    
    def abort(self):
        """Abort any ongoing capture. Call before cleanup."""
        self._abort_capture = True
        # NOTE: Do NOT call sd.stop() here. With InputStream approach, the capture loop
        # checks _abort_capture and exits cleanly, closing stream in same thread.
        # Cross-thread sd.stop() can cause PortAudio deadlocks on Windows.
    
    def _resolve_device_sync(self) -> tuple:
        """
        Synchronously resolve device ID and sample rate.
        MUST be called from executor thread - contains blocking sd.query_devices() calls.
        
        Returns:
            Tuple of (device_id, sample_rate) or (None, None) on error
        """
        # Return cached values if available
        if self._resolved_device_id is not None and self._resolved_sample_rate is not None:
            return (self._resolved_device_id, self._resolved_sample_rate)
        
        # Resolve device ID (priority: name > explicit ID > auto-detect)
        device_id = None
        
        if self._device_name:
            device_id = self.find_device_by_name(self._device_name)
            if device_id is not None:
                logger.info(f"Resolved device by name '{self._device_name}': ID {device_id}")
        
        if device_id is None and self._device_id is not None:
            device_id = self._device_id
        
        if device_id is None:
            device_id = self.find_loopback_device()
            if device_id is not None:
                logger.info(f"Auto-detected loopback device: ID {device_id}")
        
        if device_id is None:
            return (None, None)
        
        # Cache resolved device ID
        self._resolved_device_id = device_id
        
        # Resolve sample rate
        if self._requested_sample_rate is not None:
            sample_rate = self._requested_sample_rate
        else:
            sample_rate = self._get_device_sample_rate(device_id)
            logger.info(f"Using device native sample rate: {sample_rate} Hz")
        
        # Cache resolved sample rate
        self._resolved_sample_rate = sample_rate
        self.sample_rate = sample_rate
        
        return (device_id, sample_rate)
    
    async def capture(self, duration: float = DEFAULT_DURATION) -> Optional[AudioChunk]:
        """
        Capture audio for the specified duration.
        Runs in executor to avoid blocking the event loop.
        Auto-detects sample rate from device if not specified.
        
        Args:
            duration: Capture duration in seconds (default: 4.0)
            
        Returns:
            AudioChunk with captured data, or None on error
        """
        # Reset abort flag at start of capture (robustness for reused instances)
        self._abort_capture = False
        if not sd:
            logger.error("sounddevice not available")
            return None
        
        # CRITICAL FIX: Run device resolution in executor to avoid freezing event loop
        # sd.query_devices() is a BLOCKING call that can take seconds on Windows!
        loop = asyncio.get_running_loop()
        device_id, sample_rate = await loop.run_in_executor(None, self._resolve_device_sync)
        
        if device_id is None:
            logger.error("No audio device configured or auto-detected")
            return None
        
        def _blocking_capture() -> Optional[AudioChunk]:
            """Blocking capture function to run in executor."""
            try:
                if self._abort_capture:
                    return None
                    
                capture_start = time.time()
                
                logger.debug(f"Starting capture: device={device_id}, duration={duration}s, rate={self.sample_rate}")
                
                # FIX: Use InputStream with blocking read() loop instead of sd.rec() + polling
                # This is safer because:
                # 1. stream.read() blocks efficiently on hardware, no time.sleep() spinning
                # 2. Abort by breaking the loop - the 'with' block closes stream in SAME thread
                # 3. No cross-thread sd.stop() needed (which can deadlock on Windows/PortAudio)
                data_list = []
                total_frames = int(duration * self.sample_rate)
                frames_read = 0
                
                # Chunk size for reading (100ms) - allows frequent abort checks
                chunk_size = int(self.sample_rate * 0.1)
                
                with sd.InputStream(samplerate=self.sample_rate,
                                    channels=self.channels,
                                    device=device_id,
                                    dtype='int16') as stream:
                    
                    while frames_read < total_frames:
                        if self._abort_capture:
                            logger.debug("Capture aborted via flag")
                            # Breaking exits 'with' block, which safely closes stream in same thread
                            return None
                        
                        # Calculate frames to read in this iteration
                        to_read = min(chunk_size, total_frames - frames_read)
                        
                        # Blocking read - waits for hardware, no spinning
                        chunk_data, overflow = stream.read(to_read)
                        
                        if overflow:
                            logger.debug("Audio input overflow (data lost)")
                            
                        data_list.append(chunk_data)
                        frames_read += to_read
                
                # Combine chunks
                if not data_list:
                    return None
                    
                audio_data = np.concatenate(data_list)
                
                chunk = AudioChunk(
                    data=audio_data,
                    sample_rate=self.sample_rate,
                    channels=self.channels,
                    duration=duration,
                    capture_start_time=capture_start
                )
                
                logger.debug(f"Capture complete: max_amplitude={chunk.get_max_amplitude()}")
                return chunk
                
            except Exception as e:
                logger.error(f"Audio capture failed: {e}")
                return None
        
        try:
            return await loop.run_in_executor(None, _blocking_capture)
        except Exception as e:
            logger.error(f"Executor capture failed: {e}")
            return None
