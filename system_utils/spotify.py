"""
Spotify metadata fetcher for system_utils package.

Dependencies: state, helpers, image, album_art, artist_image
"""
from __future__ import annotations
import os
import json
import time
import asyncio
import uuid
import requests
from pathlib import Path
from typing import Optional, Dict, Any

from PIL import Image

from . import state
from .helpers import create_tracked_task, _normalize_track_id, _cleanup_artist_image_log_throttle
from .image import extract_dominant_colors
from .album_art import get_album_db_folder, load_album_art_from_db, ensure_album_art_db
from .artist_image import load_artist_image_from_db, _get_artist_image_fallback, ensure_artist_image_db
from config import CACHE_DIR
from logging_config import get_logger
from providers.album_art import get_album_art_provider
from providers.spotify_api import get_shared_spotify_client

logger = get_logger(__name__)


async def _download_spotify_art_background(url: str, track_id: str) -> None:
    """
    Background task to download Spotify art (Fix #3).
    This allows the metadata function to return immediately without waiting for the download.
    Includes race condition protection using track_id validation.
    
    Args:
        url: Spotify album art URL to download
        track_id: ID of the track requesting the art (for validation)
    """
    # Use semaphore to limit concurrent downloads (Fix: Apply Semaphore)
    async with state._art_download_semaphore:
        try:
            # Check if file already exists and matches URL
            if (CACHE_DIR / "spotify_art.jpg").exists():
                if hasattr(_get_current_song_meta_data_spotify, '_last_spotify_art_url') and \
                   _get_current_song_meta_data_spotify._last_spotify_art_url == url:
                    return

            logger.debug(f"Starting background download of Spotify art: {url}")
            
            # Download in executor to avoid blocking
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: requests.get(url, timeout=5)
            )
            
            if response.status_code == 200:
                # Validation: Check if track is still current before saving (Fix: Race Condition)
                spotify_client = get_shared_spotify_client()
                if spotify_client and spotify_client._metadata_cache:
                    current_track = spotify_client._metadata_cache
                    current_track_id = _normalize_track_id(
                        current_track.get('artist', ''),
                        current_track.get('title', '')
                    )
                    if current_track_id != track_id:
                        logger.debug(f"Track changed during download ({track_id} -> {current_track_id}), discarding art")
                        return

                # Save to cache
                art_path = CACHE_DIR / "spotify_art.jpg"
                # FIX: Use unique temp filename to prevent concurrent downloads from overwriting each other
                # This prevents race conditions when skipping songs rapidly
                temp_filename = f"spotify_art_{uuid.uuid4().hex}.jpg.tmp"
                temp_path = CACHE_DIR / temp_filename
                
                # Write to temp (blocking I/O in executor)
                def write_file():
                    with open(temp_path, "wb") as f:
                        f.write(response.content)
                
                await loop.run_in_executor(None, write_file)
                
                # Final Validation: Check one last time before atomic replace
                if spotify_client and spotify_client._metadata_cache:
                    current_track = spotify_client._metadata_cache
                    current_track_id = _normalize_track_id(
                        current_track.get('artist', ''),
                        current_track.get('title', '')
                    )
                    if current_track_id != track_id:
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                        return

                # Atomic replace with retry (use lock to prevent concurrent updates)
                # Run blocking I/O in executor while holding lock to prevent race conditions
                async with state._art_update_lock:
                    replaced = False
                    for attempt in range(3):
                        try:
                            # Run blocking os.replace in executor to avoid blocking event loop
                            await loop.run_in_executor(None, os.replace, temp_path, art_path)
                            replaced = True
                            break
                        except OSError:
                            if attempt < 2:
                                await asyncio.sleep(0.1)
                            else:
                                logger.debug(f"Could not atomically replace spotify_art.jpg after 3 attempts (file may be locked)")
                
                if not replaced:
                    try:
                        os.remove(temp_path)
                    except:
                        pass
                    return

                # Verify resolution (optional, fast enough)
                try:
                    with Image.open(art_path) as img:
                        logger.info(f"Downloaded album art actual resolution: {img.size[0]}x{img.size[1]}")
                except:
                    pass

                # Invalidate color cache (managed by extract_dominant_colors mtime check now)
                
                # Extract colors (CPU-bound operation, might take time)
                colors = await extract_dominant_colors(art_path)
                
                # CRITICAL FIX: Re-validate track hasn't changed AFTER color extraction
                # Color extraction is CPU-bound and might take time, so track could change during it
                # If track changed, discard colors to prevent wrong track inheriting old colors
                if spotify_client and spotify_client._metadata_cache:
                    current_track = spotify_client._metadata_cache
                    current_track_id = _normalize_track_id(
                        current_track.get('artist', ''),
                        current_track.get('title', '')
                    )
                    if current_track_id != track_id:
                        logger.debug(f"Track changed after color extraction ({track_id} -> {current_track_id}), discarding colors")
                        return
                
                # Update cache (only if track is still current)
                _get_current_song_meta_data_spotify._last_spotify_art_url = url
                _get_current_song_meta_data_spotify._last_spotify_colors = colors
                
        except Exception as e:
            logger.debug(f"Background Spotify art download failed: {e}")
            # Clean up unique temp file if it was created before the error
            if 'temp_path' in locals() and temp_path.exists():
                try:
                    os.remove(temp_path)
                except:
                    pass
        finally:
            # FIX: Ensure URL is removed from tracker when done, even if error occurred
            state._spotify_download_tracker.discard(url)


