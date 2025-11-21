"""LRCLIB Provider for synchronized lyrics"""

import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

import requests as req
import logging
from .base import LyricsProvider
from config import get_provider_config
from logging_config import get_logger

logger = get_logger(__name__)

class LRCLIBProvider(LyricsProvider):
    # Define constants for the API
    BASE_URL = "https://lrclib.net/api"
    HEADERS = {
        "Lrclib-Client": "SyncLyrics v1.0.0 (https://github.com/AnshulJ999/SyncLyrics)"
    }
    
    def __init__(self):
        """Initialize LRCLIB provider with config settings"""
        super().__init__(provider_name="lrclib")
        
        # Get config settings
        config = get_provider_config("lrclib")
        
        self.BASE_URL = config.get("base_url", self.BASE_URL)
        self.HEADERS.update(config.get("headers", {}))  # Add any additional headers from config
    
    def get_lyrics(self, artist: str, title: str, album: str = None, duration: int = None) -> list | None:
        """
        Get lyrics using LRCLIB API
        Args:
            artist (str): Artist name
            title (str): Track title
            album (str): Album name (optional)
            duration (int): Track duration in seconds (optional)
        """
        try:
            # Clean up input strings
            artist = artist.strip()
            title = title.strip()
            if album:
                album = album.strip()

            # First try the more accurate /api/get endpoint with specific parameters
            params = {
                "artist_name": artist,
                "track_name": title
            }
            if album:
                params["album_name"] = album
            if duration:
                params["duration"] = duration

            logger.info(f"LRCLib - Trying exact match with params: {params}")
            
            # Try precise match first with proper headers
            response = req.get(
                f"{self.BASE_URL}/get", 
                params=params,
                headers=self.HEADERS
            ).json()
            
            # Check if we got a valid response with synced lyrics
            # If 404 OR if 200 but no synced lyrics, we should try searching
            has_synced = response.get("syncedLyrics") is not None
            is_404 = "code" in response and response["code"] == 404
            
            if is_404 or not has_synced:
                reason = "404 Not Found" if is_404 else "No synced lyrics in exact match"
                logger.info(f"LRCLib - {reason}, trying search with specific fields")
                
                search_params = {
                    "track_name": title,
                    "artist_name": artist
                }
                if album:
                    search_params["album_name"] = album
                    
                search_result = req.get(
                    f"{self.BASE_URL}/search",
                    params=search_params,
                    headers=self.HEADERS
                ).json()
                
                # If specific search fails, try general search as last resort
                if not search_result:
                    logger.info(f"LRCLib - No results with specific fields, trying general search")
                    search_result = req.get(
                        f"{self.BASE_URL}/search",
                        params={"q": f"{artist} {title}"},
                        headers=self.HEADERS
                    ).json()
                
                if not search_result: 
                    logger.info(f"LRCLib - No search results found for: {artist} - {title}")
                    return None
                
                # Iterate through search results to find one with synced lyrics
                found_match = False
                for result in search_result:
                    if result.get("syncedLyrics"):
                        response = result
                        found_match = True
                        logger.info(f"LRCLib - Found match in search results: {result.get('name')} by {result.get('artistName')}")
                        break
                
                if not found_match:
                    logger.info(f"LRCLib - Search results found but none had synced lyrics")
                    return None

            # Extract synced lyrics
            lyrics = response.get("syncedLyrics")
            if not lyrics:
                logger.info(f"LRCLib - No synced lyrics found for: {artist} - {title}")
                return None

            # Process lyrics SAFE PARSING
            processed_lyrics = []
            for line in lyrics.split("\n"):
                try:
                    if not line.strip() or "]" not in line: continue
                    
                    # Parse Timestamp
                    time_part = line[1: line.find("]")]
                    
                    # Skip meta tags like [by:...] or [ar:...]
                    if not time_part[0].isdigit(): continue

                    m, s = time_part.split(":")
                    seconds = float(m) * 60 + float(s)
                    text = line[line.find("]") + 1:].strip()
                    
                    processed_lyrics.append((seconds, text))
                except ValueError:
                    continue # Skip lines that fail to parse
            
            return processed_lyrics if processed_lyrics else None
            
        except Exception as e:
            logger.error(f"LRCLib - Error fetching lyrics for {artist} - {title}: {str(e)}")
            return None