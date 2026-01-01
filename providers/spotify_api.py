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
import re
import threading
from dotenv import load_dotenv
import logging
import requests
from requests.exceptions import ReadTimeout
from logging_config import get_logger
from config import SPOTIFY, ALBUM_ART

# Load environment variables
# load_dotenv() # Environment variables are already loaded by config.py

logger = get_logger(__name__)


# ===========================================
# Counting Session for Accurate API Tracking
# ===========================================
# Custom requests.Session that counts ALL HTTP requests made by spotipy,
# including internal retries, token refreshes, and other hidden calls.
# This gives accurate stats that match Spotify Developer Dashboard.

class CountingSession(requests.Session):
    """A requests.Session subclass that counts all HTTP requests (thread-safe)."""
    
    def __init__(self, stats_dict: dict):
        super().__init__()
        self._stats = stats_dict
        self._lock = threading.Lock()  # Thread-safe counter
    
    def request(self, method, url, **kwargs):
        """Override request() to count every HTTP call."""
        with self._lock:
            self._stats['total_requests'] += 1
        return super().request(method, url, **kwargs)

# ===========================================
# Spotify Image URL Enhancement (Shared Utility)
# ===========================================
# Module-level cache for URL verification results
# Key: enhanced_url, Value: True if valid, False if invalid, None if not checked yet
_spotify_url_verification_cache = {}
_MAX_CACHE_SIZE = 500  # Limit cache size to prevent memory leaks
_cache_lock = threading.Lock()  # Thread safety lock for cache operations

# Throttle logging to prevent spam - track URLs we've already logged about
# Key: enhanced_url, Value: timestamp of last log
_enhancement_log_throttle = {}
_MAX_LOG_THROTTLE_SIZE = 200  # Limit throttle cache size
_LOG_THROTTLE_SECONDS = 300  # Only log once per URL every 5 minutes

