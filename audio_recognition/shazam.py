"""
Shazam Recognition Module

Handles song recognition via ShazamIO with latency-compensated results.
Uses stdlib wave module for audio conversion (no FFmpeg/pydub dependency).
"""

import io
import time
import wave
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

try:
    from shazamio import Shazam
except ImportError:
    Shazam = None

from logging_config import get_logger
from .capture import AudioChunk

logger = get_logger(__name__)


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
        track_id: Shazam's track identifier (for dedup)
        album_art_url: URL to album cover art
        isrc: International Standard Recording Code
        shazam_url: URL to view song on Shazam
        spotify_url: URL to play on Spotify (if available)
        background_image_url: Background image for visual modes
        genre: Primary genre
        shazam_lyrics_text: Raw lyrics text from Shazam (unsynced)
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
        
        Uses track_id if available, otherwise compares artist+title.
        
        Args:
            other: Another RecognitionResult to compare
            
        Returns:
            True if same song
        """
        if other is None:
            return False
            
        # Prefer track_id comparison if both have it
        if self.track_id and other.track_id:
            return self.track_id == other.track_id
            
        # Fall back to name comparison
        return (
            self.artist.lower().strip() == other.artist.lower().strip() and
            self.title.lower().strip() == other.title.lower().strip()
        )
    
    def __str__(self) -> str:
        return f"{self.artist} - {self.title} @ {self.get_current_position():.1f}s"


class ShazamRecognizer:
    """
    Handles song recognition via ShazamIO.
    
    Features:
    - Converts audio using stdlib wave (no FFmpeg dependency)
    - Automatic latency compensation in results
    - Silence detection to avoid unnecessary API calls
    """
    
    MIN_AUDIO_LEVEL = 100  # Minimum amplitude for valid audio
    
    def __init__(self):
        """Initialize Shazam client."""
        if Shazam is None:
            logger.error("shazamio not installed. Song recognition unavailable.")
            self._shazam = None
        else:
            self._shazam = Shazam()
            
    @staticmethod
    def is_available() -> bool:
        """Check if ShazamIO is available."""
        return Shazam is not None
    
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
    
    async def recognize(self, audio: AudioChunk) -> Optional[RecognitionResult]:
        """
        Recognize a song from an audio chunk.
        
        Args:
            audio: AudioChunk from capture
            
        Returns:
            RecognitionResult with latency compensation, or None if no match
        """
        if not self._shazam:
            logger.error("ShazamIO not available")
            return None
            
        # Check for silence
        if audio.is_silent(self.MIN_AUDIO_LEVEL):
            logger.debug(f"Audio is silent (max amplitude: {audio.get_max_amplitude()})")
            return None
        
        try:
            # Convert to WAV bytes
            wav_bytes = self._convert_to_wav(audio)
            
            logger.debug(f"Sending to ShazamIO ({len(wav_bytes) / 1024:.1f} KB)...")
            
            # Call ShazamIO
            result = await self._shazam.recognize(wav_bytes)
            recognition_time = time.time()
            
            # Check for matches
            if not result.get('matches'):
                logger.debug("No matches found")
                return None
            
            # Extract track info
            track = result.get('track', {})
            match = result['matches'][0]
            
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
            
            # Build result with latency compensation and all metadata
            recognition = RecognitionResult(
                title=title,
                artist=artist,
                offset=float(offset),
                capture_start_time=audio.capture_start_time,
                recognition_time=recognition_time,
                confidence=1.0,  # Shazam doesn't expose confidence directly
                time_skew=match.get('timeskew', 0.0),
                frequency_skew=match.get('frequencyskew', 0.0),
                track_id=track.get('key'),
                album=album,
                album_art_url=album_art_url,
                isrc=isrc,
                shazam_url=shazam_url,
                spotify_url=spotify_url,
                background_image_url=background_image_url,
                genre=genre,
                shazam_lyrics_text=shazam_lyrics_text
            )
            
            latency = recognition.get_latency()
            current_pos = recognition.get_current_position()
            
            logger.info(
                f"Recognized: {artist} - {title} | "
                f"Offset: {offset:.1f}s | "
                f"Latency: {latency:.1f}s | "
                f"Current: {current_pos:.1f}s"
            )
            
            return recognition
            
        except Exception as e:
            logger.error(f"Recognition failed: {e}")
            return None
    
    def _convert_to_wav(self, audio: AudioChunk) -> bytes:
        """
        Convert AudioChunk to WAV bytes using stdlib wave module.
        
        This avoids the FFmpeg/pydub dependency entirely.
        
        Args:
            audio: AudioChunk to convert
            
        Returns:
            WAV file bytes
        """
        buffer = io.BytesIO()
        
        with wave.open(buffer, 'wb') as wf:
            wf.setnchannels(audio.channels)
            wf.setsampwidth(2)  # int16 = 2 bytes per sample
            wf.setframerate(audio.sample_rate)
            wf.writeframes(audio.data.tobytes())
        
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
