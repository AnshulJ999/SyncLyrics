"""
Spotify API Integration
Handles authentication and data retrieval from Spotify Web API
"""
import sys
from pathlib import Path
import time
import asyncio

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

import spotipy
from spotipy.oauth2 import SpotifyOAuth
from typing import Optional, Dict, Any
import os
from dotenv import load_dotenv
import logging
import requests
from requests.exceptions import ReadTimeout
from logging_config import get_logger
from config import SPOTIFY

# Load environment variables
load_dotenv()

logger = get_logger(__name__)

class SpotifyAPI:
    def __init__(self):
        """Initialize Spotify API with credentials from environment variables and settings"""
        self.max_retries = 3
        self.timeout = 5  # seconds
        self.retry_delay = 1  # seconds
        self.initialized = False
        
        self._last_metadata_check = time.time() #  Initialize with current time
        self._metadata_cache = None
        self._cache_enabled = SPOTIFY["cache"]["enabled"]
        self.metadata_cache_time = SPOTIFY["cache"]["metadata_ttl"]
        
        # Request tracking
        self.request_stats = {
            'total_requests': 0,
            'cached_responses': 0,
            'api_calls': {
                'current_playback': 0,
                'search': 0,
                'other': 0
            },
            'errors': {
                'timeout': 0,
                'rate_limit': 0,
                'other': 0
            }
        }
        
        try:
            # Initialize Spotify client
            if not all([SPOTIFY["client_id"], SPOTIFY["client_secret"], SPOTIFY["redirect_uri"]]):
                logger.error("Missing Spotify credentials in config")
                return
                
            self.sp = spotipy.Spotify(
                auth_manager=SpotifyOAuth(
                    client_id=SPOTIFY["client_id"],
                    client_secret=SPOTIFY["client_secret"],
                    redirect_uri=SPOTIFY["redirect_uri"],
                    scope=SPOTIFY["scope"]
                ),
                requests_timeout=self.timeout,
                retries=self.max_retries
            )
            if self._test_connection():
                self.initialized = True
                logger.info("Spotify API initialized successfully")
            else:
                self.initialized = False
                logger.error("Failed to connect to Spotify API")
        except Exception as e:
            logger.error(f"Failed to initialize Spotify API: {e}")
            self.initialized = False

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

    async def get_current_track(self) -> Optional[Dict[str, Any]]:
        """Get current track with playback state"""
        if not self.initialized:
            logger.warning("Spotify API not initialized, skipping track fetch")
            return None
            
        # Check if we are in a backoff period
        if hasattr(self, '_backoff_until') and time.time() < self._backoff_until:
            logger.debug(f"In backoff period. Skipping request. Resuming in {self._backoff_until - time.time():.1f}s")
            return self._metadata_cache

        try:
            # Track API call
            self.request_stats['total_requests'] += 1
            self.request_stats['api_calls']['current_playback'] += 1
            
            # Check cache first
            current_time = time.time()
            if (self._cache_enabled and 
                self._metadata_cache and 
                current_time - self._last_metadata_check < self.metadata_cache_time):
                self.request_stats['cached_responses'] += 1
                logger.debug("Using cached metadata")
                return self._metadata_cache
            
            # Get current playback state
            loop = asyncio.get_event_loop()
            current = await loop.run_in_executor(None, self.sp.current_playback)
            
            # Handle rate limits and errors (Spotipy usually raises exceptions, but checking status just in case)
            if hasattr(current, 'status_code'):
                if current.status_code == 429:
                    self.request_stats['errors']['rate_limit'] += 1
                    retry_after = int(current.headers.get('Retry-After', 5))
                    self._backoff_until = time.time() + retry_after
                    logger.warning(f"Rate limit hit. Backing off for {retry_after}s")
                    return self._metadata_cache
                elif current.status_code != 200:
                    self.request_stats['errors']['other'] += 1
                    logger.error(f"Spotify API error: Status {current.status_code}")
                    return self._metadata_cache
                    
            # Process response
            if not current or not current.get('item'):
                logger.debug("No track currently playing")
                return None
                
            # Check if actually playing
            is_playing = current.get('is_playing', False)
            logger.debug(f"Playback state: {'Playing' if is_playing else 'Paused'}")
            
            # Update cache
            self._metadata_cache = {
                'title': current['item']['name'],
                'artist': current['item']['artists'][0]['name'],
                'album': current['item']['album']['name'],
                'album_art': current['item']['album']['images'][0]['url'] if current['item']['album']['images'] else None,
                'track_id': current['item']['id'],
                'url': current['item']['external_urls']['spotify'],
                'duration_ms': current['item']['duration_ms'],
                'progress_ms': current['progress_ms'],
                'is_playing': is_playing
            }
            self._last_metadata_check = current_time
            
            return self._metadata_cache
            
        except spotipy.exceptions.SpotifyException as e:
            if e.http_status == 429:
                self.request_stats['errors']['rate_limit'] += 1
                retry_after = int(e.headers.get('Retry-After', 30))
                self._backoff_until = time.time() + retry_after
                logger.warning(f"Rate limit hit (Exception). Backing off for {retry_after}s")
            else:
                self.request_stats['errors']['other'] += 1
                logger.error(f"Spotify API Exception: {e}")
            return self._metadata_cache
            
        except ReadTimeout:
            self.request_stats['errors']['timeout'] += 1
            logger.warning(f"Timeout error. Total timeouts: {self.request_stats['errors']['timeout']}")
            return self._metadata_cache
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"API error: {e}")
            return self._metadata_cache

    def search_track(self, artist: str, title: str) -> Optional[Dict[str, Any]]:
        """Search for a track on Spotify and return its details"""
        if not self.initialized:
            logger.warning("Spotify API not initialized, skipping track search")
            return None
            
        try:
            # Track API call
            self.request_stats['total_requests'] += 1
            self.request_stats['api_calls']['search'] += 1
            
            # Clean up search terms
            search_query = f"track:{title} artist:{artist}"
            results = self.sp.search(q=search_query, type='track', limit=1)
            
            if not results['tracks']['items']:
                logger.info(f"No tracks found for: {artist} - {title}")
                return None
                
            track = results['tracks']['items'][0]
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
            self.request_stats['errors']['timeout'] += 1
            logger.error("Search request timed out")
            return None
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"Error searching track: {e}")
            return None

    def get_request_stats(self) -> Dict[str, Any]:
        """Get current API request statistics"""
        total_requests = self.request_stats['total_requests']
        cached_responses = self.request_stats['cached_responses']
        
        return {
            'Total Requests': total_requests,
            'Cached Responses': cached_responses,
            'API Calls': self.request_stats['api_calls'],
            'Errors': self.request_stats['errors'],
            'Cache Age': f"{time.time() - self._last_metadata_check:.1f}s",
            'Cache Hit Rate': f"{(cached_responses / max(total_requests, 1)) * 100:.1f}%"
        }