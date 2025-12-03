"""
Artist Image Provider
Fetches high-quality artist images, logos, and backgrounds from:
1. Wikipedia/Wikimedia (Free, 1500-5000px, ultra high-res, no auth required)
2. Deezer (Free, 1000x1000px, fast)
3. TheAudioDB (Free key '123', rich metadata + MusicBrainz IDs)
4. FanArt.tv (High quality, requires MBID from AudioDB + Personal API Key)
5. Spotify (Fallback)
6. Last.fm (Fallback)
"""
import asyncio
import logging
import os
import re
import time
import unicodedata
from difflib import SequenceMatcher
import requests
from typing import List, Dict, Any, Optional
from urllib.parse import quote

# Safe import of ARTIST_IMAGE config - prevents crash if config.py is outdated
try:
    from config import ARTIST_IMAGE
except (ImportError, AttributeError):
    # Fallback if config doesn't have ARTIST_IMAGE (e.g., outdated config.py)
    ARTIST_IMAGE = {}

logger = logging.getLogger(__name__)

# Throttle for Wikipedia logs to prevent spam
# Key: (artist, log_type), Value: last log timestamp
# Initialize at module level to avoid UnboundLocalError
_wikipedia_log_throttle: Dict[tuple, float] = {}
_WIKIPEDIA_LOG_THROTTLE_SECONDS = 300  # Log at most once per 5 minutes per artist per log type
_MAX_LOG_THROTTLE_SIZE = 200  # Limit throttle cache size

def _should_log_wikipedia(artist: str, log_type: str) -> bool:
    """
    Check if we should log a Wikipedia message (throttling to prevent spam).
    
    Args:
        artist: Artist name
        log_type: Type of log (e.g., 'strategy', 'validation', 'image')
        
    Returns:
        True if we should log, False if throttled
    """
    global _wikipedia_log_throttle
    current_time = time.time()
    key = (artist.lower(), log_type)
    
    last_log_time = _wikipedia_log_throttle.get(key, 0)
    should_log = (current_time - last_log_time) >= _WIKIPEDIA_LOG_THROTTLE_SECONDS
    
    if should_log:
        _wikipedia_log_throttle[key] = current_time
        
        # Clean up old entries to prevent memory leak
        if len(_wikipedia_log_throttle) > _MAX_LOG_THROTTLE_SIZE:
            cutoff_time = current_time - 600  # 10 minutes
            _wikipedia_log_throttle = {
                k: v for k, v in _wikipedia_log_throttle.items()
                if v > cutoff_time
            }
    
    return should_log

def _validate_wikipedia_title(artist: str, title: str) -> bool:
    """
    Smart validation that checks if Wikipedia page title matches artist name.
    Handles disambiguation suffixes, special characters, articles, and fuzzy matching.
    
    This fixes issues where major artists like "Nirvana", "Architects", "Bring Me The Horizon"
    fail to match because Wikipedia uses "(band)" suffixes or slight title variations.
    
    Args:
        artist: Original artist name (e.g., "Nirvana", "Architects")
        title: Wikipedia page title (e.g., "Nirvana (band)", "Architects (British band)")
        
    Returns:
        True if title matches artist, False otherwise
    """
    if not artist or not title:
        return False
    
    # CRITICAL FIX: Check exclusion terms FIRST (before any matching logic)
    # This prevents wrong matches (e.g., "Plini" matching geology/planet pages)
    # Reject titles about non-artist topics immediately
    title_lower = title.lower()
    exclusion_terms = ['planet', 'geology', 'volcano', 'geological', 'astronomy', 'space', 
                      'science', 'geography', 'nature', 'landform', 'crater']
    if any(term in title_lower for term in exclusion_terms):
        # Title is about something else (geology, astronomy, etc.), not the artist
        return False
    
    # Normalize both strings: lowercase, strip whitespace
    artist_norm = artist.lower().strip()
    title_norm = title.lower().strip()
    
    # Remove common articles ("the") from both for comparison
    # This handles "The Beatles" vs "Beatles" and "The Weeknd" vs "The Weeknd"
    for article in ["the ", " the "]:
        if artist_norm.startswith(article.strip()):
            artist_norm = artist_norm[len(article.strip()):].strip()
        if title_norm.startswith(article.strip()):
            title_norm = title_norm[len(article.strip()):].strip()
    
    # Normalize special characters (e.g., "Motörhead" -> "motorhead")
    # This handles artists with accents, umlauts, etc.
    artist_clean = unicodedata.normalize('NFKD', artist_norm).encode('ASCII', 'ignore').decode('ASCII')
    title_clean = unicodedata.normalize('NFKD', title_norm).encode('ASCII', 'ignore').decode('ASCII')
    
    # Remove disambiguation suffixes from title (e.g., "(band)", "(musician)")
    # This is critical - Wikipedia often uses "Nirvana (band)" but we search for "Nirvana"
    disambiguation_suffixes = [
        " (band)", " (musician)", " (singer)", " (musical group)", 
        " (rapper)", " (group)", " (artist)", " (vocalist)"
    ]
    for suffix in disambiguation_suffixes:
        if title_clean.endswith(suffix):
            title_clean = title_clean[:-len(suffix)].strip()
    
    # Remove any remaining parentheses and their contents (handles edge cases)
    title_clean = re.sub(r'\s*\([^)]*\)\s*$', '', title_clean).strip()
    
    # Remove special characters and punctuation for comparison
    # This handles "Panic! at the Disco" vs "Panic at the Disco"
    artist_clean = re.sub(r'[^a-z0-9\s]', '', artist_clean).strip()
    title_clean = re.sub(r'[^a-z0-9\s]', '', title_clean).strip()
    
    # Exact match after normalization (most common case)
    if artist_clean == title_clean:
        return True
    
    # Fuzzy match using SequenceMatcher (standard library, no external deps)
    # This handles minor spelling differences or variations
    # Only use fuzzy matching if both strings are reasonably long (prevents false positives)
    if len(artist_clean) >= 3 and len(title_clean) >= 3:
        similarity = SequenceMatcher(None, artist_clean, title_clean).ratio()
        if similarity >= 0.90:  # 90% similarity threshold
            return True
    
    # Partial match: check if artist name appears in title or vice versa
    # But only if title isn't way longer (prevents "Bad" matching "Bad Religion")
    if artist_clean in title_clean:
        # Title can be up to 1.5x longer (allows "The Beatles" to match "Beatles")
        if len(title_clean) <= len(artist_clean) * 1.5:
            return True
    
    if title_clean in artist_clean:
        # Artist can be up to 1.5x longer (allows "Bring Me The Horizon" to match "Bring Me the Horizon")
        if len(artist_clean) <= len(title_clean) * 1.5:
            return True
    
    return False

