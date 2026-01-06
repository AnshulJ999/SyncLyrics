"""
Windows Media metadata fetcher for system_utils package.

Dependencies: state, helpers, image, album_art, artist_image
"""
from __future__ import annotations
import os
import json
import time
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List

from . import state
from .helpers import create_tracked_task, _remove_text_inside_parentheses_and_brackets, _normalize_track_id, _cleanup_artist_image_log_throttle
from .album_art import get_album_db_folder, load_album_art_from_db, ensure_album_art_db
from .artist_image import load_artist_image_from_db, _get_artist_image_fallback, ensure_artist_image_db
from config import CACHE_DIR
from logging_config import get_logger

logger = get_logger(__name__)

# Module-level state for Windows media manager
_win_media_manager = None

# Try to import Windows-specific modules
try:
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as MediaManager
    from winsdk.windows.storage.streams import DataReader
except ImportError:
    logger.debug("Winsdk not installed. Windows Media integration will not work.")
    MediaManager = None
    DataReader = None


def _save_windows_thumbnail_sync(path: Path, data: bytes) -> bool:
    """
    Helper function to save Windows thumbnail in a thread (Fix #2).
    This prevents blocking the event loop when writing large BMP files.
    
    Args:
        path: Path where to save the thumbnail
        data: Raw image bytes to write
        
    Returns:
        True if successful, False otherwise
    """
    import uuid
    
    try:
        # FIX: Use unique temp filename to prevent concurrent downloads from overwriting each other
        # This prevents race conditions when the same image URL is downloaded multiple times simultaneously
        temp_filename = f"{path.stem}_{uuid.uuid4().hex}{path.suffix}.tmp"
        temp_path = path.parent / temp_filename
        # Write to temp file first
        with open(temp_path, "wb") as f:
            f.write(data)
        # Atomic replace
        if path.exists():
            try:
                os.remove(path)
            except:
                pass
        os.replace(temp_path, path)
        return True
    except Exception as e:
        logger.debug(f"Failed to save Windows thumbnail: {e}")
        try:
            if temp_path.exists():
                os.remove(temp_path)
        except:
            pass
        return False


def _extract_thumbnail_sync(thumbnail_ref, track_id: str) -> Optional[bytes]:
    """
    Extract Windows SMTC thumbnail in a SYNC function using an isolated event loop.
    
    This MUST be called from a thread (via run_in_executor) to avoid deadlocking
    the main Hypercorn ASGI event loop. WinRT async operations don't properly
    yield to Python's asyncio when run inside an ASGI server's event loop.
    
    Args:
        thumbnail_ref: WinRT RandomAccessStreamReference from SMTC
        track_id: Track identifier for logging
        
    Returns:
        Thumbnail bytes if successful, None otherwise
    """
    if not thumbnail_ref:
        return None
    
    # Create a NEW event loop for this thread (isolated from main ASGI loop)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        async def _do_extract():
            try:
                # Open the stream with timeout
                stream = await asyncio.wait_for(
                    thumbnail_ref.open_read_async(),
                    timeout=3.0  # Slightly longer than before since we're in dedicated thread
                )
                
                if not stream or stream.size == 0:
                    logger.debug(f"Windows thumbnail: stream empty for '{track_id}'")
                    return None
                
                logger.debug(f"Windows thumbnail: stream opened, size={stream.size} for '{track_id}'")
                
                # Read the data
                reader = DataReader(stream)
                await asyncio.wait_for(
                    reader.load_async(stream.size),
                    timeout=3.0
                )
                
                byte_data = bytearray(stream.size)
                reader.read_bytes(byte_data)
                
                logger.debug(f"Windows thumbnail: read {len(byte_data)} bytes for '{track_id}'")
                return bytes(byte_data)
                
            except asyncio.TimeoutError:
                logger.debug(f"Windows thumbnail: extraction timed out (3.0s) for '{track_id}'")
                return None
            except Exception as e:
                logger.debug(f"Windows thumbnail: extraction failed for '{track_id}': {type(e).__name__}: {e}")
                return None
        
        # Run in the isolated event loop
        return loop.run_until_complete(_do_extract())
        
    except Exception as e:
        logger.debug(f"Windows thumbnail: thread extraction failed for '{track_id}': {e}")
        return None
    finally:
        try:
            loop.close()
        except:
            pass


