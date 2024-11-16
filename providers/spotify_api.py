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
import time
from requests.exceptions import ReadTimeout
from logging_config import get_logger

# Load environment variables
load_dotenv()

# Configure logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

logger = get_logger(__name__)

    
class SpotifyAPI:
    def __init__(self):
        """Initialize Spotify API with credentials from environment variables and settings"""
        self.max_retries = 3
        self.timeout = 5  # seconds
        self.retry_delay = 1  # seconds
        self.initialized = False
        
        try:
            # Initialize Spotify client
            self.client_id = os.getenv('SPOTIFY_CLIENT_ID')
            self.client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')
            self.redirect_uri = os.getenv('SPOTIFY_REDIRECT_URI')
            self.scope = 'user-read-currently-playing user-read-playback-state'
            
            if not all([self.client_id, self.client_secret, self.redirect_uri]):
                logger.error("Missing Spotify credentials in environment variables")
                return
                
            self.sp = self._get_spotify_client()
            self.initialized = True
            logger.info("Spotify API initialized successfully")
            
            # Test connection
            self._test_connection()
            
        except Exception as e:
            logger.error(f"Failed to initialize Spotify API: {e}")

        # Use the custom logger
        self.logger = logger

    def is_spotify_healthy(self) -> bool:
        """Quick health check for Spotify API"""
        try:
            # Try a simple API call
            response = requests.get(
                "https://api.spotify.com/v1/me/player",
                headers=self.headers,
                timeout=3  # Short timeout
            )
            self.logger.info(f"Spotify health check successful (status code: {response.status_code})")
            return response.status_code in [200, 204]  # 204 means no track playing
        except Exception as e:
            self.logger.error(f"Spotify health check failed: {e}")
            return False

    def _get_spotify_client(self) -> spotipy.Spotify:
        """Create authenticated Spotify client with timeout settings"""
        try:
            return spotipy.Spotify(
                auth_manager=SpotifyOAuth(
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                    redirect_uri=self.redirect_uri,
                    scope=self.scope
                ),
                requests_timeout=self.timeout,
                retries=self.max_retries
            )
        except Exception as e:
            logger.error(f"Failed to initialize Spotify client: {e}")
            raise

    def _test_connection(self) -> bool:
        """Test API connection with retries"""
        for attempt in range(self.max_retries):
            try:
                self.sp.current_user()  # Simple API call to test connection
                logger.info("Successfully connected to Spotify API")
                return True
            except Exception as e:
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                time.sleep(self.retry_delay)
        return False

    def get_current_track(self) -> Optional[Dict[str, Any]]:
        """Get currently playing track with proper timeout handling"""
        if not self.initialized:
            logger.warning("Spotify API not initialized, skipping track fetch")
            return None
        
        try:
            current = self.sp.current_playback()
            
            # Check for specific error responses
            if hasattr(current, 'status_code'):
                if current.status_code == 429:
                    logger.error("Rate limit exceeded. Retry-After: %s", 
                               current.headers.get('Retry-After', 'unknown'))
                    return None
                elif current.status_code != 200:
                    logger.error("Spotify API error: Status %d, Response: %s", 
                               current.status_code, current.text)
                    return None
            
            if not current or not current.get('item'):
                logger.debug("No track currently playing")
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
        except ReadTimeout:
            logger.error("Spotify API timeout - retrying once")
            try:
                current = self.sp.current_playback()
                
                # Check for specific error responses
                if hasattr(current, 'status_code'):
                    if current.status_code == 429:
                        logger.error("Rate limit exceeded. Retry-After: %s", 
                                   current.headers.get('Retry-After', 'unknown'))
                        return None
                    elif current.status_code != 200:
                        logger.error("Spotify API error: Status %d, Response: %s", 
                                   current.status_code, current.text)
                        return None
                
                if not current or not current.get('item'):
                    logger.debug("No track currently playing")
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
                logger.error(f"Retry after timeout failed: {e}")
                return None
        except Exception as e:
            logger.error("Spotify API error: %s", str(e), exc_info=True)
            return None

    def search_track(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Search for a track on Spotify and return its details"""
        if not self.initialized:
            logger.warning("Spotify API not initialized, skipping track search")
            return None
            
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
                headers=self.headers,
                timeout=self.timeout
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
            
        except ReadTimeout:
            logger.error("Search request timed out")
            return None
        except Exception as e:
            logger.error(f"Error searching track: {e}")
            return None

    def fetch_with_retry(self, url: str, headers: dict, method: str = "GET", data=None, params=None, retries=3, backoff_factor=2):
        for attempt in range(retries):
            try:
                self.logger.debug(f"Spotify API Request: {method} {url} - Attempt {attempt + 1}/{retries}")
                logger.debug(f"Headers: {headers}") # Log headers
                if params:
                    logger.debug(f"Params: {params}") # Log params if any
                if data:
                    logger.debug(f"Data: {data}") # Log data if any

                if method == "GET":
                    response = requests.get(url, headers=headers, params=params, timeout=self.timeout)
                elif method == "POST":
                    response = requests.post(url, headers=headers, data=data, params=params, timeout=self.timeout)
                else:
                    raise ValueError("Unsupported HTTP method")

                logger.debug(f"Spotify API Response: Status Code - {response.status_code}") # Log response status code
                logger.debug(f"Response Content: {response.content}") # Log response content

                response.raise_as_error()  # Raise HTTPError for bad responses (4xx or 5xx)
                return response

            except requests.exceptions.RequestException as e:
                self.logger.error(f"Spotify API Request failed: {e}")
                if attempt < retries - 1:  # Retry if attempts remain
                    time.sleep(backoff_factor * (2 ** attempt))  # Exponential backoff
        return None # Return None if all retries fail