"""
Spotify Lyrics Provider
Uses Spotify's lyrics API (powered by Musixmatch) through a proxy server
"""

from typing import Optional, Dict, Any, List, Tuple
import requests
import os
from datetime import datetime
from dotenv import load_dotenv
import logging
from .base import LyricsProvider
from .spotify_api import SpotifyAPI

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SpotifyLyrics(LyricsProvider):
    """Spotify lyrics provider using hosted API"""
    
    def __init__(self) -> None:
        """Initialize provider with API endpoint from environment"""
        super().__init__(name="Spotify", priority=1)
        load_dotenv()
        self.api_url = os.getenv('SPOTIFY_LYRICS_SERVER', 
                                'https://spotify-lyrics-api-seven-azure.vercel.app')
        self.spotify = SpotifyAPI()  # Initialize Spotify API

    def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """Get lyrics for a track by searching Spotify"""
        try:
            # First try to get currently playing track
            track = self.spotify.get_current_track()
            
            # If no track is playing or it's a different track, search for the requested track
            if not track or (
                track.get('artist') != artist and 
                track.get('title') != title
            ):
                logger.debug(f"Searching Spotify for {artist} - {title}")
                track = self.spotify.search_track(artist, title)
                if not track:
                    logger.info(f"No track found on Spotify for: {artist} - {title}")
                    return None
            
            # Use the track URL
            track_url = track['url']
            
            response = requests.get(f"{self.api_url}/?url={track_url}&format=lrc")
            data = response.json()
            
            if data.get('error'):
                logger.error(f"API error: {data.get('message')}")
                return None
            
            # Log the response for debugging
            logger.debug(f"Spotify lyrics response: {data}")
            
            # Check if lyrics are properly synced
            if (data.get('syncType') == 'UNSYNCED' or 
                not data.get('lines') or 
                all(line.get('timeTag', '00:00.00') == '00:00.00' for line in data['lines'])):
                logger.warning(f"Unsynced lyrics found for {artist} - {title}, skipping")
                return None

            # Convert to standard format
            processed_lyrics = []
            for line in data['lines']:
                if not line.get('words', '').strip():  # Skip empty lines
                    continue
                    
                # Convert timeTag to seconds
                time_parts = line['timeTag'].split(':')
                seconds = float(time_parts[0]) * 60 + float(time_parts[1])
                processed_lyrics.append((seconds, line['words']))
            
            return processed_lyrics if processed_lyrics else None

        except Exception as e:
            logger.error(f"Error fetching lyrics: {e}")
            return None 