# ==========================================
# WINDOWS PLAYBACK CONTROLS
# ==========================================

async def _get_current_session():
    """Get the current Windows media session for playback control."""
    global _win_media_manager
    if not MediaManager:
        return None
    
    try:
        if _win_media_manager is None:
            _win_media_manager = await MediaManager.request_async()
        
        if _win_media_manager:
            return _win_media_manager.get_current_session()
    except Exception as e:
        logger.debug(f"Failed to get Windows media session: {e}")
    return None


async def windows_play() -> bool:
    """Resume playback on current Windows media session."""
    session = await _get_current_session()
    if session:
        try:
            await session.try_play_async()
            logger.debug("Windows playback: play")
            return True
        except Exception as e:
            logger.warning(f"Windows play failed: {e}")
    return False


async def windows_pause() -> bool:
    """Pause playback on current Windows media session."""
    session = await _get_current_session()
    if session:
        try:
            await session.try_pause_async()
            logger.debug("Windows playback: pause")
            return True
        except Exception as e:
            logger.warning(f"Windows pause failed: {e}")
    return False


async def windows_toggle_playback() -> bool:
    """Toggle play/pause on current Windows media session."""
    session = await _get_current_session()
    if session:
        try:
            await session.try_toggle_play_pause_async()
            logger.debug("Windows playback: toggle")
            return True
        except Exception as e:
            logger.warning(f"Windows toggle failed: {e}")
    return False


async def windows_next() -> bool:
    """Skip to next track on current Windows media session."""
    session = await _get_current_session()
    if session:
        try:
            await session.try_skip_next_async()
            logger.debug("Windows playback: next")
            return True
        except Exception as e:
            logger.warning(f"Windows next failed: {e}")
    return False


async def windows_previous() -> bool:
    """Skip to previous track on current Windows media session."""
    session = await _get_current_session()
    if session:
        try:
            await session.try_skip_previous_async()
            logger.debug("Windows playback: previous")
            return True
        except Exception as e:
            logger.warning(f"Windows previous failed: {e}")
    return False

async def windows_seek(position_ms: int) -> bool:
    """Seek to position in current Windows media session.
    
    Args:
        position_ms: Position in milliseconds
        
    Returns:
        True if successful, False otherwise
    """
    session = await _get_current_session()
    if session:
        try:
            # Windows uses 100-nanosecond units (10,000 per millisecond)
            position_100ns = position_ms * 10000
            await session.try_change_playback_position_async(position_100ns)
            logger.debug(f"Windows playback: seek to {position_ms}ms")
            return True
        except Exception as e:
            logger.warning(f"Windows seek failed: {e}")
    return False


