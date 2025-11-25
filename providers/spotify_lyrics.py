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
from providers.spotify_api import get_shared_spotify_client
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
        # NOTE: We use get_shared_spotify_client() lazily in get_lyrics() instead of storing
        # an instance here. This ensures all API calls use the singleton instance and
        # statistics are consolidated across the entire app.
            
    async def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """Get lyrics for a track by searching Spotify"""
        try:
            # Return None if both artist and title are empty
            if not artist.strip() and not title.strip():
                logger.debug("Spotify - Empty artist and title, skipping lyrics search")
                return None

            # Get the shared singleton instance (consolidates all stats)
            spotify_client = get_shared_spotify_client()
            
            if spotify_client is None or not spotify_client.initialized:
                logger.error("Spotify client not initialized")
                return None
                
            # First try to get currently playing track
            track = await spotify_client.get_current_track()
            
            # If no track is playing or it's a different track, search for the requested track
            if not track or (
                track.get('artist') != artist and 
                track.get('title') != title
            ):
                logger.info(f"Spotify - Searching Spotify for {artist} - {title}")
                track = spotify_client.search_track(artist, title)
                if not track:
                    logger.info(f"No track found on Spotify for: {artist} - {title}")
                    return None
            
            # Use the track URL
            track_url = track['url']
            
            # CRITICAL FIX: Implement retry logic with exponential backoff
            # Retry on 404 (might be temporary server issue), server errors (5xx), and connection errors
            last_error = None
            for attempt in range(self.retries):
                try:
                    response = requests.get(
                        f"{self.api_url}/?url={track_url}&format=lrc",
                        timeout=self.timeout
                    )
                    
                    # Distinguish between different error types
                    if response.status_code == 404:
                        # 404 might be temporary server issue (Vercel cold start, deployment issue)
                        # Retry 2-3 times before giving up
                        if attempt < self.retries - 1:
                            backoff = 3 * (2 ** attempt)  # Exponential: 3s, 6s, 12s
                            logger.warning(f"Spotify Proxy returned 404 (server might be unavailable), retry {attempt + 1}/{self.retries} in {backoff}s")
                            time.sleep(backoff)
                            continue
                        else:
                            logger.info(f"Spotify Proxy - No lyrics found for {artist} - {title} (404 after {self.retries} attempts)")
                            return None
                    elif response.status_code == 429:
                        # Rate limited - retry with backoff
                        retry_after = int(response.headers.get('Retry-After', 2 ** attempt))
                        logger.warning(f"Spotify Proxy rate limited, retrying after {retry_after}s")
                        time.sleep(retry_after)
                        continue
                    elif response.status_code >= 500:
                        # Server error - retry with exponential backoff
                        if attempt < self.retries - 1:
                            backoff = 2 ** attempt  # Exponential: 1s, 2s, 4s
                            logger.warning(f"Spotify Proxy server error {response.status_code}, retry {attempt + 1}/{self.retries} in {backoff}s")
                            time.sleep(backoff)
                            continue
                        else:
                            logger.error(f"Spotify Proxy server error {response.status_code} after {self.retries} attempts")
                            return None
                    elif response.status_code != 200:
                        # Other error codes (4xx except 404/429) - don't retry
                        logger.error(f"Spotify Proxy returned status {response.status_code}")
                        return None
                    
                    # Success - parse response
                    try:
                        data = response.json()
                    except Exception as e:
                        logger.error(f"Spotify Proxy returned invalid JSON: {e}")
                        return None
                    
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
                    
                except requests.exceptions.Timeout:
                    # Timeout - retry with backoff
                    if attempt < self.retries - 1:
                        backoff = 2 ** attempt
                        logger.warning(f"Spotify Proxy timeout, retry {attempt + 1}/{self.retries} in {backoff}s")
                        time.sleep(backoff)
                        continue
                    else:
                        logger.error(f"Spotify Proxy timeout after {self.retries} attempts")
                        return None
                        
                except requests.exceptions.ConnectionError as e:
                    # Connection error - retry with backoff
                    if attempt < self.retries - 1:
                        backoff = 2 ** attempt
                        logger.warning(f"Spotify Proxy connection error, retry {attempt + 1}/{self.retries} in {backoff}s")
                        time.sleep(backoff)
                        continue
                    else:
                        logger.error(f"Spotify Proxy connection error after {self.retries} attempts: {e}")
                        return None

        except Exception as e:
            logger.error(f"Spotify - Error fetching lyrics: {e}")
            return None 