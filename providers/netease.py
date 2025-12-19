"""NetEase Provider (music.163.com) for synchronized lyrics"""

import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

from typing import Optional, Dict, Any, List, Tuple

import requests as req
import logging
from .base import LyricsProvider
from config import get_provider_config
from logging_config import get_logger

logger = get_logger(__name__)
# logger = logging.getLogger(__name__)

class NetEaseProvider(LyricsProvider):
    # Minimum score threshold for confident match (title must match)
    MIN_CONFIDENCE_THRESHOLD = 65
    
    def __init__(self):
        """Initialize NetEase provider with config settings"""
        super().__init__(provider_name="netease")
        
        # Get config settings
        config = get_provider_config("netease")
        
        self.headers = {
            "cookie": config.get("cookie", "NMTID=00OAVK3xqDG726ITU6jopU6jF2yMk0AAAGCO8l1BA; JSESSIONID-WYYY=8KQo11YK2GZP45RMlz8Kn80vHZ9%2FGvwzRKQXXy0iQoFKycWdBlQjbfT0MJrFa6hwRfmpfBYKeHliUPH287JC3hNW99WQjrh9b9RmKT%2Fg1Exc2VwHZcsqi7ITxQgfEiee50po28x5xTTZXKoP%2FRMctN2jpDeg57kdZrXz%2FD%2FWghb%5C4DuZ%3A1659124633932; _iuqxldmzr_=32; _ntes_nnid=0db6667097883aa9596ecfe7f188c3ec,1659122833973; _ntes_nuid=0db6667097883aa9596ecfe7f188c3ec; WNMCID=xygast.1659122837568.01.0; WEVNSM=1.0.0; WM_NI=CwbjWAFbcIzPX3dsLP%2F52VB%2Bxr572gmqAYwvN9KU5X5f1nRzBYl0SNf%2BV9FTmmYZy%2FoJLADaZS0Q8TrKfNSBNOt0HLB8rRJh9DsvMOT7%2BCGCQLbvlWAcJBJeXb1P8yZ3RHA%3D; WM_NIKE=9ca17ae2e6ffcda170e2e6ee90c65b85ae87b9aa5483ef8ab3d14a939e9a83c459959caeadce47e991fbaee82af0fea7c3b92a81a9ae8bd64b86beadaaf95c9cedac94cf5cedebfeb7c121bcaefbd8b16dafaf8fbaf67e8ee785b6b854f7baff8fd1728287a4d1d246a6f59adac560afb397bbfc25ad9684a2c76b9a8d00b2bb60b295aaafd24a8e91bcd1cb4882e8beb3c964fb9cbd97d04598e9e5a4c6499394ae97ef5d83bd86a3c96f9cbeffb1bb739aed9ea9c437e2a3; WM_TID=AAkRFnl03RdABEBEQFOBWHCPOeMra4IL; playerid=94262567")  # Get cookie from config
        }
    
    def _score_result(self, song: Dict[str, Any], target_artist: str, target_title: str, 
                      target_album: str = None, target_duration: int = None) -> int:
        """
        Score a search result based on how well it matches the target song.
        Higher score = better match.
        
        Scoring:
        - Title exact match: +80
        - Title contains target: +50
        - Artist match: +35
        - Album match: +15
        - Duration within 5s: +10
        """
        score = 0
        
        # Normalize for comparison
        song_title = song.get('name', '').lower().strip()
        song_artists = [a.get('name', '').lower().strip() for a in song.get('artists', [])]
        song_album = song.get('album', {}).get('name', '').lower().strip()
        song_duration_s = song.get('duration', 0) / 1000 if song.get('duration') else None
        
        target_title_lower = target_title.lower().strip()
        target_artist_lower = target_artist.lower().strip()
        
        # Title scoring (most important)
        if song_title == target_title_lower:
            score += 50  # Exact match
        elif target_title_lower in song_title or song_title in target_title_lower:
            score += 40  # Partial match
        
        # Artist scoring
        if any(target_artist_lower in artist or artist in target_artist_lower for artist in song_artists):
            score += 40
        
        # Album scoring (if provided)
        if target_album:
            target_album_lower = target_album.lower().strip()
            if target_album_lower in song_album or song_album in target_album_lower:
                score += 20
        
        # Duration scoring (if provided, within 5 second tolerance)
        if target_duration and song_duration_s:
            if abs(song_duration_s - target_duration) <= 3:
                score += 15
        
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
    
    def get_lyrics(self, artist: str, title: str, album: str = None, duration: int = None) -> Optional[Dict[str, Any]]:
        search_term = f"{artist} {title}"
        try:
            # Search for song
            search_response = req.get(
                "https://music.163.com/api/search/pc",
                params={"s": search_term, "limit": 10, "type": 1},
                headers=self.headers,
                timeout=10
            ).json()
            
            songs = search_response.get("result", {}).get("songs")
            if not songs:
                logger.info(f"NetEase - No search results found for: {search_term}")
                return None
            
            # Find best matching song using multi-factor scoring
            best_song, best_score = self._find_best_match(songs, artist, title, album, duration)
            
            # Use best match if confident, otherwise fall back to first result
            if best_score >= self.MIN_CONFIDENCE_THRESHOLD:
                selected_song = best_song
                song_name = selected_song.get('name', 'Unknown')
                song_artist = ', '.join([a.get('name', '') for a in selected_song.get('artists', [])])
                logger.info(f"NetEase - Selected '{song_name}' by '{song_artist}' (score: {best_score})")
            else:
                # Reject low confidence matches to avoid returning wrong lyrics
                # This prevents issues like returning 5SOS "Bad Omens" for Bad Omens band songs
                logger.info(f"NetEase - Rejecting low confidence match (score: {best_score}, threshold: {self.MIN_CONFIDENCE_THRESHOLD}) for: {search_term}")
                return None
            
            # Get lyrics for selected song - ensure song has 'id' field
            if 'id' not in selected_song:
                logger.warning(f"NetEase - Song missing 'id' field: {song_name}")
                return None
            track_id = selected_song["id"]

            # Fetch lyrics with YRC (word-synced) parameters
            lyrics_response = req.get(
                "https://music.163.com/api/song/lyric",
                params={
                    "id": track_id,
                    "lv": 1,   # Line-synced LRC lyrics
                    "yv": 1,   # YRC word-synced lyrics
                    "kv": 1    # Karaoke lyrics (alternative word-sync)
                },
                headers=self.headers,
                timeout=10
            ).json()
            
            # Get line-synced LRC lyrics
            lyrics_text = lyrics_response.get("lrc", {}).get("lyric")
            line_synced_lyrics = None
            if lyrics_text:
                line_synced_lyrics = self._parse_lrc(lyrics_text)
            
            # Get word-synced YRC lyrics
            yrc_text = lyrics_response.get("yrc", {}).get("lyric")
            word_synced_lyrics = None
            if yrc_text:
                word_synced_lyrics = self._parse_yrc(yrc_text)
                if word_synced_lyrics:
                    logger.info(f"NetEase - Got {len(word_synced_lyrics)} word-synced lines")
            
            if not line_synced_lyrics:
                logger.info(f"NetEase - No lyrics found for: {search_term}")
                return None
            
            result = {
                "lyrics": line_synced_lyrics,
                "is_instrumental": False
            }
            
            # Include word-synced data if available
            if word_synced_lyrics:
                result["word_synced_lyrics"] = word_synced_lyrics
            
            return result
            
        except Exception as e:
            logger.error(f"NetEase - Error fetching lyrics from NetEase for {search_term}: {str(e)}")
            return None
    
    def _parse_lrc(self, lyrics_text: str) -> Optional[List[Tuple[float, str]]]:
        """Parse standard LRC format into list of (timestamp, text) tuples."""
        processed_lyrics = []
        for line in lyrics_text.split("\n"):
            try:
                if not line.startswith("[") or "]" not in line:
                    continue
                time_part = line[1:line.find("]")]
                
                # Skip meta tags
                if not time_part or not time_part[0].isdigit():
                    continue
                
                m, s = time_part.split(":")
                seconds = float(m) * 60 + float(s)
                text = line[line.find("]") + 1:].strip()
                if text:
                    processed_lyrics.append((seconds, text))
            except ValueError:
                continue
        
        return processed_lyrics if processed_lyrics else None
    
    def _parse_yrc(self, yrc_text: str) -> Optional[List[Dict[str, Any]]]:
        """
        Parse NetEase YRC format into structured word-synced data.
        
        Input format:
        [ch:0]
        [16240,3600](16240,270,0)We (16510,210,0)were (16720,570,0)both...
        
        Where:
        - [16240,3600] = Line start time (ms), duration (ms)
        - (16240,270,0) = Word start time (ms), duration (ms), unknown flag
        - The time in () is ABSOLUTE, not offset from line start
        
        Output format:
        [
            {
                "start": 16.24,
                "end": 19.84,
                "text": "We were both young...",
                "words": [
                    {"word": "We", "time": 0},       # time = offset from line start
                    {"word": "were", "time": 0.27},
                    {"word": "both", "time": 0.48},
                    ...
                ]
            },
            ...
        ]
        """
        import re
        
        result = []
        
        for line in yrc_text.split("\n"):
            line = line.strip()
            
            # Skip empty lines and metadata lines
            if not line or line.startswith("[ch:"):
                continue
            
            # Parse line header: [start_ms,duration_ms]
            line_match = re.match(r'\[(\d+),(\d+)\](.+)', line)
            if not line_match:
                continue
            
            line_start_ms = int(line_match.group(1))
            line_duration_ms = int(line_match.group(2))
            line_content = line_match.group(3)
            
            line_start = line_start_ms / 1000.0
            line_end = (line_start_ms + line_duration_ms) / 1000.0
            
            # Parse words: (start_ms,duration_ms,flag)text
            word_pattern = r'\((\d+),(\d+),\d+\)([^(]*)'
            word_matches = re.findall(word_pattern, line_content)
            
            words = []
            full_text_parts = []
            
            for word_start_ms, word_duration_ms, word_text in word_matches:
                word_start_ms = int(word_start_ms)
                word_duration_ms = int(word_duration_ms)
                
                # Calculate offset from line start (convert to seconds)
                offset = (word_start_ms - line_start_ms) / 1000.0
                
                # Convert duration to seconds
                duration = word_duration_ms / 1000.0
                
                # Clean up the word text
                word = word_text.strip()
                if word:
                    words.append({
                        "word": word,
                        "time": round(offset, 3),
                        "duration": round(duration, 3)  # Explicit duration from YRC format
                    })
                    full_text_parts.append(word)
            
            if words:
                result.append({
                    "start": round(line_start, 3),
                    "end": round(line_end, 3),
                    "text": " ".join(full_text_parts),
                    "words": words
                })
        
        return result if result else None