async def enhance_spotify_image_url_async(url: str) -> str:
    """
    Async function to enhance Spotify image URL from 640px to 1400px using quality code replacement.
    Falls back to original URL if enhancement fails (404 or error).
    
    Uses caching to avoid repeated HEAD requests for the same URLs.
    Network verification runs in thread executor to avoid blocking the event loop.
    
    Based on community discovery: https://gist.github.com/soulsoiledit/8c258233419a299f093b083eb4f427ca
    Spotify image URLs contain quality codes that can be replaced to get higher resolution.
    Quality code '82c1' = 1400x1400 JPEG, 'b273' = 640x640 JPEG (default).
    
    Args:
        url: Original Spotify image URL (typically 640px from API)
        
    Returns:
        Enhanced URL (1400px) if available and verified, original URL if not
    """
    # Respect the global configuration setting
    if not ALBUM_ART.get("enable_spotify_enhanced", True):
        return url
    
    if not url or 'i.scdn.co' not in url:
        return url
    
    try:
        # Pattern matches: ab67616[1d] + exactly 8 hex chars (0000 + 4-char quality code) + rest of hash
        # URL format: ab67616d0000{quality_code}{image_hash} (album art)
        #            ab6761610000{quality_code}{image_hash} (artist images)
        # Example album art: https://i.scdn.co/image/ab67616d0000b273ff9ca10b55ce82ae553c8228
        # Example artist:   https://i.scdn.co/image/ab6761610000e5eb4104fbd80f1f795728abbd59
        #          ab67616d = album art prefix, ab676161 = artist image prefix
        #          0000 = padding, b273/e5eb = quality code (640px), rest = image hash
        # Quality codes from Gist: b273/d452/e5eb (640px), 82c1 (1400px), f848/1e02 (300px), etc.
        # Note: Wide covers use ab6742d30000 prefix (53b7 = 1280x720) but we only handle standard square covers
        pattern = r'(ab67616[1d])([0-9a-f]{8})([0-9a-f]+)'
        match = re.search(pattern, url)
        
        if not match:
            return url  # Pattern doesn't match, return original
        
        # Replace 8-char quality code (0000 + 4-char code) with 1400px version (000082c1)
        # 000082c1 = 0000 (padding) + 82c1 (1400x1400 JPEG quality code)
        enhanced_url = url.replace(match.group(2), '000082c1')
        
        # Thread-safe cache check (instant return, no network request)
        with _cache_lock:
            if enhanced_url in _spotify_url_verification_cache:
                cache_result = _spotify_url_verification_cache[enhanced_url]
                if cache_result is True:
                    logger.debug(f"Spotify image enhanced to 1400px (cached): {url[:50]}...")
                    return enhanced_url
                elif cache_result is False:
                    logger.debug(f"Spotify 1400px not available (cached), using 640px")
                    return url
        
        # Not in cache - verify with HEAD request (runs in thread executor to avoid blocking)
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.head(enhanced_url, timeout=2, allow_redirects=True)
            )
            
            # Verify: status must be 200 AND final URL should still contain the enhanced quality code
            # (Spotify might redirect 404s to error pages, so we check the final URL)
            is_valid = response.status_code == 200
            if is_valid:
                # Check if final URL after redirects still contains the enhanced quality code
                final_url = response.url if hasattr(response, 'url') else enhanced_url
                if '000082c1' not in final_url and '82c1' not in final_url:
                    # Redirected to a different URL (likely 404 page or lower quality)
                    is_valid = False
                    # Log this at DEBUG level (expected behavior for some images)
                    logger.debug(f"Spotify 1400px URL redirected (likely 404): {enhanced_url[:50]}... -> {final_url[:50]}...")
            with _cache_lock:
                _spotify_url_verification_cache[enhanced_url] = is_valid
                
                # Simple cache cleanup: remove oldest entries if cache is too large
                if len(_spotify_url_verification_cache) > _MAX_CACHE_SIZE:
                    # Remove first (oldest) entry - safe inside lock
                    oldest_key = next(iter(_spotify_url_verification_cache))
                    _spotify_url_verification_cache.pop(oldest_key)
            
            # Throttled logging - only log important events (first-time success/failure)
            current_time = time.time()
            should_log = False
            with _cache_lock:
                last_log_time = _enhancement_log_throttle.get(enhanced_url, 0)
                if current_time - last_log_time > _LOG_THROTTLE_SECONDS:
                    should_log = True
                    _enhancement_log_throttle[enhanced_url] = current_time
                    
                    # Cleanup throttle cache if too large
                    if len(_enhancement_log_throttle) > _MAX_LOG_THROTTLE_SIZE:
                        oldest_key = next(iter(_enhancement_log_throttle))
                        _enhancement_log_throttle.pop(oldest_key)
            
            if is_valid:
                # First-time success is important - log at INFO level
                # Log full enhanced URL so we can verify the quality code changed (0000b273 -> 000082c1) and click to view
                if should_log:
                    logger.info(f"Spotify image enhanced to 1400px: {enhanced_url}")
                return enhanced_url
            else:
                # First-time failure (404) is expected behavior - keep at DEBUG to avoid spam
                if should_log:
                    logger.debug(f"Spotify 1400px not available (status {response.status_code}), using 640px")
        except Exception as e:
            # Cache the failure to avoid repeated attempts (thread-safe)
            with _cache_lock:
                _spotify_url_verification_cache[enhanced_url] = False
            # Network errors are unexpected - log at INFO level (but throttled)
            current_time = time.time()
            should_log = False
            with _cache_lock:
                last_log_time = _enhancement_log_throttle.get(enhanced_url, 0)
                if current_time - last_log_time > _LOG_THROTTLE_SECONDS:
                    should_log = True
                    _enhancement_log_throttle[enhanced_url] = current_time
                    
                    if len(_enhancement_log_throttle) > _MAX_LOG_THROTTLE_SIZE:
                        oldest_key = next(iter(_enhancement_log_throttle))
                        _enhancement_log_throttle.pop(oldest_key)
            
            if should_log:
                logger.info(f"Spotify enhancement check failed: {e}, using 640px")
        
        return url  # Fallback to original URL
    except Exception as e:
        logger.debug(f"Spotify URL enhancement error: {e}, using original")
        return url

