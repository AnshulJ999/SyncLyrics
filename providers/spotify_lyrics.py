"""
Spotify Lyrics Provider
Uses Spotify's lyrics API (powered by Musixmatch) through a proxy server
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

from typing import Optional, Dict, Any, List, Tuple
import requests
import os
import time
from datetime import datetime
from dotenv import load_dotenv
import logging
from .base import LyricsProvider
from providers.spotify_api import SpotifyAPI
from logging_config import get_logger
from config import get_provider_config

# Configure logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

logger = get_logger(__name__)

class SpotifyLyrics(LyricsProvider):
    """Spotify lyrics provider using hosted API"""
    
    def __init__(self) -> None:
        """Initialize Spotify lyrics provider with config settings"""
        super().__init__(provider_name="spotify")
        
        # Get config settings
        config = get_provider_config("spotify")
        
        # Initialize API settings from config
        self.api_url = config.get('base_url', 'https://spotify-lyrics-api-azure.vercel.app')
        self.spotify = SpotifyAPI()
        if not self.spotify.initialized:
            logger.error("Failed to initialize Spotify client in SpotifyLyrics")
            
    async def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """Get lyrics for a track by searching Spotify"""
        try:
            if not self.spotify.initialized:
                logger.error("Spotify client not initialized")
                return None
                
            # First try to get currently playing track
            track = await self.spotify.get_current_track()
            
            # If no track is playing or it's a different track, search for the requested track
            if not track or (
                track.get('artist') != artist and 
                track.get('title') != title
            ):
                logger.info(f"Spotify - Searching Spotify for {artist} - {title}")
                track = self.spotify.search_track(artist, title)
                if not track:
                    logger.info(f"No track found on Spotify for: {artist} - {title}")
                    return None
            
            # Use the track URL
            track_url = track['url']
            
            response = requests.get(f"{self.api_url}/?url={track_url}&format=lrc")
            data = response.json()
            
            if data.get('error'):
                logger.error(f"Spotify - API error: {data.get('message')}")
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
            logger.error(f"Spotify - Error fetching lyrics: {e}")
            return None 