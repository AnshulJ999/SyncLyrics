import requests as req
import logging

from system_utils import get_current_song_meta_data
from providers.lrclib import LRCLIBProvider
from providers.netease import NetEaseProvider
from providers.spotify_lyrics import SpotifyLyrics
from config import LYRICS, DEBUG
from providers.qq import QQMusicProvider
from logging_config import get_logger
from logging_config import get_logger, setup_logging  # Import setup_logging
from providers.spotify_sync import SpotifyLyricsSync
from providers.spotify_api import SpotifyAPI  # Import our Spotify client
from typing import Optional, List, Tuple

# logger = logging.getLogger(__name__)

setup_logging()  # Call setup_logging before initializing providers

logger = get_logger(__name__)

# Initialize providers
providers = [
    LRCLIBProvider(),  # Priority 1
    NetEaseProvider(), # Priority 3
    SpotifyLyrics(),    # Priority 2
    QQMusicProvider()    # Priority 4

]

LATENCY_COMPENSATION = LYRICS.get("display", {}).get("latency_compensation", 0.1)

logger.info(f"Using latency compensation: {LATENCY_COMPENSATION}s")
logger.info(f"Using update interval: {LYRICS.get('display', {}).get('update_interval', 0.1)}s")

current_song_data = None
current_song_lyrics = None


async def _update_song():
    """
    This function updates the current song data and lyrics (the global variables).
    """

    global current_song_lyrics, current_song_data

    new_song_data = await get_current_song_meta_data()

    should_fetch_lyrics = new_song_data is not None and (
        current_song_data is None or (
            current_song_data["artist"] != new_song_data["artist"] or
            current_song_data["title"] != new_song_data["title"]
        ))

    if should_fetch_lyrics:
        current_song_lyrics = _get_lyrics(new_song_data["artist"], new_song_data["title"])
            
    current_song_data = new_song_data


def _get_lyrics(artist: str, title: str):
    """Try each provider in order of priority"""
    # Sort providers by priority (lower number = higher priority)
    sorted_providers = sorted(providers, key=lambda x: x.priority)
    
    for provider in sorted_providers:
        try:
            lyrics = provider.get_lyrics(artist, title)
            if lyrics:
                logger.info(f"Found lyrics using {provider.name}")
                return lyrics
        except Exception as e:
            logger.error(f"Error with {provider.name}: {str(e)}")
            continue
    
    return None


def _find_current_lyric_index(delta: float = LATENCY_COMPENSATION) -> int: # latency compensation - positive=earlier, negative=later. Find it in config.py 
    """
    This function returns the index of the current lyric in the current_song_lyrics list.

        Find the current lyric index with special handling for start/end/breaks
    
    Args:
        delta (float): Latency compensation value
        
    Returns:
        int: Current lyric index or special values:
            -2: Before first lyric
            -3: After last lyric
            -1: Between lyrics (instrumental)
    """
    if current_song_lyrics is None or current_song_data is None:
        return -1
        
    time = current_song_data["position"]
    
    # Before first lyric
    if time + delta < current_song_lyrics[0][0]:
        return -2
        
    # After last lyric
    last_lyric_time = current_song_lyrics[-1][0]
    if time + delta > last_lyric_time + 6.0:  # Show last lyric for 6 seconds
        return -3
    elif time + delta > last_lyric_time:
        return len(current_song_lyrics) - 1  # Show the last lyric

# old method    if time + delta > current_song_lyrics[-1][0]:
#        return -3
    
    # Find current lyric
    for i in range(len(current_song_lyrics) - 1):
        if current_song_lyrics[i][0] <= time + delta < current_song_lyrics[i + 1][0]:
            return i
            
    return -1  # Instrumental/between lyrics


async def get_timed_lyrics(delta: int = 0) -> str: # delta for latency compensation doesn't work rn
    """
    This function returns the current lyric of the song.

    Args:
        delta (int, optional): The delay to take into account when calculating the lyric. Defaults to 0.

    Returns:
        str: The current lyric of the song. If a lyric is not found, "Lyrics not found" is returned.
    """

    await _update_song()
    lyric_index = _find_current_lyric_index(delta)
    if lyric_index == -1: return "Lyrics not found"
    return current_song_lyrics[lyric_index][1]


async def get_timed_lyrics_previous_and_next() -> tuple[str, ...] | str:
    """
    This function returns multiple lines of lyrics, including previous and next lines.
    Returns:
        tuple[str, ...] | str: Multiple lines of lyrics centered around the current line,
                              or "Lyrics not found" if no lyrics are available.
    """
    def _lyric_representation(lyric_index: int) -> str:
        """Get lyric at index with bounds checking"""
        if current_song_lyrics is None:
            return "-"
        if lyric_index < 0 or lyric_index >= len(current_song_lyrics):
            return "-"
        return current_song_lyrics[lyric_index][1] or "-"

    await _update_song()
    
    # Check if we have valid song data and lyrics
    if current_song_data is None:
        return "No song playing"
    
    if current_song_lyrics is None:
        return "Lyrics not found"
    # Return 6 lines total: 2 previous, current, and 3 next                
    lyric_index = _find_current_lyric_index()
    
    # Instrumental/between lyrics
    if lyric_index == -1:
        # Find nearest lyrics
        time = current_song_data.get("position", 0)  # Use get() with default value
        for i in range(len(current_song_lyrics)):
            if current_song_lyrics[i][0] > time:
                prev_index = max(0, i - 1)
                return (
                    _lyric_representation(prev_index - 1),
                    _lyric_representation(prev_index),
                    "♪",
                    _lyric_representation(i),
                    _lyric_representation(i + 1),
                    _lyric_representation(i + 2)
                )
    
    # Before first lyric
    if lyric_index == -2:
        return (
            "",
            "",
            "♪",
            _lyric_representation(0),
            _lyric_representation(1),
            _lyric_representation(2)
        )
    
    # After last lyric    
    if lyric_index == -3:
        last_index = len(current_song_lyrics) - 1
        return (
            _lyric_representation(last_index - 1),
            _lyric_representation(last_index - 0),
            "♪",
            "",
            "",
            ""
        )
    
    # Normal case
    return (
        _lyric_representation(lyric_index - 2),
        _lyric_representation(lyric_index - 1),
        _lyric_representation(lyric_index),
        _lyric_representation(lyric_index + 1),
        _lyric_representation(lyric_index + 2),
        _lyric_representation(lyric_index + 3)
    )


class LyricsManager:
    def __init__(self):
        self.spotify_client = SpotifyAPI()  # Initialize Spotify client
        self.spotify_sync = SpotifyLyricsSync(self.spotify_client)
        self.current_lyrics: Optional[List[Tuple[float, str]]] = None
        self.position: float = 0
        
    async def initialize(self):
        """Initialize the lyrics manager"""
        try:
            await self.spotify_sync.initialize()
            logger.info("Lyrics manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize lyrics manager: {e}")
            raise
        
    async def update_lyrics(self) -> Optional[str]:
        """Update lyrics based on position"""
        try:
            position = self.spotify_sync.get_position()
            
            if position is None or self.current_lyrics is None:
                return None
                
            current_line = None
            # Find matching lyric line
            for timestamp, line in self.current_lyrics:
                if timestamp > position + 0.1:  # 100ms lookahead
                    break
                current_line = line
                
            return current_line
            
        except Exception as e:
            logger.error(f"Error updating lyrics: {e}")
            return None