"""LRCLIB Provider for synchronized lyrics"""

import requests as req
import logging
from .base import LyricsProvider

logger = logging.getLogger(__name__)

class LRCLIBProvider(LyricsProvider):
    # Define constants for the API
    BASE_URL = "https://lrclib.net/api"
    HEADERS = {
        "Lrclib-Client": "SyncLyrics v1.0.0 (https://github.com/AnshulJ999/SyncLyrics)"
    }
    
    def __init__(self):
        super().__init__(name="LRCLIB", priority=3)
    
    def get_lyrics(self, artist: str, title: str, album: str = None, duration: int = None):
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
            
            # If precise match fails, try search with specific fields
            if "code" in response and response["code"] == 404:
                logger.info(f"LRCLib - No exact match found, trying search with specific fields")
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
                    
                song_id = search_result[0]["id"]
                response = req.get(f"{self.BASE_URL}/get/{song_id}", headers=self.HEADERS).json()

            # Extract synced lyrics
            lyrics = response.get("syncedLyrics")
            if not lyrics:
                logger.info(f"LRCLib - No synced lyrics found for: {artist} - {title}")
                return None

            # Process lyrics
            processed_lyrics = []
            for line in lyrics.split("\n"):
                time = line[1: line.find("]") -1]
                m, s = time.split(":")
                seconds = float(m) * 60 + float(s)
                processed_lyrics.append((seconds, line[line.find("]") + 1:].strip()))
            
            return processed_lyrics if processed_lyrics else None
            
        except Exception as e:
            logger.error(f"LRCLib - Error fetching lyrics for {artist} - {title}: {str(e)}")
            return None 