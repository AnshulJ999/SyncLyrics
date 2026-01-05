"""
QQ Music Lyrics Provider
Fetches synchronized lyrics from QQ Music (y.qq.com)
Supports both English and Chinese lyrics with translations
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

from typing import Optional, Dict, Any, List, Tuple
import requests
import base64
import json
import time
import random
import logging
from html import unescape
from .base import LyricsProvider
from logging_config import get_logger
from config import get_provider_config

logger = get_logger(__name__)

# Configure logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

class QQMusicProvider(LyricsProvider):
    """QQ Music lyrics provider"""
    
    # Minimum score threshold for confident match (title must match)
    MIN_CONFIDENCE_THRESHOLD = 40
    
    def __init__(self) -> None:
        """Initialize QQ Music provider with config settings"""
        super().__init__(provider_name="qq")
        
        # Get additional config settings if needed
        config = get_provider_config("qq")
        
        self.headers = {
            'Referer': 'https://y.qq.com/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Origin': 'https://y.qq.com'
        }
        self.session.headers.update(self.headers)
    
    def _score_result(self, song: Dict[str, Any], target_artist: str, target_title: str, 
                      target_album: str = None, target_duration: int = None) -> int:
        """
        Score a search result based on how well it matches the target song.
        Higher score = better match.
        
        Scoring:
        - Title exact match: +80
        - Title contains target: +50
        - Artist match: +30
        - Album match: +15
        - Duration within 5s: +10
        """
        score = 0
        
        # Normalize for comparison
        song_title = song.get('name', '').lower().strip()
        # QQ uses 'singer' instead of 'artists'
        song_artists = [s.get('name', '').lower().strip() for s in song.get('singer', [])]
        song_album = song.get('album', {}).get('name', '').lower().strip()
        # QQ duration is in seconds (interval field)
        song_duration_s = song.get('interval', 0)
        
        target_title_lower = target_title.lower().strip()
        target_artist_lower = target_artist.lower().strip()
        
        # Title scoring (most important)
        if song_title == target_title_lower:
            score += 80  # Exact match
        elif target_title_lower in song_title or song_title in target_title_lower:
            score += 50  # Partial match
        
        # Artist scoring
        if any(target_artist_lower in artist or artist in target_artist_lower for artist in song_artists):
            score += 30
        
        # Album scoring (if provided)
        if target_album:
            target_album_lower = target_album.lower().strip()
            if target_album_lower in song_album or song_album in target_album_lower:
                score += 15
        
        # Duration scoring (if provided, within 5 second tolerance)
        if target_duration and song_duration_s:
            if abs(song_duration_s - target_duration) <= 5:
                score += 10
        
        return score
    
    def _find_best_match(self, songs: list, artist: str, title: str, 
                         album: str = None, duration: int = None) -> tuple:
        """
        Find the best matching song from search results.
        
        Returns:
            tuple: (best_song, best_score) or (None, 0) if no songs
        """
        if not songs:
            return None, 0
        
        best_song = None
        best_score = 0
        
        for song in songs:
            score = self._score_result(song, artist, title, album, duration)
            if score > best_score:
                best_score = score
                best_song = song
        
        return best_song, best_score


    def _make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        """
        Make a request with retry logic and error handling
        
        Args:
            method (str): HTTP method
            url (str): Request URL
            **kwargs: Additional request parameters
            
        Returns:
            Optional[Dict]: JSON response or None if failed
        """
        max_retries = 4
        retry_delay = 1.5

        for attempt in range(max_retries):
            try:
                # Add random delay between requests
                time.sleep(random.uniform(0.5, 2))
                
                response = self.session.request(method, url, timeout=10, **kwargs)
                response.raise_for_status()
                
                # Handle QQ Music's JSONP responses
                content = response.text
                if content.startswith('callback('):
                    content = content[9:-1]
                elif content.startswith('MusicJsonCallback'):
                    content = content[content.find('(')+1:content.rfind(')')]
                
                return json.loads(content)
                
            except Exception as e:
                logger.error(f"QQ - Request attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                else:
                    logger.error("QQ - Max retries reached. Request failed.")
                    return None

    def _search_song(self, keyword: str) -> Optional[Dict[str, Any]]:
        """
        Search for a song on QQ Music
        
        Args:
            keyword (str): Search keyword
            
        Returns:
            Optional[Dict[str, Any]]: Search results or None if failed
        """
        url = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
        params = {
            'w': keyword,
            'format': 'json',
            'p': 1,
            'n': 10,
            'aggr': 1,
            'lossless': 1,
            'cr': 1,
            'new_json': 1,
            'platform': 'yqq.json'
        }
        
        return self._make_request('GET', url, params=params)

    def _get_raw_lyrics(self, song_mid: str) -> Optional[str]:
        """
        Get raw lyrics for a song using its song_mid
        
        Args:
            song_mid (str): QQ Music song ID
            
        Returns:
            Optional[str]: Raw lyrics text
        """
        url = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
        params = {
            'songmid': song_mid,
            'g_tk_new_20200303': '5381',
            'g_tk': '5381',
            'loginUin': '0',
            'hostUin': '0',
            'format': 'json',
            'inCharset': 'utf8',
            'outCharset': 'utf-8',
            'notice': '0',
            'platform': 'yqq.json',
            'needNewCode': '0'
        }
        
        result = self._make_request('GET', url, params=params)
        
        if not result or result.get('code') != 0:
            return None
            
        try:
            if 'lyric' in result:
                return base64.b64decode(result['lyric']).decode('utf-8')
        except Exception as e:
            logger.error(f"QQ - Error decoding lyrics: {e}")
        
        return None

    def _process_lyrics(self, lyrics_text: str) -> List[Tuple[float, str]]:
        """
        Process raw lyrics text into timed lyrics
        
        Args:
            lyrics_text (str): Raw lyrics text with timestamps
            
        Returns:
            List[Tuple[float, str]]: List of (timestamp, lyric) pairs
        """
        processed_lyrics = []
        metadata_tags = ['ti', 'ar', 'al', 'by', 'offset', 'length', 're', 've']
        
        for line in lyrics_text.split('\n'):
            # Skip empty lines or lines without proper format
            if not line.strip() or not line.startswith('[') or ']' not in line:
                continue
                
            # Extract time and text
            time_str = line[1:line.find(']')]
            text = line[line.find(']') + 1:].strip()
            
            # Decode HTML entities in the text (like &apos;)
            text = unescape(text)
            
            # Skip metadata lines
            if any(time_str.startswith(tag) for tag in metadata_tags):
                continue
                
            # Skip translation lines (usually contain '/')
            if '/' in text:
                continue
                
            try:
                if ':' in time_str:
                    m, s = time_str.split(':')
                    seconds = float(m) * 60 + float(s)
                    if text:
                        processed_lyrics.append((seconds, text))
            except Exception as e:
                logger.debug(f"QQ - Skipping invalid lyric line: {e}")
                continue
        
        return sorted(processed_lyrics, key=lambda x: x[0])

    def get_lyrics(self, artist: str, title: str, album: str = None, duration: int = None) -> Optional[Dict[str, Any]]:
        """
        Get synchronized lyrics for a song
        
        Args:
            artist (str): Artist name
            title (str): Song title
            album (str): Album name (optional, used for scoring)
            duration (int): Track duration in seconds (optional, used for scoring)
            
        Returns:
            Optional[Dict[str, Any]]: Dictionary with synced lyrics and metadata or None
        """
        try:
            search_term = self._format_search_term(artist, title)
            logger.info(f"QQ - Searching QQ Music for: {search_term}")
            
            results = self._search_song(search_term)
            if not results or 'data' not in results or 'song' not in results['data']:
                logger.info(f"QQ - No search results found for: {search_term}")
                return None
            
            songs = results['data']['song']['list']
            if not songs:
                logger.info(f"QQ - No songs found for: {search_term}")
                return None
            
            # Find best matching song using multi-factor scoring
            best_song, best_score = self._find_best_match(songs, artist, title, album, duration)
            
            # Use best match if confident, otherwise reject the result
            if best_score >= self.MIN_CONFIDENCE_THRESHOLD:
                song = best_song
                song_name = song.get('name', 'Unknown')
                song_artist = song['singer'][0]['name'] if song.get('singer') and len(song['singer']) > 0 else 'Unknown'
                logger.info(f"QQ - Selected '{song_name}' by '{song_artist}' (score: {best_score})")
            else:
                # No confident match - return None instead of accepting wrong lyrics
                logger.info(f"QQ - No confident match found (best score: {best_score}), first result: '{song_name}' by '{song_artist}', skipping")
                return None
                
                # [OLD FALLBACK - commented out for reference]
                # Previously fell back to first result regardless of match quality,
                # which caused wrong lyrics (e.g., Chinese songs for English instrumentals)
                # song = songs[0]
                # song_name = song.get('name', 'Unknown')
                # song_artist = song['singer'][0]['name'] if song.get('singer') and len(song['singer']) > 0 else 'Unknown'
                # logger.warning(f"QQ - Low confidence match (score: {best_score}), falling back to first result: '{song_name}' by '{song_artist}'")
            
            # Get lyrics - ensure song has 'mid' field
            if 'mid' not in song:
                logger.warning(f"QQ - Song missing 'mid' field: {song_name}")
                return None
            lyrics_text = self._get_raw_lyrics(song['mid'])
            if not lyrics_text:
                logger.info(f"QQ - No lyrics found for: {search_term}")
                return None
            
            # Process lyrics
            processed_lyrics = self._process_lyrics(lyrics_text)
            if processed_lyrics:
                return {
                    "lyrics": processed_lyrics,
                    "is_instrumental": False
                }
            return None
            
        except Exception as e:
            logger.error(f"Error getting lyrics from QQ Music: {e}")
            return None 