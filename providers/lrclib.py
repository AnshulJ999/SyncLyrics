"""LRCLIB Provider for synchronized lyrics"""

import requests as req
import logging
from .base import LyricsProvider

logger = logging.getLogger(__name__)

class LRCLIBProvider(LyricsProvider):
    def __init__(self):
        super().__init__(name="LRCLIB", priority=1)
    
    def get_lyrics(self, artist: str, title: str):
        try:
            # Search for song, direct API call with request
            artist_title = f"{artist} {title}"
            search_result = req.get(f"https://lrclib.net/api/search?q={artist_title}").json()
            
            # Simple null check
            if not search_result: 
                logger.info(f"No search results found for: {artist_title}")
                return None
                
            # Get lyrics
            song_id = search_result[0]["id"]
            lyrics = req.get(f"https://lrclib.net/api/get/{song_id}").json()["syncedLyrics"]
            
            if not lyrics:
                logger.info(f"No lyrics found for song ID: {song_id}")
                return None

            # Process lyrics
            processed_lyrics = []
            for line in lyrics.split("\n"):
                time = line[1: line.find("]") -1]
                m, s = time.split(":")
                seconds = float(m) * 60 + float(s)
                processed_lyrics.append((seconds, line[line.find("]") + 1:].strip()))
            
            return processed_lyrics
            
        except Exception as e:
            logger.error(f"Error fetching lyrics for {artist} - {title}: {str(e)}")
            return None 