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
except (ImportError, OSError):  # OSError for missing PortAudio library
    sd = None

from logging_config import get_logger

logger = get_logger(__name__)

# Timeout wrapper for sd.query_devices() - prevents hangs when audio driver is stuck
# Uses caching to avoid repeated expensive calls
_devices_cache: Optional[list] = None
_hostapis_cache: Optional[list] = None  # Cache hostapis too (also blocking)
_devices_cache_time: float = 0
DEVICES_CACHE_TTL = 60  # Seconds (balanced between UX and stability - reduced from 30s to prevent frequent blocking calls)
QUERY_DEVICES_TIMEOUT = 4  # Seconds before giving up

# Lock to prevent concurrent device queries (can cause PortAudio mutex contention)
_device_query_lock: Optional[asyncio.Lock] = None

def _get_device_query_lock() -> asyncio.Lock:
    """Get or create the device query lock (must be created in async context)."""
    global _device_query_lock
    if _device_query_lock is None:
        _device_query_lock = asyncio.Lock()
    return _device_query_lock

def _query_devices_sync() -> tuple:
    """
    Query audio devices and host APIs with caching.
    
    This is a synchronous function that should be called from an executor.
    Results are cached to avoid repeated expensive calls.
    
    Returns:
        Tuple of (devices_list, hostapis_list)
    """
    global _devices_cache, _hostapis_cache, _devices_cache_time
    
    now = time.time()
    if _devices_cache is not None and (now - _devices_cache_time) < DEVICES_CACHE_TTL:
        return (_devices_cache, _hostapis_cache or [])
    
    if sd is None:
        return ([], [])
    
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()  # Also cache this blocking call
        # Convert to list if it's a DeviceList
        _devices_cache = list(devices) if devices else []
        _hostapis_cache = list(hostapis) if hostapis else []
        _devices_cache_time = now
        return (_devices_cache, _hostapis_cache)
    except Exception as e:
        logger.warning(f"Failed to query audio devices: {e}")
        return (_devices_cache or [], _hostapis_cache or [])

