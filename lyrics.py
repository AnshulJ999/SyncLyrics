import asyncio
import logging
from typing import Optional, List, Tuple

from system_utils import get_current_song_meta_data
from providers.lrclib import LRCLIBProvider
from providers.netease import NetEaseProvider
from providers.spotify_lyrics import SpotifyLyrics
from providers.qq import QQMusicProvider
from config import LYRICS, DEBUG, FEATURES
from logging_config import get_logger

logger = get_logger(__name__)

# Initialize providers
providers = [
    LRCLIBProvider(),   # Priority 1
    NetEaseProvider(),  # Priority 3
    SpotifyLyrics(),    # Priority 2
    QQMusicProvider()   # Priority 4
]

LATENCY_COMPENSATION = LYRICS.get("display", {}).get("latency_compensation", 0.1)
current_song_data = None
current_song_lyrics = None

async def _update_song():
    """Updates current song data and fetches lyrics if changed."""
    global current_song_lyrics, current_song_data

    new_song_data = await get_current_song_meta_data()

    # If no song or empty song, clear lyrics
    if new_song_data is None or (not new_song_data["artist"].strip() and not new_song_data["title"].strip()):
        current_song_lyrics = None
        current_song_data = new_song_data
        return

    # Check if song changed
    should_fetch_lyrics = current_song_data is None or (
        current_song_data["artist"] != new_song_data["artist"] or
        current_song_data["title"] != new_song_data["title"]
    )

    if should_fetch_lyrics:
        current_song_lyrics = await _get_lyrics(new_song_data["artist"], new_song_data["title"])

    current_song_data = new_song_data

async def _get_lyrics(artist: str, title: str):
    """
    Tries providers.
    If FEATURES['parallel_provider_fetch'] is True, tries all at once.
    """
    active_providers = [p for p in providers if p.enabled]
    sorted_providers = sorted(active_providers, key=lambda x: x.priority)

    # --- SEQUENTIAL MODE (Safe Mode) ---
    if not FEATURES.get("parallel_provider_fetch", True):
        for provider in sorted_providers:
            try:
                # Check if the method is async or sync and handle accordingly
                if asyncio.iscoroutinefunction(provider.get_lyrics):
                    lyrics = await provider.get_lyrics(artist, title)
                else:
                    lyrics = await asyncio.to_thread(provider.get_lyrics, artist, title)
                
                if lyrics:
                    logger.info(f"Found lyrics using {provider.name}")
                    return lyrics
            except Exception as e:
                logger.error(f"Error with {provider.name}: {e}")
        return None

    # --- PARALLEL MODE (Fast Mode) ---
    tasks = []
    provider_map = {} # Map tasks to provider names

    for provider in sorted_providers:
        # Wrap sync functions in thread, keep async functions as is
        if asyncio.iscoroutinefunction(provider.get_lyrics):
            coro = provider.get_lyrics(artist, title)
        else:
            coro = asyncio.to_thread(provider.get_lyrics, artist, title)
        
        task = asyncio.create_task(coro)
        tasks.append(task)
        provider_map[task] = provider.name

    # Wait for the FIRST one to complete
    if not tasks: return None
    
    # Run untill first success
    pending = set(tasks)
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        
        for task in done:
            try:
                lyrics = await task
                if lyrics:
                    provider_name = provider_map.get(task, "Unknown")
                    logger.info(f"Found lyrics using {provider_name} (Parallel)")
                    
                    # Cancel all other running tasks
                    for p in pending:
                        p.cancel()
                    return lyrics
            except Exception as e:
                provider_name = provider_map.get(task, "Unknown")
                logger.warning(f"{provider_name} failed during parallel fetch: {e}")
                # Loop continues to check other tasks or wait for pending
                
    return None

def _find_current_lyric_index(delta: float = LATENCY_COMPENSATION) -> int:
    """Returns index of current lyric line based on song position."""
    if current_song_lyrics is None or current_song_data is None:
        return -1
        
    position = current_song_data.get("position", 0)
    
    # 1. Before first lyric
    if position + delta < current_song_lyrics[0][0]:
        return -2
        
    # 2. After last lyric
    last_lyric_time = current_song_lyrics[-1][0]
    if position + delta > last_lyric_time + 6.0: # End song after 6s
        return -3
    
    # 3. Find current line
    for i in range(len(current_song_lyrics) - 1):
        if current_song_lyrics[i][0] <= position + delta < current_song_lyrics[i + 1][0]:
            return i
            
    # 4. If we are at the very last line
    if position + delta >= last_lyric_time:
        return len(current_song_lyrics) - 1

    return -1

async def get_timed_lyrics(delta: int = 0) -> str:
    """Returns just the current line text."""
    await _update_song()
    lyric_index = _find_current_lyric_index(delta)
    if lyric_index == -1: return "Lyrics not found"
    if lyric_index < 0: return "..."
    return current_song_lyrics[lyric_index][1]

async def get_timed_lyrics_previous_and_next() -> tuple:
    """Returns tuple of 6 lines: (prev2, prev1, current, next1, next2, next3)."""
    
    def safe_get_line(idx):
        if current_song_lyrics and 0 <= idx < len(current_song_lyrics):
            return current_song_lyrics[idx][1] or "♪"
        return ""

    await _update_song()
    
    if current_song_data is None: return "No song playing"
    if current_song_lyrics is None: return "Lyrics not found"
    
    idx = _find_current_lyric_index()
    
    # Handle instrumental / intro
    if idx == -1:
        # Look ahead to see what the first lyric is
        return ("", "", "♪", safe_get_line(0), safe_get_line(1), safe_get_line(2))
    
    if idx == -2: # Intro
        return ("", "", "Intro", safe_get_line(0), safe_get_line(1), safe_get_line(2))
        
    if idx == -3: # Outro
        return (safe_get_line(len(current_song_lyrics)-1), "End", "", "", "", "")

    # Standard return
    return (
        safe_get_line(idx - 2),
        safe_get_line(idx - 1),
        safe_get_line(idx),
        safe_get_line(idx + 1),
        safe_get_line(idx + 2),
        safe_get_line(idx + 3)
    )