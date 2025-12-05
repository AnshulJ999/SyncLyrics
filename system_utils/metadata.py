"""
Main metadata orchestrator for system_utils package.
Coordinates fetching song metadata from multiple sources (Windows, Spotify, GNOME).

Dependencies: state, helpers, image, album_art, windows, spotify, gnome
"""
from __future__ import annotations
import os
import platform
import time
import asyncio
import shutil
import uuid
import requests
from pathlib import Path
from typing import Optional, Dict, Any, List

import config
from . import state
from .helpers import create_tracked_task, _normalize_track_id, _log_app_state
from .image import extract_dominant_colors, get_cached_art_path
from .album_art import get_album_db_folder, ensure_album_art_db
from config import CACHE_DIR, ACTIVE_INTERVAL, IDLE_INTERVAL, IDLE_WAIT_TIME
from logging_config import get_logger
from providers.album_art import get_album_art_provider
from providers.spotify_api import get_shared_spotify_client

logger = get_logger(__name__)

# Platform detection (module-level constant)
DESKTOP = platform.system()


def _perform_debug_art_update(result: Dict[str, Any]):
    """
    Helper to update current_art.jpg in a background thread.
    This function runs in a thread executor, so it must be synchronous.
    The async lock (_art_update_lock) is acquired by the caller before
    submitting this function to the executor, ensuring no concurrent writes.
    """
    try:
        # We need get_cached_art_path. It's available in module scope.
        target_path = get_cached_art_path()
        if not target_path:
            return

        source_path = result.get("album_art_path")
        source_url = result.get("album_art_url")

        # Determine what to write
        # FIX: Use unique temp filename to prevent concurrent writes from overwriting each other
        # This prevents race conditions when multiple debug art updates happen simultaneously
        temp_filename = f"{target_path.stem}_{uuid.uuid4().hex}{target_path.suffix}.tmp"
        temp_path = target_path.parent / temp_filename
        
        # 1. If we have a local path (Thumb or DB), copy it
        if source_path:
            src = Path(source_path)
            if src.exists():
                # Avoid self-copy
                if src.resolve() == target_path.resolve():
                    return
                    
                shutil.copy2(src, temp_path)
                # Use threading lock to coordinate with other threads doing file operations
                # (The async lock is already held by caller, but we need thread-level coordination too)
                with state._art_update_thread_lock:
                    try:
                        os.replace(temp_path, target_path)
                    except OSError:
                        # File might be locked by server or user (e.g. open in viewer)
                        pass
                return

        # 2. If we have a remote URL (Spotify), download it
        if source_url and source_url.startswith('http'):
            try:
                # Use a short timeout for debug updates to avoid hanging
                response = requests.get(source_url, timeout=3)
                if response.status_code == 200:
                    with open(temp_path, 'wb') as f:
                        f.write(response.content)
                    # Use threading lock to coordinate with other threads doing file operations
                    # (The async lock is already held by caller, but we need thread-level coordination too)
                    with state._art_update_thread_lock:
                        try:
                            os.replace(temp_path, target_path)
                        except OSError:
                            # File might be locked by server or user (e.g. open in viewer)
                            pass
            except Exception:
                pass

        # Cleanup temp if it exists
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except: pass

    except Exception:
        # Fail silently in debug update
        pass


async def _update_debug_art(result: Dict[str, Any]):
    """
    Updates current_art.jpg in the cache folder to match the current song's art.
    This restores the behavior of having a 'current_art.jpg' file for debugging
    and external tools, even though the server now uses direct paths/URLs.
    """
    if not result:
        return

    try:
        # Optimization: Only update if source changed
        current_source = result.get('album_art_path') or result.get('album_art_url')
        last_source = getattr(_update_debug_art, 'last_source', None)
        
        if current_source != last_source:
            _update_debug_art.last_source = current_source
            
            # Acquire lock before calling executor to prevent concurrent writes (prevents flickering)
            async with state._art_update_lock:
                # Don't block the main thread
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, _perform_debug_art_update, result)
            
    except Exception as e:
        logger.debug(f"Failed to schedule debug art update: {e}")


