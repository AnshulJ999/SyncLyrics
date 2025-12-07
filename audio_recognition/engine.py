"""
Recognition Engine Module

Orchestrates the capture-recognize loop with state management.
Features:
- Continuous recognition with interpolation between recognitions
- Pause detection (freezes position on consecutive failures)
- Configurable intervals and thresholds
"""

import asyncio
import time
from enum import Enum
from typing import Optional, Callable, Dict, Any

from logging_config import get_logger
from .capture import AudioCaptureManager
from .shazam import ShazamRecognizer, RecognitionResult

logger = get_logger(__name__)


class EngineState(Enum):
    """Engine state machine states."""
    IDLE = "idle"              # Not running
    STARTING = "starting"      # Initializing
    LISTENING = "listening"    # Capturing audio
    RECOGNIZING = "recognizing"  # Waiting for ShazamIO
    ACTIVE = "active"          # Has valid result, interpolating
    PAUSED = "paused"          # Music paused (consecutive failures)
    STOPPING = "stopping"      # Shutting down
    ERROR = "error"            # Unrecoverable error


class RecognitionEngine:
    """
    Core audio recognition engine.
    
    Manages the capture-recognize loop and provides interpolated positions
    for smooth lyrics scrolling between recognition cycles.
    
    Features:
    - Continuous recognition loop
    - Position interpolation between recognitions
    - Pause detection (freezes position after consecutive failures)
    - Song change detection
    - Configurable intervals
    """
    
    DEFAULT_INTERVAL = 5.0           # Seconds between recognitions
    DEFAULT_CAPTURE_DURATION = 4.0   # Seconds of audio to capture
    DEFAULT_STALE_THRESHOLD = 15.0   # Seconds before result is stale
    MAX_CONSECUTIVE_FAILURES = 3     # Failures before pausing
    
    def __init__(
        self,
        device_id: Optional[int] = None,
        device_name: Optional[str] = None,
        recognition_interval: float = DEFAULT_INTERVAL,
        capture_duration: float = DEFAULT_CAPTURE_DURATION,
        latency_offset: float = 0.0,
        metadata_enricher: Optional[Callable[[str], Any]] = None,
        on_song_change: Optional[Callable[[RecognitionResult], None]] = None,
        on_state_change: Optional[Callable[[EngineState], None]] = None
    ):
        """
        Initialize the recognition engine.
        
        Args:
            device_id: Audio device ID (None = auto-detect)
            device_name: Audio device name (takes precedence over ID)
            recognition_interval: Seconds between recognition attempts
            capture_duration: Seconds of audio to capture each cycle
            latency_offset: Additional latency offset (user-adjustable)
            metadata_enricher: Optional async callback to enrich metadata using ISRC.
                               Signature: async (isrc: str) -> Optional[Dict]
                               Returns dict with canonical metadata (artist, title, etc.)
            on_song_change: Callback when song changes (sync, wrapped in try/except)
            on_state_change: Callback when state changes (sync)
        """
        self.capture = AudioCaptureManager(device_id, device_name)
        self.recognizer = ShazamRecognizer()
        
        self.interval = recognition_interval
        self.capture_duration = capture_duration
        self.latency_offset = latency_offset
        
        self.on_song_change = on_song_change
        self.on_state_change = on_state_change
        self.metadata_enricher = metadata_enricher
        
        # State
        self._state = EngineState.IDLE
        self._task: Optional[asyncio.Task] = None
        self._last_result: Optional[RecognitionResult] = None
        self._is_playing = False
        self._consecutive_failures = 0
        self._stop_requested = False
        
        # Adaptive interval state machine
        self._first_detection = False  # False = scanning, True = detected once
        self._verified_detection = False  # False = verifying, True = verified
        
        # Position tracking for interpolation
        self._last_position_update = 0.0
        self._frozen_position: Optional[float] = None
        
        # Spotify enrichment cache (populated by metadata_enricher)
        self._enriched_metadata: Optional[Dict[str, Any]] = None
        
    @property
    def state(self) -> EngineState:
        """Current engine state."""
        return self._state
    
    @property
    def is_running(self) -> bool:
        """True if engine is running (not idle/error/stopping)."""
        return self._state in (
            EngineState.STARTING,
            EngineState.LISTENING,
            EngineState.RECOGNIZING,
            EngineState.ACTIVE,
            EngineState.PAUSED
        )
    
    @property
    def is_playing(self) -> bool:
        """True if music is detected as playing (not paused)."""
        return self._is_playing
    
    @property
    def last_result(self) -> Optional[RecognitionResult]:
        """Last successful recognition result."""
        return self._last_result
    
    def get_current_position(self) -> Optional[float]:
        """
        Get the current playback position with interpolation.
        
        When music is playing, uses RecognitionResult's latency-compensated position.
        When paused, returns frozen position.
        
        Returns:
            Current position in seconds, or None if no valid result
        """
        if self._frozen_position is not None:
            # Music is paused, return frozen position
            return self._frozen_position
            
        if self._last_result is None:
            return None
            
        # Use the result's built-in latency compensation
        position = self._last_result.get_current_position()
        
        # Add user-configurable offset
        position += self.latency_offset
        
        return max(0, position)  # Don't go negative
    
    def get_current_song(self) -> Optional[Dict[str, Any]]:
        """
        Get current song info with Spotify enrichment.
        
        Returns data with canonical metadata from Spotify if enrichment succeeded,
        otherwise falls back to Shazam's original metadata.
        
        Returns:
            Full song dict with metadata or None
        """
        if self._last_result is None:
            return None
        
        # Use Spotify enriched data if available
        if self._enriched_metadata:
            return {
                # Canonical metadata from Spotify
                "artist": self._enriched_metadata["artist"],
                "title": self._enriched_metadata["title"],
                "album": self._enriched_metadata.get("album"),
                "track_id": self._enriched_metadata.get("track_id"),
                "duration_ms": self._enriched_metadata.get("duration_ms", 0),
                # Shazam-only fields (preserved)
                "isrc": self._last_result.isrc,
                "shazam_url": self._last_result.shazam_url,
                "spotify_url": self._last_result.spotify_url,
                "background_image_url": self._last_result.background_image_url,
                "genre": self._last_result.genre,
                "shazam_lyrics_text": self._last_result.shazam_lyrics_text,
                "album_art_url": self._last_result.album_art_url,
                # Debug fields
                "_shazam_artist": self._last_result.artist,
                "_shazam_title": self._last_result.title,
                "_spotify_enriched": True,
            }
        
        # Fallback to Shazam data
        return {
            "artist": self._last_result.artist,
            "title": self._last_result.title,
            "album": self._last_result.album,
            "album_art_url": self._last_result.album_art_url,
            "isrc": self._last_result.isrc,
            "shazam_url": self._last_result.shazam_url,
            "spotify_url": self._last_result.spotify_url,
            "background_image_url": self._last_result.background_image_url,
            "genre": self._last_result.genre,
            "shazam_lyrics_text": self._last_result.shazam_lyrics_text,
            "track_id": None,
            "duration_ms": 0,
            "_spotify_enriched": False,
        }
    
    def is_result_stale(self, threshold: Optional[float] = None) -> bool:
        """
        Check if the last result is too old to be reliable.
        
        Args:
            threshold: Seconds before stale (default: DEFAULT_STALE_THRESHOLD)
            
        Returns:
            True if result is stale or missing
        """
        if self._last_result is None:
            return True
            
        threshold = threshold or self.DEFAULT_STALE_THRESHOLD
        return self._last_result.get_age() > threshold
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive engine status.
        
        Returns:
            Status dict for API response
        """
        current_song = self.get_current_song()
        
        return {
            "state": self._state.value,
            "is_running": self.is_running,
            "is_playing": self._is_playing,
            "current_song": current_song,
            "position": self.get_current_position(),
            "last_recognition_age": self._last_result.get_age() if self._last_result else None,
            "consecutive_failures": self._consecutive_failures,
            "device_id": self.capture.device_id,
            "interval": self.interval,
        }
    
    async def start(self):
        """
        Start the recognition loop.
        
        If already running, does nothing.
        """
        if self.is_running:
            logger.warning("Engine already running")
            return
            
        # Check prerequisites
        if not AudioCaptureManager.is_available():
            logger.error("Audio capture not available (sounddevice not installed)")
            self._set_state(EngineState.ERROR)
            return
            
        if not ShazamRecognizer.is_available():
            logger.error("ShazamIO not available")
            self._set_state(EngineState.ERROR)
            return
        
        logger.info("Starting recognition engine...")
        self._stop_requested = False
        self._consecutive_failures = 0
        self._frozen_position = None
        self._first_detection = False
        self._verified_detection = False
        
        self._set_state(EngineState.STARTING)
        
        # Start the background loop
        self._task = asyncio.create_task(self._run_loop())
        
    async def stop(self):
        """
        Stop the recognition loop.
        
        Waits for the current cycle to complete.
        """
        if not self.is_running:
            return
            
        logger.info("Stopping recognition engine...")
        self._set_state(EngineState.STOPPING)
        self._stop_requested = True
        
        # Abort any ongoing capture to prevent blocking
        self.capture.abort()
        
        if self._task:
            try:
                # Wait for task to complete
                await asyncio.wait_for(self._task, timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Engine stop timeout, cancelling task")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            finally:
                self._task = None
        
        self._set_state(EngineState.IDLE)
        logger.info("Recognition engine stopped")
    
    async def recognize_once(self) -> Optional[RecognitionResult]:
        """
        Perform a single recognition cycle.
        
        Useful for manual mode where you want one-shot recognition.
        
        Returns:
            RecognitionResult or None
        """
        return await self._do_recognition()
    
    async def _run_loop(self):
        """
        Main recognition loop (internal).
        
        Runs continuously until stop() is called.
        """
        logger.info(f"Recognition loop started (interval: {self.interval}s)")
        
        while not self._stop_requested:
            try:
                # Do recognition
                result = await self._do_recognition()
                
                if result:
                    # Success - enrich with Spotify (async)
                    await self._handle_successful_recognition(result)
                else:
                    # Failure
                    self._handle_failed_recognition()
                
            except asyncio.CancelledError:
                logger.debug("Recognition loop cancelled")
                break
            except Exception as e:
                logger.error(f"Recognition loop error: {e}")
                self._handle_failed_recognition()
            
            # Adaptive interval based on detection state
            if not self._stop_requested:
                if not self._first_detection:
                    # State 1: Scanning for song - rapid polls
                    interval = 1.0
                elif not self._verified_detection:
                    # State 2: Verification - quick re-check
                    interval = 0.5
                else:
                    # State 3: Normal tracking
                    interval = 3.0
                
                await asyncio.sleep(interval)
        
        logger.info("Recognition loop ended")
    
    async def _do_recognition(self) -> Optional[RecognitionResult]:
        """
        Perform one recognition cycle (capture + recognize).
        
        Returns:
            RecognitionResult or None
        """
        # Update state
        self._set_state(EngineState.LISTENING)
        
        # Capture audio
        audio = await self.capture.capture(self.capture_duration)
        
        if audio is None:
            logger.warning("Audio capture failed")
            return None
        
        if audio.is_silent():
            logger.debug("Audio is silent, skipping recognition")
            return None
        
        # Recognize
        self._set_state(EngineState.RECOGNIZING)
        result = await self.recognizer.recognize(audio)
        
        # Check if we're stopping - don't process result if shutdown in progress
        if self._stop_requested:
            logger.debug("Stop requested, discarding recognition result")
            return None
        
        return result
    
    async def _handle_successful_recognition(self, result: RecognitionResult):
        """
        Handle a successful recognition result.
        
        Enriches metadata with Spotify if enricher is available.
        """
        self._consecutive_failures = 0
        self._is_playing = True
        self._frozen_position = None  # Unfreeze position
        
        # Update adaptive interval state machine
        if not self._first_detection:
            logger.debug("First detection - moving to verification state")
            self._first_detection = True
        elif not self._verified_detection:
            logger.debug("Detection verified - moving to normal tracking")
            self._verified_detection = True
        
        # Check for song change
        song_changed = not result.is_same_song(self._last_result)
        if song_changed:
            logger.info(f"Song changed to: {result}")
            
            # Reset to verification state for new song
            self._verified_detection = False
            
            # Clear previous enrichment (will re-enrich below)
            self._enriched_metadata = None
            
            # Call song change callback
            if self.on_song_change:
                try:
                    self.on_song_change(result)
                except Exception as e:
                    logger.error(f"Song change callback error: {e}")
        
        # Enrich with Spotify using ISRC (only on song change or if not yet enriched)
        if self.metadata_enricher and result.isrc and (song_changed or self._enriched_metadata is None):
            try:
                logger.debug(f"Enriching metadata with Spotify ISRC: {result.isrc}")
                enriched = await self.metadata_enricher(result.isrc)
                if enriched:
                    self._enriched_metadata = enriched
                    logger.info(f"Spotify enrichment: {result.artist} → {enriched['artist']}")
                else:
                    self._enriched_metadata = None
                    logger.debug(f"Spotify enrichment failed, using Shazam metadata")
            except Exception as e:
                logger.debug(f"Metadata enrichment error: {e}")
                self._enriched_metadata = None
        
        self._last_result = result
        self._set_state(EngineState.ACTIVE)
    
    def _handle_failed_recognition(self):
        """Handle a failed recognition attempt."""
        self._consecutive_failures += 1
        
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            # Too many failures - likely paused or no music
            if self._is_playing:
                # Transition to paused state
                logger.info(f"No music detected after {self._consecutive_failures} attempts, pausing")
                self._is_playing = False
                
                # Freeze position at last known position
                if self._last_result:
                    self._frozen_position = self._last_result.get_current_position()
                    logger.debug(f"Position frozen at {self._frozen_position:.1f}s")
                
                self._set_state(EngineState.PAUSED)
        else:
            # Still trying, stay in active state if we have a result
            if self._state == EngineState.ACTIVE:
                pass  # Stay active, keep interpolating
            else:
                self._set_state(EngineState.LISTENING)
    
    def _set_state(self, new_state: EngineState):
        """
        Update state and trigger callback.
        
        Args:
            new_state: New state to set
        """
        if new_state == self._state:
            return
            
        old_state = self._state
        self._state = new_state
        
        logger.debug(f"Engine state: {old_state.value} -> {new_state.value}")
        
        if self.on_state_change:
            try:
                self.on_state_change(new_state)
            except Exception as e:
                logger.error(f"State change callback error: {e}")
