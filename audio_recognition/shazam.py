"""
Shazam Recognition Module

Handles song recognition via ShazamIO with latency-compensated results.
Uses stdlib wave module for audio conversion (no FFmpeg/pydub dependency).
"""

import io
import json
import struct
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, Any

import numpy as np

try:
    from shazamio import Shazam
except ImportError:
    Shazam = None

from logging_config import get_logger
from .capture import AudioChunk

logger = get_logger(__name__)

# Match quality thresholds for rejecting suspicious Shazam matches
# If exceeded, the match is rejected and ACRCloud fallback is attempted
TIMESKEW_REJECT_THRESHOLD = 0.019   # Reject if abs(timeskew) > 1%
FREQSKEW_REJECT_THRESHOLD = 0.019   # Reject if abs(frequencyskew) > 1%

# Audio resampling flag - ShazamIO handles sample rates internally (downsamples to 16kHz)
# Set to True only if you experience recognition issues with 48kHz audio
ENABLE_RESAMPLING = False


@dataclass
class RecognitionResult:
    """
    Result from ShazamIO recognition with built-in latency compensation.
    
    The key insight: ShazamIO's 'offset' tells us where the song WAS at capture start.
    To get the CURRENT position, we add the elapsed time since capture started.
    
    Attributes:
        title: Song title
        artist: Song artist (unmodified from Shazam)
        album: Album name (if available)
        offset: Position in song at capture START (seconds)
        capture_start_time: Unix timestamp when capture started
        recognition_time: Unix timestamp when recognition completed
        confidence: Match confidence (0-1, estimated from Shazam's response)
        time_skew: Shazam's time skew value
        frequency_skew: Shazam's frequency skew value
        track_id: Provider-specific identifier (Shazam key, ACRCloud acrid, Local FP songId).
                  NOTE: This is NOT used for song comparison - artist+title is used instead.
        album_art_url: URL to album cover art
        isrc: International Standard Recording Code
        shazam_url: URL to view song on Shazam
        spotify_url: URL to play on Spotify (if available)
        background_image_url: Background image for visual modes
        genre: Primary genre
        shazam_lyrics_text: Raw lyrics text from Shazam (unsynced)
        recognition_provider: Which service matched ("shazam", "acrcloud", or "local_fingerprint")
        duration: Song duration in seconds (if available)
    """
    title: str
    artist: str
    offset: float
    capture_start_time: float
    recognition_time: float = field(default_factory=time.time)
    confidence: float = 1.0
    time_skew: float = 0.0
    frequency_skew: float = 0.0
    track_id: Optional[str] = None
    album: Optional[str] = None
    album_art_url: Optional[str] = None
    isrc: Optional[str] = None
    shazam_url: Optional[str] = None
    spotify_url: Optional[str] = None
    background_image_url: Optional[str] = None
    genre: Optional[str] = None
    shazam_lyrics_text: Optional[str] = None
    recognition_provider: str = "shazam"  # "shazam", "acrcloud", or "local_fingerprint"
    duration: Optional[float] = None  # Song duration in seconds
    
    def get_current_position(self) -> float:
        """
        Get the current playback position with latency compensation.
        
        Formula: actual_position = offset + (now - capture_start_time)
        
        This accounts for:
        - Audio capture duration
        - ShazamIO API processing time
        - Any additional delay
        
        Returns:
            Current position in the song (seconds)
        """
        elapsed = time.time() - self.capture_start_time
        return self.offset + elapsed
    
    def get_latency(self) -> float:
        """
        Get the total latency from capture start to result received.
        
        Returns:
            Latency in seconds
        """
        return self.recognition_time - self.capture_start_time
    
    def get_age(self) -> float:
        """
        Get the age of this result (time since recognition completed).
        
        Returns:
            Age in seconds
        """
        return time.time() - self.recognition_time
    
    def is_same_song(self, other: Optional['RecognitionResult']) -> bool:
        """
        Check if this is the same song as another result.
        
        Uses normalized artist+title comparison for consistency across all providers.
        NOTE: We intentionally do NOT compare track_id because each provider uses
        different ID formats (Shazam key, ACRCloud acrid, Local FP songId) which
        would cause false "different song" results when switching providers.
        
        Args:
            other: Another RecognitionResult to compare
            
        Returns:
            True if same song
        """
        if other is None:
            return False
            
        # Prefer track_id comparison if both have it
        # if self.track_id and other.track_id:
        #   return self.track_id == other.track_id
            
        # Fall back to name comparison
        
        # Always compare by normalized artist+title (consistent across all providers)
        # This prevents duplicate song change events when e.g. ACRCloud → Local FP
        return (
            self.artist.lower().strip() == other.artist.lower().strip() and
            self.title.lower().strip() == other.title.lower().strip()
        )
    
    def __str__(self) -> str:
        return f"{self.artist} - {self.title} @ {self.get_current_position():.1f}s"