async def _get_current_song_meta_data_spotify(target_title: str = None, target_artist: str = None, force_refresh: bool = False) -> Optional[dict]:
    """Spotify API metadata fetcher with standardized output."""
    try:
        # Use shared singleton instance (consolidates all stats across the app)
        spotify_client = get_shared_spotify_client()
        
        if spotify_client is None or not spotify_client.initialized:
            return None

        # Track metadata fetch (always, not just in debug mode)
        state._metadata_fetch_counters['spotify'] += 1

        track = None
        
        # Hybrid Cache Optimization:
        # If we are looking for a specific song (e.g. from Windows Media) and we have it cached,
        # use the cache to avoid hitting the API just for album art/colors.
        if target_title and target_artist and spotify_client._metadata_cache:
            cache = spotify_client._metadata_cache
            s_title = cache.get('title', '').lower()
            s_artist = cache.get('artist', '').lower()
            t_title = target_title.lower()
            t_artist = target_artist.lower()
            
            # Check for match (fuzzy)
            if (t_title in s_title or s_title in t_title) and \
               (t_artist in s_artist or s_artist in t_artist):
                # Check if cache is fresh enough for hybrid use (30s)
                # We allow a longer TTL here because we primarily want the Art/Colors, which don't change.
                if time.time() - spotify_client._last_metadata_check < 30:
                    track = cache
                    # logger.debug("Hybrid: Using cached Spotify data")

        # If no cache hit, fetch from API (or internal smart cache)
        if track is None:
            track = await spotify_client.get_current_track(force_refresh=force_refresh)
            
        if not track:
            return None
        
        # Track last active time for paused timeout (similar to Windows)
        is_playing = track.get("is_playing", False)
        if is_playing:
            state._spotify_last_active_time = time.time()
        
        # Extract colors from Spotify album art
        colors = ("#24273a", "#363b54")  # Default
        album_art_url = track.get("album_art")
        
        # CRITICAL FIX: Store original Spotify URL for background tasks
        # (album_art_url might be overwritten with local path if DB hit occurs)
        raw_spotify_url = album_art_url
        
        # Capture track info for DB check and background tasks
        captured_artist = track["artist"]
        captured_title = track["title"]
        captured_album = track.get("album")
        captured_artist_id = track.get("artist_id")  # For artist image backfill
        captured_track_id = _normalize_track_id(captured_artist, captured_title)
        
        # Flag to track if we found art in DB
        found_in_db = False
        # CRITICAL FIX: Separate flag for album art (not artist image fallback)
        # This ensures background fetch triggers even when artist image fallback is used
        album_art_found_in_db = False
        album_art_path = None  # Store direct path for serving without copying
        saved_background_style = None  # Initialize to prevent UnboundLocalError
        db_metadata = None  # Initialize to prevent UnboundLocalError

        # CRITICAL FIX: Separate album art (top left display) from background image
        # Album art should ALWAYS be album art, background can be artist image if selected
        background_image_url = None
        background_image_path = None
        
        # 1. Always load album art for top left display (independent of artist image preference)
        db_result = load_album_art_from_db(captured_artist, captured_album, captured_title)
        if db_result:
            found_in_db = True
            album_art_found_in_db = True  # CRITICAL: Only set when actual album art is found
            db_image_path = db_result["path"]
            db_metadata = db_result["metadata"]
            saved_background_style = db_result.get("background_style")  # Capture saved style
            
            # FIX: Add timestamp to URL to force browser cache busting
            mtime = int(time.time())
            try:
                if db_image_path.exists():
                    mtime = int(db_image_path.stat().st_mtime)
            except: pass
            
            # Album art URL is ALWAYS album art (for top left display)
            album_art_url = f"/cover-art?id={captured_track_id}&t={mtime}"

            # NEW: Store path directly so server.py can serve it without copying
            # This eliminates race conditions from file copying
            album_art_path = str(db_image_path)
            
            # Default background to album art (will be overridden if artist image is selected)
            background_image_url = album_art_url
            background_image_path = album_art_path
        
        # 2. Check for artist image preference for background (separate from album art)
        # If user selected an artist image, use it for background instead of album art
        # FIX 6.1: Use album or title fallback to match server.py preference save path
        album_or_title = captured_album if captured_album else captured_title
        artist_image_result = load_artist_image_from_db(captured_artist, album_or_title)
        if artist_image_result:
            artist_image_path = artist_image_result["path"]
            if artist_image_path.exists():
                mtime = int(artist_image_path.stat().st_mtime)
                # Use artist image for background (not for album art display)
                # Add type=background parameter so server knows to serve background_image_path
                background_image_url = f"/cover-art?id={captured_track_id}&t={mtime}&type=background"
                background_image_path = str(artist_image_path)
                # CRITICAL FIX: Throttle log to prevent spam (30+ logs per second)
                current_time = time.time()
                log_key = f"preferred_bg_{captured_artist}"
                last_log_time = state._artist_image_log_throttle.get(log_key, 0)
                if (current_time - last_log_time) >= state._ARTIST_IMAGE_LOG_THROTTLE_SECONDS:
                    logger.debug(f"Using preferred artist image for background: {captured_artist}")
                    state._artist_image_log_throttle[log_key] = current_time
                    _cleanup_artist_image_log_throttle()
        
        # If no album art found but artist image is selected, still set background
        # CRITICAL FIX: Don't set found_in_db here - we want to keep album_art_found_in_db = False
        # so the background fetch still triggers. found_in_db is only for display purposes.
        if not found_in_db and artist_image_result:
            found_in_db = True  # At least we have something for background (display only)
        
        # CRITICAL FIX: Check if artist images DB is populated with ALL expected sources
        # This ensures all provider options are available in the selection menu (similar to album art backfill)
        # Only check if we have an artist name (required for folder lookup)
        if captured_artist:
            try:
                artist_folder = get_album_db_folder(captured_artist, None)
                artist_metadata_path = artist_folder / "metadata.json"
                
                # Check if metadata exists and has artist images
                artist_metadata_exists = artist_metadata_path.exists()
                artist_images_complete = False
                
                if artist_metadata_exists:
                    try:
                        with open(artist_metadata_path, 'r', encoding='utf-8') as f:
                            artist_metadata_check = json.load(f)
                        
                        if artist_metadata_check.get("type") == "artist_images":
                            existing_images = artist_metadata_check.get("images", [])
                            # Get sources that have downloaded images
                            existing_sources = {img.get("source") for img in existing_images if img.get("downloaded")}
                            
                            # Determine which sources SHOULD be there
                            # Deezer and TheAudioDB are always available (free, no auth)
                            expected_sources = {"Deezer", "TheAudioDB"}
                            
                            # FanArt.tv (if API key exists in environment)
                            if os.getenv("FANART_TV_API_KEY"):
                                expected_sources.add("FanArt.tv")
                            
                            # Spotify (if artist_id is available)
                            if captured_artist_id:
                                expected_sources.add("Spotify")
                            
                            # NOTE: Last.fm is excluded from backfill as it's not necessary
                            # Last.fm images are often low-quality placeholders and not needed for selection menu
                            
                            # Check if we have all expected sources
                            artist_images_complete = expected_sources.issubset(existing_sources)
                    except Exception as e:
                        logger.debug(f"Failed to check artist images completeness: {e}")
                        artist_images_complete = False
                
                # Trigger background task ONLY if artist images are incomplete (and not already running)
                # Use artist name only as key (consistent with ensure_artist_image_db)
                artist_request_key = captured_artist
                
                if not artist_images_complete and artist_request_key not in state._artist_download_tracker:
                    # Start background task to fetch from ALL missing sources
                    async def background_artist_images_backfill():
                        """Background task to fetch artist images from all enabled sources"""
                        try:
                            # This will fetch from Deezer, TheAudioDB, FanArt.tv (if key exists), and Spotify (if ID available)
                            # Last.fm is excluded per user preference
                            await ensure_artist_image_db(captured_artist, captured_artist_id)
                        except Exception as e:
                            logger.debug(f"Background artist image backfill failed for {captured_artist}: {e}")
                    
                    # Use tracked task to prevent silent failures
                    create_tracked_task(background_artist_images_backfill())
            except Exception as e:
                logger.debug(f"Failed to check/trigger artist image backfill: {e}")
        
        # CRITICAL FIX #1: Initialize defaults (assume incomplete if no metadata)
        # This ensures background fetch triggers even when db_metadata is None (new songs)
        db_is_complete = False
        has_invalid_resolution = False
        
        # Determine which providers SHOULD be there
        # Spotify is always a source if we are here (since we have raw_spotify_url)
        expected_providers = {"Spotify"}
        
        # Check if other providers are enabled in the singleton instance
        # We need to get the provider instance to check config
        # FIX: Removed redundant local import that was causing UnboundLocalError
        art_provider = get_album_art_provider()
        
        if art_provider.enable_itunes:
            expected_providers.add("iTunes")
        
        if art_provider.enable_lastfm and art_provider.lastfm_api_key:
            expected_providers.add("LastFM")
        
        # Check if DB is already populated with ALL enabled providers (only if we have album art metadata)
        if db_metadata:
            # This logic respects user config: if Last.fm is disabled/no key, we won't look for it.
            existing_providers = set(db_metadata.get("providers", {}).keys())
                
            # If we have all expected providers, the DB is complete
            db_is_complete = expected_providers.issubset(existing_providers)
            
            # SELF-HEAL: Check if any existing provider has invalid/unknown resolution
            # This ensures we re-run the check to fix metadata for files that were downloaded but have 0x0 resolution
            for p_name, p_data in db_metadata.get("providers", {}).items():
                if p_data.get("downloaded") and (p_data.get("width", 0) == 0 or "unknown" in str(p_data.get("resolution", "")).lower()):
                    has_invalid_resolution = True
                    logger.debug(f"Found invalid resolution for {p_name}, triggering self-heal")
                    break
        else:
            # No metadata means definitely incomplete - need to fetch album art
            db_is_complete = False

        # CRITICAL FIX #2: Use album_art_found_in_db instead of found_in_db
        # This ensures background fetch triggers even when artist image fallback is used
        # Trigger background task ONLY if DB is incomplete OR has invalid data (and not already running)
        # Use raw_spotify_url (not album_art_url which is now a local path)
        # CRITICAL FIX: Only run this once per track to prevent infinite loops
        # Use 'spot::' namespace to distinguish from Windows fetcher checks
        checked_key = f"spot::{captured_track_id}"
        
        # FIX: Check negative cache first (prevents retry spam for non-music files)
        if checked_key in state._no_art_found_cache:
            cache_time = state._no_art_found_cache[checked_key]
            if time.time() - cache_time < state._NO_ART_FOUND_TTL:
                pass  # Skip - no art found recently
            else:
                del state._no_art_found_cache[checked_key]  # TTL expired
        
        if (not db_is_complete or has_invalid_resolution) and captured_track_id not in state._running_art_upgrade_tasks and checked_key not in state._db_checked_tracks and checked_key not in state._no_art_found_cache:
            # Mark as checked immediately to prevent re-entry on next poll
            state._db_checked_tracks[checked_key] = time.time()
            
            # Limit set size to prevent memory leaks (FIFO eviction)
            if len(state._db_checked_tracks) > state._MAX_DB_CHECKED_SIZE:
                state._db_checked_tracks.popitem(last=False)  # Remove oldest

            async def background_refresh_db():
                try:
                    # This function now returns the best URL and resolution
                    # Pass retry_count=1 to prevent infinite recursion
                    result = await ensure_album_art_db(
                        captured_artist,
                        captured_album,
                        captured_title,
                        raw_spotify_url,
                        retry_count=1
                    )
                    # FIX: On None result, add to negative cache instead of removing from checked
                    if not result:
                        state._no_art_found_cache[checked_key] = time.time()
                        if len(state._no_art_found_cache) > state._MAX_NO_ART_FOUND_CACHE_SIZE:
                            oldest = min(state._no_art_found_cache, key=state._no_art_found_cache.get)
                            del state._no_art_found_cache[oldest]
                        # CRITICAL: Also pop from _db_checked_tracks so TTL retry can work
                        state._db_checked_tracks.pop(checked_key, None)
                    return result
                except Exception as e:
                    logger.debug(f"Background DB refresh failed: {e}")
                    # On exception, also add to negative cache
                    state._no_art_found_cache[checked_key] = time.time()
                    # CRITICAL: Also pop from _db_checked_tracks so TTL retry can work
                    state._db_checked_tracks.pop(checked_key, None)
                finally:
                    state._running_art_upgrade_tasks.pop(captured_track_id, None)
            
            # Use tracked task
            task = create_tracked_task(background_refresh_db())
            state._running_art_upgrade_tasks[captured_track_id] = task
        
        # Fallback: Check for artist image if no album art found (but no explicit preference)
        # This uses first available artist image as fallback when no album art exists
        # CRITICAL FIX #2: Use album_art_found_in_db check - don't set found_in_db here
        # found_in_db is for display purposes only, album_art_found_in_db controls background fetch
        if not album_art_found_in_db:
            fallback_result = _get_artist_image_fallback(captured_artist)
            if fallback_result:
                artist_image_path = fallback_result["path"]
                mtime = int(artist_image_path.stat().st_mtime)
                # Use fallback artist image for both (only when no album art exists)
                album_art_url = f"/cover-art?id={captured_track_id}&t={mtime}"
                background_image_url = album_art_url
                album_art_path = str(artist_image_path)
                background_image_path = str(artist_image_path)
                found_in_db = True  # For display purposes only - album_art_found_in_db stays False
                # CRITICAL FIX: Throttle log to prevent spam (30+ logs per second)
                # Use same throttle mechanism as artist image fetching
                current_time = time.time()
                log_key = f"fallback_{captured_artist}"
                last_log_time = state._artist_image_log_throttle.get(log_key, 0)
                if (current_time - last_log_time) >= state._ARTIST_IMAGE_LOG_THROTTLE_SECONDS:
                    logger.debug(f"Using artist image '{fallback_result.get('source')}' as fallback for {captured_artist}")
                    state._artist_image_log_throttle[log_key] = current_time
                    _cleanup_artist_image_log_throttle()
        
        # Progressive Enhancement: Return Spotify 640px immediately, upgrade in background
        if album_art_url:
            try:
                # Get high-res album art provider
                art_provider = get_album_art_provider()
                
                # Store original Spotify URL as fallback (use raw_spotify_url, not album_art_url)
                original_spotify_url = raw_spotify_url
                # Capture track info for background task (prevents race conditions)
                captured_artist = track["artist"]
                captured_title = track["title"]
                captured_album = track.get("album")
                captured_track_id = _normalize_track_id(captured_artist, captured_title)
                
                # 1. Check cache first - if we have cached high-res, use it immediately
                # Use album-level cache (same album = same art for all tracks)
                cached_result = art_provider.get_from_cache(captured_artist, captured_title, captured_album)
                if cached_result:
                    cached_url, cached_resolution_info = cached_result
                    # Only use cached result if it's better than Spotify (not the Spotify fallback)
                    # AND if we didn't just load a preferred image from the DB (which takes precedence)
                    if cached_url != original_spotify_url and not found_in_db:
                        album_art_url = cached_url
                        # Log upgrade if not already logged for this track
                        if not hasattr(_get_current_song_meta_data_spotify, '_last_logged_track_id') or \
                           _get_current_song_meta_data_spotify._last_logged_track_id != captured_track_id:
                            logger.info(f"Using cached high-res album art for {captured_artist} - {captured_title}: {cached_resolution_info}")
                            _get_current_song_meta_data_spotify._last_logged_track_id = captured_track_id
                    else:
                        # 2. Not cached - start background task to fetch high-res AND populate DB
                        # Return Spotify URL immediately for instant UI, upgrade happens in background
                        pass

                    # ALWAYS start background task to populate DB if not running
                    # This ensures DB is populated even if we have a memory cache hit
                    
                    # CRITICAL FIX: Don't run background task if we just loaded from DB
                    # OR if we have already checked/populated the DB for this track in this session
                    # CRITICAL FIX #2: Use album_art_found_in_db instead of found_in_db
                    # This ensures background task runs even when artist image fallback is used
                    # Use 'spot::' namespace to distinguish from Windows fetcher checks
                    checked_key = f"spot::{captured_track_id}"
                    
                    # FIX: Check negative cache first (prevents retry spam for non-music files)
                    if checked_key in state._no_art_found_cache:
                        cache_time = state._no_art_found_cache[checked_key]
                        if time.time() - cache_time < state._NO_ART_FOUND_TTL:
                            pass  # Skip - no art found recently
                        else:
                            del state._no_art_found_cache[checked_key]  # TTL expired
                    
                    if not album_art_found_in_db and checked_key not in state._db_checked_tracks and checked_key not in state._no_art_found_cache:
                        if captured_track_id in state._running_art_upgrade_tasks:
                            # Task already running - only log once per track to prevent spam
                            if not hasattr(_get_current_song_meta_data_spotify, '_last_logged_art_upgrade_running_track_id') or \
                               _get_current_song_meta_data_spotify._last_logged_art_upgrade_running_track_id != captured_track_id:
                                logger.debug(f"Background art upgrade already running for {captured_track_id}, skipping duplicate task")
                                _get_current_song_meta_data_spotify._last_logged_art_upgrade_running_track_id = captured_track_id
                        else:
                            # Mark as checked immediately to prevent re-entry on next poll
                            state._db_checked_tracks[checked_key] = time.time()
                            
                            # Limit set size to prevent memory leaks (FIFO eviction)
                            if len(state._db_checked_tracks) > state._MAX_DB_CHECKED_SIZE:
                                state._db_checked_tracks.popitem(last=False)  # Remove oldest
                            
                            async def background_upgrade_art():
                                """Background task to fetch high-res art, update cache, and populate DB"""
                                try:
                                    # Only log once per track (check if we've logged this track before)
                                    if not hasattr(_get_current_song_meta_data_spotify, '_last_logged_startup_track_id') or \
                                       _get_current_song_meta_data_spotify._last_logged_startup_track_id != captured_track_id:
                                        logger.info(f"Starting background album art upgrade for {captured_artist} - {captured_title} (album: {captured_album or 'N/A'})")
                                        _get_current_song_meta_data_spotify._last_logged_startup_track_id = captured_track_id
                                    # Wait a tiny bit to let the initial response return first
                                    await asyncio.sleep(0.1)
                                    
                                    # Populate Album Art Database (fetches all options and saves them)
                                    # CRITICAL: This must run even if we skip high-res fetch
                                    high_res_result = None
                                    try:
                                        logger.info(f"Calling ensure_album_art_db for {captured_artist} - {captured_title}")
                                        # Use the result from DB population directly (avoid redundant fetch)
                                        high_res_result = await ensure_album_art_db(captured_artist, captured_album, captured_title, original_spotify_url)
                                        
                                        # FIX: On None result, add to negative cache instead of removing from checked
                                        if not high_res_result:
                                            state._no_art_found_cache[checked_key] = time.time()
                                            if len(state._no_art_found_cache) > state._MAX_NO_ART_FOUND_CACHE_SIZE:
                                                oldest = min(state._no_art_found_cache, key=state._no_art_found_cache.get)
                                                del state._no_art_found_cache[oldest]
                                            # CRITICAL: Also pop from _db_checked_tracks so TTL retry can work
                                            state._db_checked_tracks.pop(checked_key, None)
                                        
                                        # Update the provider cache immediately
                                        if high_res_result:
                                            # Update cache manually since we skipped get_high_res_art
                                            # We need to construct the cache key exactly like the provider does
                                            cache_key = art_provider._get_cache_key(captured_artist, captured_title, captured_album)
                                            art_provider._cache[cache_key] = high_res_result
                                            logger.debug(f"Updated art provider cache from DB result for {captured_artist} - {captured_title}")
                                            
                                    except Exception as e:
                                        logger.error(f"ensure_album_art_db failed: {e}")
                                        # On exception, also add to negative cache
                                        state._no_art_found_cache[checked_key] = time.time()
                                        # CRITICAL: Also pop from _db_checked_tracks so TTL retry can work
                                        state._db_checked_tracks.pop(checked_key, None)
                                    
                                    # REMOVED: Redundant call to art_provider.get_high_res_art
                                    # This prevents the double-flicker (once for remote high-res, once for local DB)
                                    # and saves sequential network requests since ensure_album_art_db already fetched everything in parallel.
                                    
                                    # Check if track changed during fetch (race condition protection)
                                    # Get current track from Spotify cache to verify
                                    current_spotify_client = get_shared_spotify_client()
                                    if current_spotify_client and current_spotify_client._metadata_cache:
                                        current_track = current_spotify_client._metadata_cache
                                        current_track_id = _normalize_track_id(
                                            current_track.get('artist', ''),
                                            current_track.get('title', '')
                                        )
                                        if current_track_id != captured_track_id:
                                            logger.debug(f"Track changed during background art fetch ({captured_track_id} -> {current_track_id}), discarding result")
                                            return
                                    
                                    # If we got a better URL, it's now cached for next poll
                                    # The frontend will pick it up on the next metadata poll (0.1s later)
                                    if high_res_result:
                                        # Update cache with the best URL and resolution
                                        _get_current_song_meta_data_spotify._last_logged_track_id = captured_track_id
                                        logger.info(f"Upgraded album art from Spotify to high-res source for {captured_artist} - {captured_title}: {high_res_result[1]}")
                                except Exception as e:
                                    logger.error(f"Background art upgrade failed for {captured_artist} - {captured_title}: {type(e).__name__}: {e}", exc_info=True)
                                    # On exception, also add to negative cache
                                    state._no_art_found_cache[checked_key] = time.time()
                                    # CRITICAL: Also pop from _db_checked_tracks so TTL retry can work
                                    state._db_checked_tracks.pop(checked_key, None)
                                finally:
                                    # Remove from running tasks when done
                                    state._running_art_upgrade_tasks.pop(captured_track_id, None)
                            
                            # Start background task (non-blocking) and track it
                            # Use tracked task to prevent garbage collection issues
                            # CRITICAL FIX: Reserve slot first to prevent race condition
                            # If task creation fails after reserving slot, we can still clean up
                            state._running_art_upgrade_tasks[captured_track_id] = None  # Reserve slot
                            try:
                                task = create_tracked_task(background_upgrade_art())
                                state._running_art_upgrade_tasks[captured_track_id] = task
                            except Exception as e:
                                # If task creation fails, ensure cleanup happens
                                state._running_art_upgrade_tasks.pop(captured_track_id, None)
                                logger.debug(f"Failed to create background art upgrade task: {e}")
                                raise
                    
            except Exception as e:
                # FIX: Log only once per track to prevent spam (but still catch errors)
                if not hasattr(_get_current_song_meta_data_spotify, '_last_logged_error_track_id') or \
                   _get_current_song_meta_data_spotify._last_logged_error_track_id != captured_track_id:
                     logger.debug(f"Failed to setup high-res album art, using Spotify default: {e}")
                     _get_current_song_meta_data_spotify._last_logged_error_track_id = captured_track_id
                pass # It is safe to keep this if you want, but it is not strictly needed anymore
        
        # CRITICAL FIX: Only attempt download if it's a remote URL (not a local path starting with /)
        # This prevents 'MissingSchema' exceptions when using cached art
        if album_art_url and not album_art_url.startswith('/'):
            try:
                # Check if we need to download new art (track changed)
                # CRITICAL FIX: Only download if URL changed OR file is missing
                current_art_exists = (CACHE_DIR / "spotify_art.jpg").exists()
                
                # OPTIMIZATION: Check if this exact URL is already being downloaded by a background task
                # This prevents the polling loop from spawning duplicates (Fix: Task Spam)
                is_downloading = album_art_url in state._spotify_download_tracker
                
                # FIX: Properly group conditions so tracker check applies to all conditions
                # Without this, if URL changed, condition would be True even if already downloading
                if (
                    not is_downloading
                    and (
                        not hasattr(_get_current_song_meta_data_spotify, '_last_spotify_art_url')
                        or _get_current_song_meta_data_spotify._last_spotify_art_url != album_art_url
                        or not current_art_exists
                    )
                ):
                    
                    # Mark as downloading to prevent duplicates
                    state._spotify_download_tracker.add(album_art_url)
                    
                    # OPTIMIZATION: Offload download to background task (Fix #3)
                    # This returns metadata immediately without waiting for the image
                    # Uses tracked task to prevent silent failures
                    # Passes captured_track_id for race condition validation
                    # CRITICAL FIX: Wrap in try/finally to ensure cleanup even if task creation fails
                    try:
                        create_tracked_task(_download_spotify_art_background(album_art_url, captured_track_id))
                    except Exception as e:
                        # If task creation fails, ensure cleanup happens
                        state._spotify_download_tracker.discard(album_art_url)
                        logger.debug(f"Failed to create download task: {e}")
                        raise
                    
                    # Use cached colors if available temporarily, or default
                    if hasattr(_get_current_song_meta_data_spotify, '_last_spotify_colors'):
                        colors = _get_current_song_meta_data_spotify._last_spotify_colors
                else:
                    # Use cached colors
                    if hasattr(_get_current_song_meta_data_spotify, '_last_spotify_colors'):
                        colors = _get_current_song_meta_data_spotify._last_spotify_colors
                        
            except Exception as e:
                logger.debug(f"Failed to setup Spotify art download: {e}")
            
        # Return standardized structure with all fields
        # Include artist_id and artist_name for visual mode and artist image fetching
        # Include background_style for Phase 2: Visual Preference Persistence
        # CRITICAL FIX: Separate album_art_url (top left display) from background_image_url (background)
        result = {
            "id": track.get("track_id"),    # CHANGED: Use REAL Spotify ID (fixes Like button)
            "track_id": captured_track_id,  # ADDED: Normalized ID (fixes Visual Mode detection)
            "artist": track["artist"],
            "title": track["title"],
            "album": track.get("album"),
            "position": track["progress_ms"] / 1000,
            "duration_ms": track.get("duration_ms"),
            "colors": colors,
            "album_art_url": album_art_url,  # ALWAYS album art (for top left display)
            "background_image_url": background_image_url if background_image_url else album_art_url,  # Artist image if selected, else album art
            "is_playing": is_playing,  # Use actual Spotify state (supports paused)
            "source": "spotify",
            "artist_id": track.get("artist_id"),  # For fetching artist images
            "artist_name": track.get("artist_name"),  # For display purposes
            "background_style": saved_background_style,  # Return saved style preference (Phase 2)
            "url": track.get("url"),  # Spotify Web URL for album art click functionality
            "last_active_time": state._spotify_last_active_time,  # For paused timeout
            # Playback control states (for shuffle/repeat button UI)
            "shuffle_state": track.get("shuffle_state"),
            "repeat_state": track.get("repeat_state")
        }
        
        # Add album_art_path if we have a direct path (DB file)
        if album_art_path:
            result["album_art_path"] = album_art_path
        
        # Add background_image_path if it exists (for server.py to serve background)
        if background_image_path:
            result["background_image_path"] = background_image_path
        
        return result
    except Exception as e:
        logger.error(f"Spotify API Error: {e}")
        return None
