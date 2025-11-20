import asyncio
import logging
import json
import os
from typing import Optional, List, Tuple

from system_utils import get_current_song_meta_data
from providers.lrclib import LRCLIBProvider
from providers.netease import NetEaseProvider
from providers.spotify_lyrics import SpotifyLyrics
from providers.qq import QQMusicProvider
from providers.musicxmatch import MusicxmatchProvider
from config import LYRICS, DEBUG, FEATURES, DATABASE_DIR
from logging_config import get_logger

logger = get_logger(__name__)

# Initialize providers
# Priority Order:
# 1. LRCLib (Best, Open Source)
# 2. Spotify + Musicxmatch (Good, Synced - Race in parallel)
# 3. NetEase (Good coverage)
# 4. QQ Music (Fallback)
providers = [
    LRCLIBProvider(),      # Priority 1
    SpotifyLyrics(),       # Priority 2
    MusicxmatchProvider(), # Priority 2
    NetEaseProvider(),     # Priority 3
    QQMusicProvider()      # Priority 4
]

LATENCY_COMPENSATION = LYRICS.get("display", {}).get("latency_compensation", 0.1)
current_song_data = None
current_song_lyrics = None

# ==========================================
# NEW: Local Database Helper Functions
# ==========================================

def _get_db_path(artist: str, title: str) -> Optional[str]:
    """Generates a safe filename for storing lyrics locally."""
    try:
        # Remove illegal characters for filenames to prevent errors
        safe_artist = "".join([c for c in artist if c.isalnum() or c in " -_"]).strip()
        safe_title = "".join([c for c in title if c.isalnum() or c in " -_"]).strip()
        filename = f"{safe_artist} - {safe_title}.json"
        return str(DATABASE_DIR / filename)
    except Exception:
        return None

