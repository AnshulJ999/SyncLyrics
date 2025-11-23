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
        
        self._last_metadata_check = 0
        self._metadata_cache = None
        self._cache_enabled = SPOTIFY["cache"]["enabled"]
        
        # Smart caching settings
        self.active_ttl = 6.0   # Default: Poll every 6s when playing (interpolate in between)
        self.active_ttl_normal = 6.0   # Normal mode (when Windows Media is active)
        self.active_ttl_fast = 2.0     # Fast mode (Spotify-only mode, reduced latency)
        self.idle_ttl = 6.0     # Poll every 6s when paused (Safe: max ~17k req/day)
        self.backoff_ttl = 30.0 # Circuit breaker timeout
        
        # Backoff state
        self._backoff_until = 0
        self._consecutive_errors = 0
        self._last_valid_response_time = time.time()
        self._last_force_refresh_failure_time = 0
        
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
            
            # Determine cache path for token persistence
            # Environment variable SPOTIPY_CACHE_PATH can be set to a persistent location
            # (e.g., /config/.spotify_cache in Home Assistant add-ons)
            cache_path = os.getenv("SPOTIPY_CACHE_PATH")
            if cache_path:
                # Ensure the cache directory exists
                cache_dir = Path(cache_path).parent
                cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Using persistent Spotify cache: {cache_path}")
            else:
                cache_path = None  # Use default (.cache in working directory)
                logger.warning("No SPOTIPY_CACHE_PATH set - tokens may not persist across restarts")
            
            # Store auth_manager as instance variable so we can use it for web-based auth flow
            self.auth_manager = SpotifyOAuth(
                client_id=SPOTIFY["client_id"],
                client_secret=SPOTIFY["client_secret"],
                redirect_uri=SPOTIFY["redirect_uri"],
                scope=SPOTIFY["scope"],
                cache_path=cache_path,
                open_browser=False  # Critical: Don't try to open browser in headless environment
            )
                
            self.sp = spotipy.Spotify(
                auth_manager=self.auth_manager,
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

    def set_fast_mode(self, enabled: bool = True):
        """
        Enable/disable fast polling mode for Spotify-only scenarios.
        Fast mode reduces active_ttl from 6.0s to 2.0s for lower latency.
        """
        if enabled:
            self.active_ttl = self.active_ttl_fast
            logger.debug("Spotify API: Fast mode enabled (active_ttl=2.0s)")
        else:
            self.active_ttl = self.active_ttl_normal
            logger.debug("Spotify API: Normal mode (active_ttl=6.0s)")

    def is_spotify_healthy(self) -> bool:
        """Quick health check for Spotify API"""
        try:
            # Check if we are in backoff
            if time.time() < self._backoff_until:
                return False
                
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

    def _calculate_progress(self, cached_data: Dict[str, Any]) -> Dict[str, Any]:
        """Interpolate progress_ms based on elapsed time since cache"""
        if not cached_data or not cached_data.get('is_playing'):
            return cached_data
            
        elapsed = (time.time() - self._last_metadata_check) * 1000
        new_progress = cached_data['progress_ms'] + elapsed
        
        # Don't exceed duration
        if cached_data.get('duration_ms') and new_progress > cached_data['duration_ms']:
            new_progress = cached_data['duration_ms']
            
        # Create a copy to avoid mutating the cache directly
        interpolated = cached_data.copy()
        interpolated['progress_ms'] = int(new_progress)
        return interpolated

    def _handle_error(self, error: Exception, status_code: Optional[int] = None):
        """Handle API errors with exponential backoff"""
        self._consecutive_errors += 1
        
        # Determine backoff time
        if status_code == 429:
            self.request_stats['errors']['rate_limit'] += 1
            retry_after = 30 # Default if header missing
            if hasattr(error, 'headers'):
                retry_after = int(error.headers.get('Retry-After', 30))
            backoff_time = retry_after
            logger.warning(f"Rate limit hit. Backing off for {backoff_time}s")
        else:
            self.request_stats['errors']['other'] += 1
            # Exponential backoff: 5s, 10s, 20s, 40s... capped at 60s
            backoff_time = min(5 * (2 ** (self._consecutive_errors - 1)), 60)
            logger.warning(f"API Error ({error}). Backing off for {backoff_time}s (Error #{self._consecutive_errors})")
            
        self._backoff_until = time.time() + backoff_time

    async def get_current_track(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """Get current track with playback state, smart caching, and interpolation"""
        if not self.initialized:
            logger.warning("Spotify API not initialized, skipping track fetch")
            return None
            
        current_time = time.time()

        # 1. Circuit Breaker / Backoff Check
        if current_time < self._backoff_until:
            # If we've been failing for too long (> 30s), invalidate cache to stop "Playing" state
            if current_time - self._last_valid_response_time > self.backoff_ttl:
                if self._metadata_cache:
                    logger.warning("Circuit breaker: Invalidating stale cache due to extended API failure")
                    self._metadata_cache = None
                return None
                
            logger.debug(f"In backoff period. Skipping request. Resuming in {self._backoff_until - current_time:.1f}s")
            return self._calculate_progress(self._metadata_cache)

        try:
            # 2. Smart Cache Check
            # Determine required TTL based on state
            is_playing = self._metadata_cache.get('is_playing', False) if self._metadata_cache else False
            
            # Force Refresh Logic with Backoff
            # If external source (Windows) says we are playing, but cache says paused, force fetch
            # BUT only if we haven't tried forcing recently and failed (to prevent local file loops)
            should_force = False
            if force_refresh and not is_playing:
                last_force_fail = getattr(self, '_last_force_refresh_failure_time', 0)
                if current_time - last_force_fail > self.idle_ttl:
                    should_force = True
            
            if should_force:
                required_ttl = 0.5 # Force fetch (allow small buffer)
            else:
                required_ttl = self.active_ttl if is_playing else self.idle_ttl
            
            if (self._cache_enabled and 
                self._metadata_cache and 
                current_time - self._last_metadata_check < required_ttl):
                
                self.request_stats['cached_responses'] += 1
                return self._calculate_progress(self._metadata_cache)
            
            # 3. API Call
            self.request_stats['total_requests'] += 1
            self.request_stats['api_calls']['current_playback'] += 1
            
            loop = asyncio.get_event_loop()
            current = await loop.run_in_executor(None, self.sp.current_playback)
            
            # 4. Success Handling
            self._consecutive_errors = 0
            self._backoff_until = 0
            self._last_valid_response_time = current_time
            
            # Process response
            if not current or not current.get('item'):
                logger.debug("No track currently playing")
                self._metadata_cache = None # Clear cache if nothing playing
                self._last_metadata_check = current_time
                
                # If we forced a refresh but got nothing, mark it as a failure to backoff
                if should_force:
                    self._last_force_refresh_failure_time = current_time
                    
                return None
                
            is_playing = current.get('is_playing', False)
            
            # If we forced a refresh but got Paused, mark it as a failure to backoff
            if should_force and not is_playing:
                self._last_force_refresh_failure_time = current_time
            
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
            self._handle_error(e, e.http_status)
            return self._calculate_progress(self._metadata_cache)
            
        except ReadTimeout as e:
            self.request_stats['errors']['timeout'] += 1
            self._handle_error(e)
            return self._calculate_progress(self._metadata_cache)
            
        except Exception as e:
            self._handle_error(e)
            return self._calculate_progress(self._metadata_cache)

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

    # Playback Control Methods
    
    async def pause_playback(self) -> bool:
        """Pause current playback"""
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            logger.info("Pausing playback")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.pause_playback)
            return True
        except Exception as e:
            logger.error(f"Failed to pause playback: {e}")
            return False
    
    async def resume_playback(self) -> bool:
        """Resume current playback"""
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            logger.info("Resuming playback")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.start_playback)
            return True
        except Exception as e:
            logger.error(f"Failed to resume playback: {e}")
            return False
    
    async def next_track(self) -> bool:
        """Skip to next track"""
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            logger.info("Skipping to next track")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.next_track)
            return True
        except Exception as e:
            logger.error(f"Failed to skip to next track: {e}")
            return False
    
    async def previous_track(self) -> bool:
        """Go to previous track"""
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            logger.info("Going to previous track")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.previous_track)
            return True
        except Exception as e:
            logger.error(f"Failed to go to previous track: {e}")
            return False
    
    def get_auth_url(self) -> Optional[str]:
        """
        Generate the Spotify authorization URL for web-based OAuth flow.
        Returns the URL that users should visit to authorize the application.
        """
        if not hasattr(self, 'auth_manager') or not self.auth_manager:
            logger.error("Auth manager not initialized")
            return None
        
        try:
            # Get the authorization URL from the auth manager
            auth_url = self.auth_manager.get_authorize_url()
            logger.info("Generated Spotify authorization URL")
            return auth_url
        except Exception as e:
            logger.error(f"Failed to generate auth URL: {e}")
            return None
    
    async def complete_auth(self, code: str) -> bool:
        """
        Complete the OAuth flow by exchanging the authorization code for access tokens.
        This is called from the /callback route after the user authorizes the app.
        
        Args:
            code: The authorization code from Spotify's callback
            
        Returns:
            True if authentication was successful, False otherwise
        """
        if not hasattr(self, 'auth_manager') or not self.auth_manager:
            logger.error("Auth manager not initialized")
            return False
        
        try:
            logger.info("Completing Spotify authentication...")
            
            # Exchange the code for tokens (this is a blocking operation, so run in executor)
            loop = asyncio.get_event_loop()
            token_info = await loop.run_in_executor(
                None, 
                lambda: self.auth_manager.get_access_token(code)
            )
            
            if not token_info:
                logger.error("Failed to get access token from Spotify")
                return False
            
            # Re-initialize the Spotify client with the new auth manager (which now has tokens)
            self.sp = spotipy.Spotify(
                auth_manager=self.auth_manager,
                requests_timeout=self.timeout,
                retries=self.max_retries
            )
            
            # Test the connection to verify authentication worked
            self.initialized = self._test_connection()
            
            if self.initialized:
                logger.info("Spotify authentication completed successfully")
            else:
                logger.error("Authentication succeeded but connection test failed")
            
            return self.initialized
            
        except Exception as e:
            logger.error(f"Failed to complete authentication: {e}")
            self.initialized = False
            return False