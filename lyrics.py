"""
Lyrics handling system for SyncLyrics
Handles fetching, storage, and timing of synchronized lyrics
"""

import asyncio
import json
import logging
import aiohttp
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any, Union
from urllib.parse import quote

from config import PROVIDERS, DATABASE_DIR, DEBUG, LYRICS
from system_utils import get_current_song_meta_data
from state_manager import get_state

# Configure logging
logger = logging.getLogger(__name__)

class LyricsProvider:
    """Base class for lyrics providers"""
    def __init__(self, name: str, priority: int):
        self.name = name
        self.priority = priority
        self._session = None
        
    async def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """Must be implemented by provider"""
        raise NotImplementedError

class LRCLibProvider(LyricsProvider):
    """LRCLib provider implementation"""
    def __init__(self):
        super().__init__("lrclib", PROVIDERS['lrclib']['priority'])
        self.base_url = PROVIDERS['lrclib']['base_url']
        self.timeout = PROVIDERS['lrclib'].get('timeout', 10)

    async def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """Get lyrics from LRCLib"""
        try:
            async with aiohttp.ClientSession() as session:
                # Try direct method first
                safe_artist = quote(artist.strip())
                safe_title = quote(title.strip())
                
                async with session.get(
                    f"{self.base_url}/get",
                    params={
                        "artist_name": safe_artist,
                        "track_name": safe_title
                    }
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and data.get("syncedLyrics"):
                            return self._process_lrc(data["syncedLyrics"])

                # Try search method if direct method fails
                async with session.get(
                    f"{self.base_url}/search",
                    params={"q": f"{safe_artist} {safe_title}"}
                ) as response:
                    if response.status != 200:
                        return None

                    results = await response.json()
                    if not results:
                        return None

                    song_id = str(results[0]["id"])
                    async with session.get(f"{self.base_url}/get/{song_id}") as response:
                        if response.status != 200:
                            return None

                        data = await response.json()
                        if data and data.get("syncedLyrics"):
                            return self._process_lrc(data["syncedLyrics"])

            return None
        except Exception as e:
            logger.error(f"LRCLib error: {e}")
            return None

    def _process_lrc(self, lyrics: str) -> Optional[List[Tuple[float, str]]]:
        """Process LRC format lyrics"""
        if not lyrics:
            return None

        processed = []
        try:
            for line in lyrics.split("\n"):
                if not line or "[" not in line:
                    continue

                time_start = line.find("[") + 1
                time_end = line.find("]")
                if time_start >= time_end:
                    continue

                time_str = line[time_start:time_end].strip()
                if ":" not in time_str:
                    continue

                m, s = time_str.split(":")
                seconds = float(m) * 60 + float(s)
                text = line[time_end + 1:].strip()

                if text:
                    processed.append((seconds, text))

            return sorted(processed) if processed else None
        except Exception:
            return None

class SpotifyProvider(LyricsProvider):
    """Spotify provider implementation"""
    def __init__(self):
        super().__init__("spotify", PROVIDERS['spotify']['priority'])
        self.base_url = PROVIDERS['spotify']['base_url']

    async def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """Get lyrics from Spotify"""
        try:
            # Get current song metadata for Spotify ID
            current_song = await get_current_song_meta_data()
            if not current_song or not current_song.get('spotify'):
                return None

            track_id = current_song['spotify'].get('track_id')
            if not track_id:
                return None

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.base_url,
                    params={"trackid": track_id, "format": "lrc"}
                ) as response:
                    if response.status != 200:
                        return None

                    data = await response.json()
                    if not data or data.get("error"):
                        return None

                    if data.get("syncType") == "LINE_SYNCED":
                        result = []
                        for line in data.get("lines", []):
                            if not line.get("words"):
                                continue
                            time_ms = line.get("startTimeMs", 0)
                            words = line["words"].strip()
                            if words:
                                result.append((float(time_ms) / 1000, words))
                        return result if result else None

            return None
        except Exception as e:
            logger.error(f"Spotify error: {e}")
            return None

class NetEaseProvider(LyricsProvider):
    """NetEase provider implementation"""
    def __init__(self):
        super().__init__("netease", PROVIDERS['netease']['priority'])
        self.base_url = PROVIDERS['netease']['base_url']
        self.headers = {
            "User-Agent": "Mozilla/5.0",
            "Cookie": PROVIDERS['netease'].get('cookie', '')
        }

    async def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """Get lyrics from NetEase"""
        try:
            async with aiohttp.ClientSession() as session:
                # Search for song
                search_query = quote(f"{artist} {title}")
                async with session.get(
                    f"{self.base_url}/search/pc",
                    params={
                        "s": search_query,
                        "type": 1,
                        "limit": 10
                    },
                    headers=self.headers
                ) as response:
                    if response.status != 200:
                        return None

                    data = await response.json()
                    songs = data.get("result", {}).get("songs", [])
                    if not songs:
                        return None

                    # Get lyrics for first matching song
                    song_id = songs[0]["id"]
                    async with session.get(
                        f"{self.base_url}/song/lyric",
                        params={
                            "id": song_id,
                            "lv": -1,
                            "kv": -1,
                            "tv": -1
                        },
                        headers=self.headers
                    ) as response:
                        if response.status != 200:
                            return None

                        data = await response.json()
                        if not data or not data.get("lrc", {}).get("lyric"):
                            return None

                        return self._process_lrc(data["lrc"]["lyric"])

        except Exception as e:
            logger.error(f"NetEase error: {e}")
            return None

    def _process_lrc(self, lyrics: str) -> Optional[List[Tuple[float, str]]]:
        """Process LRC format lyrics"""
        return LRCLibProvider._process_lrc(self, lyrics)

