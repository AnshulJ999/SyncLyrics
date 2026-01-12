"""
Shared enrichment pipeline for plugin sources.

This is completely SEPARATE from legacy source enrichment in metadata.py.
It replicates the same functionality but is isolated to prevent any risk
to legacy sources.

Handles:
- Album art DB lookup and background fetch
- Artist image preference and backfill
- Color extraction from local art
- Background tasks for progressive enhancement

Note: This is intentionally duplicated from metadata.py logic.
Future optimization could extract shared helpers, but for v1,
isolation is prioritized over DRY.
"""
import time
import asyncio
from pathlib import Path
from typing import Dict, Any
from logging_config import get_logger

logger = get_logger(__name__)


async def enrich_plugin_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply full enrichment to plugin source metadata.
    
    This function replicates all enrichment logic from metadata.py but
    is completely isolated. If this has a bug, legacy sources are unaffected.
    
    Enrichment includes:
    1. Album art DB lookup (use cached art if available)
    2. Artist image preference (use selected artist image for background)
    3. Fallback to artist image if no album art
    4. Color extraction from local art
    5. Background art fetch (if not in DB)
    6. Artist image backfill (fetch from all providers)
    
    Args:
        metadata: Raw metadata dict from plugin source
        
    Returns:
        Enriched metadata dict with album art, colors, artist images, etc.
    """
    # Import here to avoid circular imports
    from ..album_art import load_album_art_from_db, ensure_album_art_db
    from ..artist_image import load_artist_image_from_db, ensure_artist_image_db, _get_artist_image_fallback
    from ..image import extract_dominant_colors
    from ..helpers import create_tracked_task, _normalize_track_id
    from .. import state
    
    result = metadata.copy()
    
    # === Extract base info with safe defaults ===
    artist = result.get('artist', '')
    title = result.get('title', '')
    album = result.get('album')
    source_name = result.get('source', 'plugin')
    
    if not artist or not title:
        # Can't enrich without basic info
        return result
    
    # Ensure track_id exists (required for caching and change detection)
    if not result.get('track_id'):
        result['track_id'] = _normalize_track_id(artist, title)
    track_id = result['track_id']
    
    # Ensure default colors (will be overwritten if local art exists)
    if 'colors' not in result or result['colors'] is None:
        result['colors'] = ("#24273a", "#363b54")
    
    # Ensure last_active_time for paused timeout logic
    if 'last_active_time' not in result:
        result['last_active_time'] = time.time() if result.get('is_playing') else 0
    
    # Get event loop for executor calls
    loop = asyncio.get_running_loop()
    
    # === STEP 1: Album Art DB Lookup ===
    album_art_found_in_db = False
    saved_background_style = None
    
    try:
        db_result = await loop.run_in_executor(None, load_album_art_from_db, artist, album, title)
        if db_result:
            album_art_found_in_db = True
            db_path = db_result.get("path")
            saved_background_style = db_result.get("background_style")
            
            if db_path and db_path.exists():
                mtime = int(db_path.stat().st_mtime)
                local_url = f"/cover-art?id={track_id}&t={mtime}"
                result["album_art_url"] = local_url
                result["album_art_path"] = str(db_path)
                result["background_image_url"] = local_url
                result["background_image_path"] = str(db_path)
            
            if saved_background_style:
                result["background_style"] = saved_background_style
    except Exception as e:
        logger.debug(f"Plugin enrichment: Album art DB lookup failed: {e}")
    
    # === STEP 2: Artist Image Preference (for background) ===
    try:
        album_or_title = album if album else title
        artist_img = await loop.run_in_executor(None, load_artist_image_from_db, artist, album_or_title)
        
        if artist_img:
            img_path = artist_img["path"]
            if img_path.exists():
                mtime = int(img_path.stat().st_mtime)
                result["background_image_url"] = f"/cover-art?id={track_id}&t={mtime}&type=background"
                result["background_image_path"] = str(img_path)
    except Exception as e:
        logger.debug(f"Plugin enrichment: Artist image lookup failed: {e}")
    
    # === STEP 3: Fallback to Artist Image if No Album Art ===
    if not album_art_found_in_db:
        try:
            fallback = _get_artist_image_fallback(artist)
            if fallback and fallback["path"].exists():
                mtime = int(fallback["path"].stat().st_mtime)
                result["album_art_url"] = f"/cover-art?id={track_id}&t={mtime}"
                result["album_art_path"] = str(fallback["path"])
                # Only set background if not already set by artist preference
                if "background_image_url" not in result or not result["background_image_url"]:
                    result["background_image_url"] = result["album_art_url"]
                    result["background_image_path"] = result["album_art_path"]
        except Exception as e:
            logger.debug(f"Plugin enrichment: Artist fallback failed: {e}")
    
    # === STEP 4: Color Extraction (if we have local art and default colors) ===
    if result.get("album_art_path") and result.get("colors") == ("#24273a", "#363b54"):
        try:
            local_path = Path(result["album_art_path"])
            if local_path.exists():
                result["colors"] = await extract_dominant_colors(local_path)
        except Exception as e:
            logger.debug(f"Plugin enrichment: Color extraction failed: {e}")
    
    # === STEP 5: Background Art Fetch (if not in DB) ===
    checked_key = f"{source_name}::{track_id}"
    
    # Check negative cache (prevents retry spam for tracks with no art)
    if checked_key in state._no_art_found_cache:
        cache_time = state._no_art_found_cache[checked_key]
        if time.time() - cache_time >= state._NO_ART_FOUND_TTL:
            del state._no_art_found_cache[checked_key]  # TTL expired
    
    if (not album_art_found_in_db 
        and checked_key not in state._db_checked_tracks 
        and checked_key not in state._no_art_found_cache
        and track_id not in state._running_art_upgrade_tasks):
        
        # Mark as checked to prevent duplicate tasks
        state._db_checked_tracks[checked_key] = time.time()
        if len(state._db_checked_tracks) > state._MAX_DB_CHECKED_SIZE:
            state._db_checked_tracks.popitem(last=False)  # FIFO eviction
        
        # Capture variables for closure (prevents race conditions)
        cap_artist, cap_album, cap_title = artist, album, title
        cap_art_url = result.get("album_art_url")
        cap_track_id, cap_checked_key = track_id, checked_key
        
        async def background_art_fetch():
            """Background task to fetch and cache album art."""
            try:
                # Only pass URL if it's remote (not local path)
                url = cap_art_url if cap_art_url and not cap_art_url.startswith('/') else None
                art_result = await ensure_album_art_db(cap_artist, cap_album, cap_title, url)
                if not art_result:
                    # No art found - add to negative cache
                    state._no_art_found_cache[cap_checked_key] = time.time()
                    if len(state._no_art_found_cache) > state._MAX_NO_ART_FOUND_CACHE_SIZE:
                        oldest = min(state._no_art_found_cache, key=state._no_art_found_cache.get)
                        del state._no_art_found_cache[oldest]
                    state._db_checked_tracks.pop(cap_checked_key, None)
            except Exception as e:
                logger.debug(f"Plugin background art fetch failed for {cap_artist} - {cap_title}: {e}")
                state._no_art_found_cache[cap_checked_key] = time.time()
                state._db_checked_tracks.pop(cap_checked_key, None)
            finally:
                state._running_art_upgrade_tasks.pop(cap_track_id, None)
        
        try:
            task = create_tracked_task(background_art_fetch())
            state._running_art_upgrade_tasks[track_id] = task
        except Exception as e:
            logger.debug(f"Failed to create background art task: {e}")
            state._running_art_upgrade_tasks.pop(track_id, None)
    
    # === STEP 6: Artist Image Backfill ===
    artist_id = result.get("artist_id")
    if artist and artist not in state._artist_download_tracker:
        # Capture for closure
        cap_artist, cap_artist_id = artist, artist_id
        
        async def background_artist_images():
            """Background task to fetch artist images from all sources."""
            try:
                await ensure_artist_image_db(cap_artist, cap_artist_id)
            except Exception as e:
                logger.debug(f"Plugin artist image fetch failed for {cap_artist}: {e}")
        
        try:
            create_tracked_task(background_artist_images())
        except Exception as e:
            logger.debug(f"Failed to create artist image task: {e}")
    
    return result