async def get_current_song_meta_data() -> Optional[dict]:
    """
    Main orchestrator to get song data from configured sources with hybrid enrichment.
    
    CRITICAL FIX: Uses a lock to prevent concurrent execution.
    Checks if song changed before using cache to prevent stale metadata.
    """
    # Import platform-specific fetchers here to avoid circular imports
    from .windows import _get_current_song_meta_data_windows
    from .spotify import _get_current_song_meta_data_spotify
    from .gnome import _get_current_song_meta_data_gnome
    
    # CRITICAL FIX: Lock the entire fetching process
    # This prevents the race condition where Task B reads cache while Task A is still updating it
    async with state._meta_data_lock:
        current_time = time.time()
        last_check = getattr(get_current_song_meta_data, '_last_check_time', 0)
        is_active = getattr(get_current_song_meta_data, '_is_active', True)
        last_active_time = getattr(get_current_song_meta_data, '_last_active_time', 0)
        
        required_interval = ACTIVE_INTERVAL if is_active else IDLE_INTERVAL
        
        last_song = getattr(get_current_song_meta_data, '_last_song', None)
        last_track_id = getattr(get_current_song_meta_data, '_last_track_id', None)
        
        # Only use cache if within interval AND song hasn't changed
        if (current_time - last_check) < required_interval:
            cached_result = getattr(get_current_song_meta_data, '_last_result', None)
            if cached_result:
                # IMPROVED: Check both song name AND track_id for more reliable change detection
                # This handles rapid track changes better than name-only comparison
                cached_song_name = f"{cached_result.get('artist', '')} - {cached_result.get('title', '')}"
                cached_track_id = cached_result.get('track_id') or cached_result.get('id')
                
                # Verify both song name and track_id match (if track_id is available)
                song_name_matches = last_song == cached_song_name
                
                # Track ID matching logic:
                # - If both have track_ids, they must be equal
                # - If both are missing (None/empty), they match (both None)
                # - If one has track_id and other doesn't, they DON'T match (different tracks)
                if cached_track_id and last_track_id:
                    # Both have track_ids - must be equal
                    track_id_matches = (cached_track_id == last_track_id)
                elif not cached_track_id and not last_track_id:
                    # Both missing - match (both None, can't distinguish)
                    track_id_matches = True
                else:
                    # One has track_id, other doesn't - different tracks
                    track_id_matches = False
                
                if song_name_matches and track_id_matches:
                    # Song hasn't changed, safe to use cache
                    # CRITICAL FIX: Update _last_song and _last_track_id to stay in sync with cached data
                    get_current_song_meta_data._last_song = cached_song_name
                    if cached_track_id:
                        get_current_song_meta_data._last_track_id = cached_track_id
                    return cached_result
                else:
                    # Song changed! Invalidate cache and fetch fresh data
                    # This ensures we detect song changes immediately, not after cache expires
                    change_reason = []
                    if not song_name_matches:
                        change_reason.append(f"name ({last_song} -> {cached_song_name})")
                    if not track_id_matches:
                        change_reason.append(f"track_id ({last_track_id} -> {cached_track_id})")
                    logger.debug(f"Song changed in cache ({', '.join(change_reason)}), invalidating cache to fetch fresh data")
                    get_current_song_meta_data._last_check_time = 0  # Force refresh by resetting check time
            else:
                # If last result was None (Idle/Paused) and we are within interval,
                # return None immediately. This prevents aggressive polling when nothing is playing.
                return None
        
        # Update check time only when we are committed to fetching (inside the lock)
        get_current_song_meta_data._last_check_time = current_time
        
        sources = config.MEDIA_SOURCE.get("sources", [])
        sorted_sources = [s for s in sorted(sources, key=lambda x: int(x.get("priority", 999))) 
                        if s.get("enabled", False)]

        result = None
        windows_media_checked = False
        windows_media_result = None
        
        # 1. Fetch Primary Data from sorted sources
        for source in sorted_sources:
            try:
                if source["name"] == "windows_media" and DESKTOP == "Windows":
                    windows_media_checked = True
                    windows_media_result = await _get_current_song_meta_data_windows()
                    if windows_media_result:
                        result = windows_media_result
                elif source["name"] == "spotify":
                    result = await _get_current_song_meta_data_spotify()
                elif source["name"] == "gnome" and DESKTOP == "Linux":
                    result = _get_current_song_meta_data_gnome()
                    
                if result:
                    # Source is already set in the function
                    break
            except Exception:
                continue
        
        # Detect Spotify-only mode: Windows Media was checked but returned None, Spotify is primary source
        is_spotify_only = (result and 
                        result.get("source") == "spotify" and 
                        (not windows_media_checked or windows_media_result is None))
        
        # Adjust Spotify API polling speed based on mode
        # Fast mode (2.0s) for Spotify-only to reduce latency, Normal mode (6.0s) when Windows Media is active
        spotify_client = get_shared_spotify_client()
        if spotify_client and spotify_client.initialized:
            if is_spotify_only:
                spotify_client.set_fast_mode(True)
            else:
                spotify_client.set_fast_mode(False)
        
        # 2. HYBRID ENRICHMENT - Merge Spotify data if primary source lacks album art/controls
        if result and result.get("source") == "windows_media":
            try:
                # Smart Wake-Up Logic: Only force refresh if Windows says playing BUT Spotify cache says paused
                # This prevents unnecessary force_refresh flags and reduces API calls
                is_windows_playing = result.get("is_playing", False)
                spotify_cached_paused = False
                
                # Check Spotify cache state to determine if we need to wake it up
                if spotify_client and spotify_client._metadata_cache:
                    spotify_cached_paused = not spotify_client._metadata_cache.get('is_playing', False)
                
                # Only force refresh when there's a mismatch (Windows playing + Spotify paused)
                force_wake = is_windows_playing and spotify_cached_paused
                
                spotify_data = await _get_current_song_meta_data_spotify(
                    target_title=result.get("title"),
                    target_artist=result.get("artist"),
                    force_refresh=force_wake
                )
                if spotify_data:
                    # Fuzzy match check: If title and artist are roughly the same
                    win_title = result.get("title", "").lower()
                    win_artist = result.get("artist", "").lower()
                    spot_title = spotify_data.get("title", "").lower()
                    spot_artist = spotify_data.get("artist", "").lower()
                    
                    # Match if titles overlap or artist+title combo matches
                    title_match = win_title in spot_title or spot_title in win_title
                    artist_match = win_artist in spot_artist or spot_artist in win_artist
                    
                    if title_match and (artist_match or not win_artist):
                        # Steal Album Art (Progressive Enhancement: return Spotify immediately, upgrade in background)
                        spotify_art_url = spotify_data.get("album_art_url")
                        if spotify_art_url:
                            try:
                                # CRITICAL FIX: If URL is local (starts with /), it means we loaded from DB (user preference).
                                # Don't try to upgrade/override it with cached remote art.
                                if spotify_art_url.startswith('/'):
                                    result["album_art_url"] = spotify_art_url
                                    # CRITICAL FIX: Also copy the album_art_path if Spotify loaded from DB
                                    # This ensures server.py serves the high-res DB image instead of the low-res thumbnail
                                    if spotify_data.get("album_art_path"):
                                        result["album_art_path"] = spotify_data["album_art_path"]
                                else:
                                    art_provider = get_album_art_provider()
                                    
                                    # Check cache first - if cached high-res exists, use it immediately
                                    # Use album-level cache (same album = same art for all tracks)
                                    cached_result = art_provider.get_from_cache(
                                        spotify_data.get("artist", ""),
                                        spotify_data.get("title", ""),
                                        spotify_data.get("album")
                                    )
                                    if cached_result:
                                        cached_url, _ = cached_result
                                        if cached_url != spotify_art_url:
                                            result["album_art_url"] = cached_url
                                        else:
                                            result["album_art_url"] = spotify_art_url
                                    else:
                                        # Not cached - use Spotify immediately, upgrade in background
                                        result["album_art_url"] = spotify_art_url
                                    
                                    # CRITICAL FIX: Clear Windows thumbnail path when using remote Spotify URL
                                    # This ensures frontend uses the remote URL directly instead of serving low-res thumbnail
                                    if result.get("album_art_path") and not spotify_data.get("album_art_path"):
                                        # Spotify doesn't have a local path (remote URL), so clear Windows path
                                        # Frontend will use album_art_url (remote) directly
                                        result.pop("album_art_path", None)
                                        
                                        # Check if a background task is already running for this track
                                        hybrid_track_id = _normalize_track_id(
                                            spotify_data.get('artist', ''),
                                            spotify_data.get('title', '')
                                        )
                                        if hybrid_track_id in state._running_art_upgrade_tasks:
                                            # Task already running, skip creating duplicate - only log once per track to prevent spam
                                            if not hasattr(get_current_song_meta_data, '_last_logged_hybrid_art_upgrade_running_track_id') or \
                                               get_current_song_meta_data._last_logged_hybrid_art_upgrade_running_track_id != hybrid_track_id:
                                                logger.debug(f"Background art upgrade already running for {hybrid_track_id}, skipping duplicate task")
                                                get_current_song_meta_data._last_logged_hybrid_art_upgrade_running_track_id = hybrid_track_id
                                        else:
                                            # Start background task to fetch high-res
                                            async def background_upgrade_hybrid():
                                                try:
                                                    await asyncio.sleep(0.1)
                                                    # Use ensure_album_art_db instead of just get_high_res_art
                                                    # This ensures proper saving to DB, not just memory caching
                                                    # This fixes the issue where Spotify art wasn't being saved
                                                    # when Windows Media fetcher ran first (race condition fix)
                                                    high_res_result = await ensure_album_art_db(
                                                        spotify_data.get("artist", ""),
                                                        spotify_data.get("album"),
                                                        spotify_data.get("title", ""),
                                                        spotify_art_url
                                                    )
                                                    
                                                    # Update cache manually if successful (so UI updates immediately)
                                                    if high_res_result:
                                                        art_provider = get_album_art_provider()
                                                        cache_key = art_provider._get_cache_key(
                                                            spotify_data.get("artist", ""),
                                                            spotify_data.get("title", ""),
                                                            spotify_data.get("album")
                                                        )
                                                        art_provider._cache[cache_key] = high_res_result
                                                except Exception as e:
                                                    logger.debug(f"Background art upgrade failed in hybrid mode: {e}")
                                                finally:
                                                    # Remove from running tasks when done
                                                    state._running_art_upgrade_tasks.pop(hybrid_track_id, None)
                                            
                                            # Use tracked task
                                            task = create_tracked_task(background_upgrade_hybrid())
                                            state._running_art_upgrade_tasks[hybrid_track_id] = task
                            except Exception as e:
                                logger.debug(f"Failed to setup high-res art in hybrid mode: {e}")
                                result["album_art_url"] = spotify_art_url
                                # Also copy path if available (even on error, we might have a valid path)
                                if spotify_data.get("album_art_path"):
                                    result["album_art_path"] = spotify_data["album_art_path"]
                        
                        # Steal Colors from Spotify (now properly extracted!)
                        if spotify_data.get("colors"):
                            result["colors"] = spotify_data.get("colors")

                        # Enable Controls by marking as hybrid
                        # Frontend will allow controls for this source type
                        result["source"] = "spotify_hybrid"
                        
                        # CRITICAL FIX: Copy Spotify ID for Like button functionality
                        # This ensures the Like button works even when playing from Windows Media
                        if spotify_data.get("id"):
                            result["id"] = spotify_data.get("id")
                        
                        # Copy Spotify URL for album art click functionality
                        # This enables opening the song in Spotify app/web when clicking album art
                        if spotify_data.get("url"):
                            result["url"] = spotify_data.get("url")
                        
                        # Copy Artist ID and Name for Visual Mode
                        # This ensures artist slideshows work even when playing from Windows Media
                        if spotify_data.get("artist_id"):
                            result["artist_id"] = spotify_data.get("artist_id")
                        if spotify_data.get("artist_name"):
                            result["artist_name"] = spotify_data.get("artist_name")
                        
                        # Copy Background Style preference (Phase 2)
                        if spotify_data.get("background_style"):
                            result["background_style"] = spotify_data.get("background_style")
                        
                        # if DEBUG["enabled"]:
                        #    logger.info(f"Hybrid mode: Enriched Windows Media data with Spotify album art and controls")
            except Exception as e:
                logger.error(f"Hybrid enrichment failed: {e}")
        
        # 4. If we still don't have colors (e.g. local file), extract them
        if result and result.get("source") == "windows_media":
            # NEW: Use the specific path we found/created, falling back to legacy search
            # This fixes color extraction for the new unique thumbnail system (thumb_*.jpg)
            local_art_path = None
            if result.get("album_art_path"):
                local_art_path = Path(result["album_art_path"])
            else:
                local_art_path = get_cached_art_path()
            
            if result.get("colors") == ("#24273a", "#363b54") and local_art_path and local_art_path.exists():
                 # Only extract if we have a valid local file and default colors
                 # Now async, so we await it
                 result["colors"] = await extract_dominant_colors(local_art_path)

        # 3. State Management (Active vs Idle)
        if result:
            get_current_song_meta_data._is_active = True
            get_current_song_meta_data._last_active_time = current_time
            
            last_song = getattr(get_current_song_meta_data, '_last_song', None)
            current_song_name = f"{result.get('artist')} - {result.get('title')}"
            
            # Update last_song inside the lock
            if last_song != current_song_name:
                get_current_song_meta_data._last_song = current_song_name
                get_current_song_meta_data._last_source = result.get('source')
                _log_app_state()
        else:
            if (current_time - last_active_time) > IDLE_WAIT_TIME:
                get_current_song_meta_data._is_active = False

        get_current_song_meta_data._last_result = result
        
        # IMPROVED: Store track_id for rapid change detection
        # This helps detect track changes even when song name might be similar
        if result:
            result_song_name = f"{result.get('artist', '')} - {result.get('title', '')}"
            result_track_id = result.get('track_id') or result.get('id')
            get_current_song_meta_data._last_song = result_song_name
            if result_track_id:
                get_current_song_meta_data._last_track_id = result_track_id
        
        # RESTORED: Update current_art.jpg for debugging/external tools
        # This ensures the cache folder always has the current art file
        await _update_debug_art(result)
        
        _log_app_state()
        
        return result