class ShazamRecognizer:
    """
    Handles song recognition via ShazamIO with ACRCloud fallback.
    
    Features:
    - Converts audio using stdlib wave (no FFmpeg dependency)
    - Automatic latency compensation in results
    - Silence detection to avoid unnecessary API calls
    - ACRCloud fallback when Shazamio fails (if configured)
    """
    
    MIN_AUDIO_LEVEL = 100  # Minimum amplitude for valid audio
    
    def __init__(self):
        """Initialize Shazam client and optional ACRCloud fallback."""
        self._no_match_count = 0  # For throttled logging
        self._wav_bytes_cache: bytes = b''  # Cache WAV for ACRCloud fallback
        
        if Shazam is None:
            logger.error("shazamio not installed. Song recognition unavailable.")
            self._shazam = None
        else:
            self._shazam = Shazam()
        
        # Initialize Local Fingerprint recognizer (ENV-guarded, disabled by default)
        # Only imported and initialized if LOCAL_FP_ENABLED=true
        self._local = None
        try:
            from config import LOCAL_FINGERPRINT
            if LOCAL_FINGERPRINT["enabled"]:
                from .local import LocalRecognizer
                self._local = LocalRecognizer()
                logger.info("Local fingerprint recognition enabled")
        except ImportError as e:
            logger.debug(f"Local fingerprint module not available: {e}")
        
        # Initialize ACRCloud fallback (auto-disabled if not configured)
        try:
            from .acrcloud import ACRCloudRecognizer
            self._acrcloud = ACRCloudRecognizer()
        except ImportError:
            self._acrcloud = None
            logger.debug("ACRCloud module not available")
            
    @staticmethod
    def is_available() -> bool:
        """Check if ShazamIO is available."""
        return Shazam is not None
    
    def set_position_tracker(self, tracker) -> None:
        """
        Set position tracker for multi-match verification.
        
        The tracker is passed to LocalRecognizer for selecting the best match
        when multiple candidates are returned from SFP.
        """
        if self._local:
            self._local._position_tracker = tracker
    
    async def close(self):
        """Close the Shazam client and cleanup resources (aiohttp sessions)."""
        if self._shazam:
            # ShazamIO may have internal aiohttp session
            # Try to close it if possible
            try:
                if hasattr(self._shazam, 'close'):
                    await self._shazam.close()
                elif hasattr(self._shazam, '_session'):
                    await self._shazam._session.close()
            except Exception:
                pass  # Best effort cleanup
            self._shazam = None
        
        # Stop the local fingerprint daemon if running
        if self._local:
            try:
                self._local.stop_daemon()
            except Exception:
                pass  # Best effort cleanup
    
    async def prewarm(self) -> None:
        """
        Pre-warm the local fingerprint daemon to eliminate cold-start latency.
        
        Should be called when engine starts. Safe to call even if local FP
        is not enabled - will just return immediately.
        """
        if self._local:
            await self._local.prewarm_daemon()
    
    def _save_debug_audio(self, wav_bytes: bytes, is_buffered: bool = False) -> None:
        """Save audio to cache for debugging.
        
        Args:
            wav_bytes: WAV audio data to save
            is_buffered: If True, this is buffered audio (longer duration)
        """
        from .debug_utils import save_debug_audio
        
        save_debug_audio(wav_bytes, is_buffered)
        
        # Also verify WAV header
        self._verify_wav_header(wav_bytes)
    
    def _verify_wav_header(self, wav_bytes: bytes) -> None:
        """Verify WAV header sample rate is correct."""
        try:
            if len(wav_bytes) < 28:
                return
            # WAV format: bytes 24-27 contain sample rate (little-endian uint32)
            header_rate = struct.unpack('<I', wav_bytes[24:28])[0]
            expected_rate = 44100
            if header_rate != expected_rate:
                logger.warning(f"WAV header sample rate mismatch: {header_rate} Hz (expected {expected_rate} Hz)")
        except Exception as e:
            logger.debug(f"Failed to verify WAV header: {e}")
    
    def _save_debug_match(self, provider: str, result: dict) -> None:
        """Save match to history for debugging (keeps last 6 matches)."""
        from .debug_utils import save_match_to_history
        
        save_match_to_history(provider=provider, result=result)
    
    async def recognize(self, audio: AudioChunk, buffer_config: Optional[Dict[str, Any]] = None) -> Optional[RecognitionResult]:
        """
        Recognize a song from an audio chunk.
        
        Args:
            audio: AudioChunk from capture (may be combined buffer or single capture)
            buffer_config: Optional buffer configuration dict with:
                - local_fp: bool - whether to use buffered audio for Local FP
                - shazam: bool - whether to use buffered audio for Shazam
                - acrcloud: bool - whether to use buffered audio for ACRCloud
                - single_audio: AudioChunk - original single capture for non-buffered services
            
        Returns:
            RecognitionResult with latency compensation, or None if no match
        """
        if not self._shazam:
            logger.error("ShazamIO not available")
            return None
        
        # Parse buffer config (with safe defaults)
        buffer_config = buffer_config or {}
        use_buffer_local = buffer_config.get("local_fp", False)
        use_buffer_shazam = buffer_config.get("shazam", False)
        use_buffer_acrcloud = buffer_config.get("acrcloud", False)
        buffered_audio = buffer_config.get("buffered_audio")
        
        # Select audio for each service
        # audio = single capture (latest 6s), buffered_audio = combined buffer (up to 18s)
        local_audio = buffered_audio if use_buffer_local and buffered_audio else audio
        shazam_audio = buffered_audio if use_buffer_shazam and buffered_audio else audio
        acrcloud_audio = buffered_audio if use_buffer_acrcloud and buffered_audio else audio
        
        # Get silence threshold from session config (allows runtime adjustment)
        try:
            from system_utils.session_config import get_effective_value
            silence_threshold = get_effective_value("silence_threshold", self.MIN_AUDIO_LEVEL)
        except ImportError:
            silence_threshold = self.MIN_AUDIO_LEVEL
            
        # Check for silence (use single audio for silence check - most recent)
        max_amp = audio.get_max_amplitude()
        if max_amp < silence_threshold:
            logger.debug(f"Audio is silent (max amplitude: {max_amp}, threshold: {silence_threshold})")
            return None
        
        # Save debug audio EARLY - before any recognition, so we always capture it
        # Prefer buffered audio for longer duration if available
        try:
            if buffered_audio:
                buffered_wav_bytes = self._convert_to_wav(buffered_audio)
                self._save_debug_audio(buffered_wav_bytes, is_buffered=True)
            else:
                single_wav_bytes = self._convert_to_wav(audio)
                self._save_debug_audio(single_wav_bytes, is_buffered=False)
        except Exception as e:
            logger.debug(f"Failed to save debug audio early: {e}")
        
        # 1. Try LOCAL FINGERPRINT FIRST (instant, offline, zero cost)
        if self._local and self._local.is_available():
            try:
                local_result = await self._local.recognize(local_audio)
                if local_result:
                    logger.info(f"Local FP recognized match: {local_result.artist} - {local_result.title}")
                    self._no_match_count = 0
                    return local_result
                logger.debug("Local: No match, falling back to ShazamIO")
            except Exception as e:
                logger.warning(f"Local recognition error: {e}")
        
        # 2. Try ShazamIO (cloud)
        try:
            # Convert to WAV bytes
            wav_bytes = self._convert_to_wav(shazam_audio)
            
            # Note: Debug audio is saved earlier (before Local FP) so it always captures
            
            logger.debug(f"Sending to ShazamIO ({len(wav_bytes) / 1024:.1f} KB)...")
            
            # Call ShazamIO
            result = await self._shazam.recognize(wav_bytes)
            recognition_time = time.time()
            
            # Check for matches
            if not result.get('matches'):
                self._no_match_count += 1
                # Throttled INFO logging: 1st and every 4th
                if self._no_match_count == 1 or self._no_match_count % 4 == 0:
                    logger.info(f"Shazamio: No matches found (attempt #{self._no_match_count})")
                else:
                    logger.debug(f"Shazamio: No matches found (attempt #{self._no_match_count})")
                
                # Try ACRCloud fallback if available
                if self._acrcloud and self._acrcloud.is_available():
                    logger.info("Trying ACRCloud fallback...")
                    acrcloud_wav = self._convert_to_wav(acrcloud_audio)
                    acrcloud_result = await self._acrcloud.recognize(acrcloud_audio, acrcloud_wav)
                    if acrcloud_result:
                        self._no_match_count = 0  # Reset on ACRCloud success
                        return acrcloud_result
                    logger.debug("ACRCloud fallback: No match")
                
                return None
            
            # Extract track info
            track = result.get('track', {})
            match = result['matches'][0]
            
            # Reset no-match counter on successful match
            self._no_match_count = 0
            
            # Extract core fields - keep artist name as-is from Shazam
            title = track.get('title', 'Unknown')
            artist = track.get('subtitle', 'Unknown')
            offset = match.get('offset', 0)
            
            # Extract ISRC (International Standard Recording Code)
            isrc = track.get('isrc')
            
            # Extract URLs
            shazam_url = track.get('url')
            spotify_url = self._extract_spotify_url(track)
            
            # Extract genre
            genre = None
            genres = track.get('genres', {})
            if isinstance(genres, dict):
                genre = genres.get('primary')
            
            # Extract album and cover art from Shazam response
            # Shazam stores images in 'images' or 'share' sections
            album = None
            album_art_url = None
            
            # Try to get album name from sections
            sections = track.get('sections', [])
            for section in sections:
                if section.get('type') == 'SONG':
                    metadata = section.get('metadata', [])
                    for item in metadata:
                        if item.get('title') == 'Album':
                            album = item.get('text')
                            break
            
            # Try to get cover art URL
            # Priority: coverarthq (high-res) > coverart > share.image
            images = track.get('images', {})
            album_art_url = (
                images.get('coverarthq') or  # High-res first
                images.get('coverart') or
                track.get('share', {}).get('image')
            )
            
            # Extract background image for visual modes
            background_image_url = images.get('background')
            
            # Extract lyrics if available (unsynced text)
            shazam_lyrics_text = self._extract_lyrics(track)
            
            # Extract skew values for quality check
            time_skew_val = match.get('timeskew', 0.0)
            freq_skew_val = match.get('frequencyskew', 0.0)
            
            # Quality check: Reject matches with high skew values (likely false positives)
            # If rejected, ACRCloud fallback will be attempted
            if abs(time_skew_val) > TIMESKEW_REJECT_THRESHOLD:
                logger.warning(
                    f"Shazamio: REJECTED - timeskew {time_skew_val:.6f} exceeds threshold "
                    f"({TIMESKEW_REJECT_THRESHOLD}) for '{artist} - {title}'"
                )
                # Try ACRCloud fallback
                if self._acrcloud and self._acrcloud.is_available():
                    logger.info("Trying ACRCloud fallback after skew rejection...")
                    acrcloud_wav = self._convert_to_wav(acrcloud_audio)
                    acrcloud_result = await self._acrcloud.recognize(acrcloud_audio, acrcloud_wav)
                    if acrcloud_result:
                        return acrcloud_result
                return None
            
            if abs(freq_skew_val) > FREQSKEW_REJECT_THRESHOLD:
                logger.warning(
                    f"Shazamio: REJECTED - frequencyskew {freq_skew_val:.6f} exceeds threshold "
                    f"({FREQSKEW_REJECT_THRESHOLD}) for '{artist} - {title}'"
                )
                # Try ACRCloud fallback
                if self._acrcloud and self._acrcloud.is_available():
                    logger.info("Trying ACRCloud fallback after skew rejection...")
                    acrcloud_wav = self._convert_to_wav(acrcloud_audio)
                    acrcloud_result = await self._acrcloud.recognize(acrcloud_audio, acrcloud_wav)
                    if acrcloud_result:
                        return acrcloud_result
                return None
            
            # Build result with latency compensation and all metadata
            recognition = RecognitionResult(
                title=title,
                artist=artist,
                offset=float(offset),
                capture_start_time=audio.capture_start_time,
                recognition_time=recognition_time,
                confidence=1.0,  # Shazam doesn't expose confidence directly
                time_skew=time_skew_val,
                frequency_skew=freq_skew_val,
                track_id=track.get('key'),
                album=album,
                album_art_url=album_art_url,
                isrc=isrc,
                shazam_url=shazam_url,
                spotify_url=spotify_url,
                background_image_url=background_image_url,
                genre=genre,
                shazam_lyrics_text=shazam_lyrics_text,
                recognition_provider="shazam"
            )
            
            latency = recognition.get_latency()
            current_pos = recognition.get_current_position()
            time_skew = match.get('timeskew', 0.0)
            freq_skew = match.get('frequencyskew', 0.0)
            
            logger.info(
                f"Recognized: {artist} - {title} | "
                f"Offset: {offset:.1f}s | "
                f"Latency: {latency:.1f}s | "
                f"Current: {current_pos:.1f}s | "
                f"Skew: t={time_skew:.6f}, f={freq_skew:.4f}"
            )
            
            # Save last match to cache for debugging
            self._save_debug_match('shazam', result)
            
            return recognition
            
        except Exception as e:
            logger.error(f"Recognition failed: {e}")
            return None
    
    def _convert_to_wav(self, audio: AudioChunk) -> bytes:
        """
        Convert AudioChunk to WAV bytes using stdlib wave module.
        
        This avoids the FFmpeg/pydub dependency entirely.
        Resamples to 44100 Hz if needed (ShazamIO works better with 44.1kHz).
        
        Args:
            audio: AudioChunk to convert
            
        Returns:
            WAV file bytes
        """
        TARGET_SAMPLE_RATE = 44100
        
        audio_data = audio.data
        sample_rate = audio.sample_rate
        channels = audio.channels
        
        # Resample to 44100 Hz if needed (WASAPI devices often return 48000 Hz)
        # NOTE: ShazamIO internally downsamples to 16kHz, so this step is optional
        # Set ENABLE_RESAMPLING = True at module level if you experience issues
        if ENABLE_RESAMPLING and sample_rate != TARGET_SAMPLE_RATE:
            try:
                # Try scipy for high-quality resampling
                from scipy import signal
                
                # Calculate new length
                num_samples = len(audio_data) if audio_data.ndim == 1 else audio_data.shape[0]
                new_num_samples = int(num_samples * TARGET_SAMPLE_RATE / sample_rate)
                
                # Resample (scipy returns float64)
                if audio_data.ndim == 1:
                    resampled = signal.resample(audio_data, new_num_samples)
                    # Clip to int16 range to prevent overflow, then convert
                    audio_data = np.clip(resampled, -32768, 32767).astype(np.int16)
                else:
                    # Stereo: resample each channel separately
                    resampled = np.zeros((new_num_samples, channels), dtype=np.float64)
                    for ch in range(channels):
                        resampled[:, ch] = signal.resample(audio_data[:, ch], new_num_samples)
                    # Clip to int16 range to prevent overflow, then convert
                    audio_data = np.clip(resampled, -32768, 32767).astype(np.int16)
                
                logger.debug(f"Resampled {sample_rate}Hz → {TARGET_SAMPLE_RATE}Hz ({num_samples} → {new_num_samples} samples)")
                sample_rate = TARGET_SAMPLE_RATE
                
            except ImportError:
                # Fallback: simple linear interpolation (lower quality but no deps)
                logger.warning("scipy not available, using simple resampling")
                num_samples = len(audio_data) if audio_data.ndim == 1 else audio_data.shape[0]
                new_num_samples = int(num_samples * TARGET_SAMPLE_RATE / sample_rate)
                
                old_indices = np.linspace(0, num_samples - 1, num_samples)
                new_indices = np.linspace(0, num_samples - 1, new_num_samples)
                
                if audio_data.ndim == 1:
                    audio_data = np.interp(new_indices, old_indices, audio_data).astype(np.int16)
                else:
                    resampled = np.zeros((new_num_samples, channels), dtype=np.int16)
                    for ch in range(channels):
                        resampled[:, ch] = np.interp(new_indices, old_indices, audio_data[:, ch]).astype(np.int16)
                    audio_data = resampled
                
                sample_rate = TARGET_SAMPLE_RATE
        
        buffer = io.BytesIO()
        
        with wave.open(buffer, 'wb') as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # int16 = 2 bytes per sample
            wf.setframerate(sample_rate)
            wf.writeframes(audio_data.tobytes())
        
        return buffer.getvalue()
    
    def _extract_spotify_url(self, track: dict) -> Optional[str]:
        """
        Extract Spotify URL from Shazam track data.
        
        Shazam sometimes includes Spotify links in hub.actions or providers.
        
        Args:
            track: Shazam track dict
            
        Returns:
            Spotify URL or None
        """
        try:
            # Check hub.actions for Spotify
            hub = track.get('hub', {})
            actions = hub.get('actions', [])
            for action in actions:
                uri = action.get('uri', '')
                if 'spotify' in uri.lower():
                    return uri
            
            # Check providers array
            providers = track.get('providers', [])
            for provider in providers:
                if provider.get('type') == 'spotify':
                    actions = provider.get('actions', [])
                    for action in actions:
                        uri = action.get('uri', '')
                        if uri:
                            return uri
        except Exception as e:
            logger.debug(f"Could not extract Spotify URL: {e}")
        
        return None
    
    def _extract_lyrics(self, track: dict) -> Optional[str]:
        """
        Extract lyrics text from Shazam track data.
        
        Shazam sometimes includes unsynced lyrics in sections.
        
        Args:
            track: Shazam track dict
            
        Returns:
            Lyrics text or None
        """
        try:
            sections = track.get('sections', [])
            for section in sections:
                if section.get('type') == 'LYRICS':
                    # Lyrics are in text array
                    text_lines = section.get('text', [])
                    if text_lines:
                        return '\n'.join(text_lines)
        except Exception as e:
            logger.debug(f"Could not extract lyrics: {e}")
        
        return None
