"""
ACRCloud Recognition Module

Fallback audio recognition via ACRCloud when Shazamio fails.
Credentials loaded from environment variables.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from logging_config import get_logger
from .shazam import RecognitionResult
from .capture import AudioChunk

logger = get_logger(__name__)


class ACRCloudRecognizer:
    """
    ACRCloud audio recognition as fallback for Shazamio.
    
    Features:
    - Only used when Shazamio fails
    - Daily request limit to conserve quota
    - Cooldown between requests
    - Auto-disabled if credentials missing
    """
    
    def __init__(self):
        """Initialize ACRCloud client from environment variables."""
        self._host = os.getenv("ACRCLOUD_HOST", "")
        self._access_key = os.getenv("ACRCLOUD_ACCESS_KEY", "")
        self._access_secret = os.getenv("ACRCLOUD_ACCESS_SECRET", "")
        
        # Rate limiting
        self._daily_limit = int(os.getenv("ACRCLOUD_DAILY_LIMIT", "100"))
        self._cooldown_seconds = int(os.getenv("ACRCLOUD_COOLDOWN", "30"))
        
        # Usage tracking
        self._requests_today = 0
        self._last_request_date: Optional[date] = None
        self._last_request_time: float = 0
        
        # Check if properly configured
        self._enabled = bool(self._host and self._access_key and self._access_secret)
        
        if self._enabled:
            logger.info(f"ACRCloud initialized (host: {self._host}, daily limit: {self._daily_limit})")
        else:
            logger.debug("ACRCloud not configured (missing credentials in .env)")
    
    def is_available(self) -> bool:
        """Check if ACRCloud is configured and available."""
        return self._enabled
    
    def _reset_daily_counter_if_needed(self) -> None:
        """Reset the daily request counter if it's a new day."""
        today = date.today()
        if self._last_request_date != today:
            self._requests_today = 0
            self._last_request_date = today
            logger.debug("ACRCloud daily counter reset")
    
    def _can_make_request(self) -> tuple[bool, str]:
        """
        Check if we can make an ACRCloud request.
        
        Returns:
            Tuple of (can_request, reason)
        """
        if not self._enabled:
            return False, "ACRCloud not configured"
        
        self._reset_daily_counter_if_needed()
        
        # Check daily limit
        if self._requests_today >= self._daily_limit:
            return False, f"Daily limit reached ({self._daily_limit})"
        
        # Check cooldown
        time_since_last = time.time() - self._last_request_time
        if time_since_last < self._cooldown_seconds:
            remaining = self._cooldown_seconds - time_since_last
            return False, f"Cooldown active ({remaining:.0f}s remaining)"
        
        return True, "OK"
    
    def _create_signature(self, timestamp: str) -> str:
        """Create HMAC-SHA1 signature for ACRCloud API."""
        http_method = "POST"
        http_uri = "/v1/identify"
        data_type = "audio"
        signature_version = "1"
        
        string_to_sign = f"{http_method}\n{http_uri}\n{self._access_key}\n{data_type}\n{signature_version}\n{timestamp}"
        
        sign = base64.b64encode(
            hmac.new(
                self._access_secret.encode('ascii'),
                string_to_sign.encode('ascii'),
                digestmod=hashlib.sha1
            ).digest()
        ).decode('ascii')
        
        return sign
    
    async def recognize(self, audio: AudioChunk, wav_bytes: bytes) -> Optional[RecognitionResult]:
        """
        Recognize a song using ACRCloud.
        
        Args:
            audio: AudioChunk with capture timing info
            wav_bytes: WAV audio data
            
        Returns:
            RecognitionResult or None if no match/error
        """
        can_request, reason = self._can_make_request()
        if not can_request:
            logger.debug(f"ACRCloud skipped: {reason}")
            return None
        
        try:
            timestamp = str(int(time.time()))
            signature = self._create_signature(timestamp)
            
            files = {
                'sample': ('audio.wav', wav_bytes, 'audio/wav'),
            }
            data = {
                'access_key': self._access_key,
                'data_type': 'audio',
                'signature_version': '1',
                'signature': signature,
                'sample_bytes': len(wav_bytes),
                'timestamp': timestamp,
            }
            
            url = f"https://{self._host}/v1/identify"
            
            logger.debug(f"Sending to ACRCloud ({len(wav_bytes) / 1024:.1f} KB)...")
            
            # Make request (synchronous, but wrapped in async context)
            response = requests.post(url, files=files, data=data, timeout=30)
            recognition_time = time.time()
            
            # Update usage tracking
            self._requests_today += 1
            self._last_request_time = recognition_time
            
            result = response.json()
            
            # Check status
            status = result.get('status', {})
            if status.get('code') != 0:
                logger.debug(f"ACRCloud no match: {status.get('msg', 'Unknown')}")
                return None
            
            # Extract metadata
            metadata = result.get('metadata', {})
            music = metadata.get('music', [])
            
            if not music:
                logger.debug("ACRCloud: No music in response")
                return None
            
            # Use first (best) match
            track = music[0]
            
            # Extract fields
            title = track.get('title', 'Unknown')
            artists = track.get('artists', [])
            artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
            album_info = track.get('album', {})
            album = album_info.get('name')
            
            # Offset calculation: ACRCloud's play_offset_ms is the position at END of sample,
            # but our system (like Shazamio) expects position at START of capture.
            # We need to subtract the sample duration used for recognition.
            offset_ms = track.get('play_offset_ms', 0)
            sample_end_ms = track.get('sample_end_time_offset_ms', 0)
            sample_begin_ms = track.get('sample_begin_time_offset_ms', 0)
            sample_duration_ms = sample_end_ms - sample_begin_ms
            
            # Convert to seconds and adjust to capture START position
            offset = (offset_ms - sample_duration_ms) / 1000.0
            
            # External IDs
            external_ids = track.get('external_ids', {})
            isrc = external_ids.get('isrc')
            
            # Genre
            genres = track.get('genres', [])
            genre = genres[0].get('name') if genres else None
            
            # Spotify metadata
            spotify_url = None
            external_meta = track.get('external_metadata', {})
            spotify = external_meta.get('spotify', {})
            if spotify:
                spotify_track = spotify.get('track', {})
                spotify_id = spotify_track.get('id')
                if spotify_id:
                    spotify_url = f"https://open.spotify.com/track/{spotify_id}"
            
            # Duration (ACRCloud provides this)
            duration_ms = track.get('duration_ms', 0)
            duration = duration_ms / 1000.0 if duration_ms else None
            
            # Build result (same format as Shazamio)
            recognition = RecognitionResult(
                title=title,
                artist=artist,
                offset=offset,
                capture_start_time=audio.capture_start_time,
                recognition_time=recognition_time,
                confidence=track.get('score', 100) / 100.0,
                time_skew=0.0,
                frequency_skew=0.0,
                track_id=track.get('acrid'),
                album=album,
                album_art_url=None,  # ACRCloud doesn't provide album art
                isrc=isrc,
                shazam_url=None,  # Not from Shazam
                spotify_url=spotify_url,
                background_image_url=None,
                genre=genre,
                shazam_lyrics_text=None,
                source="acrcloud",
                duration=duration
            )
            
            latency = recognition.get_latency()
            current_pos = recognition.get_current_position()
            
            logger.info(
                f"ACRCloud recognized: {artist} - {title} | "
                f"Offset: {offset:.1f}s | "
                f"Latency: {latency:.1f}s | "
                f"Current: {current_pos:.1f}s | "
                f"Requests today: {self._requests_today}/{self._daily_limit}"
            )
            
            # Save last match to cache for debugging
            self._save_debug_match(result)
            
            return recognition
            
        except requests.exceptions.Timeout:
            logger.warning("ACRCloud request timed out")
            return None
        except Exception as e:
            logger.error(f"ACRCloud recognition failed: {e}")
            return None
    
    def get_usage_stats(self) -> dict:
        """Get current usage statistics."""
        self._reset_daily_counter_if_needed()
        return {
            "enabled": self._enabled,
            "requests_today": self._requests_today,
            "daily_limit": self._daily_limit,
            "cooldown_seconds": self._cooldown_seconds,
            "remaining_today": max(0, self._daily_limit - self._requests_today),
        }
    
    def _save_debug_match(self, result: dict) -> None:
        """Save last ACRCloud match response to cache for debugging."""
        try:
            cache_dir = Path("cache")
            cache_dir.mkdir(parents=True, exist_ok=True)
            match_path = cache_dir / "last_acrcloud_match.json"
            with open(match_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Failed to save debug match: {e}")
