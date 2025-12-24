"""
Recognition Engine Module

Orchestrates the capture-recognize loop with state management.
Features:
- Continuous recognition with interpolation between recognitions
- Pause detection (freezes position on consecutive failures)
- Configurable intervals and thresholds
"""

import asyncio
import math
import time
from enum import Enum
from typing import Optional, Callable, Dict, Any

from logging_config import get_logger
from .capture import AudioCaptureManager
from .shazam import ShazamRecognizer, RecognitionResult
from .buffer import FrontendAudioQueue

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
    DEFAULT_CAPTURE_DURATION = 5.0   # Seconds of audio to capture
    DEFAULT_STALE_THRESHOLD = 15.0   # Seconds before result is stale
    MAX_CONSECUTIVE_FAILURES = 5     # Failures before pausing
    
    def __init__(
        self,
        device_id: Optional[int] = None,
        device_name: Optional[str] = None,
        recognition_interval: float = DEFAULT_INTERVAL,
        capture_duration: float = DEFAULT_CAPTURE_DURATION,
        latency_offset: float = 0.0,
        metadata_enricher: Optional[Callable[[str], Any]] = None,
        title_search_enricher: Optional[Callable[[str, str], Any]] = None,
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
            title_search_enricher: Optional async callback to search by artist+title.
                                   Signature: async (artist: str, title: str) -> Optional[Dict]
                                   Used as fallback when ISRC lookup fails.
            on_song_change: Callback when song changes (sync, wrapped in try/except)
            on_state_change: Callback when state changes (sync)
        """
        self.capture = AudioCaptureManager(device_id, device_name)
        self.recognizer = ShazamRecognizer()
        # Default values (actual values read dynamically via properties from session_config)
        self._default_interval = recognition_interval
        self._default_capture_duration = capture_duration
        self._default_latency_offset = latency_offset
        
        self.on_song_change = on_song_change
        self.on_state_change = on_state_change
        self.metadata_enricher = metadata_enricher
        self.title_search_enricher = title_search_enricher
        
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
        self._frozen_position: Optional[float] = None
        
        # Spotify enrichment cache (populated by metadata_enricher)
        self._enriched_metadata: Optional[Dict[str, Any]] = None
        self._enrichment_attempted = False  # Prevents retry spam for songs not on Spotify
        
        # Frontend audio queue (R11: queue-based ingestion for frontend mode)
        self._frontend_queue: Optional[FrontendAudioQueue] = None
        self._frontend_mode = False
        
        # Audio level tracking for UI meter (0.0 - 1.0)
        self._last_audio_level: float = 0.0
        
        # Recognition attempt tracking for frontend visibility
        self._consecutive_no_match: int = 0  # Separate from failure counter
        self._last_attempt_result: str = "idle"  # "matched" | "no_match" | "silent" | "error" | "idle"
        self._last_attempt_time: float = 0.0
        
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
    
    @property
    def interval(self) -> float:
        """Recognition interval - reads from session config dynamically."""
        try:
            from system_utils.session_config import get_effective_value
            return get_effective_value("recognition_interval", self._default_interval)
        except ImportError:
            return self._default_interval
    
    @property
    def capture_duration(self) -> float:
        """Capture duration - reads from session config dynamically."""
        try:
            from system_utils.session_config import get_effective_value
            return get_effective_value("capture_duration", self._default_capture_duration)
        except ImportError:
            return self._default_capture_duration
    
    @property
    def latency_offset(self) -> float:
        """Latency offset - reads from session config dynamically."""
        try:
            from system_utils.session_config import get_effective_value
            return get_effective_value("latency_offset", self._default_latency_offset)
        except ImportError:
            return self._default_latency_offset
    
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
            # Use Spotify duration (reliable) - Shazam doesn't provide accurate duration
            spotify_duration = self._enriched_metadata.get("duration_ms", 0)
            return {
                # Canonical metadata from Spotify/Spicetify DB
                "artist": self._enriched_metadata["artist"],
                "title": self._enriched_metadata["title"],
                "album": self._enriched_metadata.get("album"),
                "track_id": self._enriched_metadata.get("track_id"),
                "duration_ms": spotify_duration if spotify_duration > 0 else 0,
                # NEW: Spotify ID for Like button (extracted from track_id or track_uri)
                "id": self._enriched_metadata.get("track_id"),
                # NEW: Artist fields for Visual Mode
                "artist_id": self._enriched_metadata.get("artist_id"),
                "artist_name": self._enriched_metadata.get("artist_name") or self._enriched_metadata.get("artist"),
                # NEW: Spotify URL for clicking album art
                "url": self._enriched_metadata.get("url"),
                # NEW: Colors from Spicetify DB (for background)
                "colors": self._enriched_metadata.get("colors"),
                # NEW: Audio analysis from Spicetify DB (for waveform/spectrum)
                "audio_analysis": self._enriched_metadata.get("audio_analysis"),
                # Shazam-only fields (preserved)
                "isrc": self._last_result.isrc,
                "shazam_url": self._last_result.shazam_url,
                "spotify_url": self._last_result.spotify_url or self._enriched_metadata.get("url"),
                "background_image_url": self._last_result.background_image_url,
                "genre": self._last_result.genre,
                "shazam_lyrics_text": self._last_result.shazam_lyrics_text,
                "album_art_url": self._enriched_metadata.get("album_art_url") or self._last_result.album_art_url,
                # Recognition provider (shazam or acrcloud)
                "recognition_provider": self._last_result.recognition_provider,
                # Debug fields
                "_shazam_artist": self._last_result.artist,
                "_shazam_title": self._last_result.title,
                "_spotify_enriched": True,
                "_enrichment_source": self._enriched_metadata.get("_enrichment_source", "spotify_api"),
            }
        
        # Fallback to raw recognition data
        # Use duration from RecognitionResult if available (ACRCloud provides this)
        # Handle NaN from Shazam (Shazam doesn't provide duration)
        raw_duration = self._last_result.duration
        if raw_duration and not math.isnan(raw_duration) and raw_duration > 0:
            duration_ms = int(raw_duration * 1000)
        else:
            duration_ms = 0
        
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
            "duration_ms": duration_ms,
            # Recognition provider (shazam or acrcloud)
            "recognition_provider": self._last_result.recognition_provider,
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
            "consecutive_no_match": self._consecutive_no_match,
            "last_attempt_result": self._last_attempt_result,
            "last_attempt_time": self._last_attempt_time,
            "device_id": self.capture.device_id,
            "interval": self.interval,
            "frontend_mode": self._frontend_mode,
            "audio_level": self._last_audio_level,
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
        
        # NOTE: Device resolution is done LAZILY in capture() when backend mode needs it.
        # We intentionally do NOT call resolve_device_async() here because:
        # 1. In Frontend Mode, backend capture is never used
        # 2. Calling sd.query_devices() initializes PortAudio driver
        # 3. If PortAudio is initialized but no stream is opened/closed, it hangs on exit
        # This lazy approach prevents the shutdown hang when using frontend mic.
        
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
        
        # Fix 1.2: Abort capture FIRST to unblock any pending reads
        self.capture.abort()
        
        # Then signal the loop to stop
        self._stop_requested = True
        self._set_state(EngineState.STOPPING)
        
        if self._task:
            try:
                # Fix 1.1: Reduced timeout from 10s to 3s for snappy shutdown
                await asyncio.wait_for(self._task, timeout=3.0)
            except asyncio.TimeoutError:
                logger.warning("Engine stop timeout, cancelling task")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            finally:
                self._task = None
        
        # Cleanup ShazamIO aiohttp sessions
        if self.recognizer:
            try:
                await self.recognizer.close()
            except Exception:
                pass
        
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
                
                if result == "BUFFERING":
                    # Frontend buffer not ready yet - skip failure handling
                    pass
                elif result:
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
                    # State 1: Scanning for song - half of recognition interval, capped at 3s
                    interval = min(3.0, self.interval / 2)
                elif not self._verified_detection:
                    # State 2: Verification - quick re-check
                    interval = 0.75
                else:
                    # State 3: Normal tracking - use configured interval
                    interval = self.interval
                
                # Fix 1.3: Sleep in small chunks to allow faster stop response
                # Fix: Use time.time() to avoid float accumulation errors that cause lyrics drift
                end_time = time.time() + interval
                while time.time() < end_time and not self._stop_requested:
                    remaining = max(0, end_time - time.time())
                    await asyncio.sleep(min(0.2, remaining))
        
        logger.info("Recognition loop ended")
    
    async def _do_recognition(self) -> Optional[RecognitionResult]:
        """
        Perform one recognition cycle (capture + recognize).
        
        In frontend mode (R11), pulls audio from frontend queue instead of capturing.
        
        Returns:
            RecognitionResult or None
        """
        # Update state
        self._set_state(EngineState.LISTENING)
        
        # Get audio - either from frontend queue or backend capture
        if self._frontend_mode and self._frontend_queue and self._frontend_queue.enabled:
            # Frontend mode: get audio from queue
            audio_data = await self._frontend_queue.get_recognition_audio(self.capture_duration)
            
            if audio_data is None or len(audio_data) == 0:
                logger.debug("Not enough frontend audio data yet")
                # Don't count as failure - just waiting for buffer to fill
                # Return early without calling _handle_failed_recognition
                return "BUFFERING"  # Special sentinel
            
            # Create AudioChunk from frontend data
            import time
            from .capture import AudioChunk
            audio = AudioChunk(
                data=audio_data,
                sample_rate=44100,  # Frontend always sends 44100 Hz
                channels=1,
                duration=self.capture_duration,
                capture_start_time=time.time() - self.capture_duration
            )
        else:
            # Backend mode: capture from audio device
            audio = await self.capture.capture(self.capture_duration)
        
        if audio is None:
            # Distinguish between intentional abort (frontend took over) vs real failure
            if self._frontend_mode:
                logger.info("Backend capture cancelled (frontend reconnected)")
            else:
                logger.warning("Audio capture failed")
            self._last_audio_level = 0.0
            return None
        
        # Update audio level for UI meter (normalize int16 amplitude to 0.0-1.0)
        try:
            max_amp = audio.get_max_amplitude()
            # Max amplitude for int16 is 32768, amplify slightly for visibility
            self._last_audio_level = min(1.0, (max_amp / 32768.0) * 2.0)
        except Exception:
            self._last_audio_level = 0.0
        
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
    
    def enable_frontend_mode(self) -> 'FrontendAudioQueue':
        """
        Enable frontend audio mode.
        
        Creates and returns the frontend audio queue for WebSocket handler to use.
        Disables backend capture to prevent conflicts (R4).
        
        Returns:
            FrontendAudioQueue instance
        """
        if self._frontend_queue is None:
            self._frontend_queue = FrontendAudioQueue()
        
        self._frontend_queue.enable()
        self._frontend_mode = True
        
        # Abort any ongoing backend capture (R4: mutual exclusion)
        self.capture.abort()
        
        logger.info("Frontend audio mode enabled")
        return self._frontend_queue
    
    def disable_frontend_mode(self) -> None:
        """
        Disable frontend audio mode.
        
        Returns to backend capture mode.
        """
        if self._frontend_queue:
            self._frontend_queue.disable()
        
        self._frontend_mode = False
        logger.info("Frontend audio mode disabled, returning to backend capture")
    
    async def _handle_successful_recognition(self, result: RecognitionResult):
        """
        Handle a successful recognition result.
        
        Enriches metadata with Spotify if enricher is available.
        """
        self._consecutive_failures = 0
        self._consecutive_no_match = 0  # Reset no-match counter on success
        self._last_attempt_result = "matched"
        self._last_attempt_time = time.time()
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
            self._enrichment_attempted = False  # Allow enrichment for new song
            
            # Call song change callback
            if self.on_song_change:
                try:
                    self.on_song_change(result)
                except Exception as e:
                    logger.error(f"Song change callback error: {e}")
        
        # Enrich with Spotify using ISRC (only on song change or if not yet enriched)
        # _enrichment_attempted prevents retry spam for songs not on Spotify
        should_enrich = (self.metadata_enricher and result.isrc and 
                         (song_changed or (self._enriched_metadata is None and not self._enrichment_attempted)))
        
        if should_enrich:
            self._enrichment_attempted = True  # Prevent retry on failure
            # Fire-and-forget: Don't block recognition loop waiting for Spotify API
            asyncio.create_task(self._enrich_metadata_async(result))
        
        # Fix: Update recognition_provider on EVERY match (not just song changes)
        # This ensures the badge updates correctly when switching between Shazam/ACRCloud
        self._last_result = result
        self._set_state(EngineState.ACTIVE)
    
    async def _enrich_metadata_async(self, result: 'RecognitionResult'):
        """
        Background task to enrich metadata with priority chain.
        
        Priority order (fastest first):
        1. Spicetify DB (local file lookup, ~1-5ms)
        2. ISRC lookup via Spotify API (~200ms)
        3. Artist+Title search via Spotify API (~300ms, fallback)
        
        Runs in background via create_task to avoid blocking recognition loop.
        Checks if result is still current before applying to avoid race conditions.
        """
        enriched = None
        enrichment_source = None
        
        try:
            # Priority 1: Spicetify DB (fastest - local file read)
            try:
                from system_utils.spicetify_db import load_from_db
                cached = load_from_db(result.artist, result.title)
                if cached and cached.get('track_metadata'):
                    enriched = self._format_spicetify_to_enriched(cached)
                    if enriched:
                        enrichment_source = "Spicetify DB"
                        logger.debug(f"Spicetify DB hit for: {result.artist} - {result.title}")
            except Exception as e:
                logger.debug(f"Spicetify DB lookup failed: {e}")
            
            # Priority 2: ISRC lookup via Spotify API (existing behavior)
            if not enriched and result.isrc and self.metadata_enricher:
                try:
                    logger.debug(f"Trying ISRC lookup: {result.isrc}")
                    enriched = await self.metadata_enricher(result.isrc)
                    if enriched:
                        enrichment_source = "ISRC"
                except Exception as e:
                    logger.debug(f"ISRC lookup failed: {e}")
            
            # Priority 3: Artist+Title search via Spotify API (fallback)
            if not enriched and self.title_search_enricher:
                try:
                    logger.debug(f"Trying title search: {result.artist} - {result.title}")
                    enriched = await self.title_search_enricher(result.artist, result.title)
                    if enriched:
                        enrichment_source = "Artist+Title search"
                except Exception as e:
                    logger.debug(f"Title search failed: {e}")
            
            # Race guard: Check if this result is still the current song
            # Use artist+title comparison since ISRC may not always be available
            if self._last_result:
                current_match = (
                    self._last_result.artist.lower() == result.artist.lower() and 
                    self._last_result.title.lower() == result.title.lower()
                )
                if current_match:
                    if enriched:
                        self._enriched_metadata = enriched
                        logger.info(f"Enrichment via {enrichment_source}: {result.artist} - {result.title}")
                    else:
                        self._enriched_metadata = None
                        logger.debug("All enrichment methods failed, using raw recognition data")
                else:
                    logger.debug(f"Enrichment completed but song changed, discarding")
            else:
                logger.debug("No current result, discarding enrichment")
                
        except Exception as e:
            logger.debug(f"Metadata enrichment error: {e}")
            # Only clear if still current song
            if self._last_result and self._last_result.artist.lower() == result.artist.lower():
                self._enriched_metadata = None
    
    def _format_spicetify_to_enriched(self, cached: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Convert Spicetify DB format to enriched metadata format.
        
        Spicetify DB has rich metadata from Spotify Desktop client.
        """
        try:
            track_meta = cached.get('track_metadata', {})
            if not track_meta:
                return None
            
            # Extract Spotify track ID from track_uri (spotify:track:xxx -> xxx)
            track_uri = cached.get('track_uri', '')
            track_id = None
            if track_uri and ':' in track_uri:
                parts = track_uri.split(':')
                if len(parts) >= 3 and parts[1] == 'track':
                    track_id = parts[2]
            
            # Convert spotify:image: URI to HTTPS URL if needed
            album_art_url = track_meta.get('album_art_url', '')
            if album_art_url and album_art_url.startswith('spotify:image:'):
                image_id = album_art_url.replace('spotify:image:', '')
                album_art_url = f'https://i.scdn.co/image/{image_id}'
            
            return {
                'artist': track_meta.get('artist', ''),
                'title': track_meta.get('name', ''),
                'album': track_meta.get('album'),
                'track_id': track_id,
                'duration_ms': track_meta.get('duration_ms', 0),
                'album_art_url': album_art_url,
                'url': track_meta.get('url'),
                'artist_id': track_meta.get('artist_id'),
                'artist_name': track_meta.get('artist'),
                # Extra fields from Spicetify
                'colors': cached.get('colors'),
                'audio_analysis': cached.get('audio_analysis'),
                '_enrichment_source': 'spicetify_db',
            }
        except Exception as e:
            logger.debug(f"Failed to format Spicetify data: {e}")
            return None
    
    def _handle_failed_recognition(self):
        """Handle a failed recognition attempt."""
        self._consecutive_failures += 1
        self._consecutive_no_match += 1
        self._last_attempt_result = "no_match"
        self._last_attempt_time = time.time()
        
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            # Too many failures - likely paused or no music
            if self._is_playing:
                # Transition to paused state
                logger.info(f"No music detected after {self._consecutive_failures} attempts, pausing")
                self._is_playing = False
                
                # Reset verification for fast re-detection when music resumes
                self._verified_detection = False
                self._first_detection = False
                
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
