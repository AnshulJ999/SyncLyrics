"""
QQ Music Lyrics Provider
Fetches synchronized lyrics from QQ Music (y.qq.com)
Supports both English and Chinese lyrics with translations
"""

from typing import Optional, Dict, Any, List, Tuple
import requests
import base64
import json
import time
import random
import logging
from .base import LyricsProvider

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QQMusicProvider(LyricsProvider):
    """QQ Music lyrics provider"""
    
    def __init__(self) -> None:
        """Initialize provider with lowest priority"""
        super().__init__(name="QQ Music", priority=4)
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
        max_retries = 3
        retry_delay = 1

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
                logger.error(f"Request attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay * (attempt + 1))
                else:
                    logger.error("Max retries reached. Request failed.")
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
            logger.error(f"Error decoding lyrics: {e}")
        
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
                logger.debug(f"Skipping invalid lyric line: {e}")
                continue
        
        return sorted(processed_lyrics, key=lambda x: x[0])

    def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """
        Get synchronized lyrics for a song
        
        Args:
            artist (str): Artist name
            title (str): Song title
            
        Returns:
            Optional[List[Tuple[float, str]]]: List of (timestamp, lyric) pairs
                                             or None if lyrics not found
        """
        try:
            search_term = self._format_search_term(artist, title)
            logger.info(f"Searching QQ Music for: {search_term}")
            
            results = self._search_song(search_term)
            if not results or 'data' not in results or 'song' not in results['data']:
                logger.info(f"No search results found for: {search_term}")
                return None
            
            songs = results['data']['song']['list']
            if not songs:
                logger.info(f"No songs found for: {search_term}")
                return None
            
            # Get the first matching song
            song = songs[0]
            logger.info(f"Found song: {song['name']} - {song['singer'][0]['name']}")
            
            # Get lyrics
            lyrics_text = self._get_raw_lyrics(song['mid'])
            if not lyrics_text:
                logger.info(f"No lyrics found for: {search_term}")
                return None
            
            # Process lyrics
            processed_lyrics = self._process_lyrics(lyrics_text)
            return processed_lyrics if processed_lyrics else None
            
        except Exception as e:
            logger.error(f"Error getting lyrics from QQ Music: {e}")
            return None 