class LyricsDatabase:
    """Handle storing and retrieving lyrics"""
    def __init__(self):
        self.db_path = DATABASE_DIR
        self.db_path.mkdir(exist_ok=True)

    def _get_file_path(self, artist: str, title: str) -> Path:
        """Generate safe filename for lyrics"""
        safe_name = f"{artist} - {title}".replace('/', '_').replace('\\', '_')
        return self.db_path / f"{safe_name}.json"

    async def store_lyrics(self, artist: str, title: str, 
                         lyrics: List[Tuple[float, str]], 
                         provider: str) -> None:
        """Store lyrics from a provider"""
        try:
            file_path = self._get_file_path(artist, title)
            data = {}

            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

            data.setdefault('metadata', {}).update({
                'artist': artist,
                'title': title,
                'last_updated': datetime.now().isoformat()
            })

            data.setdefault('lyrics', {})[provider] = {
                'lyrics': lyrics,
                'fetched': datetime.now().isoformat()
            }

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            logger.error(f"Error storing lyrics: {e}")

    async def get_lyrics(self, artist: str, title: str, 
                        provider: Optional[str] = None) -> Optional[List[Tuple[float, str]]]:
        """Get lyrics from database"""
        try:
            file_path = self._get_file_path(artist, title)
            if not file_path.exists():
                return None

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            lyrics_data = data.get('lyrics', {})
            
            if provider:
                return lyrics_data.get(provider, {}).get('lyrics')
            
            for prov in sorted(lyrics_data.keys(), 
                             key=lambda x: next((p.priority for p in providers if p.name == x), 0),
                             reverse=True):
                if lyrics := lyrics_data[prov].get('lyrics'):
                    return lyrics
            return None

        except Exception as e:
            logger.error(f"Error reading lyrics: {e}")
            return None

# Initialize components
db = LyricsDatabase()
providers = [
    LRCLibProvider(),
    SpotifyProvider(),
    NetEaseProvider()
]

# Global state
current_song_data = None
current_lyrics = None

async def _update_song():
    """Update current song data and lyrics"""
    global current_song_data, current_lyrics

    new_song_data = await get_current_song_meta_data()
    if not new_song_data:
        current_song_data = None
        return

    need_lyrics = (
        current_song_data is None or
        new_song_data["artist"] != current_song_data["artist"] or
        new_song_data["title"] != current_song_data["title"]
    )

    if need_lyrics:
        # Try database first
        lyrics = await db.get_lyrics(new_song_data["artist"], new_song_data["title"])
        
        if not lyrics:
            # Try providers
            for provider in providers:
                try:
                    lyrics = await provider.get_lyrics(
                        new_song_data["artist"],
                        new_song_data["title"]
                    )
                    if lyrics:
                        await db.store_lyrics(
                            new_song_data["artist"],
                            new_song_data["title"],
                            lyrics,
                            provider.name
                        )
                        break
                except Exception as e:
                    logger.error(f"Provider {provider.name} error: {e}")
                    continue

        current_lyrics = lyrics

    current_song_data = new_song_data

def _find_current_lyric_index(delta: float = 0.1) -> int:
    """Find current lyric index"""
    if current_lyrics and current_song_data:
        position = current_song_data["position"] + delta
        
        # Handle start of song
        if position < current_lyrics[0][0]:
            return 0
            
        for i in range(len(current_lyrics) - 1):
            if current_lyrics[i][0] <= position < current_lyrics[i + 1][0]:
                return i
        
        # Handle end of song
        if position >= current_lyrics[-1][0]:
            return len(current_lyrics) - 1
            
    return -1

async def get_timed_lyrics(delta: float = 0) -> Optional[str]:
    """Get current lyric with timing"""
    await _update_song()
    index = _find_current_lyric_index(delta)
    
    if index == -1:
        return None
        
    return current_lyrics[index][1]

async def get_timed_lyrics_previous_and_next() -> Union[Tuple[str, ...], str]:
    """Get previous, current, and next lyrics"""
    await _update_song()
    
    if not current_song_data:
        return "No song playing"
        
    if not current_lyrics:
        return "Lyrics not found"
        
    index = _find_current_lyric_index()
    if index == -1:
        return "Lyrics not found"
        
    # Get 2 lines before and 2 lines after
    lines = []
    for i in range(index - 2, index + 4):
        if 0 <= i < len(current_lyrics):
            lines.append(current_lyrics[i][1])
        else:
            lines.append("-")
            
    return tuple(lines)