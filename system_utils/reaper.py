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

# Optional psutil import (for process detection)
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    psutil = None

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

# Shutdown guard - prevents auto-restart during app cleanup
_shutting_down = False


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
        Check if Reaper.exe is running by checking the process list.
        
        Uses process matching (not window titles) for accurate detection.
        Works on Windows and Unix systems.
        
        Returns:
            True if Reaper process detected
        """
        try:
            # Try psutil first (cross-platform, most reliable)
            if PSUTIL_AVAILABLE:
                process_name = "reaper.exe" if platform.system() == "Windows" else "reaper"
                
                # Check all running processes
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        proc_name = proc.info['name'].lower()
                        if proc_name == process_name.lower():
                            logger.debug(f"Reaper process found: {proc_name} (PID: {proc.info['pid']})")
                            return True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        # Process may have terminated or we don't have permission
                        continue
                
                return False
            else:
                # psutil not available, use platform-specific commands
                logger.debug("psutil not available, using platform-specific process check")
                
                import subprocess
                
                if platform.system() == "Windows":
                    # Windows: use tasklist command
                    try:
                        result = subprocess.run(
                            ['tasklist', '/FI', 'IMAGENAME eq reaper.exe', '/FO', 'CSV'],
                            capture_output=True,
                            text=True,
                            timeout=5,
                            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                        )
                        # Check if reaper.exe appears in output (excluding header)
                        output = result.stdout.lower()
                        # CSV format: "Image Name","PID","Session Name",...
                        # We want to find "reaper.exe" in the first column
                        lines = output.strip().split('\n')
                        for line in lines[1:]:  # Skip header
                            if line.startswith('"reaper.exe"'):
                                logger.debug("Reaper process found via tasklist")
                                return True
                        return False
                    except Exception as e:
                        logger.debug(f"Windows process check failed: {e}")
                        return False
                else:
                    # Unix/Linux/macOS: use ps command
                    try:
                        result = subprocess.run(
                            ['ps', '-A'],
                            capture_output=True,
                            text=True,
                            timeout=5
                        )
                        # Check if "reaper" appears in process list
                        output = result.stdout.lower()
                        # Look for exact process name match (avoid false positives)
                        lines = output.split('\n')
                        for line in lines:
                            # ps output format varies, but typically has process name
                            # Look for standalone "reaper" word (not "reaperd" or "reaperize")
                            if ' reaper ' in line or line.strip().endswith(' reaper'):
                                logger.debug("Reaper process found via ps")
                                return True
                        return False
                    except Exception as e:
                        logger.debug(f"Unix process check failed: {e}")
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
        
        On first call, immediately checks Reaper status (bypasses throttle)
        to detect already-running Reaper on SyncLyrics startup.
        """
        global _shutting_down
        
        # Don't auto-start during shutdown!
        if _shutting_down:
            return
        
        if not self._enabled or not self._auto_detect:
            return
        
        # Force immediate check on first call (detect already-running Reaper)
        if self._last_reaper_check == 0:
            self._reaper_running = self.is_reaper_running()
            # Don't set _last_reaper_check here - let check_reaper_status do it
            if self._reaper_running:
                logger.info("Reaper detected on startup")
        
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
        global _shutting_down
        
        # Reset shutdown guard - allows restart after manual stop
        # This flag is only meant to prevent restart during app cleanup
        _shutting_down = False
        
        if not AUDIO_REC_AVAILABLE:
            logger.error("Audio recognition not available")
            return
        
        if self._engine and self._engine.is_running:
            logger.debug("Engine already running")
            return
        
        self._manual_mode = manual
        mode_str = "manual" if manual else "auto (Reaper)"
        logger.info(f"Starting audio recognition ({mode_str})")
        
        # Create metadata enricher callback using Spotify API
        metadata_enricher = None
        try:
            from providers.spotify_api import get_shared_spotify_client
            spotify_client = get_shared_spotify_client()
            
            if spotify_client and spotify_client.initialized:
                # Create async wrapper for the sync ISRC search
                # This runs in thread executor to avoid blocking the event loop
                async def spotify_enricher(isrc: str):
                    loop = asyncio.get_running_loop()
                    return await loop.run_in_executor(
                        None,
                        spotify_client.search_track_by_isrc,
                        isrc
                    )
                metadata_enricher = spotify_enricher
                logger.debug("Spotify metadata enricher configured")
            else:
                logger.debug("Spotify not available for metadata enrichment")
        except Exception as e:
            logger.debug(f"Could not set up Spotify enricher: {e}")
        
        # Create engine with current settings
        self._engine = RecognitionEngine(
            device_id=self._device_id,
            device_name=self._device_name,
            recognition_interval=self._recognition_interval,
            capture_duration=self._capture_duration,
            latency_offset=self._latency_offset,
            metadata_enricher=metadata_enricher,
            on_song_change=self._on_song_change
        )
        
        await self._engine.start()
    
    async def stop(self):
        """Stop audio recognition."""
        global _shutting_down
        
        # Set shutdown flag to prevent auto-restart during cleanup
        _shutting_down = True
        
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
        # Now includes enriched Spotify metadata when available
        return {
            "artist": song["artist"],  # From Spotify if enriched, else Shazam
            "title": song["title"],    # From Spotify if enriched, else Shazam
            "album": song.get("album"),
            "position": position,
            "duration": song.get("duration_ms", 0) // 1000 if song.get("duration_ms") else 0,
            "is_playing": self._engine.is_playing,
            "source": "audio_recognition",
            # Track ID from Spotify enrichment (for album art cache busting, etc.)
            "track_id": song.get("track_id"),
            # Shazam/Spotify metadata fields
            "isrc": song.get("isrc"),
            "shazam_url": song.get("shazam_url"),
            "spotify_url": song.get("spotify_url"),
            "background_image_url": song.get("background_image_url"),
            "genre": song.get("genre"),
            "shazam_lyrics_text": song.get("shazam_lyrics_text"),
            # Album art URL (enriched from Spotify or fallback to Shazam)
            "album_art_url": song.get("album_art_url"),
            # Default colors (will be overridden by album art extraction)
            "colors": ("#24273a", "#363b54"),
            # Debug metadata
            "_audio_rec_mode": self.mode,
            "_audio_rec_state": self._engine.state.value if self._engine else None,
            "_reaper_detected": self._reaper_running,
            "_spotify_enriched": song.get("_spotify_enriched", False),
            "_shazam_artist": song.get("_shazam_artist"),  # Original Shazam artist
            "_shazam_title": song.get("_shazam_title"),    # Original Shazam title
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