async def _get_current_song_meta_data_windows() -> Optional[dict]:
    """Windows Media metadata fetcher with standardized output."""
    global _win_media_manager
    if not MediaManager: 
        return None

    try:
        # Track metadata fetch (always, not just in debug mode)
        state._metadata_fetch_counters['windows_media'] += 1
            
        if _win_media_manager is None:
            _win_media_manager = await MediaManager.request_async()
        if not _win_media_manager: 
            return None

        current_session = _win_media_manager.get_current_session()
        if not current_session: 
            return None
        
        # --- APP BLOCKLIST CHECK ---
        # Get the App ID (e.g., "chrome.exe" or "Microsoft.MicrosoftEdge...")
        try:
            from settings import settings
            app_id = current_session.source_app_user_model_id.lower()
            blocklist = settings.get("system.windows.app_blocklist", [])
            
            # Track if this is a new app_id to avoid log spam
            is_new_app_id = (app_id != state._last_windows_app_id)
            
            # Only log when app_id changes to avoid log spam
            if is_new_app_id:
                logger.info(f"Windows Media detected from app_id: '{app_id}' (blocklist: {blocklist})")
                state._last_windows_app_id = app_id
            
            # Check if any blocklisted string is in the app_id
            if blocklist:
                for blocked_app in blocklist:
                    blocked_lower = blocked_app.lower()
                    if blocked_lower in app_id:
                        # Only log blocking when app_id first changes
                        if is_new_app_id:
                            logger.info(f"Ignoring media from blocked app: '{app_id}' (matched blocklist entry: '{blocked_app}')")
                        return None
                # If we get here, no match was found (detection already logged above if new app_id)
            else:
                # Blocklist is empty (detection already logged above if new app_id)
                pass
        except Exception as e:
            # Log the error instead of silently swallowing it
            logger.warning(f"Error checking app blocklist: {e} (app_id may be unavailable, allowing media to proceed)")
        # ---------------------------
            
        playback_info = current_session.get_playback_info()
        # FIX: Accept both Playing (4) and Paused (5) states
        # Windows PlaybackStatus enum: Closed=0, Opened=1, Changing=2, Stopped=3, Playing=4, Paused=5
        # Previously only accepted Playing (4), causing source to flip to Spotify when paused
        # This broke playback controls (would control Spotify instead of Windows app)
        playback_status = playback_info.playback_status if playback_info else None
        if playback_status not in (4, 5):  # 4 = Playing, 5 = Paused
            return None
            
        info = await current_session.try_get_media_properties_async()
        if not info: 
            return None
            
        artist = info.artist
        title = info.title
        album = info.album_title

        # ================================================================
        # FIX: Skip tracks with no artist metadata (non-music files)
        # This prevents wasted API calls for lyrics/album art that will fail anyway.
        # Uncomment to enable this fix.
        # ================================================================
        # if not artist:
        #     # Throttled log: only log once every 60 seconds to prevent spam
        #     current_time = time.time()
        #     if current_time - state._smtc_empty_artist_last_log_time >= state._SMTC_EMPTY_ARTIST_LOG_INTERVAL:
        #         state._smtc_empty_artist_last_log_time = current_time
        #         logger.debug(f"Windows SMTC: Skipping track with no artist: '{title}'")
        #     return None

        if not album:
            title = _remove_text_inside_parentheses_and_brackets(title)
            # artist = ""  # [REMOVED] Don't wipe artist name just because album is missing

        timeline = current_session.get_timeline_properties()
        if not timeline: 
            return None
            
        seconds = timeline.position.total_seconds()
        
        # Check for invalid timestamp (Windows epoch 1601-01-01)
        # We use a safe threshold like year 2000
        if timeline.last_updated_time.year < 2000:
            # Invalid timestamp means we can't calculate elapsed time
            # If position is also 0, we probably have no data
            if seconds == 0:
                return None
            position = seconds
        else:
            # FIX: Only interpolate position when PLAYING (status 4)
            # When paused (status 5), the song isn't advancing, so don't add elapsed time
            if playback_status == 4:
                elapsed = time.time() - timeline.last_updated_time.timestamp()
                # Cap interpolation to 5 seconds to prevent runaway drift
                # SMTC updates every 4-5s; this limits pause-detection lag
                elapsed = min(elapsed, 5.0)
                position = seconds + elapsed
            else:
                # Paused - use raw position without interpolation
                position = seconds
        
        # Get duration if available
        duration_ms = None
        try:
            duration_ms = int(timeline.end_time.total_seconds() * 1000)
        except:
            pass

        # Create track ID
        current_track_id = _normalize_track_id(artist, title)
        
        # Flag to track if we found art in DB
        found_in_db = False
        # CRITICAL FIX: Separate flag for album art (not artist image fallback)
        # This ensures background fetch triggers even when artist image fallback is used
        album_art_found_in_db = False
        album_art_url = None
        result_extra_fields = {}  # Store album_art_path for direct serving
        saved_background_style = None  # Initialize to prevent UnboundLocalError

        # CRITICAL FIX: Separate album art (top left display) from background image
        # Album art should ALWAYS be album art, background can be artist image if selected
        background_image_url = None
        background_image_path = None
        
        # 1. Always load album art for top left display (independent of artist image preference)
        # FIX: Run in executor to avoid blocking event loop during file I/O
        loop = asyncio.get_running_loop()
        db_result = await loop.run_in_executor(None, load_album_art_from_db, artist, album, title)
        if db_result:
            found_in_db = True
            album_art_found_in_db = True  # CRITICAL: Only set when actual album art is found
            db_image_path = db_result["path"]
            saved_background_style = db_result.get("background_style")  # Capture saved style
            
            # FIX: Add timestamp to URL to force browser cache busting when file updates
            mtime = int(time.time())
            try:
                if db_image_path.exists():
                    mtime = int(db_image_path.stat().st_mtime)
            except: pass
            
            # Album art URL is ALWAYS album art (for top left display)
            album_art_url = f"/cover-art?id={current_track_id}&t={mtime}"
            
            # NEW: Pass the path directly so server.py can serve it without copying
            # This eliminates race conditions from file copying
            result_extra_fields = {"album_art_path": str(db_image_path)}
            
            # Default background to album art (will be overridden if artist image is selected)
            background_image_url = album_art_url
            background_image_path = str(db_image_path)
        
        # 2. Check for artist image preference for background (separate from album art)
        # If user selected an artist image, use it for background instead of album art
        # FIX: Run in executor to avoid blocking event loop during file I/O
        # FIX 6.1: Use album or title fallback to match server.py preference save path
        album_or_title = album if album else title
        artist_image_result = await loop.run_in_executor(None, load_artist_image_from_db, artist, album_or_title)
        if artist_image_result:
            artist_image_path = artist_image_result["path"]
            if artist_image_path.exists():
                mtime = int(artist_image_path.stat().st_mtime)
                # Use artist image for background (not for album art display)
                # Add type=background parameter so server knows to serve background_image_path
                background_image_url = f"/cover-art?id={current_track_id}&t={mtime}&type=background"
                background_image_path = str(artist_image_path)
                # CRITICAL FIX: Throttle log to prevent spam (30+ logs per second)
                current_time = time.time()
                log_key = f"preferred_bg_{artist}"
                last_log_time = state._artist_image_log_throttle.get(log_key, 0)
                if (current_time - last_log_time) >= state._ARTIST_IMAGE_LOG_THROTTLE_SECONDS:
                    logger.debug(f"Using preferred artist image for background: {artist}")
                    state._artist_image_log_throttle[log_key] = current_time
                    _cleanup_artist_image_log_throttle()
        
        # CRITICAL FIX: Check if artist images DB is populated with ALL expected sources
        # This ensures all provider options are available in the selection menu (similar to album art backfill)
        # Only check if we have an artist name (required for folder lookup)
        if artist:
            try:
                artist_folder = get_album_db_folder(artist, None)
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
                            
                            # NOTE: Spotify and Last.fm are excluded for Windows Media source
                            # Windows Media doesn't provide artist_id, so Spotify fallback isn't available
                            # Last.fm is excluded from backfill as it's not necessary
                            
                            # Check if we have all expected sources
                            artist_images_complete = expected_sources.issubset(existing_sources)
                    except Exception as e:
                        logger.debug(f"Failed to check artist images completeness: {e}")
                        artist_images_complete = False
                
                # Trigger background task ONLY if artist images are incomplete (and not already running)
                # Use artist name only as key (consistent with ensure_artist_image_db)
                artist_request_key = artist
                
                if not artist_images_complete and artist_request_key not in state._artist_download_tracker:
                    # Start background task to fetch from ALL missing sources
                    async def background_artist_images_backfill():
                        """Background task to fetch artist images from all enabled sources"""
                        try:
                            # This will fetch from Deezer, TheAudioDB, and FanArt.tv (if key exists)
                            # Spotify is not available for Windows Media (no artist_id)
                            # Last.fm is excluded per user preference
                            await ensure_artist_image_db(artist, None)  # No artist_id for Windows Media
                        except Exception as e:
                            logger.debug(f"Background artist image backfill failed for {artist}: {e}")
                    
                    # Use tracked task to prevent silent failures
                    create_tracked_task(background_artist_images_backfill())
            except Exception as e:
                logger.debug(f"Failed to check/trigger artist image backfill: {e}")
        
        # Fallback: Check for artist image if no album art found (but no explicit preference)
        # This uses first available artist image as fallback when no album art exists
        # Only use for background, not for album art display
        # CRITICAL FIX #2: Use album_art_found_in_db check - don't set found_in_db here
        # found_in_db is for display purposes only, album_art_found_in_db controls background fetch
        if not album_art_found_in_db:
            fallback_result = _get_artist_image_fallback(artist)
            if fallback_result:
                artist_image_path = fallback_result["path"]
                mtime = int(artist_image_path.stat().st_mtime)
                # Use fallback artist image for both (only when no album art exists)
                album_art_url = f"/cover-art?id={current_track_id}&t={mtime}"
                background_image_url = album_art_url
                result_extra_fields = {"album_art_path": str(artist_image_path)}
                background_image_path = str(artist_image_path)
                found_in_db = True  # For display purposes only - album_art_found_in_db stays False
                # CRITICAL FIX: Throttle log to prevent spam (30+ logs per second)
                # Use same throttle mechanism as artist image fetching
                current_time = time.time()
                log_key = f"fallback_{artist}"
                last_log_time = state._artist_image_log_throttle.get(log_key, 0)
                if (current_time - last_log_time) >= state._ARTIST_IMAGE_LOG_THROTTLE_SECONDS:
                    logger.debug(f"Using artist image '{fallback_result.get('source')}' as fallback for {artist}")
                    state._artist_image_log_throttle[log_key] = current_time
                    _cleanup_artist_image_log_throttle()

        # 2. Windows Thumbnail Extraction (Fallback)
        # Only if not found in DB
        if not album_art_found_in_db:
            try:
                thumbnail_ref = info.thumbnail
                # Create a unique filename for this track's thumbnail to avoid race conditions
                # e.g., thumb_Artist_Title.jpg
                thumb_filename = f"thumb_{current_track_id}.jpg"
                thumb_path = CACHE_DIR / thumb_filename
                
                # FIX: Only extract if track changed (don't retry same track even if file doesn't exist)
                # This prevents infinite retry loops when thumbnail extraction fails/times out
                should_extract = thumbnail_ref and current_track_id != state._last_windows_track_id
                
                if should_extract:
                    # CRITICAL: Mark as tried FIRST, before any async operations
                    # This prevents retry spam even if the extraction fails
                    state._last_windows_track_id = current_track_id
                    
                    # Diagnostic logging (throttled - only on track change, so max once per song)
                    logger.debug(f"Windows thumbnail: attempting extraction for '{current_track_id}' (has_ref: True)")
                    
                    # CRITICAL FIX: Run WinRT calls in a separate thread with isolated event loop
                    # This avoids deadlocks with Hypercorn's ASGI event loop - WinRT async ops
                    # don't properly yield to Python asyncio when run inside an ASGI server
                    try:
                        byte_data = await asyncio.wait_for(
                            asyncio.to_thread(_extract_thumbnail_sync, thumbnail_ref, current_track_id),
                            timeout=5.0  # Extra margin for thread scheduling
                        )
                        
                        if byte_data:
                            # Save the thumbnail
                            loop = asyncio.get_running_loop()
                            save_ok = await loop.run_in_executor(None, _save_windows_thumbnail_sync, thumb_path, byte_data)
                            
                            if save_ok:
                                logger.debug(f"Windows thumbnail: saved to {thumb_path}")
                                # Cleanup: Delete OLD thumbnails to keep cache small
                                # We only keep the current one
                                for f in CACHE_DIR.glob("thumb_*.jpg"):
                                    if f.name != thumb_filename:
                                        try:
                                            os.remove(f)
                                        except:
                                            pass
                            else:
                                logger.debug(f"Windows thumbnail: save failed for '{current_track_id}'")
                        else:
                            logger.debug(f"Windows thumbnail: no data returned for '{current_track_id}'")
                            
                    except asyncio.TimeoutError:
                        logger.debug(f"Windows thumbnail: thread extraction timed out (5.0s) for '{current_track_id}'")
                    except Exception as e:
                        logger.debug(f"Windows thumbnail: thread extraction error for '{current_track_id}': {type(e).__name__}: {e}")
                
                # If the file exists (either just saved or already there), use it
                if thumb_path.exists():
                    # Use file's mtime for stable URL (not time.time() which changes every second)
                    thumb_mtime = int(thumb_path.stat().st_mtime)
                    album_art_url = f"/cover-art?id={current_track_id}&t={thumb_mtime}"
                    result_extra_fields = {"album_art_path": str(thumb_path)}
            except Exception as e:
                logger.debug(f"Windows thumbnail: unexpected error: {e}")

        # 3. Background High-Res Fetch (Progressive Upgrade)
        # Only if not found in DB and not checked this session
        # CRITICAL FIX #2: Use album_art_found_in_db instead of found_in_db
        # This ensures background fetch triggers even when artist image fallback is used
        # Use 'win::' namespace to avoid blocking Spotify fetcher which might have better URLs
        checked_key = f"win::{current_track_id}"
        
        # FIX: Check negative cache first (prevents retry spam for non-music files)
        if checked_key in state._no_art_found_cache:
            cache_time = state._no_art_found_cache[checked_key]
            if time.time() - cache_time < state._NO_ART_FOUND_TTL:
                pass  # Skip - no art found recently, don't retry yet
            else:
                # TTL expired, remove from negative cache to allow retry
                del state._no_art_found_cache[checked_key]
        
        if not album_art_found_in_db and checked_key not in state._db_checked_tracks and checked_key not in state._no_art_found_cache:
            if current_track_id not in state._running_art_upgrade_tasks:
                 state._db_checked_tracks[checked_key] = time.time()
                 if len(state._db_checked_tracks) > state._MAX_DB_CHECKED_SIZE:
                     state._db_checked_tracks.popitem(last=False)  # Remove oldest (FIFO)
                     
                 async def background_windows_art_upgrade():
                     try:
                         # Fetch and save to DB (no spotify_url available)
                         result = await ensure_album_art_db(artist, album, title, None)
                         # FIX: On None result, add to negative cache instead of removing from checked
                         # This prevents infinite retry loop for tracks with no art (non-music files)
                         if not result:
                             # No art found - add to negative cache (don't retry for _NO_ART_FOUND_TTL seconds)
                             state._no_art_found_cache[checked_key] = time.time()
                             if len(state._no_art_found_cache) > state._MAX_NO_ART_FOUND_CACHE_SIZE:
                                 oldest = min(state._no_art_found_cache, key=state._no_art_found_cache.get)
                                 del state._no_art_found_cache[oldest]
                             # CRITICAL: Also pop from _db_checked_tracks so TTL retry can work
                             state._db_checked_tracks.pop(checked_key, None)
                         # We don't need to do anything else; the NEXT poll loop 
                         # will see the file in DB (step 1 above) and auto-upgrade the UI.
                     except Exception as e:
                         logger.debug(f"Windows background art fetch failed: {e}")
                         # On exception (network error), also add to negative cache
                         # This prevents retry spam when network is down
                         state._no_art_found_cache[checked_key] = time.time()
                         # CRITICAL: Also pop from _db_checked_tracks so TTL retry can work
                         state._db_checked_tracks.pop(checked_key, None)
                     finally:
                         # CRITICAL FIX: Always remove from running tasks, even if task creation failed
                         state._running_art_upgrade_tasks.pop(current_track_id, None)
                         
                 # CRITICAL FIX: Wrap task creation in try/finally to ensure cleanup
                 try:
                     task = create_tracked_task(background_windows_art_upgrade())
                     state._running_art_upgrade_tasks[current_track_id] = task
                 except Exception as e:
                     # If task creation fails, ensure cleanup happens
                     state._running_art_upgrade_tasks.pop(current_track_id, None)
                     logger.debug(f"Failed to create Windows art upgrade task: {e}")
                     raise

                 # FIX: Wait for DB to avoid flicker
                 try:
                     # Refined Fix: Use asyncio.wait to avoid cancelling the background task on timeout
                     # wait_for() would CANCEL the task on timeout, killing the download
                     # wait() just returns - task stays in pending set and continues running
                     done, pending = await asyncio.wait([task], timeout=0.3)
                     
                     if task in done:
                         # Task completed within timeout - check DB for high-res art
                         # FIX: Run in executor to avoid blocking event loop during file I/O
                         db_result = await loop.run_in_executor(None, load_album_art_from_db, artist, album, title)
                         if db_result:
                             # Found it! Update variables to use High-Res immediately
                             found_in_db = True
                             db_image_path = db_result["path"]
                             
                             # NEW: Use path directly instead of copying (eliminates race conditions)
                             try:
                                 # FIX: Add timestamp for cache busting
                                 mtime = int(time.time())
                                 try:
                                     if db_image_path.exists():
                                         mtime = int(db_image_path.stat().st_mtime)
                                 except: pass
                                 album_art_url = f"/cover-art?id={current_track_id}&t={mtime}"
                                 result_extra_fields = {"album_art_path": str(db_image_path)}
                             except Exception as e:
                                 logger.debug(f"Failed to set DB art path after wait: {e}")
                     # If task in pending: timeout occurred, but task continues in background
                     # Next poll cycle will see the result in DB
                 except Exception:
                     pass  # Fallback to Windows thumbnail

        # Re-fetch app_id safely for frontend control logic (optimistic enabling)
        # This allows frontend to enable controls immediately for Spotify app before enrichment completes
        app_id = None
        try:
            app_id = current_session.source_app_user_model_id.lower()
        except:
            pass  # If unavailable, app_id remains None (frontend will wait for enrichment)

        # CRITICAL FIX: Separate album_art_url (top left display) from background_image_url (background)
        result = {
            "track_id": current_track_id,  # ADDED: Normalized ID for frontend change detection
            "artist": artist,
            "title": title,
            "album": album if album else None,
            "position": position,
            "duration_ms": duration_ms,
            "colors": ("#24273a", "#363b54"),
            "album_art_url": album_art_url,  # ALWAYS album art (for top left display)
            "background_image_url": background_image_url if background_image_url else album_art_url,  # Artist image if selected, else album art
            "is_playing": playback_status == 4,  # True only if Playing (not Paused)
            "source": "windows_media",
            "app_id": app_id,  # ADDED: Pass app_id for optimistic control enabling (enables controls for spotify.exe before enrichment)
            "background_style": saved_background_style  # Return saved style preference
        }
        
        # Track last active time for paused timeout logic
        if playback_status == 4:  # Playing
            state._windows_last_active_time = time.time()
        
        # Include last_active_time in result for metadata.py timeout check
        result["last_active_time"] = state._windows_last_active_time
        
        # Add album_art_path if we have a direct path (DB file or unique thumbnail)
        if result_extra_fields.get("album_art_path"):
            result["album_art_path"] = result_extra_fields["album_art_path"]
        
        # CRITICAL FIX: Add background_image_path if it exists (for server.py to serve background)
        # This was missing in Windows Media function but present in Spotify function
        if background_image_path:
            result["background_image_path"] = background_image_path
        
        return result
            
    except Exception as e:
        logger.error(f"Windows Media Error: {e}")
        _win_media_manager = None
        return None