def _load_from_db(artist: str, title: str) -> Optional[list]:
    """Loads lyrics from disk, prioritizing highest-quality provider available."""
    if not FEATURES.get("save_lyrics_locally", False): return None
    
    db_path = _get_db_path(artist, title)
    if not db_path or not os.path.exists(db_path): return None
    
    try:
        with open(db_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # NEW FORMAT: Multi-provider storage
        if "saved_lyrics" in data and isinstance(data["saved_lyrics"], dict):
            saved_lyrics = data["saved_lyrics"]
            
            # Find the BEST provider available (lowest priority number = best)
            best_priority = 999
            best_lyrics = None
            best_provider = None
            
            for provider in providers:
                if provider.name in saved_lyrics:
                    if provider.priority < best_priority:
                        best_priority = provider.priority
                        best_lyrics = saved_lyrics[provider.name]
                        best_provider = provider.name
            
            if best_lyrics:
                logger.info(f"Loaded lyrics from Local DB: {best_provider} (Priority {best_priority})")
                return best_lyrics
        
        # LEGACY FORMAT: Single provider (backward compatibility)
        elif data.get('lyrics') and isinstance(data['lyrics'], list):
            source = data.get('source', 'Unknown')
            logger.info(f"Loaded lyrics from Local DB (legacy): {source}")
            return data['lyrics']
            
    except Exception as e:
        logger.error(f"Failed to load from Local DB: {e}")
    
    return None

def _save_to_db(artist: str, title: str, lyrics: list, source: str) -> None:
    """Saves found lyrics to disk with multi-provider support (merge mode)."""
    if not FEATURES.get("save_lyrics_locally", False) or not lyrics: return
    
    try:
        db_path = _get_db_path(artist, title)
        if not db_path: return
        
        # Start with base structure
        data = {
            "artist": artist,
            "title": title,
            "saved_lyrics": {}  # Multi-provider storage
        }
        
        # Load existing file if it exists (for merging)
        if os.path.exists(db_path):
            try:
                with open(db_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                    
                # Check if it's the NEW format (has "saved_lyrics" dict)
                if "saved_lyrics" in existing and isinstance(existing["saved_lyrics"], dict):
                    data = existing  # Keep all existing providers
                    
                # Migrate LEGACY format (single provider) to NEW format
                elif "lyrics" in existing and "source" in existing:
                    legacy_source = existing.get("source", "Unknown")
                    legacy_lyrics = existing.get("lyrics", [])
                    if legacy_lyrics:
                        data["saved_lyrics"][legacy_source] = legacy_lyrics
                        logger.info(f"Migrated legacy DB entry from {legacy_source}")
                        
            except Exception as e:
                logger.warning(f"Could not load existing DB, creating new: {e}")
        
        # Add/Update this provider's lyrics
        data["saved_lyrics"][source] = lyrics
        
        # Save merged data
        with open(db_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            
        logger.info(f"Saved {source} lyrics to DB (now has {len(data['saved_lyrics'])} providers)")
    except Exception as e:
        logger.error(f"Failed to save to DB: {e}")

# ==========================================
# Main Logic
# ==========================================

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
        artist = new_song_data["artist"]
        title = new_song_data["title"]
        
        # 1. Try Local DB First (Zero Latency)
        local_lyrics = _load_from_db(artist, title)
        if local_lyrics:
            current_song_lyrics = local_lyrics
        else:
            # 2. Try Internet (Smart Race)
            current_song_lyrics = await _get_lyrics(artist, title)

    current_song_data = new_song_data

async def _get_lyrics(artist: str, title: str):
    """
    Tries providers to find lyrics.
    
    Modes:
    1. Sequential: Tries one by one. Safe, but slow.
    2. Parallel (Smart): Tries all at once. 
       - Prioritizes High Quality (LRCLib/Spotify).
       - If Low Quality (QQ/NetEase) comes first, waits a configurable grace period for High Quality before giving up.
    """
    active_providers = [p for p in providers if p.enabled]
    sorted_providers = sorted(active_providers, key=lambda x: x.priority)

    # --- SEQUENTIAL MODE (Safe Mode) ---
    # This mode is used if Parallel Fetching is disabled in config
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
                    _save_to_db(artist, title, lyrics, provider.name) # Save result
                    return lyrics
            except Exception as e:
                logger.error(f"Error with {provider.name}: {e}")
        return None

    # --- PARALLEL MODE (Fast Mode with Smart Priority) ---
    tasks = []
    provider_map = {} # Map tasks to provider objects

    for provider in sorted_providers:
        # Wrap sync functions in thread, keep async functions as is
        if asyncio.iscoroutinefunction(provider.get_lyrics):
            coro = provider.get_lyrics(artist, title)
        else:
            coro = asyncio.to_thread(provider.get_lyrics, artist, title)
        
        task = asyncio.create_task(coro)
        tasks.append(task)
        provider_map[task] = provider

    if not tasks: return None
    
    pending = set(tasks)
    best_result = None
    best_provider_name = "Unknown"
    
    while pending:
        # Wait for the NEXT provider to finish (First Completed)
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        
        for task in done:
            try:
                lyrics = await task
                provider = provider_map.get(task)
                
                if lyrics:
                    # Case A: High Quality Provider (Priority 1 or 2)
                    # If this finishes, we take it immediately because it's the best.
                    if provider.priority <= 2:
                        logger.info(f"Found High Quality lyrics using {provider.name} (Priority {provider.priority})")
                        _save_to_db(artist, title, lyrics, provider.name)
                        
                        # Cancel all other running tasks to save resources
                        for p in pending: p.cancel()
                        return lyrics
                    
                    # Case B: Low Quality Provider (Priority 3 or 4)
                    # If this finishes first, we hold onto it but DON'T return yet.
                    # We want to give High Quality providers a chance.
                    if best_result is None:
                        best_result = lyrics
                        best_provider_name = provider.name
                        logger.info(f"Found backup lyrics using {provider.name}. Waiting for better...")
                        _save_to_db(artist, title, lyrics, provider.name)  # Save backup immediately

            except Exception:
                continue # Ignore errors from individual providers
        
        # If we have a result (backup), check if we should keep waiting or just use it
        if best_result and pending:
            # Check: Are any High Priority tasks still running?
            high_priority_pending = any(provider_map[t].priority <= 2 for t in pending)
            
            if not high_priority_pending:
                # No better providers left running. Return the backup.
                logger.info(f"No better providers left. Using {best_provider_name}.")
                _save_to_db(artist, title, best_result, best_provider_name)
                for p in pending: p.cancel()
                return best_result
            
            # High Quality providers are still running. Give them a configurable "Grace Period".
            # If they don't finish in time, we just take the backup.
            try:
                done_hq, pending_hq = await asyncio.wait(
                    pending,
                    timeout=LYRICS.get("smart_race_timeout", 3.0),
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Did anyone finish during the grace window?
                for task in done_hq:
                    try:
                        hq_lyrics = await task
                        provider = provider_map.get(task)
                        if hq_lyrics and provider.priority <= 2:
                             logger.info(f"Managed to upgrade to {provider.name} during grace period!")
                             _save_to_db(artist, title, hq_lyrics, provider.name)
                             for p in pending: p.cancel()
                             return hq_lyrics
                    except: pass
                
                # Time is up. High Quality took too long. Return backup.
                logger.info("Grace period over. Returning backup.")
                _save_to_db(artist, title, best_result, best_provider_name)
                for p in pending: p.cancel()
                return best_result

            except Exception:
                return best_result

    return best_result

# ==========================================
# Helper Functions (Unchanged)
# ==========================================

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
    return (
        safe_get_line(idx - 2),
        safe_get_line(idx - 1),
        safe_get_line(idx),
        safe_get_line(idx + 1),
        safe_get_line(idx + 2),
        safe_get_line(idx + 3)
    )