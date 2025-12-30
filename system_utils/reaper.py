"""
Reaper DAW Integration Module

Provides audio recognition-based media source for Reaper DAW.
Auto-detects when Reaper is running and starts recognition automatically.

This integrates with system_utils/metadata.py as a media source.
"""

import asyncio
import os
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

# LAZY IMPORT: audio_recognition is NOT imported at module level to prevent
# PortAudio/sounddevice initialization when audio recognition is disabled.
# Import happens inside functions that actually need it.
# Use _check_audio_rec_available() to check availability without triggering import.

_audio_rec_available: Optional[bool] = None  # Cached availability check

def _check_audio_rec_available() -> bool:
    """Check if audio recognition is available (lazy check, caches result)."""
    global _audio_rec_available
    if _audio_rec_available is None:
        try:
            from audio_recognition import RecognitionEngine
            _audio_rec_available = True
        except ImportError:
            _audio_rec_available = False
            logger.warning("Audio recognition not available")
    return _audio_rec_available

# Shutdown guard - prevents auto-restart during app cleanup
_shutting_down = False

# Auto-detect state tracking
_auto_detect_task = None       # Background task handle
_auto_started = False          # True if WE started the engine (not user)
_manual_override = False       # True if user manually started/stopped
_last_reaper_state = False     # Last known Reaper window state
_reaper_session_active = False # True if Reaper was detected while engine running


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
        self._engine = None  # Type: Optional[RecognitionEngine] - lazy import
        self._enabled = True
        self._manual_mode = False  # True = user triggered, False = auto (Reaper)
        self._frontend_started = False  # True = engine started by frontend WebSocket
        self._auto_detect = True
        self._reaper_running = False
        self._last_reaper_check = 0
        self._check_in_progress = False  # Fix H5: Guard flag to prevent pile-up
        self._grace_task = None  # Track grace period task for cancellation on reconnect
        
        # Settings (will be populated from config)
        self._device_id: Optional[int] = None
        self._device_name: Optional[str] = None
        self._recognition_interval = 5.0
        self._capture_duration = 5.0
        self._latency_offset = 0.0
        
    @staticmethod
    def is_available() -> bool:
        """Check if audio recognition is available (lazy check)."""
        return _check_audio_rec_available()
    
    @staticmethod
    def is_reaper_running() -> bool:
        """
        Check if Reaper is running.
        
        Detection method:
        - Windows: Fast window class detection (FindWindowW "REAPERwnd")
        - Other platforms: Returns False (use --reaper flag instead)
        
        Returns:
            True if Reaper detected (Windows only)
        """
        # Method 1: Fast window class detection (Windows only)
        # Uses ctypes to find windows with class "REAPERwnd" - much faster than psutil
        # Window class is a fixed identifier, more reliable than title matching
        if platform.system() == "Windows":
            try:
                import ctypes
                from ctypes import wintypes
                
                user32 = ctypes.windll.user32
                
                # FindWindowW is the fastest way - no enumeration needed
                # Returns handle if found, NULL (0) if not
                hwnd = user32.FindWindowW("REAPERwnd", None)
                
                if hwnd:
                    # Optionally get the title for logging
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buffer = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buffer, length + 1)
                        logger.debug(f"Reaper detected via window class: '{buffer.value}'")
                    else:
                        logger.debug("Reaper detected via window class: REAPERwnd")
                    return True
                # If not found via window class, return False
                # Window class detection is reliable - no need for fallback
                return False
                
            except Exception as e:
                logger.debug(f"Window class detection failed: {e}")
                return False  # Don't fallback to psutil - too problematic
        
        # Non-Windows platforms: Return False (Reaper detection not supported)
        # User can use --reaper flag to manually enable
        return False
        
        # ========================================================================
        # DISABLED: psutil process iteration - too slow and causes stability issues
        # Kept for reference. Window class detection above is sufficient for Windows.
        # For other platforms, use --reaper flag to manually enable.
        # ========================================================================
        if False:  # Dead code - disabled
            try:
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
                            output = result.stdout.lower()
                            lines = output.strip().split('\n')
                            for line in lines[1:]:
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
                            output = result.stdout.lower()
                            lines = output.split('\n')
                            for line in lines:
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
        capture_duration: float = 5.0,
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
        
        # If engine exists, it will read updated config via properties
        # (No direct setter needed - engine reads from session_config dynamically)
    
    def refresh_config_from_session(self) -> None:
        """
        Refresh configuration from session_config.
        
        Reads the current effective config (session overrides + settings.json)
        and applies it to this source. Call this after changing session overrides
        to apply them immediately.
        """
        from system_utils.session_config import get_audio_config_with_overrides
        
        config = get_audio_config_with_overrides()
        
        # Update internal settings
        self._device_id = config.get("device_id")
        self._device_name = config.get("device_name", "")
        self._recognition_interval = config.get("recognition_interval", 5.0)
        self._capture_duration = config.get("capture_duration", 5.0)
        self._latency_offset = config.get("latency_offset", 0.0)
        self._auto_detect = config.get("reaper_auto_detect", False)
        self._enabled = config.get("enabled", False)
        
        # If engine exists, it will read updated config via properties
        # (No direct setter needed - engine reads from session_config dynamically)
        
        logger.debug(f"Config refreshed from session: enabled={self._enabled}, mode={config.get('mode')}")
    
    async def check_reaper_status(self) -> bool:
        """
        Check if Reaper is running and update internal state.
        
        Throttled to avoid excessive checks.
        Uses guard flag to prevent pile-up if previous check is hanging.
        
        Returns:
            True if Reaper is running
        """
        now = time.time()
        
        # Throttle checks
        if now - self._last_reaper_check < self.REAPER_CHECK_INTERVAL:
            return self._reaper_running
        
        # Fix H5: Prevent pile-up of checks if previous one is still running
        if self._check_in_progress:
            logger.debug("Reaper check already in progress, skipping")
            return self._reaper_running
        
        self._check_in_progress = True
        self._last_reaper_check = now
        was_running = self._reaper_running
        
        try:
            # CRITICAL FIX: Run blocking psutil.process_iter() call in daemon executor
            # to prevent freezing the event loop for seconds during process iteration
            # AND add timeout to prevent indefinite hang if psutil blocks (common on Windows)
            # Daemon threads are killed on app exit, preventing zombie processes
            from system_utils.helpers import run_in_daemon_executor
            try:
                self._reaper_running = await asyncio.wait_for(
                    run_in_daemon_executor(self.is_reaper_running),
                    timeout=2.0
                )
            except asyncio.TimeoutError:
                logger.warning("Reaper process detection timed out - keeping previous state")
                # Keep previous state to avoid disrupting playback if it was just a hiccup
            except Exception as e:
                logger.debug(f"Reaper detection error: {e}")
            
            # Log state changes
            if self._reaper_running and not was_running:
                logger.info("Reaper detected")
            elif not self._reaper_running and was_running:
                logger.info("Reaper no longer detected")
        finally:
            self._check_in_progress = False
        
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
        
        # check_reaper_status() handles throttling and first-call detection properly
        # It also runs the blocking psutil call in executor to avoid freezing event loop
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
        global _shutting_down, _auto_started, _manual_override
        
        # Fix M2: Check shutdown flag AGAIN right before starting
        # This prevents race where: check passes -> cleanup starts -> we start engine
        if _shutting_down:
            logger.debug("Ignoring start request - shutdown in progress")
            return
        
        # Track manual override for auto-detect logic
        if manual:
            _manual_override = True   # User took control - don't auto-stop
            _auto_started = False     # This wasn't an auto-start
        else:
            _auto_started = True      # This was an auto-start
        
        # Fix M2: Only reset shutdown guard on MANUAL start (user-triggered)
        # Auto-starts should NOT reset this flag to prevent race condition during cleanup
        if manual:
            _shutting_down = False
        
        if not _check_audio_rec_available():
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
        title_search_enricher = None
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
                
                # NEW: Create async wrapper for artist+title search (fallback)
                # Includes validation to prevent wrong song enrichment
                async def title_enricher(artist: str, title: str, album: str = None):
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(
                        None,
                        spotify_client.search_track,
                        artist, title
                    )
                    
                    if not result:
                        return None
                    
                    # VALIDATION: Ensure Spotify result matches Shazam/ACRCloud detection
                    # This prevents enriching with wrong song metadata
                    result_artist = (result.get('artist') or '').lower().strip()
                    result_title = (result.get('title') or '').lower().strip()
                    result_album = (result.get('album') or '').lower().strip()
                    input_artist = (artist or '').lower().strip()
                    input_title = (title or '').lower().strip()
                    input_album = (album or '').lower().strip()
                    
                    # Artist match: Must have significant overlap
                    # Handles cases like "ERRA" vs "Erra", "Plini" vs "plini"
                    artist_match = (
                        input_artist in result_artist or 
                        result_artist in input_artist or
                        _fuzzy_match(input_artist, result_artist)
                    )
                    
                    # Title match: Must have significant overlap
                    # Handles cases like "Drift" vs "Drift (Deluxe Version)"
                    title_match = (
                        input_title in result_title or 
                        result_title in input_title or
                        _fuzzy_match(input_title, result_title)
                    )
                    
                    # Album match: Optional (different regions may have different album names)
                    # But if both exist and match, it's a strong signal
                    album_match = True  # Default to true (optional)
                    if input_album and result_album:
                        album_match = (
                            input_album in result_album or 
                            result_album in input_album or
                            _fuzzy_match(input_album, result_album)
                        )
                    
                    # REQUIRE: Artist AND Title must match
                    # Album is a bonus validation but not required
                    if not (artist_match and title_match):
                        logger.warning(
                            f"Title search REJECTED - mismatch detected: "
                            f"wanted '{artist} - {title}' (album: {album}), "
                            f"got '{result.get('artist')} - {result.get('title')}' (album: {result.get('album')})"
                        )
                        return None
                    
                    if not album_match:
                        logger.debug(
                            f"Title search: album mismatch ('{input_album}' vs '{result_album}') "
                            f"but proceeding since artist+title match"
                        )
                    
                    logger.info(f"Title search ACCEPTED: {artist} - {title}")
                    
                    # search_track returns slightly different format, normalize it
                    return {
                        'artist': result.get('artist', artist),
                        'title': result.get('title', title),
                        'album': result.get('album'),
                        'track_id': None,  # search_track doesn't return this
                        'duration_ms': result.get('duration_ms', 0),
                        'album_art_url': result.get('album_art'),
                        'url': result.get('url'),
                        '_enrichment_source': 'title_search',
                    }
                
                def _fuzzy_match(str1: str, str2: str, threshold: float = 0.7) -> bool:
                    """Simple fuzzy match based on word overlap."""
                    if not str1 or not str2:
                        return False
                    words1 = set(str1.split())
                    words2 = set(str2.split())
                    if not words1 or not words2:
                        return False
                    overlap = len(words1 & words2)
                    total = min(len(words1), len(words2))
                    return (overlap / total) >= threshold if total > 0 else False
                
                title_search_enricher = title_enricher
                
                logger.debug("Spotify metadata enricher configured (ISRC + title search)")
            else:
                logger.debug("Spotify not available for metadata enrichment")
        except Exception as e:
            logger.debug(f"Could not set up Spotify enricher: {e}")
        
        # LAZY IMPORT: Only import RecognitionEngine when actually starting
        from audio_recognition import RecognitionEngine
        
        # Create engine with current settings
        self._engine = RecognitionEngine(
            device_id=self._device_id,
            device_name=self._device_name,
            recognition_interval=self._recognition_interval,
            capture_duration=self._capture_duration,
            latency_offset=self._latency_offset,
            metadata_enricher=metadata_enricher,
            title_search_enricher=title_search_enricher,
            on_song_change=self._on_song_change
        )
        
        await self._engine.start()
    
    async def stop(self, manual: bool = True):
        """
        Stop audio recognition.
        
        Args:
            manual: True if user-triggered, False if auto-stopped by system
        """
        global _auto_started, _manual_override
        
        # Track manual override for auto-detect logic
        if manual:
            _manual_override = True   # User took control - don't auto-start
            _auto_started = False
        
        # Clear runtime flag so main loop stops checking for audio rec immediately
        try:
            from system_utils.metadata import set_audio_rec_runtime_enabled
            set_audio_rec_runtime_enabled(False, False)
        except ImportError:
            pass
        
        # NOTE: We do NOT set _shutting_down here.
        # That flag is only for actual app cleanup (set by sync_lyrics.py).
        # Setting it here would block future auto-detection when Reaper reopens.
        
        
        if self._engine:
            await self._engine.stop()
            self._engine = None
        
        self._manual_mode = False
        
        # CRITICAL FIX: Clear metadata cache so Windows/Spotify can take over
        # Without this, stale audio_recognition data persists and blocks other sources
        try:
            from system_utils.metadata import get_current_song_meta_data
            if hasattr(get_current_song_meta_data, '_last_result'):
                last_result = get_current_song_meta_data._last_result
                if last_result and last_result.get('source') == 'audio_recognition':
                    get_current_song_meta_data._last_result = None
                    logger.debug("Cleared audio recognition cache")
        except Exception as e:
            logger.debug(f"Failed to clear metadata cache: {e}")
        
        logger.info("Audio recognition stopped")
    
    def _on_song_change(self, result: Any):
        """
        Callback when song changes.
        
        Args:
            result: RecognitionResult from audio_recognition module
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
        
        # Calculate duration in both formats for frontend compatibility
        # Frontend expects duration_ms for progress bar/waveform
        duration_ms = song.get("duration_ms", 0) or 0
        duration_sec = duration_ms // 1000 if duration_ms else 0
        
        # DEBUG: Trace values for progress bar debugging
        # provider = song.get("recognition_provider", "unknown")
        # if provider == "acrcloud":
         #   logger.debug(f"ACRCloud metadata: position={position:.1f}s, duration_ms={duration_ms}, duration_sec={duration_sec}, enriched={song.get('_spotify_enriched', False)}")
        
        # Return in standard system_utils format
        # Now includes enriched Spotify/Spicetify metadata when available
        
        # Use colors from enrichment if available, otherwise default
        colors = song.get("colors")
        if not colors or (isinstance(colors, dict) and not colors):
            colors = ("#24273a", "#363b54")  # Default theme colors
        
        return {
            "artist": song["artist"],  # From Spotify if enriched, else Shazam
            "title": song["title"],    # From Spotify if enriched, else Shazam
            "album": song.get("album"),
            "position": position,
            "duration": duration_sec,      # Seconds for backend compatibility
            "duration_ms": duration_ms,    # Milliseconds for frontend (progress bar/waveform)
            "is_playing": self._engine.is_playing,
            "source": "audio_recognition",  # For internal routing
            # Recognition provider (shazam or acrcloud) for display
            "recognition_provider": song.get("recognition_provider", "shazam"),
            # NEW: Spotify ID for Like button
            "id": song.get("id") or song.get("track_id"),
            # Track ID from Spotify enrichment (for album art cache busting, etc.)
            "track_id": song.get("track_id"),
            # NEW: Artist fields for Visual Mode
            "artist_id": song.get("artist_id"),
            "artist_name": song.get("artist_name") or song.get("artist"),
            # NEW: Spotify URL for clicking album art
            "url": song.get("url") or song.get("spotify_url"),
            # Shazam/Spotify metadata fields
            "isrc": song.get("isrc"),
            "shazam_url": song.get("shazam_url"),
            "spotify_url": song.get("spotify_url"),
            "background_image_url": song.get("background_image_url"),
            "genre": song.get("genre"),
            "shazam_lyrics_text": song.get("shazam_lyrics_text"),
            # Album art URL (enriched from Spotify or fallback to Shazam)
            "album_art_url": song.get("album_art_url"),
            # Colors from enrichment or default
            "colors": colors,
            # NEW: Audio analysis from Spicetify DB (for waveform/spectrum)
            "audio_analysis": song.get("audio_analysis"),
            # Debug metadata
            "_audio_rec_mode": self.mode,
            "_audio_rec_state": self._engine.state.value if self._engine else None,
            "_reaper_detected": self._reaper_running,
            "_spotify_enriched": song.get("_spotify_enriched", False),
            "_enrichment_source": song.get("_enrichment_source"),
            "_shazam_artist": song.get("_shazam_artist"),  # Original Shazam artist
            "_shazam_title": song.get("_shazam_title"),    # Original Shazam title
        }
    
    def get_status(self) -> Dict[str, Any]:
        """
        Get comprehensive status for API endpoint.
        
        Returns:
            Status dict including session config state
        """
        engine_status = self._engine.get_status() if self._engine else {}
        
        # Get capture mode from session config
        try:
            from system_utils.session_config import get_effective_value
            capture_mode = get_effective_value("mode", "backend")
        except ImportError:
            capture_mode = "backend"
        
        return {
            "available": _check_audio_rec_available(),
            "enabled": self._enabled,
            "active": self.is_active,
            "mode": self.mode,
            "capture_mode": capture_mode,  # "backend" or "frontend"
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
    capture_duration: float = 5.0,
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


# =============================================================================
# Reaper Auto-Detect Background Task
# =============================================================================

# ENV override: REAPER_CHECK_INTERVAL=10 in .env for faster detection (default: 30s)
REAPER_CHECK_INTERVAL = int(os.getenv("REAPER_CHECK_INTERVAL", "30"))


def _is_other_source_playing() -> bool:
    """
    Check if another source (Spotify/Windows/Spicetify) is actively playing music.
    
    This is a lightweight check that doesn't trigger heavy imports.
    Returns True if we should NOT start audio recognition.
    """
    try:
        # Check the last metadata result without fetching new data
        from system_utils.metadata import get_current_song_meta_data
        
        # Access cached result if available
        if hasattr(get_current_song_meta_data, '_last_result'):
            last_result = get_current_song_meta_data._last_result
            if last_result:
                source = last_result.get('source', '')
                is_playing = last_result.get('is_playing', False)
                
                # If another source is actively playing, don't start audio recognition
                if source and source != 'audio_recognition' and is_playing:
                    logger.debug(f"Other source active: {source} (playing={is_playing})")
                    return True
        
        return False
    except Exception as e:
        logger.debug(f"Error checking other sources: {e}")
        return False


async def _reaper_auto_detect_loop():
    """
    Background task that checks for Reaper every 30 seconds.
    
    Logic:
    - If Reaper window is open AND no other music playing → start engine
    - If Reaper window closes AND we auto-started → stop engine
    - If user manually started/stopped → respect their choice
    """
    global _auto_started, _manual_override, _last_reaper_state, _shutting_down
    
    logger.info(f"Reaper auto-detect started (checking every {REAPER_CHECK_INTERVAL}s)")
    
    while not _shutting_down:
        try:
            await asyncio.sleep(REAPER_CHECK_INTERVAL)
            
            if _shutting_down:
                break
            
            # Check if Reaper window is open (pure ctypes - instant, no imports)
            reaper_open = ReaperAudioSource.is_reaper_running()
            
            # Log state changes
            if reaper_open != _last_reaper_state:
                logger.debug(f"Reaper window state changed: {'open' if reaper_open else 'closed'}")
                _last_reaper_state = reaper_open
                
                # Reset manual override when Reaper state changes
                # This allows auto-detect to work again after Reaper is closed and reopened
                if not reaper_open:
                    # Reaper just closed - reset override so next open can auto-start
                    _manual_override = False
            
            source = get_reaper_source()
            
            if reaper_open:
                # Reaper is open - should we start the engine?
                
                if source.is_active:
                    # Engine already running - mark this as a Reaper session
                    # This ties engine lifecycle to Reaper (even if manually restarted)
                    _reaper_session_active = True
                    continue
                
                if _manual_override:
                    # User manually stopped - don't auto-start
                    logger.debug("Reaper open but manual override active - not auto-starting")
                    continue
                
                if _is_other_source_playing():
                    # Other music is playing (Spotify/Windows) - don't interrupt
                    continue
                
                # All conditions met - auto-start the engine
                logger.info("Reaper detected, no other sources active - auto-starting audio recognition")
                try:
                    await source.start(manual=False)  # manual=False marks this as auto-started
                    _auto_started = True
                    _reaper_session_active = True  # Mark as Reaper session
                    
                    # Set runtime flag so metadata uses audio recognition
                    from system_utils.metadata import set_audio_rec_runtime_enabled
                    set_audio_rec_runtime_enabled(True, True)
                except Exception as e:
                    logger.error(f"Failed to auto-start audio recognition: {e}")
            
            else:
                # Reaper is closed - should we stop the engine?
                
                if not source.is_active:
                    # Engine not running - nothing to do
                    _reaper_session_active = False  # Clear session flag
                    continue
                
                if not _reaper_session_active:
                    # Not a Reaper session (e.g. started without Reaper ever being detected)
                    logger.debug("Reaper closed but not a Reaper session - not auto-stopping")
                    continue
                
                # Reaper was part of this session and is now closed - stop the engine
                logger.info("Reaper closed - auto-stopping audio recognition")
                try:
                    await source.stop(manual=False)  # manual=False marks this as auto-stopped
                    _auto_started = False
                    _reaper_session_active = False  # Clear session flag
                except Exception as e:
                    logger.error(f"Failed to auto-stop audio recognition: {e}")
        
        except asyncio.CancelledError:
            logger.debug("Reaper auto-detect task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in Reaper auto-detect loop: {e}")


async def start_reaper_auto_detect():
    """
    Start the Reaper auto-detect background task.
    
    Call this at app startup if reaper_auto_detect is enabled.
    Safe to call even if already running.
    """
    global _auto_detect_task
    
    if _auto_detect_task is not None and not _auto_detect_task.done():
        logger.debug("Reaper auto-detect already running")
        return
    
    _auto_detect_task = asyncio.create_task(_reaper_auto_detect_loop())
    logger.debug("Reaper auto-detect task started")


def stop_reaper_auto_detect():
    """
    Stop the Reaper auto-detect background task.
    
    Call this on app shutdown. Safe to call even if not running.
    """
    global _auto_detect_task, _shutting_down
    
    _shutting_down = True
    
    if _auto_detect_task is not None:
        _auto_detect_task.cancel()
        _auto_detect_task = None
        logger.debug("Reaper auto-detect task stopped")