def enhance_spotify_image_url_sync(url: str) -> str:
    """
    Synchronous wrapper for enhance_spotify_image_url_async.
    Used when called from thread executors (e.g., album_art.py).
    
    This function uses the same cache as the async version, so results are shared.
    Network verification is synchronous (acceptable since it runs in a thread).
    
    Args:
        url: Original Spotify image URL (typically 640px from API)
        
    Returns:
        Enhanced URL (1400px) if available and verified, original URL if not
    """
    # Respect the global configuration setting
    if not ALBUM_ART.get("enable_spotify_enhanced", True):
        return url
    
    if not url or 'i.scdn.co' not in url:
        return url
    
    try:
        # Pattern matches: ab67616[1d] + exactly 8 hex chars (0000 + 4-char quality code) + rest of hash
        # URL format: ab67616d0000{quality_code}{image_hash} (album art)
        #            ab6761610000{quality_code}{image_hash} (artist images)
        # Example album art: https://i.scdn.co/image/ab67616d0000b273ff9ca10b55ce82ae553c8228
        # Example artist:   https://i.scdn.co/image/ab6761610000e5eb4104fbd80f1f795728abbd59
        #          ab67616d = album art prefix, ab676161 = artist image prefix
        #          0000 = padding, b273/e5eb = quality code (640px), rest = image hash
        # Quality codes from Gist: b273/d452/e5eb (640px), 82c1 (1400px), f848/1e02 (300px), etc.
        # Note: Wide covers use ab6742d30000 prefix (53b7 = 1280x720) but we only handle standard square covers
        pattern = r'(ab67616[1d])([0-9a-f]{8})([0-9a-f]+)'
        match = re.search(pattern, url)
        
        if not match:
            return url  # Pattern doesn't match, return original
        
        # Replace 8-char quality code (0000 + 4-char code) with 1400px version (000082c1)
        # 000082c1 = 0000 (padding) + 82c1 (1400x1400 JPEG quality code)
        enhanced_url = url.replace(match.group(2), '000082c1')
        
        # Thread-safe cache check (instant return, no network request)
        with _cache_lock:
            if enhanced_url in _spotify_url_verification_cache:
                cache_result = _spotify_url_verification_cache[enhanced_url]
                if cache_result is True:
                    # Cached hits are frequent - keep at DEBUG to avoid spam
                    logger.debug(f"Spotify image enhanced to 1400px (cached): {url[:50]}...")
                    return enhanced_url
                elif cache_result is False:
                    # Cached failures are frequent - keep at DEBUG to avoid spam
                    logger.debug(f"Spotify 1400px not available (cached), using 640px")
                    return url
        
        # Not in cache - verify with HEAD request (synchronous, but runs in thread executor)
        try:
            response = requests.head(enhanced_url, timeout=2, allow_redirects=True)
            
            # Verify: status must be 200 AND final URL should still contain the enhanced quality code
            # (Spotify might redirect 404s to error pages, so we check the final URL)
            is_valid = response.status_code == 200
            if is_valid:
                # Check if final URL after redirects still contains the enhanced quality code
                final_url = response.url if hasattr(response, 'url') else enhanced_url
                if '000082c1' not in final_url and '82c1' not in final_url:
                    # Redirected to a different URL (likely 404 page or lower quality)
                    is_valid = False
                    # Log this at DEBUG level (expected behavior for some images)
                    logger.debug(f"Spotify 1400px URL redirected (likely 404): {enhanced_url[:50]}... -> {final_url[:50]}...")
            with _cache_lock:
                _spotify_url_verification_cache[enhanced_url] = is_valid
                
                # Simple cache cleanup: remove oldest entries if cache is too large
                if len(_spotify_url_verification_cache) > _MAX_CACHE_SIZE:
                    # Remove first (oldest) entry - safe inside lock
                    oldest_key = next(iter(_spotify_url_verification_cache))
                    _spotify_url_verification_cache.pop(oldest_key)
            
            # Throttled logging - only log important events (first-time success/failure)
            current_time = time.time()
            should_log = False
            with _cache_lock:
                last_log_time = _enhancement_log_throttle.get(enhanced_url, 0)
                if current_time - last_log_time > _LOG_THROTTLE_SECONDS:
                    should_log = True
                    _enhancement_log_throttle[enhanced_url] = current_time
                    
                    # Cleanup throttle cache if too large
                    if len(_enhancement_log_throttle) > _MAX_LOG_THROTTLE_SIZE:
                        oldest_key = next(iter(_enhancement_log_throttle))
                        _enhancement_log_throttle.pop(oldest_key)
            
            if is_valid:
                # First-time success is important - log at INFO level
                # Log full enhanced URL so we can verify the quality code changed (0000b273 -> 000082c1) and click to view
                if should_log:
                    logger.info(f"Spotify image enhanced to 1400px: {enhanced_url}")
                return enhanced_url
            else:
                # First-time failure (404) is expected behavior - keep at DEBUG to avoid spam
                if should_log:
                    logger.debug(f"Spotify 1400px not available (status {response.status_code}), using 640px")
        except Exception as e:
            # Cache the failure to avoid repeated attempts (thread-safe)
            with _cache_lock:
                _spotify_url_verification_cache[enhanced_url] = False
            # Network errors are unexpected - log at INFO level (but throttled)
            current_time = time.time()
            should_log = False
            with _cache_lock:
                last_log_time = _enhancement_log_throttle.get(enhanced_url, 0)
                if current_time - last_log_time > _LOG_THROTTLE_SECONDS:
                    should_log = True
                    _enhancement_log_throttle[enhanced_url] = current_time
                    
                    if len(_enhancement_log_throttle) > _MAX_LOG_THROTTLE_SIZE:
                        oldest_key = next(iter(_enhancement_log_throttle))
                        _enhancement_log_throttle.pop(oldest_key)
            
            if should_log:
                logger.info(f"Spotify enhancement check failed: {e}, using 640px")
        
        return url  # Fallback to original URL
    except Exception as e:
        logger.debug(f"Spotify URL enhancement error: {e}, using original")
        return url

