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
            backgrounds = data.get('artistbackground', [])
            # Sort by likes (descending) using safe helper function to prevent crashes on malformed data
            backgrounds.sort(key=safe_likes, reverse=True)
            
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
            # Sort by likes for quality ranking using safe helper function
            thumbs = data.get('artistthumb', [])
            thumbs.sort(key=safe_likes, reverse=True)
            
            for thumb in thumbs:
                if isinstance(thumb, dict) and thumb.get('url'):
                    images.append({
                        'url': thumb['url'],
                        'source': 'FanArt.tv',
                        'type': 'thumbnail',
                        'width': 1000,
                        'height': 1000
                    })
                
            # HD Music Logos (Transparent PNG logos, typically 800x310)
            for logo in data.get('hdmusiclogo', []):
                if isinstance(logo, dict) and logo.get('url'):
                    images.append({
                        'url': logo['url'],
                        'source': 'FanArt.tv',
                        'type': 'logo',
                        'width': 800,
                        'height': 310
                    })
            
            # Album Covers (High-quality album artwork, typically 1000x1000+)
            # Only fetch if enabled (can be disabled if too many duplicates with album art DB)
            if self.enable_fanart_albumcover:
                # Sort by likes for quality ranking (get best album covers first) using safe helper function
                album_covers = data.get('albumcover', [])
                album_covers.sort(key=safe_likes, reverse=True)
                
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
        Often provides 1500-5000px images for popular artists.
        Free, no API key required, but slower than other sources (2-3 API calls).
        
        Args:
            artist: Artist name to search for
            
        Returns:
            List of image dicts with Wikipedia/Wikimedia images
        """
        try:
            # Step 1: Search Wikipedia for artist page
            search_url = "https://en.wikipedia.org/w/api.php"
            search_params = {
                'action': 'query',
                'format': 'json',
                'list': 'search',
                'srsearch': f"{artist} musician",  # Add "musician" to refine search and avoid false matches
                'srlimit': 1,
                'srnamespace': 0  # Only search in main namespace (articles, not talk pages)
            }
            
            resp = self.session.get(search_url, params=search_params, timeout=self.timeout)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            if not data.get('query', {}).get('search'):
                return []
            
            # Get page title from search results
            page_title = data['query']['search'][0]['title']
            
            # VALIDATION: Check if page title matches artist name to prevent wrong artist matches
            # This prevents cases like "Drake Bell" returning "Drake" or "Paul" returning "Paul the Apostle"
            artist_lower = artist.lower()
            title_lower = page_title.lower()
            # Check if artist name appears in title or vice versa (handles "The Beatles" vs "Beatles")
            if artist_lower not in title_lower and title_lower not in artist_lower:
                logger.debug(f"Wikipedia: Page title '{page_title}' doesn't match artist '{artist}', skipping to avoid wrong artist")
                return []
            
            # Step 2: Get page images (requesting high resolution)
            images_url = "https://en.wikipedia.org/w/api.php"
            images_params = {
                'action': 'query',
                'format': 'json',
                'titles': page_title,
                'prop': 'pageimages',
                'pithumbsize': 4000,  # Request up to 4000px (Wikipedia's max for thumbnails)
                'pilimit': 1  # Only get the main infobox image (usually the best artist photo)
            }
            
            resp = self.session.get(images_url, params=images_params, timeout=self.timeout)
            if resp.status_code != 200:
                return []
            
            data = resp.json()
            pages = data.get('query', {}).get('pages', {})
            
            images = []
            for page_id, page_data in pages.items():
                # Main infobox image (usually highest quality portrait photo)
                if page_data.get('thumbnail'):
                    thumb = page_data['thumbnail']
                    width = thumb.get('width', 0)
                    height = thumb.get('height', 0)
                    
                    # Filter 1: Minimum size - filter out tiny images/icons
                    # Only include images that are at least 300px wide (likely to be actual photos)
                    if width > 300:
                        # Filter 2: Aspect ratio - filter out logos, banners, and non-portrait images
                        # We want artist photos, typically between 0.4:1 (tall) and 2.0:1 (wide)
                        # This prevents logos (very wide, e.g., 5:1) and banners (very tall, e.g., 0.2:1)
                        if height > 0:
                            aspect_ratio = width / height
                            # Accept reasonable aspect ratios for photos (portrait to landscape)
                            if 0.4 < aspect_ratio < 2.0:
                                images.append({
                                    'url': thumb['source'],
                                    'source': 'Wikipedia',
                                    'type': 'photo',
                                    'width': width,
                                    'height': height
                                })
                            else:
                                logger.debug(f"Wikipedia: Skipped image with suspicious aspect ratio {aspect_ratio:.2f} ({width}x{height}) - likely logo/banner, not photo")
                        else:
                            # Height is 0 or missing - skip to avoid division by zero
                            logger.debug(f"Wikipedia: Skipped image with invalid height ({width}x{height})")
            
            if images:
                logger.debug(f"Wikipedia: Found {len(images)} image(s) for {artist} (resolution: {images[0].get('width', 0)}x{images[0].get('height', 0)})")
            
            return images
            
        except Exception as e:
            logger.debug(f"Wikipedia fetch failed for {artist}: {e}")
            return []

