"""
Spotify API Integration
Handles authentication and data retrieval from Spotify Web API
"""

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from typing import Optional, Dict, Any
import os
from dotenv import load_dotenv
import logging
import requests

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SpotifyAPI:
    def __init__(self):
        """Initialize Spotify API with credentials from environment variables"""
        self.client_id = os.getenv('SPOTIFY_CLIENT_ID')
        self.client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
        self.redirect_uri = os.getenv('SPOTIFY_REDIRECT_URI')
        self.scope = 'user-read-currently-playing user-read-playback-state'
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            logger.error("Missing Spotify credentials in environment variables")
            raise ValueError("Missing Spotify credentials")
        
        self.sp = self._get_spotify_client()
        
    def _get_spotify_client(self) -> spotipy.Spotify:
        """Create authenticated Spotify client"""
        try:
            return spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                scope=self.scope
            ))
        except Exception as e:
            logger.error(f"Failed to initialize Spotify client: {e}")
            raise

    def get_current_track(self) -> Optional[Dict[str, Any]]:
        """Get currently playing track information"""
        try:
            current = self.sp.current_playback()
            if not current or not current.get('item'):
                return None
                
            track = current['item']
            return {
                'title': track['name'],
                'artist': track['artists'][0]['name'],
                'album': track['album']['name'],
                'album_art': track['album']['images'][0]['url'],
                'track_id': track['id'],
                'url': track['external_urls']['spotify'],
                'duration_ms': track['duration_ms'],
                'progress_ms': current['progress_ms']
            }
        except Exception as e:
            logger.error(f"Error getting current track: {e}")
            return None

    def search_track(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Search for a track on Spotify and return its details"""
        try:
            # Clean up search terms
            search_query = f"{artist} {title}".replace(" ", "+")
            
            # Make request to Spotify search API
            response = requests.get(
                f"https://api.spotify.com/v1/search",
                params={
                    "q": search_query,
                    "type": "track",
                    "limit": 1
                },
                headers=self.headers
            )
            
            if response.status_code != 200:
                logger.error(f"Search failed with status {response.status_code}")
                return None
                
            data = response.json()
            tracks = data.get('tracks', {}).get('items', [])
            
            if not tracks:
                logger.info(f"No tracks found for: {artist} - {title}")
                return None
                
            track = tracks[0]
            return {
                'title': track['name'],
                'artist': track['artists'][0]['name'],
                'album': track['album']['name'],
                'url': track['external_urls']['spotify'],
                'album_art': track['album']['images'][0]['url'] if track['album']['images'] else None,
                'duration_ms': track['duration_ms'],
                'progress_ms': 0  # Not applicable for search results
            }
            
        except Exception as e:
            logger.error(f"Error searching track: {e}")
            return None