async def safe_query_devices(timeout: float = QUERY_DEVICES_TIMEOUT) -> list:
    """
    Query audio devices with timeout protection and concurrency guard.
    
    If the query takes longer than timeout seconds, returns cached/empty results.
    This prevents the main loop from hanging when Windows audio service is unresponsive.
    
    Uses a lock to prevent multiple coroutines from hitting PortAudio simultaneously
    when cache expires (can cause mutex contention on Windows).
    """
    # Acquire lock to prevent concurrent queries (only one caller queries at a time)
    async with _get_device_query_lock():
        loop = asyncio.get_running_loop()
        try:
            devices, _ = await asyncio.wait_for(
                loop.run_in_executor(None, _query_devices_sync),
                timeout=timeout
            )
            return devices
        except asyncio.TimeoutError:
            logger.warning(f"Device query timeout ({timeout}s) - audio driver may be hung")
            return _devices_cache if _devices_cache else []
        except Exception as e:
            logger.warning(f"Device query failed: {e}")
            return _devices_cache if _devices_cache else []


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
    MIN_AMPLITUDE = 100  # Minimum amplitude to consider valid audio
    
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
    
    # Generic devices to exclude from listing (duplicates/mappers that clutter the list)
    EXCLUDE_PATTERNS = [
        "microsoft sound mapper",
        "primary sound capture driver",
        "communications",  # Usually just a role alias
    ]
    
    # Fix 2.1: Class-level cache for auto-detected loopback device
    _loopback_cache: Optional[Dict[str, Any]] = None
    _loopback_cache_time: float = 0
    LOOPBACK_CACHE_TTL = 120  # 2 minutes
    
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
        """
        Get current device ID.
        
        FIX H1: This property is now NON-BLOCKING.
        It only returns cached values. If device hasn't been resolved yet,
        returns None. Call resolve_device_async() before first use.
        """
        return self._resolved_device_id
    
    async def resolve_device_async(self) -> Optional[int]:
        """
        Resolve device ID asynchronously (runs blocking calls in executor).
        
        Call this during initialization, not during capture.
        Result is cached in self._resolved_device_id.
        
        Returns:
            Device ID or None if no device found.
        """
        # Already resolved?
        if self._resolved_device_id is not None:
            return self._resolved_device_id
        
        # Import here to avoid circular import
        from system_utils.helpers import run_in_daemon_executor
        
        try:
            # Run blocking device resolution in daemon executor
            # _resolve_device_sync returns (device_id, sample_rate) tuple
            result = await run_in_daemon_executor(self._resolve_device_sync)
            if result and isinstance(result, tuple):
                self._resolved_device_id, self._resolved_sample_rate = result
            else:
                self._resolved_device_id = None
            return self._resolved_device_id
        except Exception as e:
            logger.error(f"Failed to resolve audio device: {e}")
            return None
    
    # NOTE: _resolve_device_sync is defined later (line ~485) with tuple return
    # This comment replaces a duplicate definition that was dead code
    
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
        List available audio input devices.
        
        On Windows: Filters to WASAPI devices only (cleaner list, modern API)
        On other OS: Shows all devices
        
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
            all_devices, host_apis = _query_devices_sync()  # Uses cached version (devices + hostapis)
            
            # On Windows, filter to MME + WASAPI only (cleaner list)
            # Excludes: DirectSound (legacy), WDM-KS (too low-level)
            import platform
            allowed_api_indices = set()
            if platform.system() == 'Windows':
                for idx, api in enumerate(host_apis):
                    api_name = api.get('name', '')
                    if 'MME' in api_name or 'WASAPI' in api_name:
                        allowed_api_indices.add(idx)
            
            for i, device in enumerate(all_devices):
                # Only include input devices (>0 input channels)
                if device.get('max_input_channels', 0) <= 0:
                    continue
                
                # On Windows, only include MME + WASAPI devices
                if allowed_api_indices and device.get('hostapi') not in allowed_api_indices:
                    continue
                    
                name = device.get('name', f'Device {i}')
                name_lower = name.lower()
                
                # Skip generic/excluded devices
                if any(p in name_lower for p in AudioCaptureManager.EXCLUDE_PATTERNS):
                    continue
                
                # Detect if likely a loopback device
                is_loopback = any(
                    pattern in name_lower 
                    for pattern in AudioCaptureManager.LOOPBACK_PATTERNS
                )
                
                # Get API name for display
                api_idx = device.get('hostapi', 0)
                api_name = host_apis[api_idx].get('name', 'Unknown') if api_idx < len(host_apis) else 'Unknown'
                
                devices.append({
                    'index': i,
                    'id': i,  # Alias for frontend compatibility
                    'name': name,
                    'channels': device.get('max_input_channels', 0),
                    'sample_rate': device.get('default_samplerate', 44100),
                    'api': api_name,
                    'is_loopback': is_loopback
                })
                    
        except Exception as e:
            logger.error(f"Failed to list audio devices: {e}")
            
        return devices
    
    @classmethod
    def find_loopback_device(cls) -> Optional[int]:
        """
        Auto-detect a loopback device (MOTU, VB-Cable, etc.).
        
        Fix 2.1: Uses class-level cache with 5-minute TTL to avoid
        repeated expensive sd.query_devices() calls.
        
        Returns:
            Device index or None if not found.
            Priority: MOTU > VB-Cable > Voicemeeter > any loopback
        """
        import time
        
        # Check if cache is valid
        if cls._loopback_cache and (time.time() - cls._loopback_cache_time < cls.LOOPBACK_CACHE_TTL):
            # Quick verify device still exists at cached index
            try:
                idx = cls._loopback_cache['index']
                if sd:
                    info = sd.query_devices(idx, 'input')
                    if info['name'] == cls._loopback_cache['name']:
                        logger.debug(f"Using cached loopback device: {cls._loopback_cache['name']} (ID: {idx})")
                        return idx
            except Exception:
                pass
            # Cache invalid, clear it
            cls._loopback_cache = None
        
        # Expensive detection
        devices = cls.list_devices()
        loopback_devices = [d for d in devices if d['is_loopback']]
        
        if not loopback_devices:
            logger.debug("No loopback devices found")
            return None
            
        # Sort by: 1) API preference (MME > WASAPI), 2) pattern priority
        def priority_key(device):
            # API priority: MME=0, WASAPI=1, other=2
            api = device.get('api', '').upper()
            if 'MME' in api:
                api_priority = 0
            elif 'WASAPI' in api:
                api_priority = 1
            else:
                api_priority = 2
            
            # Pattern priority (MOTU > VB-Cable > Voicemeeter > other)
            name_lower = device['name'].lower()
            pattern_priority = len(cls.LOOPBACK_PATTERNS)
            for i, pattern in enumerate(cls.LOOPBACK_PATTERNS):
                if pattern in name_lower:
                    pattern_priority = i
                    break
            
            return (api_priority, pattern_priority)
            
        loopback_devices.sort(key=priority_key)
        
        best_device = loopback_devices[0]
        
        # Cache the result
        cls._loopback_cache = {
            'index': best_device['index'],
            'name': best_device['name']
        }
        cls._loopback_cache_time = time.time()
        
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
        """Check if the configured device is currently available.
        WARNING: This is a BLOCKING method. Use is_device_available_async() from async contexts."""
        if not sd:
            return False
            
        device_id = self.device_id
        if device_id is None:
            return False
            
        try:
            devices, _ = _query_devices_sync()  # Cached version (returns tuple)
            return 0 <= device_id < len(devices)
        except Exception:
            return False
    
    async def is_device_available_async(self) -> bool:
        """Async version of is_device_available. Runs in daemon executor."""
        from system_utils.helpers import run_in_daemon_executor
        return await run_in_daemon_executor(self.is_device_available)
    
    @staticmethod
    async def list_devices_async() -> List[Dict[str, Any]]:
        """Async version of list_devices. Runs in daemon executor."""
        from system_utils.helpers import run_in_daemon_executor
        return await run_in_daemon_executor(AudioCaptureManager.list_devices)
    
    @classmethod
    async def find_loopback_device_async(cls) -> Optional[int]:
        """Async version of find_loopback_device. Runs in daemon executor."""
        from system_utils.helpers import run_in_daemon_executor
        return await run_in_daemon_executor(cls.find_loopback_device)
    
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
        # Read current device settings from session_config (allows runtime changes)
        try:
            from system_utils.session_config import get_effective_value
            current_device_id = get_effective_value("device_id", self._device_id)
            current_device_name = get_effective_value("device_name", self._device_name)
        except ImportError:
            current_device_id = self._device_id
            current_device_name = self._device_name
        
        # Normalize -1 to None
        if current_device_id == -1:
            current_device_id = None
        
        # Check if device settings changed - if so, invalidate cache
        if (current_device_id != self._device_id or current_device_name != self._device_name):
            logger.debug(f"Device settings changed: id={current_device_id}, name={current_device_name}")
            self._device_id = current_device_id
            self._device_name = current_device_name
            self._resolved_device_id = None
            self._resolved_sample_rate = None
        
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
            logger.debug(f"Using explicit device ID: {device_id}")
        
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
        
        # CRITICAL FIX: Run device resolution in daemon executor to avoid freezing event loop
        # sd.query_devices() is a BLOCKING call that can take seconds on Windows!
        # Daemon threads are killed on app exit, preventing zombie processes.
        from system_utils.helpers import run_in_daemon_executor
        device_id, sample_rate = await run_in_daemon_executor(self._resolve_device_sync)
        
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
                            # Explicit close for PortAudio safety on Windows
                            # (context manager would do this, but explicit is safer for drivers)
                            stream.close()
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
            # CRITICAL FIX: Add timeout to prevent hanging forever if PortAudio blocks
            # Daemon executor ensures thread is killed on app exit
            return await asyncio.wait_for(
                run_in_daemon_executor(_blocking_capture),
                timeout=duration + 3.0  # Capture duration + safety margin
            )
        except asyncio.TimeoutError:
            logger.error(f"Audio capture timeout after {duration + 3.0}s - aborting")
            self._abort_capture = True
            return None
        except Exception as e:
            logger.error(f"Executor capture failed: {e}")
            return None
