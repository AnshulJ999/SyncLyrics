"""
High-Resolution Album Art Provider
Attempts to retrieve high-resolution album art from multiple sources:
1. Enhanced Spotify (try to get larger sizes, up to 3000x3000px)
2. iTunes/Apple Music API (up to 3000x3000px, free, no auth, rate limited to 20 req/min)
3. Last.fm API (up to 1000x1000px, requires API key)
4. Fallback to Spotify's default 640x640px

Goal: Get highest quality possible (prefer 3000x3000px) for large displays.
"""
import sys
from pathlib import Path
import asyncio
import requests
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
import logging
import os
from logging_config import get_logger
from config import conf, ALBUM_ART

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

logger = get_logger(__name__)

class AlbumArtProvider:
    """Provider for high-resolution album art from multiple sources"""
    
    def __init__(self):
        """Initialize the album art provider"""
        # Safely get config with defaults
        try:
            album_art_config = ALBUM_ART if ALBUM_ART else {}
        except (NameError, AttributeError):
            album_art_config = {}
            
        self.timeout = album_art_config.get("timeout", 5)
        self.retries = album_art_config.get("retries", 2)
        
        # Last.fm API key (optional, from env only - never from settings.json for security)
        # Get from environment variable only (should be in .env file)
        self.lastfm_api_key = os.getenv("LASTFM_API_KEY")
        
        # Enable/disable specific sources
        self.enable_itunes = album_art_config.get("enable_itunes", True)
        self.enable_lastfm = album_art_config.get("enable_lastfm", True)
        self.enable_spotify_enhanced = album_art_config.get("enable_spotify_enhanced", True)
        
        # Minimum resolution threshold (default: prefer 3000x3000 or higher for best quality)
        self.min_resolution = album_art_config.get("min_resolution", 3000)
        
        # Overall timeout for the entire high-res art fetching process
        self.overall_timeout = self.timeout * 3  # Allow time for multiple sources
        
        # In-memory cache to prevent API spam on every poll
        # Key: "artist::title" (normalized), Value: cached URL or None
        self._cache = {}
        self._cache_size = 100  # Limit cache size to prevent memory leaks
        
    def _try_enhance_spotify_url(self, spotify_url: str) -> Optional[str]:
        """
        Try to enhance Spotify image URL to get higher resolution.
        Simply modifies the URL string - no network requests to avoid latency.
        If the enhanced URL doesn't work, the frontend will fall back gracefully.
        
        Args:
            spotify_url: Original Spotify image URL
            
        Returns:
            Enhanced URL if possible, None otherwise
        """
        if not spotify_url or not self.enable_spotify_enhanced:
            return None
            
        try:
            # Spotify image URLs are typically CDN URLs
            # Try replacing common size indicators in the URL
            # Some Spotify URLs have format like: .../640x640.jpg or .../ab67616d0000b273...
            # We can try requesting larger sizes by modifying the URL
            
            # Method 1: Try replacing 640 with larger sizes (try highest quality first)
            # NOTE: This is experimental - Spotify may not actually serve higher res
            # even if we modify the URL. The actual resolution is unknown until downloaded.
            for size in [3000, 2000, 1600, 1200, 1000]:
                enhanced = spotify_url.replace('640', str(size))
                if enhanced != spotify_url:
                    # URL modified, but actual resolution is unknown
                    logger.debug(f"Modified Spotify URL (attempted {size}x{size}, actual resolution unknown)")
                    return enhanced
            
            # Method 2: Try appending size parameters (some CDNs support this)
            parsed = urlparse(spotify_url)
            query_params = parse_qs(parsed.query)
            
            # Try different size parameters (try highest quality first)
            for size_param in ['size', 'w', 'width', 'dimension']:
                for size in [3000, 2000, 1000]:
                    query_params[size_param] = [str(size)]
                    new_query = urlencode(query_params, doseq=True)
                    enhanced_url = urlunparse(parsed._replace(query=new_query))
                    if enhanced_url != spotify_url:
                        logger.debug(f"Enhanced Spotify URL with size parameter ({size}x{size})")
                        return enhanced_url
                    
        except Exception as e:
            logger.debug(f"Failed to enhance Spotify URL: {e}")
            
        return None
    
    def _get_itunes_art(self, artist: str, title: str, album: Optional[str] = None) -> Optional[Tuple[str, int]]:
        """
        Get album art from iTunes/Apple Music API.
        Returns up to 3000x3000px images, free, no authentication required.
        Rate limited to 20 requests/minute per IP.
        
        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional, helps with accuracy)
            
        Returns:
            Tuple of (image_url, resolution) or None if not found
        """
        if not self.enable_itunes:
            return None
            
        try:
            # Build search query
            search_term = f"{artist} {title}"
            if album:
                search_term = f"{artist} {album}"
            
            # iTunes Search API (free, no auth required)
            url = "https://itunes.apple.com/search"
            params = {
                "term": search_term,
                "media": "music",
                "entity": "song",
                "limit": 1
            }
            
            response = requests.get(url, params=params, timeout=self.timeout)
            if response.status_code != 200:
                return None
                
            data = response.json()
            if not data.get("results"):
                return None
                
            track = data["results"][0]
            
            # Get artwork URL - iTunes provides different sizes
            # artworkUrl100 = 100x100
            # artworkUrl512 = 512x512 (if available)
            # artworkUrl1000 = 1000x1000 (if available)
            # We prefer the largest available
            
            artwork_url = None
            resolution = 0
            
            # Try to get the highest resolution available
            for size_key in ["artworkUrl1000", "artworkUrl512", "artworkUrl100"]:
                if size_key in track and track[size_key]:
                    artwork_url = track[size_key]
                    # Extract resolution from key name
                    if "1000" in size_key:
                        resolution = 1000
                    elif "512" in size_key:
                        resolution = 512
                    elif "100" in size_key:
                        resolution = 100
                    break
            
            if artwork_url:
                # Replace image size in URL to get maximum resolution
                # iTunes URLs can be modified: .../100x100bb.jpg -> .../3000x3000bb.jpg
                # Try highest quality first (3000, 2000, 1000)
                # Trust the URL modification - frontend will handle failures
                if resolution < 3000:
                    # Try to get highest quality version (Apple's magic URL pattern)
                    for target_size in [3000, 2000, 1000]:
                        if resolution < target_size:
                            enhanced_url = artwork_url.replace(f"{resolution}x{resolution}bb", f"{target_size}x{target_size}bb")
                            # Also try without 'bb' suffix
                            if enhanced_url == artwork_url:
                                enhanced_url = artwork_url.replace(f"{resolution}x{resolution}", f"{target_size}x{target_size}")
                            if enhanced_url != artwork_url:
                                artwork_url = enhanced_url
                                resolution = target_size
                                logger.debug(f"iTunes: Enhanced to {target_size}x{target_size}")
                                break
                
                logger.info(f"iTunes: Found album art ({resolution}x{resolution}) for {artist} - {title}")
                return (artwork_url, resolution)
                
        except Exception as e:
            logger.debug(f"iTunes API error: {e}")
            
        return None
    
    def _get_lastfm_art(self, artist: str, title: str, album: Optional[str] = None) -> Optional[Tuple[str, int]]:
        """
        Get album art from Last.fm API.
        Returns up to 1000x1000px images (extralarge size).
        Requires API key (optional, will skip if not configured).
        
        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional, helps with accuracy)
            
        Returns:
            Tuple of (image_url, resolution) or None if not found
        """
        if not self.enable_lastfm or not self.lastfm_api_key:
            return None
            
        try:
            # Last.fm API
            url = "http://ws.audioscrobbler.com/2.0/"
            params = {
                "method": "track.getInfo",
                "api_key": self.lastfm_api_key,
                "artist": artist,
                "track": title,
                "format": "json"
            }
            
            response = requests.get(url, params=params, timeout=self.timeout)
            if response.status_code != 200:
                return None
                
            data = response.json()
            if "error" in data or "track" not in data:
                return None
                
            track = data["track"]
            album_data = track.get("album", {})
            
            if not album_data:
                return None
                
            # Last.fm provides images in different sizes:
            # small, medium, large, extralarge
            # extralarge is typically 1000x1000px or larger
            images = album_data.get("image", [])
            
            # Find the largest image
            largest_url = None
            largest_size = 0
            size_map = {"small": 34, "medium": 64, "large": 174, "extralarge": 1000}
            
            for img in images:
                size_text = img.get("#text", "")
                size_value = size_map.get(size_text.lower(), 0)
                if size_value > largest_size and img.get("#text"):
                    largest_url = img.get("#text")
                    largest_size = size_value
            
            if largest_url and largest_size >= self.min_resolution:
                logger.info(f"Last.fm: Found album art ({largest_size}x{largest_size}) for {artist} - {title}")
                return (largest_url, largest_size)
            elif largest_url:
                logger.debug(f"Last.fm: Found album art but resolution ({largest_size}x{largest_size}) below threshold")
                
        except Exception as e:
            logger.debug(f"Last.fm API error: {e}")
            
        return None
    
    def _get_cache_key(self, artist: str, title: str) -> str:
        """Generate normalized cache key from artist and title"""
        return f"{artist.lower().strip()}::{title.lower().strip()}"
    
    async def get_high_res_art(
        self, 
        artist: str, 
        title: str, 
        album: Optional[str] = None,
        spotify_url: Optional[str] = None
    ) -> Optional[Tuple[str, str]]:
        """
        Get high-resolution album art from multiple sources.
        Tries sources in order of preference and returns the first high-res image found.
        All blocking network operations run in thread executor to avoid blocking event loop.
        
        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional)
            spotify_url: Existing Spotify album art URL (optional, will try to enhance)
            
        Returns:
            Tuple of (URL, resolution_info) or None if not found.
            resolution_info format: "3000x3000 (Spotify enhanced)" or "3000x3000 (iTunes)" etc.
        """
        if not artist or not title:
            return None
        
        # Check cache first (instant return, prevents API spam)
        cache_key = self._get_cache_key(artist, title)
        if cache_key in self._cache:
            cached_result = self._cache[cache_key]
            # Return cached result silently (no log spam)
            # Cache stores (url, resolution_info) tuples
            return cached_result  # Returns (URL, resolution_info) or None (cached failure)
        
        loop = asyncio.get_event_loop()
        
        # Wrap entire operation in timeout to prevent hanging
        try:
            result = await asyncio.wait_for(
                self._get_high_res_art_internal(loop, artist, title, album, spotify_url),
                timeout=self.overall_timeout
            )
            
            # Cache the result (even if None, to prevent repeated failed lookups)
            # Result is now a tuple (url, resolution_info) or None
            self._cache[cache_key] = result
            
            # Simple cache cleanup: remove oldest entry if cache is too large
            if len(self._cache) > self._cache_size:
                # Remove first (oldest) entry
                oldest_key = next(iter(self._cache))
                self._cache.pop(oldest_key)
                logger.debug(f"Album art cache: removed oldest entry (size was {self._cache_size + 1})")
            
            return result
        except asyncio.TimeoutError:
            logger.debug(f"High-res album art fetch timed out after {self.overall_timeout}s, using Spotify fallback")
            # Cache the fallback too
            fallback_result = (spotify_url, "640x640 (Spotify default, timeout)") if spotify_url else None
            self._cache[cache_key] = fallback_result
            return fallback_result
        except Exception as e:
            logger.debug(f"Error fetching high-res album art: {e}, using Spotify fallback")
            # Cache the fallback too
            fallback_result = (spotify_url, "640x640 (Spotify default, error)") if spotify_url else None
            self._cache[cache_key] = fallback_result
            return fallback_result
    
    async def _get_high_res_art_internal(
        self,
        loop: asyncio.AbstractEventLoop,
        artist: str,
        title: str,
        album: Optional[str],
        spotify_url: Optional[str]
    ) -> Optional[Tuple[str, str]]:
        """Internal method that does the actual work, with all blocking calls in executor"""
        # Try sources in order of preference
        sources = []
        
        # 1. Try enhancing Spotify URL (fastest, if we have it)
        # This makes blocking requests, so run in executor
        if spotify_url and self.enable_spotify_enhanced:
            try:
                enhanced = await loop.run_in_executor(
                    None,
                    self._try_enhance_spotify_url,
                    spotify_url
                )
                if enhanced:
                    # Note: Spotify URL modification doesn't guarantee higher resolution
                    # We modify the URL but Spotify may still serve 640x640
                    # Mark as "unknown" since we can't verify without downloading
                    resolution_info = "unknown (Spotify URL modified, may still be 640x640)"
                    logger.info(f"Using enhanced Spotify URL for {artist} - {title} (resolution unknown)")
                    return (enhanced, resolution_info)
            except Exception as e:
                logger.debug(f"Failed to enhance Spotify URL: {e}")
        
        # 2. Try iTunes API (free, good quality, no auth)
        # Run in executor since it makes blocking requests
        if self.enable_itunes:
            try:
                itunes_result = await loop.run_in_executor(
                    None,
                    self._get_itunes_art,
                    artist,
                    title,
                    album
                )
                if itunes_result:
                    sources.append(("iTunes", itunes_result))
            except Exception as e:
                logger.debug(f"iTunes API call failed: {e}")
        
        # 3. Try Last.fm API (requires API key, but good quality)
        # Run in executor since it makes blocking requests
        if self.enable_lastfm and self.lastfm_api_key:
            try:
                lastfm_result = await loop.run_in_executor(
                    None,
                    self._get_lastfm_art,
                    artist,
                    title,
                    album
                )
                if lastfm_result:
                    sources.append(("Last.fm", lastfm_result))
            except Exception as e:
                logger.debug(f"Last.fm API call failed: {e}")
        
        # Try all sources and pick the best one
        best_url = None
        best_resolution = 0
        best_source = "Unknown"
        
        for source_name, result in sources:
            if result:
                url, resolution = result
                if resolution >= self.min_resolution:
                    # Found a high-res image, use it immediately
                    resolution_info = f"{resolution}x{resolution} ({source_name})"
                    logger.info(f"Using {source_name} album art ({resolution}x{resolution}) for {artist} - {title}")
                    return (url, resolution_info)
                elif resolution > best_resolution:
                    # Keep track of best resolution found so far
                    best_url = url
                    best_resolution = resolution
                    best_source = source_name
        
        # If we found something but it's below threshold, use it anyway
        if best_url:
            resolution_info = f"{best_resolution}x{best_resolution} ({best_source}, below {self.min_resolution}px threshold)"
            logger.info(f"Using {best_resolution}x{best_resolution} album art (below {self.min_resolution}px threshold) for {artist} - {title}")
            return (best_url, resolution_info)
        
        # Fallback to Spotify URL if provided
        if spotify_url:
            logger.debug(f"Falling back to Spotify URL for {artist} - {title}")
            return (spotify_url, "640x640 (Spotify default)")
        
        return None

# Singleton instance
_album_art_provider_instance: Optional[AlbumArtProvider] = None

def get_album_art_provider() -> AlbumArtProvider:
    """Get the shared album art provider instance"""
    global _album_art_provider_instance
    if _album_art_provider_instance is None:
        _album_art_provider_instance = AlbumArtProvider()
    return _album_art_provider_instance

