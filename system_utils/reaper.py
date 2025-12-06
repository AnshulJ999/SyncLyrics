"""
Reaper DAW Integration Module

Provides audio recognition-based media source for Reaper DAW.
Auto-detects when Reaper is running and starts recognition automatically.

This integrates with system_utils/metadata.py as a media source.
"""

import asyncio
import platform
import time
from typing import Optional, Dict, Any

from logging_config import get_logger

logger = get_logger(__name__)

# Import audio recognition components
try:
    from audio_recognition import RecognitionEngine, EngineState, RecognitionResult
    AUDIO_REC_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Audio recognition not available: {e}")
    AUDIO_REC_AVAILABLE = False
    RecognitionEngine = None
    EngineState = None
    RecognitionResult = None


class ReaperAudioSource:
    """
    Media source that uses audio fingerprinting for Reaper DAW.
    
    Features:
    - Auto-detects when Reaper.exe is running
    - Auto-starts recognition when Reaper detected (if enabled)
    - Provides metadata in standard system_utils format
    - Manual mode for non-Reaper use cases
    """
    
    REAPER_CHECK_INTERVAL = 5.0  # Seconds between Reaper detection checks
    
    def __init__(self):
        """Initialize Reaper audio source."""
        self._engine: Optional[RecognitionEngine] = None
        self._enabled = True
        self._manual_mode = False  # True = user triggered, False = auto (Reaper)
        self._auto_detect = True
        self._reaper_running = False
        self._last_reaper_check = 0
        
        # Settings (will be populated from config)
        self._device_id: Optional[int] = None
        self._device_name: Optional[str] = None
        self._recognition_interval = 5.0
        self._capture_duration = 4.0
        self._latency_offset = 0.0
        
    @staticmethod
    def is_available() -> bool:
        """Check if audio recognition is available."""
        return AUDIO_REC_AVAILABLE
    
    @staticmethod
    def is_reaper_running() -> bool:
        """
        Check if Reaper.exe is running (Windows only).
        
        Returns:
            True if Reaper process detected
        """
        if platform.system() != "Windows":
            return False
        
        try:
            # Use win32gui to find Reaper windows
            import win32gui
            
            reaper_found = False
            
            def check_window(hwnd, _):
                nonlocal reaper_found
                if win32gui.IsWindowVisible(hwnd):
                    title = win32gui.GetWindowText(hwnd)
                    # Reaper window titles typically contain "REAPER"
                    if "REAPER" in title.upper():
                        reaper_found = True
                return True
            
            win32gui.EnumWindows(check_window, None)
            return reaper_found
            
        except ImportError:
            logger.debug("win32gui not available, trying process check")
            
            # Fallback: check process list
            try:
                import subprocess
                result = subprocess.run(
                    ['tasklist', '/FI', 'IMAGENAME eq reaper.exe'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                return 'reaper.exe' in result.stdout.lower()
            except Exception as e:
                logger.debug(f"Process check failed: {e}")
                return False
                
        except Exception as e:
            logger.debug(f"Reaper detection failed: {e}")
            return False
    
    def configure(
        self,
        device_id: Optional[int] = None,
        device_name: Optional[str] = None,
        recognition_interval: float = 5.0,
        capture_duration: float = 4.0,
        latency_offset: float = 0.0,
        auto_detect: bool = True
    ):
        """
        Configure the audio source settings.
        
        Args:
            device_id: Audio device ID
            device_name: Audio device name (preferred)
            recognition_interval: Seconds between recognitions
            capture_duration: Audio capture duration
            latency_offset: User-adjustable latency offset
            auto_detect: Auto-start when Reaper detected
        """
        self._device_id = device_id
        self._device_name = device_name
        self._recognition_interval = recognition_interval
        self._capture_duration = capture_duration
        self._latency_offset = latency_offset
        self._auto_detect = auto_detect
        
        # If engine exists, update its settings
        if self._engine:
            self._engine.interval = recognition_interval
            self._engine.capture_duration = capture_duration
            self._engine.latency_offset = latency_offset
    
    async def check_reaper_status(self) -> bool:
        """
        Check if Reaper is running and update internal state.
        
        Throttled to avoid excessive checks.
        
        Returns:
            True if Reaper is running
        """
        now = time.time()
        
        # Throttle checks
        if now - self._last_reaper_check < self.REAPER_CHECK_INTERVAL:
            return self._reaper_running
        
        self._last_reaper_check = now
        was_running = self._reaper_running
        self._reaper_running = self.is_reaper_running()
        
        # Log state changes
        if self._reaper_running and not was_running:
            logger.info("Reaper detected")
        elif not self._reaper_running and was_running:
            logger.info("Reaper no longer detected")
        
        return self._reaper_running
    
    async def auto_manage(self):
        """
        Auto-manage recognition based on Reaper state.
        
        Call this from metadata.py on each poll.
        If auto_detect is enabled:
        - Starts recognition when Reaper detected
        - Stops recognition when Reaper closes (unless manual mode)
        """
        if not self._enabled or not self._auto_detect:
            return
        
        reaper_running = await self.check_reaper_status()
        
        if reaper_running and not self.is_active:
            # Reaper started, begin recognition
            logger.info("Reaper detected, starting audio recognition")
            await self.start(manual=False)
            
        elif not reaper_running and self.is_active and not self._manual_mode:
            # Reaper closed, stop recognition (unless manual mode)
            logger.info("Reaper closed, stopping audio recognition")
            await self.stop()
    
    async def start(self, manual: bool = False):
        """
        Start audio recognition.
        
        Args:
            manual: True if user-triggered (won't auto-stop when Reaper closes)
        """
        if not AUDIO_REC_AVAILABLE:
            logger.error("Audio recognition not available")
            return
        
        if self._engine and self._engine.is_running:
            logger.debug("Engine already running")
            return
        
        self._manual_mode = manual
        mode_str = "manual" if manual else "auto (Reaper)"
        logger.info(f"Starting audio recognition ({mode_str})")
        
        # Create engine with current settings
        self._engine = RecognitionEngine(
            device_id=self._device_id,
            device_name=self._device_name,
            recognition_interval=self._recognition_interval,
            capture_duration=self._capture_duration,
            latency_offset=self._latency_offset,
            on_song_change=self._on_song_change
        )
        
        await self._engine.start()
    
    async def stop(self):
        """Stop audio recognition."""
        if self._engine:
            await self._engine.stop()
            self._engine = None
        
        self._manual_mode = False
        logger.info("Audio recognition stopped")
    
    def _on_song_change(self, result: RecognitionResult):
        """
        Callback when song changes.
        
        Can be used to trigger lyrics refresh.
        """
        logger.info(f"Song changed: {result.artist} - {result.title}")
    
    @property
    def is_active(self) -> bool:
        """True if audio recognition is running and has data."""
        return self._engine is not None and self._engine.is_running
    
    @property
    def is_playing(self) -> bool:
        """True if music is detected as playing."""
        if self._engine:
            return self._engine.is_playing
        return False
    
    @property
    def mode(self) -> Optional[str]:
        """Current mode: 'reaper', 'manual', or None."""
        if not self.is_active:
            return None
        return "manual" if self._manual_mode else "reaper"
    
    def get_current_position(self) -> Optional[float]:
        """
        Get interpolated current position.
        
        Returns:
            Position in seconds, or None
        """
        if self._engine:
            return self._engine.get_current_position()
        return None
    
    def get_current_song(self) -> Optional[Dict[str, str]]:
        """
        Get current song info.
        
        Returns:
            {"artist": str, "title": str} or None
        """
        if self._engine:
            return self._engine.get_current_song()
        return None
    
    async def get_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Get current song metadata in system_utils format.
        
        This returns data compatible with the standard metadata format
        used by get_current_song_meta_data().
        
        Returns:
            Standard metadata dict or None if no data
        """
        if not self._engine:
            return None
        
        song = self._engine.get_current_song()
        if not song:
            return None
        
        position = self._engine.get_current_position()
        if position is None:
            position = 0
        
        # Return in standard system_utils format
        return {
            "artist": song["artist"],
            "title": song["title"],
            "album": None,  # Shazam doesn't provide album reliably
            "position": position,
            "duration": 0,  # Unknown from Shazam
            "is_playing": self._engine.is_playing,
            "source": "audio_recognition",
            "album_art_url": None,
            "track_id": None,
            # Default colors (will be overridden by album art extraction)
            "colors": ("#24273a", "#363b54"),
            # Additional metadata for debugging
            "_audio_rec_mode": self.mode,
            "_audio_rec_state": self._engine.state.value if self._engine else None,
            "_reaper_detected": self._reaper_running,
        }
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status for API endpoint.
        
        Returns:
            Status dict
        """
        engine_status = self._engine.get_status() if self._engine else {}
        
        return {
            "available": AUDIO_REC_AVAILABLE,
            "enabled": self._enabled,
            "active": self.is_active,
            "mode": self.mode,
            "reaper_detected": self._reaper_running,
            "auto_detect": self._auto_detect,
            "manual_mode": self._manual_mode,
            "device_id": self._device_id,
            "device_name": self._device_name,
            **engine_status
        }


# Module-level singleton
_reaper_source: Optional[ReaperAudioSource] = None


def get_reaper_source() -> ReaperAudioSource:
    """
    Get or create the Reaper audio source singleton.
    
    Returns:
        ReaperAudioSource instance
    """
    global _reaper_source
    if _reaper_source is None:
        _reaper_source = ReaperAudioSource()
    return _reaper_source


async def init_reaper_source(
    enabled: bool = True,
    device_id: Optional[int] = None,
    device_name: Optional[str] = None,
    recognition_interval: float = 5.0,
    capture_duration: float = 4.0,
    latency_offset: float = 0.0,
    auto_detect: bool = True
):
    """
    Initialize the Reaper audio source with settings.
    
    Call this at app startup with values from config.
    
    Args:
        enabled: Enable/disable the feature
        device_id: Audio device ID
        device_name: Audio device name
        recognition_interval: Recognition interval
        capture_duration: Capture duration  
        latency_offset: Latency offset
        auto_detect: Auto-detect Reaper
    """
    source = get_reaper_source()
    source._enabled = enabled
    source.configure(
        device_id=device_id,
        device_name=device_name,
        recognition_interval=recognition_interval,
        capture_duration=capture_duration,
        latency_offset=latency_offset,
        auto_detect=auto_detect
    )
    
    logger.info(f"Reaper audio source initialized (enabled={enabled}, auto_detect={auto_detect})")