# ===========================================
# Singleton Pattern for Shared SpotifyAPI
# ===========================================
# This ensures only ONE SpotifyAPI instance exists across the entire app.
# All modules (system_utils, spotify_lyrics, etc.) share the same instance,
# so statistics are consolidated and caching is efficient.

_spotify_client_instance: Optional['SpotifyAPI'] = None

def get_shared_spotify_client() -> Optional['SpotifyAPI']:
    """
    Returns the shared SpotifyAPI singleton instance.
    Creates the instance on first call (lazy initialization).
    
    This ensures:
    - All API calls are tracked in one place (accurate statistics)
    - Single cache (more efficient, avoids duplicate requests)
    - Single auth flow (no confusion with multiple tokens)
    
    Returns:
        SpotifyAPI instance, or None if initialization fails
    """
    global _spotify_client_instance
    if _spotify_client_instance is None:
        _spotify_client_instance = SpotifyAPI()
    return _spotify_client_instance

def reset_shared_spotify_client() -> None:
    """
    Resets the shared SpotifyAPI instance.
    Used for testing or when re-authentication is needed.
    """
    global _spotify_client_instance
    _spotify_client_instance = None

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
        self._last_track_id = None  # Track the current track ID to detect track changes
        
        # Smart caching settings - now configurable via config.py/environment variables
        # These can be set via SPOTIFY_POLLING_FAST_INTERVAL and SPOTIFY_POLLING_SLOW_INTERVAL
        polling_config = SPOTIFY.get("polling", {})
        self.active_ttl_fast = float(polling_config.get("fast_interval", 2.0))   # Fast mode (Spotify-only)
        self.active_ttl_normal = float(polling_config.get("slow_interval", 6.0)) # Normal mode (Windows Media hybrid)
        self.idle_ttl = float(polling_config.get("slow_interval", 6.0))          # Poll rate when paused
        self.active_ttl = self.active_ttl_normal  # Default: Start in normal mode
        self.backoff_ttl = 30.0  # Circuit breaker timeout (not user-configurable)

        
        # Backoff state
        self._backoff_until = 0
        self._consecutive_errors = 0
        self._last_valid_response_time = time.time()
        self._last_force_refresh_failure_time = 0
        
        # Artist Image Cache
        # Key: artist_id, Value: list of image URLs
        self._artist_image_cache = {}
        
        # Queue Cache (prevents rate limiting from frequent frontend polling)
        self._queue_cache = None
        self._queue_cache_time = 0
        self._QUEUE_CACHE_TTL = 10  # seconds - balance between freshness and rate limits
        
        # FIX: Throttle for credential/auth errors to prevent log spam
        self._credentials_error_logged = False
        
        # Request tracking
        # Tracks ALL Spotify API calls for rate limit monitoring
        # Spotify's rate limit is typically ~180 requests/minute for most endpoints
        self.request_stats = {
            'total_requests': 0,  # Total API calls made to Spotify (the key metric for rate limits)
            'total_function_calls': 0,  # Total calls to get_current_track() (includes cache hits)
            'cached_responses': 0,  # Number of times cache was used (avoided API calls)
            'api_calls': {
                'current_playback': 0,  # /me/player calls
                'current_user': 0,  # /me calls (auth test)
                'search': 0,  # /search calls
                'playback_control': 0,  # pause/resume/next/previous calls
                'other': 0
            },
            'errors': {
                'timeout': 0,
                'rate_limit': 0,
                'auth': 0,  # Authentication errors
                'other': 0
            }
        }
        
        try:
            # Initialize Spotify client
            if not all([SPOTIFY["client_id"], SPOTIFY["client_secret"], SPOTIFY["redirect_uri"]]):
                if not self._credentials_error_logged:
                    logger.warning("Missing Spotify credentials - Spotify features disabled (set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env)")
                    self._credentials_error_logged = True
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
                requests_session=CountingSession(self.request_stats),  # Count ALL HTTP requests
                requests_timeout=self.timeout,
                retries=self.max_retries
            )
            
            # CRITICAL FIX: Only test connection if we have cached tokens
            # If no tokens exist, _test_connection() will trigger spotipy's interactive prompt
            # which fails in headless environments with "EOF when reading a line"
            # Instead, we mark as not initialized and let the web-based OAuth flow handle it
            cached_token = self.auth_manager.get_cached_token()
            if cached_token:
                # OPTIMIZATION: Don't test connection synchronously in __init__
                # This blocks the event loop for seconds/minutes if network is slow
                # Assume initialized if tokens exist, let the first async request handle auth errors
                self.initialized = True
                logger.info("Spotify API initialized with cached tokens (connection verification deferred)")
            else:
                # No cached tokens - don't test connection, wait for web auth
                self.initialized = False
                logger.info("No cached Spotify tokens - web authentication required")
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
        """
        Quick health check for Spotify API.
        Returns True if API is responding, False if in backoff or API is down.
        Note: This makes a real API call so use sparingly.
        """
        try:
            # Check if we are in backoff period
            if time.time() < self._backoff_until:
                return False
            
            if not self.initialized:
                return False
                
            # Track this API call (endpoint-specific only, total is counted by CountingSession)
            self.request_stats['api_calls']['current_user'] += 1
            
            # Use spotipy to make the API call (handles auth automatically)
            self.sp.current_user()
            logger.debug("Spotify health check successful")
            return True
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.debug(f"Spotify health check failed: {e}")
            return False

    def _test_connection(self) -> bool:
        """Test API connection with retries. Tracks API calls for statistics."""
        for attempt in range(self.max_retries):
            try:
                # Track this API call (endpoint-specific only, total is counted by CountingSession)
                self.request_stats['api_calls']['current_user'] += 1
                
                self.sp.current_user()  # Simple API call to test connection
                logger.info("Successfully connected to Spotify API")
                return True
            except Exception as e:
                self.request_stats['errors']['auth'] += 1
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                time.sleep(self.retry_delay)
        return False

    def _calculate_progress(self, cached_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Interpolate progress_ms based on elapsed time since cache.
        
        CRITICAL FIX: Validates track_id before interpolating to prevent returning
        stale data from a previous track when the track changed but cache is still valid.
        """
        if not cached_data or not cached_data.get('is_playing'):
            return cached_data
        
        # CRITICAL FIX: Don't interpolate if track ID changed
        # This prevents returning wrong song data during the transition period
        # If _last_track_id is set and doesn't match cached track_id, this is stale data
        current_track_id = cached_data.get('track_id')
        if self._last_track_id is not None and current_track_id != self._last_track_id:
            logger.debug(f"Track ID mismatch in cache ({self._last_track_id} vs {current_track_id}), returning None to prevent stale data")
            return None
            
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

    def _enhance_spotify_image_url(self, url: str) -> str:
        """
        Try to enhance Spotify image URL from 640px to 1400px using quality code replacement.
        Falls back to original URL if enhancement fails (404 or error).
                
        Note: This is a synchronous wrapper. For async contexts, use enhance_spotify_image_url_async directly.
        For thread executor contexts, use enhance_spotify_image_url_sync.
        Instance method wrapper for enhance_spotify_image_url_async.
        Maintains backward compatibility for any code that calls this as an instance method.

        Based on community discovery: https://gist.github.com/soulsoiledit/8c258233419a299f093b083eb4f427ca
        Spotify image URLs contain quality codes that can be replaced to get higher resolution.
        Quality code '82c1' = 1400x1400 JPEG, 'b273' = 640x640 JPEG (default).
        
        Args:
            url: Original Spotify image URL (typically 640px from API)
            
        Returns:
            Enhanced URL (1400px) if available and verified, original URL if not
        """
        # Use the sync version since this is a sync method
        return enhance_spotify_image_url_sync(url)

    async def get_current_track(self, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """Get current track with playback state, smart caching, and interpolation"""
        if not self.initialized:
            logger.warning("Spotify API not initialized, skipping track fetch")
            return None
        
        # Track total function calls for statistics
        self.request_stats['total_function_calls'] += 1
            
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
                current_time - self._last_metadata_check < required_ttl):
                
                self.request_stats['cached_responses'] += 1
                return self._calculate_progress(self._metadata_cache)
            
            # 3. API Call (endpoint-specific tracking, total is counted by CountingSession)
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
                self._last_track_id = None  # Clear track ID when no track is playing
                self._last_metadata_check = current_time
                
                # If we forced a refresh but got nothing, mark it as a failure to backoff
                if should_force:
                    self._last_force_refresh_failure_time = current_time
                    
                return None
                
            is_playing = current.get('is_playing', False)
            
            # If we forced a refresh but got Paused, mark it as a failure to backoff
            if should_force and not is_playing:
                self._last_force_refresh_failure_time = current_time
            
            # CRITICAL FIX: Detect track change and invalidate cache
            # Get the new track ID from the API response
            new_track_id = current['item']['id']
            
            # If track changed, invalidate cache to prevent interpolation of old song data
            if self._metadata_cache and self._metadata_cache.get('track_id') != new_track_id:
                old_track_id = self._metadata_cache.get('track_id')
                logger.debug(f"Track changed: {old_track_id} -> {new_track_id}, invalidating cache to prevent stale data")
                self._metadata_cache = None  # Clear cache to force fresh data
            
            # Get highest quality album art (Spotify API returns images sorted largest to smallest)
            # To be absolutely sure we get the largest, we explicitly find it by dimensions
            album_images = current['item']['album'].get('images', [])
            album_art_url = None
            if album_images:
                # Find the image with the largest dimensions (width * height)
                # This ensures we always get the highest quality available, even if API order changes
                largest_image = max(album_images, 
                                  key=lambda img: (img.get('width', 0) or 0) * (img.get('height', 0) or 0),
                                  default=album_images[0] if album_images else None)
                if largest_image:
                    album_art_url = largest_image['url']
                    # Try to enhance to 1400px if available (falls back to 640px if not)
                    # Use async version since we're in an async method (get_current_track)
                    album_art_url = await enhance_spotify_image_url_async(album_art_url)
            
            # Update cache with new track data
            self._metadata_cache = {
                'title': current['item']['name'],
                'artist': current['item']['artists'][0]['name'],
                'album': current['item']['album']['name'],
                'album_art': album_art_url,
                'track_id': new_track_id,
                'artist_id': current['item']['artists'][0]['id'] if current['item'].get('artists') else None,
                'artist_name': current['item']['artists'][0]['name'] if current['item'].get('artists') else None,
                'url': current['item']['external_urls']['spotify'],
                'duration_ms': current['item']['duration_ms'],
                'progress_ms': current['progress_ms'],
                'is_playing': is_playing
            }
            
            # CRITICAL FIX: Store track ID for validation in _calculate_progress
            # This ensures we can detect if cached data is from a different track
            self._last_track_id = new_track_id
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
            # Track API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['search'] += 1
            
            # Clean up search terms
            search_query = f"track:{title} artist:{artist}"
            results = self.sp.search(q=search_query, type='track', limit=1)
            
            if not results['tracks']['items']:
                logger.info(f"No tracks found for: {artist} - {title}")
                return None
                
            track = results['tracks']['items'][0]
            # Get highest quality album art (Spotify API returns images sorted largest to smallest)
            # To be absolutely sure we get the largest, we explicitly find it by dimensions
            album_images = track['album'].get('images', [])
            album_art_url = None
            if album_images:
                # Find the image with the largest dimensions (width * height)
                # This ensures we always get the highest quality available, even if API order changes
                largest_image = max(album_images,
                                  key=lambda img: (img.get('width', 0) or 0) * (img.get('height', 0) or 0),
                                  default=album_images[0] if album_images else None)
                if largest_image:
                    album_art_url = largest_image['url']
                    # Try to enhance to 1400px if available (falls back to 640px if not)
                    # Use sync version since search_track is a synchronous method
                    album_art_url = enhance_spotify_image_url_sync(album_art_url)
            
            return {
                'title': track['name'],
                'artist': track['artists'][0]['name'],
                'album': track['album']['name'],
                'url': track['external_urls']['spotify'],
                'album_art': album_art_url,
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

    def search_track_by_isrc(self, isrc: str) -> Optional[Dict[str, Any]]:
        """
        Search for a track on Spotify using ISRC code.
        
        ISRC (International Standard Recording Code) uniquely identifies recordings.
        This provides exact matching, unlike text search which can be ambiguous.
        
        Used by audio recognition to get canonical Spotify metadata
        (proper capitalization, Spotify track ID) from Shazam's ISRC codes.
        
        Args:
            isrc: ISRC code (e.g., "USHR10622153")
            
        Returns:
            Dict with Spotify metadata or None if not found:
            {
                'artist': str,      # Canonical artist name
                'title': str,       # Canonical track title  
                'album': str,       # Album name
                'track_id': str,    # Spotify track ID
                'duration_ms': int, # Track duration
                'album_art_url': str,  # High-res album art
            }
        """
        if not self.initialized:
            logger.debug("Spotify not initialized, skipping ISRC search")
            return None
            
        if not isrc:
            return None
            
        try:
            # Track API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['search'] += 1
            
            # Search by ISRC - returns exact match
            results = self.sp.search(q=f"isrc:{isrc}", type='track', limit=1)
            
            if not results or not results.get('tracks') or not results['tracks'].get('items'):
                logger.debug(f"No Spotify match for ISRC: {isrc}")
                return None
                
            track = results['tracks']['items'][0]
            
            # Get highest quality album art
            album_images = track['album'].get('images', [])
            album_art_url = None
            if album_images:
                largest_image = max(album_images,
                                   key=lambda img: (img.get('width', 0) or 0) * (img.get('height', 0) or 0),
                                   default=album_images[0] if album_images else None)
                if largest_image:
                    album_art_url = largest_image['url']
                    # Enhance to 1400px if available
                    album_art_url = enhance_spotify_image_url_sync(album_art_url)
            
            result = {
                'artist': track['artists'][0]['name'],
                'title': track['name'],
                'album': track['album']['name'],
                'track_id': track['id'],
                'duration_ms': track['duration_ms'],
                'album_art_url': album_art_url,
            }
            
            logger.info(f"ISRC lookup success: {isrc} → {result['artist']} - {result['title']}")
            return result
            
        except ReadTimeout:
            self.request_stats['errors']['timeout'] += 1
            logger.debug(f"ISRC search timed out: {isrc}")
            return None
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.debug(f"ISRC search error for {isrc}: {e}")
            return None

    def get_request_stats(self) -> Dict[str, Any]:
        """
        Get current API request statistics for monitoring rate limits.
        
        Spotify's rate limits are approximately:
        - ~180 requests/minute for most endpoints
        - Rolling window based on app + user combination
        
        Returns dict with all tracking metrics.
        """
        total_requests = self.request_stats['total_requests']  # API calls only
        total_function_calls = self.request_stats['total_function_calls']  # All function calls
        cached_responses = self.request_stats['cached_responses']
        
        # Calculate cache hit rate based on total function calls, not just API calls
        # This gives a realistic percentage (cache hits / total calls)
        # Higher = better for rate limiting (more requests avoided via cache)
        cache_hit_rate = (cached_responses / max(total_function_calls, 1)) * 100
        
        # Calculate cache efficiency: how many API calls we saved
        # If we had 100 function calls, 80 were cached, we only made 20 API calls
        # Cache efficiency = 80% (we avoided 80% of potential API calls)
        cache_efficiency = (cached_responses / max(total_function_calls, 1)) * 100
        
        return {
            'Total Requests': total_requests,  # Actual API calls to Spotify (key metric for rate limits)
            'Total Function Calls': total_function_calls,  # All calls to get_current_track()
            'Cached Responses': cached_responses,  # Times we used cache instead of API
            'API Calls': self.request_stats['api_calls'],  # Breakdown by endpoint
            'Errors': self.request_stats['errors'],  # Errors by type
            'Cache Age': f"{time.time() - self._last_metadata_check:.1f}s",
            'Cache Hit Rate': f"{cache_hit_rate:.1f}%"  # Percentage of calls that hit cache
        }

    # Playback Control Methods
    
    async def pause_playback(self) -> bool:
        """Pause current playback. Tracks API call for statistics."""
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            # Track this API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['playback_control'] += 1
            
            logger.info("Pausing playback")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.pause_playback)
            return True
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"Failed to pause playback: {e}")
            return False
    
    async def resume_playback(self) -> bool:
        """Resume current playback. Tracks API call for statistics."""
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            # Track this API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['playback_control'] += 1
            
            logger.info("Resuming playback")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.start_playback)
            return True
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"Failed to resume playback: {e}")
            return False
    
    async def next_track(self) -> bool:
        """Skip to next track. Tracks API call for statistics."""
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            # Track this API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['playback_control'] += 1
            
            logger.info("Skipping to next track")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.next_track)
            return True
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"Failed to skip to next track: {e}")
            return False
    
    async def previous_track(self) -> bool:
        """Go to previous track. Tracks API call for statistics."""
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            # Track this API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['playback_control'] += 1
            
            logger.info("Going to previous track")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.previous_track)
            return True
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"Failed to go to previous track: {e}")
            return False
    
    async def seek_to_position(self, position_ms: int) -> bool:
        """Seek to position in current playback. Tracks API call for statistics.
        
        Args:
            position_ms: Position to seek to in milliseconds
            
        Returns:
            True if successful, False otherwise
        """
        if not self.initialized:
            logger.warning("Spotify API not initialized")
            return False
            
        try:
            # Track this API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['playback_control'] += 1
            
            logger.info(f"Seeking to position {position_ms}ms")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self.sp.seek_track(position_ms))
            return True
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"Failed to seek: {e}")
            return False
    
    async def get_artist_images(self, artist_id: str) -> list:
        """
        Fetch artist images from Spotify API.
        
        Args:
            artist_id: Spotify artist ID
            
        Returns:
            List of image URLs sorted by size (largest first)
        """
        if not artist_id:
            return []

        # Check cache first
        if artist_id in self._artist_image_cache:
            logger.debug(f"Returning cached images for artist {artist_id}")
            return self._artist_image_cache[artist_id]

        if not self.initialized:
            logger.warning("Spotify API not initialized, cannot fetch artist images")
            return []
            
        try:
            # Track this API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['other'] += 1
            
            logger.debug(f"Fetching artist images for artist_id: {artist_id}")
            loop = asyncio.get_event_loop()
            artist = await loop.run_in_executor(None, self.sp.artist, artist_id)
            
            images = artist.get('images', [])
            
            # Sort by size (width * height), largest first
            images_sorted = sorted(
                images, 
                key=lambda x: (x.get('width', 0) or 0) * (x.get('height', 0) or 0), 
                reverse=True
            )
            
            # Try to enhance each image URL to 1400px if available (falls back to 640px if not)
            # Use asyncio.gather to verify all images in parallel (much faster than sequential)
            enhancement_tasks = [enhance_spotify_image_url_async(img['url']) for img in images_sorted]
            try:
                image_urls = await asyncio.wait_for(
                    asyncio.gather(*enhancement_tasks),
                    timeout=15.0
                )
            except asyncio.TimeoutError:
                logger.warning(f"Spotify image enhancement timed out for artist {artist_id}")
                image_urls = [img['url'] for img in images_sorted]  # Use original URLs
            
            # Log enhanced URLs for artist images (similar to album art logging)
            enhanced_count = sum(1 for orig, enhanced in zip([img['url'] for img in images_sorted], image_urls) if orig != enhanced)
            if enhanced_count > 0:
                logger.info(f"Retrieved {len(image_urls)} artist images for {artist.get('name', artist_id)} ({enhanced_count} enhanced to 1400px)")
            else:
                logger.info(f"Retrieved {len(image_urls)} artist images for {artist.get('name', artist_id)} (no 1400px versions available)")
            
            # Cache the results
            self._artist_image_cache[artist_id] = image_urls
            
            return image_urls
            
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"Error fetching artist images for {artist_id}: {e}")
            return []
    
    def get_auth_url(self) -> Optional[str]:
        """
        Generate the Spotify authorization URL for web-based OAuth flow.
        Returns the URL that users should visit to authorize the application.
        """
        if not hasattr(self, 'auth_manager') or not self.auth_manager:
            # FIX: Throttled to debug level - this is expected when credentials are missing
            logger.debug("Auth manager not initialized - Spotify auth unavailable")
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
            logger.debug("Auth manager not initialized - cannot complete auth")
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
                requests_session=CountingSession(self.request_stats),  # Track ALL requests
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

    async def get_queue(self) -> Optional[Dict[str, Any]]:
        """Fetch the user's current playback queue with caching.
        
        Uses a 10-second cache to prevent rate limiting from frequent
        frontend polling (Queue Drawer + Next-Up Card).
        """
        if not self.initialized:
            return None
        
        # Check cache first
        current_time = time.time()
        if self._queue_cache and (current_time - self._queue_cache_time) < self._QUEUE_CACHE_TTL:
            logger.debug("Returning cached queue data")
            return self._queue_cache
            
        try:
            # Track API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['other'] += 1
            
            loop = asyncio.get_event_loop()
            queue_data = await loop.run_in_executor(None, self.sp.queue)
            
            # Update cache
            self._queue_cache = queue_data
            self._queue_cache_time = current_time
            
            return queue_data
        except Exception as e:
            self.request_stats['errors']['other'] += 1
            logger.error(f"Failed to fetch queue: {e}")
            # Return stale cache on error (better than nothing)
            return self._queue_cache

    async def is_track_liked(self, track_id: str) -> bool:
        """Check if a track is saved in the user's library."""
        if not self.initialized or not track_id:
            return False
            
        try:
            # Track API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['other'] += 1
            
            loop = asyncio.get_event_loop()
            # API expects a list of IDs
            results = await loop.run_in_executor(None, self.sp.current_user_saved_tracks_contains, [track_id])
            return results[0] if results else False
        except Exception as e:
            logger.error(f"Failed to check if track is liked: {e}")
            return False

    async def like_track(self, track_id: str) -> bool:
        """Save a track to the user's library."""
        if not self.initialized or not track_id:
            return False
            
        try:
            # Track API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['other'] += 1
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.current_user_saved_tracks_add, [track_id])
            return True
        except Exception as e:
            logger.error(f"Failed to like track: {e}")
            return False

    async def unlike_track(self, track_id: str) -> bool:
        """Remove a track from the user's library."""
        if not self.initialized or not track_id:
            return False
            
        try:
            # Track API call (endpoint-specific, total counted by CountingSession)
            self.request_stats['api_calls']['other'] += 1
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.sp.current_user_saved_tracks_delete, [track_id])
            return True
        except Exception as e:
            logger.error(f"Failed to unlike track: {e}")
            return False