def safe_likes(item: Dict[str, Any]) -> int:
    """
    Safely extract likes count from FanArt.tv item dict.
    Handles missing keys, empty strings, and non-numeric values gracefully.
    
    Args:
        item: Dictionary from FanArt.tv API response
        
    Returns:
        Integer likes count, or 0 if invalid/missing
    """
    try:
        val = item.get('likes')
        # Handle None, empty string, or falsy values
        if not val or val == "":
            return 0
        # Convert to int (handles string numbers like "100")
        return int(val)
    except (ValueError, TypeError):
        # Handle non-numeric strings, wrong types, etc.
        return 0

class ArtistImageProvider:
    """
    Provider for fetching high-quality artist images from multiple sources.
    Prioritizes free sources (Deezer, TheAudioDB) and premium sources (FanArt.tv) when available.
    """
    def __init__(self):
        """Initialize the artist image provider with API keys and configuration"""
        self.session = requests.Session()
        
        # Set User-Agent header (required by Wikipedia API and best practice for other APIs)
        # Wikipedia specifically requires a User-Agent that identifies your application
        self.session.headers.update({
            'User-Agent': 'SyncLyrics/1.0.0 (https://github.com/AnshulJ999/SyncLyrics; contact@example.com)'
        })
        
        # Get timeout from config (default: 5 seconds)
        try:
            self.timeout = ARTIST_IMAGE.get("timeout", 5)
        except (NameError, AttributeError):
            self.timeout = 5
        
        # API Keys
        # FanArt.tv requires a personal API key (user will provide via env var)
        self.fanart_api_key = os.getenv("FANART_TV_API_KEY")
        # TheAudioDB free API key is '123' (as per official documentation)
        self.audiodb_api_key = os.getenv("AUDIODB_API_KEY", "123")
        
        # Toggle Sources
        self.enable_deezer = True
        self.enable_audiodb = True
        self.enable_fanart = bool(self.fanart_api_key)
        
        # Get config options for new features
        try:
            self.enable_wikipedia = ARTIST_IMAGE.get("enable_wikipedia", True)
            self.enable_fanart_albumcover = ARTIST_IMAGE.get("enable_fanart_albumcover", True)
        except (NameError, AttributeError):
            # Fallback if config not available
            self.enable_wikipedia = True
            self.enable_fanart_albumcover = True
        
        # Log initialization status
        api_key_status = "set" if self.fanart_api_key else "missing"
        
        # Mask API keys for security (show full key only if it's the default free key)
        if self.fanart_api_key:
            masked_fanart_key = f"{self.fanart_api_key[:4]}...{self.fanart_api_key[-4:]}" if len(self.fanart_api_key) > 8 else "***"
        else:
            masked_fanart_key = "missing"
            
        # Mask AudioDB key - show full "123" if default, otherwise mask it
        if self.audiodb_api_key == "123":
            masked_audiodb_key = "123"
        else:
            masked_audiodb_key = f"{self.audiodb_api_key[:4]}...{self.audiodb_api_key[-4:]}" if len(self.audiodb_api_key) > 8 else "***"
        
        # Build log message with all features
        features = []
        features.append(f"FanArt: {self.enable_fanart} (Key: {api_key_status} [{masked_fanart_key if self.fanart_api_key else 'missing'}])")
        features.append(f"AudioDB: {self.enable_audiodb} (Key: {masked_audiodb_key})")
        features.append(f"Deezer: {self.enable_deezer}")
        features.append(f"Wikipedia: {self.enable_wikipedia}")
        if self.enable_fanart:
            features.append(f"FanArt AlbumCover: {self.enable_fanart_albumcover}")
        
        logger.info(f"ArtistImageProvider initialized - {', '.join(features)}")

    async def get_artist_images(self, artist_name: str) -> List[Dict[str, Any]]:
        """
        Fetch artist images from all enabled sources in parallel.
        
        Args:
            artist_name: Name of the artist to search for
            
        Returns:
            List of dicts with format: {'url': str, 'source': str, 'width': int, 'height': int, 'type': str}
        """
        if not artist_name:
            return []

        loop = asyncio.get_running_loop()
        tasks = []
        
        # 1. Wikipedia/Wikimedia (Ultra high-res, 1500-5000px, free, no auth required)
        if self.enable_wikipedia:
            tasks.append(loop.run_in_executor(None, self._fetch_wikipedia, artist_name))
        
        # 2. Deezer (Fast, high quality, free, no auth required)
        if self.enable_deezer:
            tasks.append(loop.run_in_executor(None, self._fetch_deezer, artist_name))
            
        # 3. TheAudioDB (Rich metadata + MBID for FanArt.tv)
        if self.enable_audiodb:
            tasks.append(loop.run_in_executor(None, self._fetch_theaudiodb, artist_name))
            
        # Run all in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        all_images = []
        mbid = None
        
        # Process results
        for res in results:
            if isinstance(res, Exception):
                logger.error(f"Artist image fetch error: {res}")
                continue
            if isinstance(res, dict): 
                # AudioDB returns dict with images + mbid
                if 'images' in res:
                    all_images.extend(res['images'])
                if 'mbid' in res and res['mbid']:
                    mbid = res['mbid']
            elif isinstance(res, list): 
                # Deezer and Wikipedia return lists
                all_images.extend(res)

        # 3. FanArt.tv (Requires MBID from AudioDB)
        # Only fetch if we have both MBID and API key
        if self.enable_fanart and self.fanart_api_key and mbid:
            try:
                fanart_images = await loop.run_in_executor(None, self._fetch_fanart, mbid)
                all_images.extend(fanart_images)
            except Exception as e:
                logger.error(f"FanArt.tv fetch failed: {e}")

        # Deduplicate by URL to avoid storing the same image multiple times
        # Defensive: Handle cases where image dict might be malformed or missing 'url' key
        unique_images = []
        seen_urls = set()
        
        for img in all_images:
            # Safely get URL - skip images without valid URL
            img_url = img.get('url') if isinstance(img, dict) else None
            if not img_url or not isinstance(img_url, str):
                logger.debug(f"Skipping image with invalid or missing URL: {img}")
                continue
                
            if img_url not in seen_urls:
                unique_images.append(img)
                seen_urls.add(img_url)
                
        return unique_images

    def _fetch_deezer(self, artist: str) -> List[Dict[str, Any]]:
        """
        Fetch artist images from Deezer API.
        Deezer provides high-quality 1000x1000px images for free, no authentication required.
        
        Args:
            artist: Artist name to search for
            
        Returns:
            List of image dicts with Deezer images
        """
        try:
            # Step 1: Search for artist to get ID
            search_url = f"https://api.deezer.com/search/artist?q={quote(artist)}"
            resp = self.session.get(search_url, timeout=self.timeout)
            if resp.status_code != 200: 
                return []
            
            data = resp.json()
            if not data.get('data') or len(data.get('data', [])) == 0:
                return []
            
            # Get best match (first result) - extra safety check
            artist_obj = data['data'][0]
            if not isinstance(artist_obj, dict):
                logger.debug(f"Deezer: Invalid artist object type: {type(artist_obj)}")
                return []
            
            # Verify name match loosely to ensure we got the right artist
            # Defensive: Check if 'name' field exists
            artist_name = artist_obj.get('name')
            if not artist_name or not isinstance(artist_name, str):
                logger.debug(f"Deezer: Artist object missing or invalid 'name' field")
                return []
                
            artist_lower = artist.lower()
            deezer_name_lower = artist_name.lower()
            if artist_lower not in deezer_name_lower and deezer_name_lower not in artist_lower:
                logger.debug(f"Deezer: Name mismatch - searched '{artist}', got '{artist_name}'")
                return []
            
            # Check if search result has picture fields (it usually does)
            # If not, fetch full artist details
            if not any(artist_obj.get(f'picture_{size}') for size in ['xl', 'big', 'medium', 'small']):
                # Search result doesn't have picture fields, fetch full artist details
                artist_id = artist_obj.get('id')
                if artist_id:
                    try:
                        artist_detail_url = f"https://api.deezer.com/artist/{artist_id}"
                        detail_resp = self.session.get(artist_detail_url, timeout=self.timeout)
                        if detail_resp.status_code == 200:
                            artist_obj = detail_resp.json()
                    except Exception as e:
                        logger.debug(f"Deezer: Failed to fetch artist details: {e}")
                
            images = []
            # Deezer provides different sizes: xl (1000x1000), big (500x500), medium (250x250)
            # We prefer the largest available
            for size in ['xl', 'big', 'medium']:
                key = f'picture_{size}'
                if artist_obj.get(key):
                    width = 1000 if size == 'xl' else (500 if size == 'big' else 250)
                    images.append({
                        'url': artist_obj[key],
                        'source': 'Deezer',
                        'type': 'artist',
                        'width': width,
                        'height': width
                    })
                    break # Just take the largest one available
            
            if images:
                logger.debug(f"Deezer: Found {len(images)} image(s) for {artist}")
                        # Log only if no images found (to avoid spam, but inform about missing data)
            if not images:
                logger.debug(f"Deezer: No images found for {artist}")

            return images
        except Exception as e:
            logger.debug(f"Deezer fetch failed for {artist}: {e}")
            return []

    def _fetch_theaudiodb(self, artist: str) -> Dict[str, Any]:
        """
        Fetch artist images from TheAudioDB API.
        TheAudioDB provides multiple image types (thumbnails, logos, backgrounds) and includes
        MusicBrainz ID (MBID) which is needed for FanArt.tv.
        
        Args:
            artist: Artist name to search for
            
        Returns:
            Dict with format: {'images': List[Dict], 'mbid': Optional[str]}
        """
        result = {'images': [], 'mbid': None}
        try:
            # TheAudioDB v1 API endpoint with free key '123'
            url = f"https://www.theaudiodb.com/api/v1/json/{self.audiodb_api_key}/search.php?s={quote(artist)}"
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                return result
                
            data = resp.json()
            if not data or not data.get('artists') or len(data.get('artists', [])) == 0:
                return result
                
            # Get first artist result - extra safety check
            artist_data = data['artists'][0]
            if not isinstance(artist_data, dict):
                logger.debug(f"TheAudioDB: Invalid artist data type: {type(artist_data)}")
                return result
            
            # Save MBID for FanArt.tv (critical for accessing FanArt.tv API)
            result['mbid'] = artist_data.get('strMusicBrainzID')
            
            # Extract Images - TheAudioDB provides multiple image types
            images = []
            
            # 1. Main Thumbnail (strArtistThumb)
            if artist_data.get('strArtistThumb'):
                images.append({
                    'url': artist_data['strArtistThumb'],
                    'source': 'TheAudioDB',
                    'type': 'thumbnail',
                    'width': 0,  # Will be verified on download
                    'height': 0
                })
                
            # 2. Fanart (Backgrounds) - Multiple fanart images available (strArtistFanart, strArtistFanart2, etc.)
            for i in ['', '2', '3', '4']:
                key = f'strArtistFanart{i}'
                if artist_data.get(key):
                    images.append({
                        'url': artist_data[key],
                        'source': 'TheAudioDB',
                        'type': 'background',
                        'width': 1920,  # Typically HD backgrounds
                        'height': 1080
                    })
                    
            # 3. Logo/Clearart (Transparent PNG logos)
            if artist_data.get('strArtistLogo'):
                images.append({
                    'url': artist_data['strArtistLogo'],
                    'source': 'TheAudioDB',
                    'type': 'logo',
                    'width': 0,  # Will be verified on download
                    'height': 0
                })
                
            result['images'] = images
            if images:
                logger.debug(f"TheAudioDB: Found {len(images)} image(s) for {artist}, MBID: {result['mbid']}")
                        # Log only if no images found (to avoid spam, but inform about missing data)
            if not images:
                logger.debug(f"TheAudioDB: No images found for {artist}")

            return result
            
        except Exception as e:
            logger.debug(f"TheAudioDB fetch failed for {artist}: {e}")
            return result

    def _fetch_fanart(self, mbid: str) -> List[Dict[str, Any]]:
        """
        Fetch artist images from FanArt.tv API.
        FanArt.tv provides the highest quality curated images but requires:
        1. MusicBrainz ID (MBID) - obtained from TheAudioDB
        2. Personal API key - user must provide via FANART_TV_API_KEY env var
        
        Args:
            mbid: MusicBrainz ID of the artist
            
        Returns:
            List of image dicts with FanArt.tv images
        """
        if not self.fanart_api_key or not mbid:
            return []
            
        try:
            # FanArt.tv v3 API endpoint
            url = f"https://webservice.fanart.tv/v3/music/{mbid}?api_key={self.fanart_api_key}"
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code != 200:
                logger.debug(f"FanArt.tv returned status {resp.status_code} for MBID {mbid}")
                return []
                
            data = resp.json()
            images = []
            
            # Artist Backgrounds (High-resolution backgrounds, typically 1920x1080+)
            # Sort by likes (community-curated quality) - highest liked images first
            # FALLBACK: If no likes data available, use API order (old behavior)
            backgrounds = data.get('artistbackground', [])
            # Check if sorting will work (at least one item has likes > 0)
            has_likes = any(safe_likes(bg) > 0 for bg in backgrounds)
            if has_likes:
                # Sort by likes (descending) - highest liked images first
                backgrounds.sort(key=safe_likes, reverse=True)
                logger.debug(f"FanArt.tv: Sorted {len(backgrounds)} backgrounds by likes")
            else:
                # Fallback: Use API order (old behavior) - no data loss if sorting fails
                logger.debug(f"FanArt.tv: No likes data available, using API order for {len(backgrounds)} backgrounds")
            
            for bg in backgrounds:
                if isinstance(bg, dict) and bg.get('url'):
                    images.append({
                        'url': bg['url'],
                        'source': 'FanArt.tv',
                        'type': 'background',
                        'width': 1920,
                        'height': 1080
                    })
                
            # Artist Thumbnails (Main artist photos, typically 1000x1000+)
            # Sort by likes for quality ranking, with fallback to API order
            thumbs = data.get('artistthumb', [])
            has_likes_thumbs = any(safe_likes(thumb) > 0 for thumb in thumbs)
            if has_likes_thumbs:
                thumbs.sort(key=safe_likes, reverse=True)
                logger.debug(f"FanArt.tv: Sorted {len(thumbs)} thumbnails by likes")
            else:
                logger.debug(f"FanArt.tv: No likes data available, using API order for {len(thumbs)} thumbnails")
            
            for thumb in thumbs:
                if isinstance(thumb, dict) and thumb.get('url'):
                    images.append({
                        'url': thumb['url'],
                        'source': 'FanArt.tv',
                        'type': 'thumbnail',
                        'width': 1000,
                        'height': 1000
                    })
                
            # FIX #2: REMOVED HD Music Logos - We only want photos, not logos
            # Logos (800x310) were replacing good photos and don't belong in artist images
            # Old code that fetched logos has been removed to prevent data loss
            
            # Album Covers (High-quality album artwork, typically 1000x1000+)
            # Only fetch if enabled (can be disabled if too many duplicates with album art DB)
            if self.enable_fanart_albumcover:
                # Sort by likes for quality ranking (get best album covers first) using safe helper function
                # FALLBACK: If no likes data, use API order
                album_covers = data.get('albumcover', [])
                has_likes_covers = any(safe_likes(cover) > 0 for cover in album_covers)
                if has_likes_covers:
                    album_covers.sort(key=safe_likes, reverse=True)
                    logger.debug(f"FanArt.tv: Sorted {len(album_covers)} album covers by likes")
                else:
                    logger.debug(f"FanArt.tv: No likes data available, using API order for {len(album_covers)} album covers")
                
                # Limit to top 5 highest-quality album covers to avoid flooding
                # (Artist images should focus on artist photos, not entire discography)
                for cover in album_covers[:5]:
                    if isinstance(cover, dict) and cover.get('url'):
                        images.append({
                            'url': cover['url'],
                            'source': 'FanArt.tv',
                            'type': 'albumcover',
                            'width': 1000,
                            'height': 1000
                        })
                
            if images:
                logger.debug(f"FanArt.tv: Found {len(images)} image(s) for MBID {mbid}")
                        # Log only if no images found (to avoid spam, but inform about missing data)
            if not images:
                logger.debug(f"FanArt.tv: No images found for MBID {mbid}")

            return images
        except Exception as e:
            logger.debug(f"FanArt.tv fetch failed for MBID {mbid}: {e}")
            return []
    
    def _fetch_wikipedia(self, artist: str) -> List[Dict[str, Any]]:
        """
        Fetch high-resolution artist images from Wikipedia/Wikimedia Commons.
        Provides 1500-12000px ultra-high-res images for artists (7-10 images on average).
        Free, no API key required.
        
        Uses a hybrid multi-strategy approach:
        
        PAGE DISCOVERY (finds Wikipedia page):
        1. Direct lookup (fastest, most accurate - Wikipedia normalizes/redirects automatically)
        2. Search with artist name only (if direct lookup fails)
        3. Search with "band" modifier (for bands/groups)
        4. Search with "musician" modifier (last resort, for solo artists)
        
        IMAGE FETCHING (hybrid approach for maximum coverage):
        A. Wikimedia Commons search (PRIMARY - finds 7-10 images on average)
           - Searches Commons directly for artist photos
           - Best coverage, especially for niche artists
        B. All Wikipedia article images (FALLBACK - finds 2-7 images)
           - Gets all images from the Wikipedia article (not just infobox)
           - Good coverage when Commons search finds few results
        C. pageimages infobox (LAST RESORT - finds 0-1 images)
           - Gets main infobox image only
           - Used when other strategies fail
        
        Smart validation handles:
        - Disambiguation suffixes: "Nirvana (band)" matches "Nirvana"
        - Special characters: "Motörhead" matches "Motorhead"
        - Articles: "The Beatles" matches "Beatles"
        - Fuzzy matching for minor variations
        
        Quality filtering:
        - Minimum resolution: >=1000px (ensures high-quality images)
        - Aspect ratio: 0.3-2.5 (filters out logos/banners)
        - Filename filtering: Skips obvious non-photos (logos, album covers, etc.)
        
        Args:
            artist: Artist name to search for
            
        Returns:
            List of image dicts with Wikipedia/Wikimedia images (up to 10, filtered by quality)
        """
        try:
            page_title = None
            strategy_used = None
            
            # Strategy 1: Direct lookup (fastest, most accurate)
            # Wikipedia automatically normalizes page titles and handles redirects
            if _should_log_wikipedia(artist, 'strategy'):
                logger.debug(f"Wikipedia: Strategy 1 (direct lookup) - Checking '{artist}'")
            
            lookup_url = "https://en.wikipedia.org/w/api.php"
            lookup_params = {
                'action': 'query',
                'format': 'json',
                'titles': artist,
                'prop': 'info',
                'inprop': 'url'
            }
            
            resp = self.session.get(lookup_url, params=lookup_params, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                pages = data.get('query', {}).get('pages', {})
                
                # Check if page exists (not -1) and title matches using smart validation
                for page_id, page_data in pages.items():
                    if page_id != '-1':  # Page exists
                        title = page_data.get('title', '')
                        # Use smart validation that handles disambiguation, special chars, etc.
                        if _validate_wikipedia_title(artist, title):
                            page_title = title
                            strategy_used = "Direct lookup"
                            if _should_log_wikipedia(artist, 'strategy'):
                                logger.debug(f"Wikipedia: Direct lookup found page '{page_title}' (ID: {page_id})")
                            break
            
            # Strategy 2: Search with artist name only (if direct lookup failed)
            if not page_title:
                if _should_log_wikipedia(artist, 'strategy'):
                    logger.debug(f"Wikipedia: Strategy 2 (search) - Trying '{artist}'")
                
                search_url = "https://en.wikipedia.org/w/api.php"
                search_params = {
                    'action': 'query',
                    'format': 'json',
                    'list': 'search',
                    'srsearch': artist,  # Just artist name, no modifier
                    'srlimit': 5,  # Get top 5 results to find best match
                    'srnamespace': 0
                }
                
                resp = self.session.get(search_url, params=search_params, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    search_results = data.get('query', {}).get('search', [])
                    
                    if _should_log_wikipedia(artist, 'strategy'):
                        result_titles = [r.get('title', '') for r in search_results[:3]]
                        logger.debug(f"Wikipedia: Search returned {len(search_results)} results: {result_titles}")
                    
                    # Prioritize disambiguation pages (e.g., "Nirvana (band)" over "Nirvana")
                    # First pass: Look for pages with disambiguation suffixes
                    for result in search_results:
                        title = result.get('title', '')
                        # Check if this is a disambiguation page (band, musician, etc.)
                        if any(suffix in title.lower() for suffix in ['(band)', '(musician)', '(musical group)', '(singer)', '(rapper)']):
                            if _validate_wikipedia_title(artist, title):
                                page_title = title
                                strategy_used = "Search (disambiguation prioritized)"
                                if _should_log_wikipedia(artist, 'strategy'):
                                    logger.debug(f"Wikipedia: Selected disambiguation page '{page_title}'")
                                break
                    
                    # Second pass: If no disambiguation page found, check all results
                    if not page_title:
                        for result in search_results:
                            title = result.get('title', '')
                            if _validate_wikipedia_title(artist, title):
                                page_title = title
                                strategy_used = "Search (artist name)"
                                if _should_log_wikipedia(artist, 'strategy'):
                                    logger.debug(f"Wikipedia: Selected page '{page_title}'")
                                break
            
            # Strategy 3: Search with "band" modifier (for bands/groups)
            if not page_title:
                if _should_log_wikipedia(artist, 'strategy'):
                    logger.debug(f"Wikipedia: Strategy 3 (search) - Trying '{artist} band'")
                
                search_url = "https://en.wikipedia.org/w/api.php"
                search_params = {
                    'action': 'query',
                    'format': 'json',
                    'list': 'search',
                    'srsearch': f"{artist} band",  # Add "band" to refine search for bands/groups
                    'srlimit': 5,
                    'srnamespace': 0
                }
                
                resp = self.session.get(search_url, params=search_params, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    search_results = data.get('query', {}).get('search', [])
                    
                    if _should_log_wikipedia(artist, 'strategy'):
                        result_titles = [r.get('title', '') for r in search_results[:3]]
                        logger.debug(f"Wikipedia: Search returned {len(search_results)} results: {result_titles}")
                    
                    # Prioritize disambiguation pages first
                    for result in search_results:
                        title = result.get('title', '')
                        if any(suffix in title.lower() for suffix in ['(band)', '(musical group)']):
                            if _validate_wikipedia_title(artist, title):
                                page_title = title
                                strategy_used = "Search (band modifier, disambiguation)"
                                if _should_log_wikipedia(artist, 'strategy'):
                                    logger.debug(f"Wikipedia: Selected page '{page_title}'")
                                break
                    
                    # Fallback to all results if no disambiguation found
                    if not page_title:
                        for result in search_results:
                            title = result.get('title', '')
                            if _validate_wikipedia_title(artist, title):
                                page_title = title
                                strategy_used = "Search (band modifier)"
                                if _should_log_wikipedia(artist, 'strategy'):
                                    logger.debug(f"Wikipedia: Selected page '{page_title}'")
                                break
            
            # Strategy 4: Search with "musician" modifier (last resort, for solo artists)
            if not page_title:
                if _should_log_wikipedia(artist, 'strategy'):
                    logger.debug(f"Wikipedia: Strategy 4 (search) - Trying '{artist} musician'")
                
                search_url = "https://en.wikipedia.org/w/api.php"
                search_params = {
                    'action': 'query',
                    'format': 'json',
                    'list': 'search',
                    'srsearch': f"{artist} musician",  # Add "musician" to refine search for solo artists
                    'srlimit': 5,
                    'srnamespace': 0
                }
                
                resp = self.session.get(search_url, params=search_params, timeout=self.timeout)
                if resp.status_code == 200:
                    data = resp.json()
                    search_results = data.get('query', {}).get('search', [])
                    
                    if _should_log_wikipedia(artist, 'strategy'):
                        result_titles = [r.get('title', '') for r in search_results[:3]]
                        logger.debug(f"Wikipedia: Search returned {len(search_results)} results: {result_titles}")
                    
                    # Prioritize disambiguation pages first
                    for result in search_results:
                        title = result.get('title', '')
                        if any(suffix in title.lower() for suffix in ['(musician)', '(singer)', '(rapper)', '(vocalist)']):
                            if _validate_wikipedia_title(artist, title):
                                page_title = title
                                strategy_used = "Search (musician modifier, disambiguation)"
                                if _should_log_wikipedia(artist, 'strategy'):
                                    logger.debug(f"Wikipedia: Selected page '{page_title}'")
                                break
                    
                    # Fallback to all results if no disambiguation found
                    if not page_title:
                        for result in search_results:
                            title = result.get('title', '')
                            if _validate_wikipedia_title(artist, title):
                                page_title = title
                                strategy_used = "Search (musician modifier)"
                                if _should_log_wikipedia(artist, 'strategy'):
                                    logger.debug(f"Wikipedia: Selected page '{page_title}'")
                                break
            
            # If we still don't have a page title, try Commons search anyway (might find images even without Wikipedia page)
            # But if we have a page title, we'll use it for better results
            
            # Step 2: Fetch images using hybrid approach (best results first)
            # Strategy A: Wikimedia Commons search (BEST - finds 7-10 images on average)
            # Strategy B: All images from Wikipedia article (GOOD - finds 2-7 images)
            # Strategy C: pageimages infobox (FALLBACK - finds 0-1 images)
            
            images = []
            seen_urls = set()  # Deduplicate by URL across all strategies
            image_source = None
            
            # Strategy A: Wikimedia Commons search (primary - best coverage)
            commons_images = self._fetch_wikimedia_commons(artist, seen_urls)
            if commons_images:
                images.extend(commons_images)
                image_source = "Wikimedia Commons search"
                # If we got good results (10+ images), we're done
                if len(images) >= 10:
                    if _should_log_wikipedia(artist, 'image'):
                        resolutions = [f"{img.get('width', 0)}x{img.get('height', 0)}" for img in images[:5]]
                        logger.info(f"Wikipedia: Found {len(images)} image(s) for '{artist}' via {image_source} (resolutions: {', '.join(resolutions)})")
                    # Sort and limit
                    images.sort(key=lambda x: max(x.get('width', 0), x.get('height', 0)), reverse=True)
                    return images[:10]  # Return up to 10 high-quality images
            
            # Strategy B: All images from Wikipedia article (fallback if Commons didn't find enough)
            if page_title:
                article_images = self._fetch_all_article_images(page_title, seen_urls)
                if article_images:
                    images.extend(article_images)
                    if not image_source:
                        image_source = "Wikipedia article images"
                    # If we now have enough, we're done
                    if len(images) >= 3:
                        if _should_log_wikipedia(artist, 'image'):
                            resolutions = [f"{img.get('width', 0)}x{img.get('height', 0)}" for img in images[:5]]
                            logger.info(f"Wikipedia: Found {len(images)} image(s) for '{artist}' via {image_source} (resolutions: {', '.join(resolutions)})")
                        images.sort(key=lambda x: max(x.get('width', 0), x.get('height', 0)), reverse=True)
                        return images[:10]
            
            # Strategy C: pageimages infobox (last resort - usually only 0-1 images)
            if page_title:
                infobox_images = self._fetch_pageimages_infobox(page_title, seen_urls)
                if infobox_images:
                    images.extend(infobox_images)
                    if not image_source:
                        image_source = "Wikipedia infobox"
            
            # Final processing
            if images:
                # Sort by resolution (highest first) to prioritize best quality
                images.sort(key=lambda x: max(x.get('width', 0), x.get('height', 0)), reverse=True)
                # Return up to 10 high-quality images (increased from 5 to match Commons results)
                images = images[:10]
                
                if _should_log_wikipedia(artist, 'image'):
                    resolutions = [f"{img.get('width', 0)}x{img.get('height', 0)}" for img in images[:5]]
                    logger.info(f"Wikipedia: Found {len(images)} image(s) for '{artist}' via {image_source or 'multiple strategies'} (resolutions: {', '.join(resolutions)})")
            elif _should_log_wikipedia(artist, 'validation'):
                if page_title:
                    logger.debug(f"Wikipedia: Page '{page_title}' found but no high-res images (>=1000px) available from any source")
                else:
                    logger.debug(f"Wikipedia: Could not find page or images for '{artist}'")
            
            return images
            
        except Exception as e:
            if _should_log_wikipedia(artist, 'error'):
                logger.debug(f"Wikipedia fetch failed for {artist}: {e}")
            return []
    
    def _fetch_wikimedia_commons(self, artist: str, seen_urls: set) -> List[Dict[str, Any]]:
        """
        Search Wikimedia Commons directly for artist images.
        This is the PRIMARY strategy as it finds the most images (7-10 on average).
        
        Args:
            artist: Artist name to search for
            seen_urls: Set of URLs already seen (for deduplication)
            
        Returns:
            List of high-quality image dicts from Wikimedia Commons
        """
        images = []
        try:
            # Search Wikimedia Commons for artist images
            commons_url = "https://commons.wikimedia.org/w/api.php"
            
            # Try multiple search terms to maximize coverage
            search_terms = [
                artist,  # Direct search
                f"{artist} (band)",  # With disambiguation
            ]
            
            for search_term in search_terms:
                # Search for bitmap images (photos, not SVG logos)
                search_params = {
                    'action': 'query',
                    'format': 'json',
                    'list': 'search',
                    'srsearch': f'filetype:bitmap {search_term}',
                    'srnamespace': 6,  # File namespace
                    'srlimit': 20  # Get top 20 results
                }
                
                resp = self.session.get(commons_url, params=search_params, timeout=self.timeout)
                if resp.status_code != 200:
                    continue
                
                data = resp.json()
                search_results = data.get('query', {}).get('search', [])
                
                if not search_results:
                    continue
                
                # Get image info for top results
                titles = [r['title'] for r in search_results[:15]]  # Check top 15
                
                if not titles:
                    continue
                
                info_params = {
                    'action': 'query',
                    'format': 'json',
                    'titles': '|'.join(titles),
                    'prop': 'imageinfo',
                    'iiprop': 'url|size|dimensions',
                    'iiurlwidth': 4000  # Request high-res versions
                }
                
                info_resp = self.session.get(commons_url, params=info_params, timeout=self.timeout)
                if info_resp.status_code != 200:
                    continue
                
                info_data = info_resp.json()
                pages = info_data.get('query', {}).get('pages', {})
                
                for page_id, page_data in pages.items():
                    imageinfo = page_data.get('imageinfo', [])
                    if not imageinfo:
                        continue
                    
                    info = imageinfo[0]
                    w = info.get('width', 0)
                    h = info.get('height', 0)
                    url = info.get('url', '')
                    
                    # Skip if already seen
                    if url in seen_urls:
                        continue
                    
                    # Filter: >=1000px, valid aspect ratio
                    if w >= 1000 and h > 0:
                        aspect = w / h
                        if 0.3 < aspect < 2.5:
                            title = page_data.get('title', '')
                            filename_lower = title.lower()
                            
                            # Skip obvious non-photos (logos, banners, album covers) and non-artist content
                            # FIX #5: Added exclusion terms to prevent wrong images (e.g., planets for "Plini")
                            skip_keywords = [
                                'logo', 'banner', 'icon', 'symbol', 'emblem', 'flag', 
                                'album cover', 'cover art',
                                'planet', 'geology', 'volcano', 'astronomy', 'space', 
                                'science', 'geography', 'nature', 'landform', 'crater'
                            ]
                            if any(kw in filename_lower for kw in skip_keywords):
                                continue
                            
                            images.append({
                                'url': url,
                                'source': 'Wikipedia',
                                'type': 'photo',
                                'width': w,
                                'height': h
                            })
                            seen_urls.add(url)
                            
                            # Limit to 10 images per search term
                            if len(images) >= 10:
                                break
                
                # If we found good results, stop searching
                if len(images) >= 10:
                    break
            
            return images
            
        except Exception as e:
            if _should_log_wikipedia(artist, 'error'):
                logger.debug(f"Wikimedia Commons search failed for {artist}: {e}")
            return []
    
    def _fetch_all_article_images(self, page_title: str, seen_urls: set) -> List[Dict[str, Any]]:
        """
        Get ALL images from Wikipedia article (not just infobox).
        This is Strategy B - finds 2-7 images on average.
        
        Args:
            page_title: Wikipedia page title
            seen_urls: Set of URLs already seen (for deduplication)
            
        Returns:
            List of high-quality image dicts from Wikipedia article
        """
        images = []
        try:
            url = "https://en.wikipedia.org/w/api.php"
            params = {
                'action': 'query',
                'format': 'json',
                'titles': page_title,
                'generator': 'images',
                'gimlimit': 50,  # Get up to 50 images from article
                'prop': 'imageinfo',
                'iiprop': 'url|size|dimensions',
                'iiurlwidth': 4000  # Request high-res versions
            }
            
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            pages = data.get('query', {}).get('pages', {})
            
            for page_id, page_data in pages.items():
                # Skip non-image pages
                if page_data.get('ns') != 6:  # Namespace 6 = File namespace
                    continue
                
                imageinfo = page_data.get('imageinfo', [])
                if not imageinfo:
                    continue
                
                info = imageinfo[0]
                w = info.get('width', 0)
                h = info.get('height', 0)
                url = info.get('url', '')
                
                # Skip if already seen
                if url in seen_urls:
                    continue
                
                # Filter: >=1000px, valid aspect ratio
                if w >= 1000 and h > 0:
                    aspect = w / h
                    if 0.3 < aspect < 2.5:
                        # Check if filename suggests it's an artist photo (not a logo/banner)
                        title = page_data.get('title', '')
                        filename_lower = title.lower()
                        
                        # Skip obvious non-photos
                        # FIX #5: Added exclusion terms to prevent wrong images (e.g., planets for "Plini")
                        skip_keywords = [
                            'logo', 'banner', 'icon', 'symbol', 'emblem', 'flag', 'album cover',
                            'planet', 'geology', 'volcano', 'astronomy', 'space',
                            'science', 'geography', 'nature', 'landform', 'crater'
                        ]
                        if any(kw in filename_lower for kw in skip_keywords):
                            continue
                        
                        images.append({
                            'url': url,
                            'source': 'Wikipedia',
                            'type': 'photo',
                            'width': w,
                            'height': h
                        })
                        seen_urls.add(url)
                        
                        # Limit to 10 images
                        if len(images) >= 10:
                            break
            
            return images
            
        except Exception as e:
            if _should_log_wikipedia(page_title, 'error'):
                logger.debug(f"Wikipedia article images fetch failed for '{page_title}': {e}")
            return []
    
    def _fetch_pageimages_infobox(self, page_title: str, seen_urls: set) -> List[Dict[str, Any]]:
        """
        Get main infobox image from Wikipedia (fallback strategy).
        This is Strategy C - usually only finds 0-1 images.
        
        Args:
            page_title: Wikipedia page title
            seen_urls: Set of URLs already seen (for deduplication)
            
        Returns:
            List of image dicts (usually 0-1 images)
        """
        images = []
        try:
            url = "https://en.wikipedia.org/w/api.php"
            params = {
                'action': 'query',
                'format': 'json',
                'titles': page_title,
                'prop': 'pageimages',
                'pithumbsize': 4000,  # Request up to 4000px
                'pilimit': 5
            }
            
            resp = self.session.get(url, params=params, timeout=self.timeout)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            pages = data.get('query', {}).get('pages', {})
            
            for page_id, page_data in pages.items():
                if page_data.get('thumbnail'):
                    thumb = page_data['thumbnail']
                    w = thumb.get('width', 0)
                    h = thumb.get('height', 0)
                    url = thumb.get('source', '')
                    
                    # Skip if already seen
                    if url in seen_urls:
                        continue
                    
                    # Filter: >=1000px, valid aspect ratio
                    if w >= 1000 and h > 0:
                        aspect = w / h
                        if 0.3 < aspect < 2.5:
                            images.append({
                                'url': url,
                                'source': 'Wikipedia',
                                'type': 'photo',
                                'width': w,
                                'height': h
                            })
                            seen_urls.add(url)
                            break  # Usually only one infobox image
            
            return images
            
        except Exception as e:
            if _should_log_wikipedia(page_title, 'error'):
                logger.debug(f"Wikipedia infobox fetch failed for '{page_title}': {e}")
            return []

