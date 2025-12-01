"""
High-Resolution Album Art Provider
Attempts to retrieve high-resolution album art from multiple sources:
1. Enhanced Spotify (try to get larger sizes, up to 3000x3000px)
2. iTunes/Apple Music API (up to 5000x5000px using Ben Dodson method, free, no auth, rate limited to 20 req/min)
3. Last.fm API (up to 1000x1000px+ by removing size segments, requires API key)
4. Fallback to Spotify's default 640x640px

Goal: Get highest quality possible (prefer 3000x3000px+) for large displays.
"""
import sys
from pathlib import Path
import asyncio
import requests
from typing import Optional, Dict, Any, Tuple, List
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
        
        # Debug logging for configuration
        api_key_status = "set" if self.lastfm_api_key else "missing"
        if self.lastfm_api_key:
            # Don't log the actual key, but show first/last chars for verification
            masked_key = f"{self.lastfm_api_key[:4]}...{self.lastfm_api_key[-4:]}" if len(self.lastfm_api_key) > 8 else "***"
            logger.info(f"AlbumArtProvider initialized - iTunes: {self.enable_itunes}, Last.fm: {self.enable_lastfm} (API key: {api_key_status} [{masked_key}]), Spotify Enhanced: {self.enable_spotify_enhanced}")
        else:
            logger.warning(f"AlbumArtProvider initialized - iTunes: {self.enable_itunes}, Last.fm: {self.enable_lastfm} (API key: {api_key_status} - check .env file!), Spotify Enhanced: {self.enable_spotify_enhanced}")
        
        # Minimum resolution threshold (default: prefer 3000x3000 or higher for best quality)
        self.min_resolution = album_art_config.get("min_resolution", 3000)
        
        # Overall timeout for the entire high-res art fetching process
        # Sequential: iTunes (3s) → Last.fm (3s) = max 6s total
        self.overall_timeout = 6.0  # Allow time for sequential sources (3s each)
        
        # In-memory cache to prevent API spam on every poll
        # Key: "artist::title" (normalized), Value: cached URL or None
        self._cache = {}
        self._cache_size = 100  # Limit cache size to prevent memory leaks
        
        # Track logged tracks to prevent duplicate log messages
        # Key: "artist::title", Value: True (if already logged)
        self._logged_tracks = set()
        self._logged_tracks_size = 200  # Limit size to prevent memory leaks
        
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
        Uses Ben Dodson method (9999x9999 URL) to get original full-size images (often 3000-5000px).
        Free, no authentication required. Rate limited to 20 requests/minute per IP.
        
        Validates album name match to ensure we get the correct album art (not a different version).
        
        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional, used for validation to ensure correct match)
            
        Returns:
            Tuple of (image_url, resolution) or None if not found
        """
        if not self.enable_itunes:
            return None
            
        try:
            # Build search query - prefer album if available for better accuracy
            search_term = f"{artist} {title}"
            if album:
                search_term = f"{artist} {album}"
            
            # iTunes Search API (free, no auth required)
            url = "https://itunes.apple.com/search"
            params = {
                "term": search_term,
                "media": "music",
                "entity": "song",
                "limit": 10  # Get multiple results to find best match
            }
            
            response = requests.get(url, params=params, timeout=self.timeout)
            if response.status_code != 200:
                return None
                
            data = response.json()
            if not data.get("results"):
                return None
            
            # Normalize album name for comparison (if provided)
            target_album_normalized = None
            if album:
                target_album_normalized = album.lower().strip()
                # Remove common suffixes that might differ between platforms
                for suffix in [" (deluxe edition)", " (deluxe)", " (remastered)", " (remaster)", " (expanded edition)"]:
                    if target_album_normalized.endswith(suffix.lower()):
                        target_album_normalized = target_album_normalized[:-len(suffix)]
                        break
            
            # Find best matching track
            best_match = None
            best_score = 0
            
            for track in data["results"]:
                itunes_album = track.get("collectionName", "").lower().strip()
                itunes_artist = track.get("artistName", "").lower().strip()
                itunes_title = track.get("trackName", "").lower().strip()
                
                # Normalize iTunes album name (remove common suffixes)
                itunes_album_normalized = itunes_album
                for suffix in [" (deluxe edition)", " (deluxe)", " (remastered)", " (remaster)", " (expanded edition)"]:
                    if itunes_album_normalized.endswith(suffix.lower()):
                        itunes_album_normalized = itunes_album_normalized[:-len(suffix)]
                        break
                
                # Score the match
                score = 0
                
                # Artist match (required)
                artist_lower = artist.lower().strip()
                itunes_artist_lower = itunes_artist # Already lower/stripped above
                
                # Exact match (best)
                if artist_lower == itunes_artist_lower:
                    score += 50
                # Word-based match (good for "The Beatles" vs "Beatles")
                # This prevents "Sting" from matching "Casting Crowns" while allowing partial word matches
                elif artist_lower in itunes_artist_lower.split() or itunes_artist_lower in artist_lower.split():
                    score += 30
                # Lenient substring (fallback, lower score)
                elif artist_lower in itunes_artist_lower or itunes_artist_lower in artist_lower:
                    score += 10
                else:
                    continue  # Skip if artist doesn't match
                
                # Album match (high priority if album name provided)
                if target_album_normalized:
                    if itunes_album_normalized == target_album_normalized:
                        score += 50  # Exact album match
                    elif target_album_normalized in itunes_album_normalized or itunes_album_normalized in target_album_normalized:
                        score += 30  # Partial album match
                else:
                    # No album to compare, check title match
                    if title.lower().strip() in itunes_title or itunes_title in title.lower().strip():
                        score += 20
                
                # Track title match (lower priority than album)
                if title.lower().strip() in itunes_title or itunes_title in title.lower().strip():
                    score += 10
                
                # Prefer higher resolution results
                if track.get("artworkUrl1000"):
                    score += 5
                elif track.get("artworkUrl512"):
                    score += 3
                
                if score > best_score:
                    best_score = score
                    best_match = track
            
            # Use best match, or fall back to first result if no good match found
            if best_match and best_score >= 10:  # At least artist match required
                track = best_match
                if target_album_normalized and best_score < 40:
                    # Album name provided but no good match - log warning
                    itunes_album = track.get("collectionName", "Unknown")
                    logger.debug(f"iTunes: Album name mismatch - Spotify: '{album}', iTunes: '{itunes_album}' (using best match anyway)")
            else:
                # Fall back to first result
                track = data["results"][0]
                if target_album_normalized:
                    itunes_album = track.get("collectionName", "Unknown")
                    logger.debug(f"iTunes: No good match found, using first result - Spotify: '{album}', iTunes: '{itunes_album}'")
            
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
                # iTunes URLs can be modified: .../100x100bb.jpg -> .../9999x9999bb.jpg
                # Using Ben Dodson method: 9999x9999 returns the original full-size image (often 3000-5000px)
                # Try 9999x9999 first to get the original, then fallback to specific sizes if needed
                if resolution < 9999:
                    # First, try the Ben Dodson method: use 9999x9999 to get original full-size
                    # This will return the largest available original (often 3000-5000px)
                    enhanced_url = artwork_url.replace(f"{resolution}x{resolution}bb", "9999x9999bb")
                    # Also try without 'bb' suffix
                    if enhanced_url == artwork_url:
                        enhanced_url = artwork_url.replace(f"{resolution}x{resolution}", "9999x9999")
                    
                    if enhanced_url != artwork_url:
                        artwork_url = enhanced_url
                        # We don't know the actual size until download - could be 1500px, 3000px, or 5000px
                        # The actual resolution will be verified when the image is downloaded
                        # Use a placeholder that indicates it's unknown/estimated
                        resolution = 0  # 0 = unknown, will be verified on download
                        logger.debug(f"iTunes: Enhanced to original full-size using 9999x9999 method (actual size will be verified on download)")
                    else:
                        # Fallback: try specific high-res sizes if 9999x9999 replacement didn't work
                        for target_size in [3000, 2000, 1000]:
                            if resolution < target_size:
                                enhanced_url = artwork_url.replace(f"{resolution}x{resolution}bb", f"{target_size}x{target_size}bb")
                                if enhanced_url == artwork_url:
                                    enhanced_url = artwork_url.replace(f"{resolution}x{resolution}", f"{target_size}x{target_size}")
                                if enhanced_url != artwork_url:
                                    artwork_url = enhanced_url
                                    resolution = target_size
                                    logger.debug(f"iTunes: Enhanced to {target_size}x{target_size}")
                                    break
                
                # Only log once per track to avoid duplicate logs from multiple background tasks
                log_key = f"{artist.lower().strip()}::{title.lower().strip()}"
                if log_key not in self._logged_tracks:
                    if resolution == 0:
                        # Unknown size (using 9999x9999 method) - actual size will be verified on download
                        logger.info(f"iTunes: Found album art (original full-size, actual resolution will be verified) for {artist} - {title}")
                    else:
                        logger.info(f"iTunes: Found album art ({resolution}x{resolution}) for {artist} - {title}")
                    self._logged_tracks.add(log_key)
                    # Cleanup old entries if cache is too large
                    if len(self._logged_tracks) > self._logged_tracks_size:
                        # Remove oldest entries (simple: clear half when full)
                        self._logged_tracks = set(list(self._logged_tracks)[self._logged_tracks_size // 2:])
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
        if not self.enable_lastfm:
            logger.debug(f"Last.fm disabled in _get_lastfm_art for {artist} - {title}")
            return None
        
        if not self.lastfm_api_key:
            logger.warning(f"Last.fm API key is missing! Check .env file for LASTFM_API_KEY. Artist: {artist}, Title: {title}")
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
            
            logger.debug(f"Last.fm API request: {url} with params: method={params['method']}, artist={artist}, track={title}")
            response = requests.get(url, params=params, timeout=self.timeout)
            
            if response.status_code != 200:
                logger.warning(f"Last.fm API returned status {response.status_code} for {artist} - {title}")
                return None
                
            data = response.json()
            logger.debug(f"Last.fm API response for {artist} - {title}: {str(data)[:500]}...")  # Log first 500 chars of response
            
            # Check for API errors
            if "error" in data:
                error_code = data.get("error")
                error_message = data.get("message", "Unknown error")
                logger.warning(f"Last.fm API error {error_code}: {error_message} for {artist} - {title}")
                return None
                
            if "track" not in data:
                logger.warning(f"Last.fm: No track data in response for {artist} - {title}. Response keys: {list(data.keys())}")
                return None
            
            track = data["track"]
            if not track:
                logger.warning(f"Last.fm: Track data is empty for {artist} - {title}")
                return None
                
            album_data = track.get("album", {})
            
            if not album_data:
                logger.warning(f"Last.fm: No album data for track {artist} - {title}. Track keys: {list(track.keys()) if isinstance(track, dict) else 'not a dict'}")
                return None
            
            logger.info(f"Last.fm: Found album data for {artist} - {title}. Album keys: {list(album_data.keys()) if isinstance(album_data, dict) else 'not a dict'}")
                
            # Last.fm provides images in different sizes:
            # small, medium, large, extralarge
            # extralarge is typically 1000x1000px or larger
            images = album_data.get("image", [])
            
            logger.debug(f"Last.fm: Found {len(images)} image(s) for {artist} - {title}. Images: {images}")
            
            # Find the largest image
            largest_url = None
            largest_size = 0
            size_map = {"small": 34, "medium": 64, "large": 174, "extralarge": 1000}
            
            for img in images:
                if not isinstance(img, dict):
                    logger.debug(f"Last.fm: Skipping non-dict image entry: {img}")
                    continue
                size_text = img.get("size", "")  # Last.fm uses "size" not "#text" for the size field
                url = img.get("#text", "")  # URL is in "#text" field
                
                # Try to extract actual size from URL path (e.g., /i/u/300x300/ or /i/u/1000x1000/)
                actual_size_from_url = 0
                if url:
                    import re
                    # Match patterns like /300x300/, /1000x1000/, /174s/, etc.
                    size_match = re.search(r'/(\d+)x?(\d+)?[s/]', url)
                    if size_match:
                        width = int(size_match.group(1))
                        height = int(size_match.group(2)) if size_match.group(2) else width
                        actual_size_from_url = max(width, height)
                
                # Use size_map for fallback, but prefer actual URL size if available
                mapped_size = size_map.get(size_text.lower(), 0)
                size_value = actual_size_from_url if actual_size_from_url > 0 else mapped_size
                
                logger.debug(f"Last.fm: Image entry - size: '{size_text}', url: '{url[:50] if url else 'empty'}...', mapped_size: {mapped_size}, actual_url_size: {actual_size_from_url}, final_size: {size_value}")
                if size_value > largest_size and url:
                    largest_url = url
                    largest_size = size_value
            
            logger.debug(f"Last.fm: Selected largest image - size: {largest_size}, url: {largest_url[:50] if largest_url else 'None'}...")
            
            # Last.fm API returns URLs with size segments like /300x300/, /174s/, etc.
            # To get the original full-size image, we need to REMOVE the size segment entirely
            # Example: .../i/u/300x300/hash.jpg -> .../i/u/hash.jpg (original full-size, often 1000x1000+)
            if largest_url:
                import re
                # Remove size segments like /300x300/, /174s/, /64s/, /34s/ from the URL
                # Pattern matches: /digitsxdigits/ or /digits+s/ followed by /
                # More precise pattern to avoid breaking the URL structure
                original_url = re.sub(r'/\d+x?\d*[s]/', '/', largest_url)
                # Handle the case where size segment is at the end (with trailing slash)
                original_url = re.sub(r'/\d+x\d+/', '/', original_url)
                # Fix any double slashes that might result (but preserve ://)
                original_url = re.sub(r'(?<!:)/+', '/', original_url)
                
                if original_url != largest_url:
                    # CHANGED: Downgrade to DEBUG
                    logger.debug(f"Last.fm: Removing size segment from URL to get original full-size image")
                    logger.debug(f"Last.fm: Original URL: {largest_url[:80]}...")
                    logger.debug(f"Last.fm: Modified URL: {original_url[:80]}...")
                    largest_url = original_url
                    # We don't know the actual size until we download it, but assume it's >= 1000
                    # The actual resolution will be verified when the image is downloaded
                    largest_size = 0  # Changed from 1000 to 0 to force verification
            
            # NEW: Return URL even if size is unknown (0)
            # The actual resolution will be checked when image is downloaded and saved
            if largest_url:
                logger.info(f"Last.fm: Found album art for {artist} - {title} (resolution will be verified on download)")
                return (largest_url, 0)  # Return with 0 resolution, will be verified later
                
        except requests.exceptions.Timeout:
            logger.warning(f"Last.fm API timeout ({self.timeout}s) for {artist} - {title}")
            return None
        except requests.exceptions.RequestException as e:
            logger.warning(f"Last.fm API request failed: {e} for {artist} - {title}")
            return None
        except Exception as e:
            logger.error(f"Last.fm API call failed with unexpected error: {type(e).__name__}: {e} for {artist} - {title}")
            return None
    
    def _get_cache_key(self, artist: str, title: str, album: Optional[str] = None) -> str:
        """
        Generate normalized cache key from artist and album (preferred) or title (fallback).
        Album-level caching: same album = same art for all tracks.
        """
        artist_norm = artist.lower().strip()
        if album:
            # Album-level cache: same album = same art for all tracks
            return f"{artist_norm}::{album.lower().strip()}"
        else:
            # Fallback to track-level cache if no album
            return f"{artist_norm}::{title.lower().strip()}"
    
    def is_cached(self, artist: str, title: str, album: Optional[str] = None) -> bool:
        """Check if high-res art is cached for this album/track"""
        if not artist or not title:
            return False
        cache_key = self._get_cache_key(artist, title, album)
        return cache_key in self._cache and self._cache[cache_key] is not None
    
    def get_from_cache(self, artist: str, title: str, album: Optional[str] = None) -> Optional[Tuple[str, str]]:
        """
        Get cached high-res art if available, returns (url, resolution_info) or None.
        Uses album-level cache if album is provided (same album = same art for all tracks).
        """
        if not artist or not title:
            return None
        cache_key = self._get_cache_key(artist, title, album)
        cached_result = self._cache.get(cache_key)
        if cached_result and cached_result is not None:
            return cached_result  # Returns (url, resolution_info) tuple
        return None
    
    async def get_high_res_art(
        self, 
        artist: str, 
        title: str, 
        album: Optional[str] = None,
        spotify_url: Optional[str] = None
    ) -> Optional[Tuple[str, str]]:
        """
        Get high-resolution album art from multiple sources.
        Sequential approach: iTunes → Last.fm → Spotify fallback (640px).
        All blocking network operations run in thread executor to avoid blocking event loop.
        
        Args:
            artist: Artist name
            title: Track title
            album: Album name (optional)
            spotify_url: Existing Spotify album art URL (optional, used as fallback)
            
        Returns:
            Tuple of (URL, resolution_info) or None if not found.
            resolution_info format: "3000x3000 (iTunes)" or "1000x1000 (Last.fm)" or "640x640 (Spotify default)"
        """
        if not artist or not title:
            return None
        
        # Check cache first (instant return, prevents API spam)
        # Use album-level cache if available (same album = same art for all tracks)
        cache_key = self._get_cache_key(artist, title, album)
        if cache_key in self._cache:
            cached_result = self._cache[cache_key]
            # Log cache hit for debugging
            logger.debug(f"Album art cache hit for {artist} - {title} (key: {cache_key}): {cached_result}")
            # Cache stores (url, resolution_info) tuples
            return cached_result  # Returns (URL, resolution_info) or None (cached failure)
        
        logger.debug(f"Album art cache miss for {artist} - {title} (key: {cache_key}), fetching from sources...")
        
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
        """
        Internal method that does the actual work, with all blocking calls in executor.
        Sequential approach: iTunes → Last.fm → Spotify fallback.
        Timeout: 3 seconds per source.
        """
        # 1. Try iTunes API first (best quality, up to 3000x3000px, free, no auth)
        # Run in executor since it makes blocking requests
        if self.enable_itunes:
            try:
                itunes_result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        self._get_itunes_art,
                        artist,
                        title,
                        album
                    ),
                    timeout=3.0
                )
                if itunes_result:
                    url, resolution = itunes_result
                    # Only log once per track to avoid duplicate logs from multiple background tasks
                    log_key = f"{artist.lower().strip()}::{title.lower().strip()}"
                    if log_key not in self._logged_tracks:
                        if resolution == 0:
                            # Unknown size (using 9999x9999 method) - actual size will be verified on download
                            resolution_info = "original full-size (iTunes, actual size will be verified)"
                            logger.info(f"Using iTunes album art (original full-size, actual resolution will be verified) for {artist} - {title}")
                            self._logged_tracks.add(log_key)
                            return (url, resolution_info)
                        elif resolution >= self.min_resolution:
                            # Found high-res image, return immediately
                            resolution_info = f"{resolution}x{resolution} (iTunes)"
                            logger.info(f"Using iTunes album art ({resolution}x{resolution}) for {artist} - {title}")
                            self._logged_tracks.add(log_key)
                            return (url, resolution_info)
                        elif resolution >= 1000:
                            # Good enough quality, return it
                            resolution_info = f"{resolution}x{resolution} (iTunes)"
                            logger.info(f"Using iTunes album art ({resolution}x{resolution}) for {artist} - {title}")
                            self._logged_tracks.add(log_key)
                            return (url, resolution_info)
                    else:
                        # Already logged, just return without logging
                        if resolution == 0:
                            resolution_info = "original full-size (iTunes, actual size will be verified)"
                        else:
                            resolution_info = f"{resolution}x{resolution} (iTunes)"
                        return (url, resolution_info)
                    # If iTunes returned <1000px, continue to try Last.fm
            except asyncio.TimeoutError:
                logger.debug(f"iTunes API timeout for {artist} - {title}")
            except Exception as e:
                logger.debug(f"iTunes API call failed: {e}")
        
        # 2. Try Last.fm API (requires API key, up to 1000x1000px)
        # Run in executor since it makes blocking requests
        if not self.enable_lastfm:
            logger.debug(f"Last.fm disabled in config for {artist} - {title}")
        elif not self.lastfm_api_key:
            logger.debug(f"Last.fm API key not configured (check .env file) for {artist} - {title}")
        else:
            try:
                logger.debug(f"Attempting Last.fm API call for {artist} - {title}")
                lastfm_result = await asyncio.wait_for(
                    loop.run_in_executor(
                        None,
                        self._get_lastfm_art,
                        artist,
                        title,
                        album
                    ),
                    timeout=3.0
                )
                if lastfm_result:
                    url, resolution = lastfm_result
                    resolution_info = f"{resolution}x{resolution} (Last.fm)"
                    logger.info(f"Using Last.fm album art ({resolution}x{resolution}) for {artist} - {title}")
                    return (url, resolution_info)
                else:
                    logger.debug(f"Last.fm returned no result for {artist} - {title}")
            except asyncio.TimeoutError:
                logger.debug(f"Last.fm API timeout for {artist} - {title}")
            except Exception as e:
                logger.debug(f"Last.fm API call failed: {e}")
        
        # 3. Fallback to Spotify URL (640x640px)
        if spotify_url:
            logger.debug(f"Falling back to Spotify URL (640x640) for {artist} - {title}")
            return (spotify_url, "640x640 (Spotify default)")
        
        return None
    
    def _get_spotify_art(self, spotify_url: Optional[str]) -> Optional[Tuple[str, int]]:
        """
        Get Spotify album art URL and resolution.
        Spotify typically provides 640x640px images.
        
        FIX: Always return the original URL to ensure reliable downloads.
        Spotify enhancement is known to not work (returns 404), so we skip it entirely.
        
        Args:
            spotify_url: Spotify album art URL
            
        Returns:
            Tuple of (image_url, resolution) or None if not found
        """
        if not spotify_url:
            return None
        
                # Try to enhance the URL to get higher resolution
        # enhanced_url = self._try_enhance_spotify_url(spotify_url)
        # if enhanced_url:
        #     # Resolution is unknown until download, but assume 640x640 minimum
        #     return (enhanced_url, 640)
        
        # Return original URL with estimated resolution

        # FIX: Always return original URL - Spotify enhancement doesn't work
        # Enhanced URLs return 404, causing downloads to fail and no art to be saved
        # By returning the original URL, we guarantee a working 640x640 image
        return (spotify_url, 640)
    
    async def get_all_art_options(
        self,
        artist: str,
        album: Optional[str],
        title: str,
        spotify_url: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch album art from all enabled providers in parallel.
        Returns a list of all found candidates (not just the first good one).
        
        Args:
            artist: Artist name
            album: Album name (optional)
            title: Track title
            spotify_url: Existing Spotify album art URL (optional)
            
        Returns:
            List of dictionaries with format:
            [
                {
                    "provider": "iTunes",
                    "url": "https://...",
                    "resolution": "3000x3000",
                    "width": 3000,
                    "height": 3000
                },
                ...
            ]
        """
        if not artist or not title:
            return []
        
        options = []
        loop = asyncio.get_event_loop()
        
        # Create tasks for all providers in parallel
        tasks = []
        
        # Task 1: iTunes
        if self.enable_itunes:
            tasks.append((
                "iTunes",
                loop.run_in_executor(None, self._get_itunes_art, artist, title, album)
            ))
        
        # Task 2: Last.fm
        if self.enable_lastfm and self.lastfm_api_key:
            tasks.append((
                "LastFM",
                loop.run_in_executor(None, self._get_lastfm_art, artist, title, album)
            ))
        
        # Task 3: Spotify
        if spotify_url:
            tasks.append((
                "Spotify",
                loop.run_in_executor(None, self._get_spotify_art, spotify_url)
            ))
        
        # Wait for all tasks to complete (with timeout)
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*[task[1] for task in tasks], return_exceptions=True),
                timeout=self.overall_timeout
            )
            
            # Process results
            for i, (provider_name, _) in enumerate(tasks):
                result = results[i]
                
                if isinstance(result, Exception):
                    logger.debug(f"{provider_name} art fetch failed: {result}")
                    continue
                
                if result:
                    url, resolution = result
                    
                    # Handle unknown resolution (0 means unknown, will be verified on download)
                    if resolution == 0:
                        # For unknown resolution, we'll set a placeholder that indicates it needs verification
                        # The actual resolution will be determined when the image is downloaded
                        width = height = 0  # Will be determined on download
                        resolution_str = "unknown (will verify on download)"
                    else:
                        width = height = resolution
                        resolution_str = f"{resolution}x{resolution}"
                    
                    options.append({
                        "provider": provider_name,
                        "url": url,
                        "resolution": resolution_str,
                        "width": width,
                        "height": height
                    })
                    
                    logger.debug(f"{provider_name}: Found album art ({resolution_str}) for {artist} - {title}")
        
        except asyncio.TimeoutError:
            logger.debug(f"get_all_art_options timed out after {self.overall_timeout}s")
        except Exception as e:
            logger.error(f"Error in get_all_art_options: {e}")
        
        return options

    async def get_artist_images(self, artist: str) -> List[Dict[str, Any]]:
        """
        Fetch artist images from all available sources.
        
        Returns:
            List of dicts: {'url': str, 'source': str, 'width': int, 'height': int}
        """
        images = []
        loop = asyncio.get_running_loop()
        
        # 1. iTunes (High Quality)
        if self.enable_itunes:
            try:
                itunes_images = await loop.run_in_executor(None, self._get_itunes_artist_images, artist)
                images.extend(itunes_images)
            except Exception as e:
                logger.debug(f"iTunes artist image fetch failed: {e}")

        # 2. Last.fm (Good Quality)
        if self.enable_lastfm and self.lastfm_api_key:
            try:
                lastfm_images = await loop.run_in_executor(None, self._get_lastfm_artist_images, artist)
                images.extend(lastfm_images)
            except Exception as e:
                logger.debug(f"Last.fm artist image fetch failed: {e}")
                
        return images

    def _get_itunes_artist_images(self, artist: str) -> List[Dict[str, Any]]:
        """Fetch artist image from iTunes Search API"""
        try:
            # Search for artist
            params = {
                "term": artist,
                "entity": "musicArtist",
                "limit": 1
            }
            url = f"https://itunes.apple.com/search?{urlencode(params)}"
            response = requests.get(url, timeout=self.timeout)
            
            if response.status_code != 200:
                return []
                
            data = response.json()
            if not data.get("resultCount"):
                return []
                
            result = data["results"][0]
            # iTunes doesn't always provide artist images via API, but we can check artistLinkUrl 
            # or sometimes amgArtistId. However, for now, we rely on standard ArtworkUrl100 if present.
            # NOTE: iTunes Search API often returns blank for artist artwork compared to albums.
            # We will try to extract the standard keys if they exist.
            # iTunes Search API for artists rarely includes artwork directly,
            # but we check for any artwork URL fields that might exist
            artwork_url = None
            
            # Check common artwork URL fields (though unlikely for artists)
            for key in ["artworkUrl100", "artworkUrl512", "artworkUrl1000"]:
                if result.get(key):
                    artwork_url = result[key]
                    # Try to enhance to full size using 9999x9999 method
                    if "100x100" in artwork_url:
                        artwork_url = artwork_url.replace("100x100bb", "9999x9999bb")
                    elif "512x512" in artwork_url:
                        artwork_url = artwork_url.replace("512x512bb", "9999x9999bb")
                    elif "1000x1000" in artwork_url:
                        artwork_url = artwork_url.replace("1000x1000bb", "9999x9999bb")
                    break
            
            if artwork_url:
                return [{
                    "url": artwork_url,
                    "source": "iTunes",
                    "width": 0,  # Unknown, will be verified on download
                    "height": 0
                }]
            
            # iTunes API typically doesn't return artist artwork
            return [] 
        except Exception as e:
            logger.debug(f"iTunes artist image fetch error: {e}")
            return []

    def _get_lastfm_artist_images(self, artist: str) -> List[Dict[str, Any]]:
        """Fetch artist images from Last.fm API"""
        if not self.lastfm_api_key:
            return []
            
        try:
            # Use artist.getInfo which is the most reliable public API method
            params = {
                "method": "artist.getInfo",
                "artist": artist,
                "api_key": self.lastfm_api_key,
                "format": "json",
                "limit": 5 # Fetch top 5
            }
            url = f"http://ws.audioscrobbler.com/2.0/?{urlencode(params)}"
            response = requests.get(url, timeout=self.timeout)
            
            if response.status_code != 200:
                return []
                
            data = response.json()
            
            # Check for API errors
            if "error" in data:
                logger.debug(f"Last.fm API error for artist {artist}: {data.get('message', 'Unknown error')}")
                return []
            
            # Last.fm returns images in 'small', 'medium', 'large', 'extralarge', 'mega'
            artist_data = data.get("artist", {})
            if not artist_data:
                return []
                
            images = artist_data.get("image", [])
            results = []
            
            for img in images:
                if not isinstance(img, dict):
                    continue
                    
                size = img.get("size", "")
                url = img.get("#text", "")
                
                # Only use large images (extralarge or mega)
                if size in ["mega", "extralarge"] and url:
                    # Remove size segments from URL to get original full-size image
                    import re
                    original_url = re.sub(r'/\d+x?\d*[s]/', '/', url)
                    original_url = re.sub(r'/\d+x\d+/', '/', original_url)
                    original_url = re.sub(r'(?<!:)/+', '/', original_url)
                    
                    results.append({
                        "url": original_url if original_url != url else url,
                        "source": "Last.fm",
                        "width": 0,  # Unknown, will be verified on download
                        "height": 0
                    })
                    
            # Deduplicate by URL
            unique_results = []
            seen = set()
            for r in results:
                url = r.get("url", "")
                if url and url not in seen:
                    unique_results.append(r)
                    seen.add(url)
                    
            return unique_results
        except Exception as e:
            logger.debug(f"Last.fm artist image fetch error: {e}")
            return []

# Singleton instance
_album_art_provider_instance: Optional[AlbumArtProvider] = None

def get_album_art_provider() -> AlbumArtProvider:
    """Get the shared album art provider instance"""
    global _album_art_provider_instance
    if _album_art_provider_instance is None:
        _album_art_provider_instance = AlbumArtProvider()
    return _album_art_provider_instance

def reset_album_art_provider() -> None:
    """Reset the singleton instance (useful when config changes)"""
    global _album_art_provider_instance
    _album_art_provider_instance = None
    logger.debug("AlbumArtProvider singleton reset - will reinitialize on next access")

