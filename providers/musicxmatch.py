"""Musicxmatch Provider for synchronized lyrics"""

import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from typing import Optional, List, Tuple, Dict, Any
from .base import LyricsProvider
from config import get_provider_config
from logging_config import get_logger

logger = get_logger(__name__)

class MusicxmatchProvider(LyricsProvider):
    """Provider for fetching lyrics from Musixmatch API"""
    
    def __init__(self):
        """Initialize Musicxmatch provider with config settings"""
        super().__init__(provider_name="musicxmatch")
        
        # Get config settings
        config = get_provider_config("musicxmatch")
        
        # Import the library here to avoid issues if not installed
        try:
            from musicxmatch_api import MusixMatchAPI
            self.api = MusixMatchAPI()
            self._available = True
        except ImportError:
            logger.error("musicxmatch_api library not installed. Run: pip install musicxmatch_api")
            self._available = False
            self.enabled = False
    
    def get_lyrics(self, artist: str, title: str, album: str = None, duration: int = None) -> Optional[Dict[str, Any]]:
        """
        Get lyrics using Musicxmatch API
        
        Args:
            artist (str): Artist name
            title (str): Track title
            album (str): Album name (optional, not used but kept for compatibility)
            duration (int): Track duration in seconds (optional, not used but kept for compatibility)
            
        Returns:
            Optional[Dict[str, Any]]: Dictionary with synced lyrics and metadata or None
        """
        if not self._available:
            return None
            
        try:
            # Clean up input strings
            artist = artist.strip()
            title = title.strip()
            
            # Search for the track
            logger.info(f"Musicxmatch - Searching for: {artist} - {title}")
            search_query = f"{artist} {title}"
            search_results = self.api.search_tracks(search_query)
            
            if not search_results or "message" not in search_results:
                logger.info(f"Musicxmatch - No results found for: {artist} - {title}")
                return None
            
            # Check status code
            status_code = search_results.get("message", {}).get("header", {}).get("status_code")
            if status_code != 200:
                logger.warning(f"Musicxmatch - API returned status {status_code} (likely captcha/blocked)")
                return None

            # Extract body
            body = search_results.get("message", {}).get("body", {})
            
            # Handle case where body is a list (e.g. empty list [])
            if isinstance(body, list):
                if not body:
                    logger.info(f"Musicxmatch - No results (empty body) for: {artist} - {title}")
                    return None
                # If it's a non-empty list, we don't know the structure, so log and return
                logger.warning(f"Musicxmatch - Unexpected body format (list): {body}")
                return None
                
            # Extract track list from body dict
            track_list = body.get("track_list", [])
            
            if not track_list:
                logger.info(f"Musicxmatch - No tracks in search results for: {artist} - {title}")
                return None
            
            # Get the first (best match) track ID
            track = track_list[0].get("track", {})
            track_id = track.get("track_id")
            
            if not track_id:
                logger.info(f"Musicxmatch - No track ID found for: {artist} - {title}")
                return None
            
            logger.info(f"Musicxmatch - Found track ID: {track_id}")
            
            # Get lyrics for the track
            lyrics_response = self.api.get_track_lyrics(track_id=track_id)
            
            if not lyrics_response or "message" not in lyrics_response:
                logger.info(f"Musicxmatch - No lyrics response for track ID: {track_id}")
                return None
            
            # Extract lyrics body
            lyrics_data = lyrics_response.get("message", {}).get("body", {}).get("lyrics", {})
            lyrics_body = lyrics_data.get("lyrics_body", "")
            
            # Check if we have synced lyrics
            # Note: The basic API might not provide synced lyrics, only plain text
            # We'll try to get subtitle/synced lyrics if available
            subtitle_response = None
            subtitle_body = ""  # Initialize to prevent NameError if subtitle_response fails
            try:
                # Try to get synced lyrics (this might not be available in community API)
                subtitle_response = self.api.get_track_subtitle(track_id=track_id)
            except Exception as e:
                logger.debug(f"Musicxmatch - Subtitle API not available: {e}")
            
            # If we have subtitle/synced lyrics, parse them
            if subtitle_response and "message" in subtitle_response:
                subtitle_data = subtitle_response.get("message", {}).get("body", {}).get("subtitle", {})
                subtitle_body = subtitle_data.get("subtitle_body", "")
                
            if subtitle_body:
                parsed = self._parse_synced_lyrics(subtitle_body)
                if parsed:
                    return {
                        "lyrics": parsed,
                        "is_instrumental": False
                    }
            
            # If no synced lyrics available, log and return None
            # We only want synced lyrics for this application
            if lyrics_body:
                logger.info(f"Musicxmatch - Found plain text lyrics but no synced lyrics for: {artist} - {title}")
            else:
                logger.info(f"Musicxmatch - No lyrics found for: {artist} - {title}")
            
            return None
            
        except Exception as e:
            logger.error(f"Musicxmatch - Error fetching lyrics for {artist} - {title}: {str(e)}")
            return None
    
    def _parse_synced_lyrics(self, subtitle_body: str) -> Optional[List[Tuple[float, str]]]:
        """
        Parse synced lyrics from Musicxmatch subtitle format
        
        Args:
            subtitle_body (str): JSON string containing synced lyrics
            
        Returns:
            Optional[List[Tuple[float, str]]]: Parsed lyrics or None
        """
        try:
            import json
            
            # Musicxmatch subtitles are in JSON format
            subtitle_data = json.loads(subtitle_body)
            
            if not isinstance(subtitle_data, list):
                return None
            
            processed_lyrics = []
            
            for line in subtitle_data:
                if isinstance(line, dict):
                    # Extract time and text
                    time = line.get("time", {}).get("total", 0)
                    text = line.get("text", "").strip()
                    
                    if text:
                        processed_lyrics.append((float(time), text))
            
            if processed_lyrics:
                logger.info(f"Musicxmatch - Successfully parsed {len(processed_lyrics)} synced lyric lines")
                return processed_lyrics
            
            return None
            
        except Exception as e:
            logger.error(f"Musicxmatch - Error parsing synced lyrics: {e}")
            return None
