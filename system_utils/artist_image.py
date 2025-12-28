"""
Artist Image Database module for system_utils package.
Handles artist image storage, retrieval, and fetching from providers.

Dependencies: state, helpers, album_art
"""
from __future__ import annotations
import os
import json
import time
import asyncio
import shutil
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime
from urllib.parse import quote

from PIL import Image

from . import state
from .helpers import create_tracked_task, _cleanup_artist_image_log_throttle
from .album_art import get_album_db_folder, save_album_db_metadata, discover_custom_images, _download_and_save_sync
from config import FEATURES
from logging_config import get_logger
from providers.artist_image import ArtistImageProvider
from providers.spotify_api import get_shared_spotify_client

logger = get_logger(__name__)


def load_artist_image_from_db(artist: str, album: str = None) -> Optional[Dict[str, Any]]:
    """
    Load preferred artist image from database if available.
    Returns the preferred image path if found.
    
    OPTIMIZATION: Results are cached to avoid calling discover_custom_images on every poll cycle (10x per second).
    Cache refreshes when artist/album changes or after 15 seconds.
    
    PER-ALBUM PREFERENCES (6.1):
    - Preference is read from ALBUM folder (ArtistName - AlbumName/metadata.json)
    - Image files are still read from ARTIST folder (ArtistName/)
    - This allows different albums to have different background preferences
    
    Args:
        artist: Artist name
        album: Album name (optional, for per-album preferences)
        
    Returns:
        Dictionary with 'path' (Path to image) and 'metadata' (full metadata dict) if found, None otherwise
    """
    # Check if feature is enabled
    if not FEATURES.get("album_art_db", True):
        return None
    
    # LEGACY MODE FLAG: Set to True to use artist-wide preferences (old behavior)
    # When False (default), uses per-album preferences
    ARTIST_IMAGE_LEGACY_MODE = False
    
    # ----------------------------------------------------------------------
    # DEAD CODE: Legacy artist-wide preference logic
    # Flip ARTIST_IMAGE_LEGACY_MODE = True above to restore this behavior
    # ----------------------------------------------------------------------
    if ARTIST_IMAGE_LEGACY_MODE:
        # Use artist-only folder for preference lookup (old behavior)
        preference_folder = get_album_db_folder(artist, None)
    else:
        # NEW: Use album folder for preference lookup (per-album behavior)
        # If album is None/empty, fall back to artist folder
        if album:
            preference_folder = get_album_db_folder(artist, album)
        else:
            preference_folder = get_album_db_folder(artist, None)
    # ----------------------------------------------------------------------
    
    # Artist folder always used for actual image files
    artist_folder = get_album_db_folder(artist, None)
    
    # Cache key includes album for per-album caching
    cache_key = (artist, album) if album else (artist, None)
    
    # OPTIMIZATION: Check cache first to avoid repeated file I/O and discovery calls
    current_time = time.time()
    if cache_key in state._artist_image_load_cache:
        cached_time, cached_result = state._artist_image_load_cache[cache_key]
        # Use cache if less than TTL seconds old
        if (current_time - cached_time) < state._ARTIST_IMAGE_CACHE_TTL:
            return cached_result
    
    try:
        # Check for preference in album folder first (new per-album behavior)
        preference_metadata_path = preference_folder / "metadata.json"
        artist_metadata_path = artist_folder / "metadata.json"
        
        preferred_filename = None
        preferred_provider = None
        
        # Try to load preference from album folder
        if preference_metadata_path.exists():
            try:
                with open(preference_metadata_path, 'r', encoding='utf-8') as f:
                    pref_metadata = json.load(f)
                
                # Look for the new per-album preference field
                preferred_filename = pref_metadata.get("preferred_artist_image_filename")
                # Also check legacy fields for backward compatibility (if reading from artist folder in legacy mode)
                if not preferred_filename and ARTIST_IMAGE_LEGACY_MODE:
                    preferred_filename = pref_metadata.get("preferred_image_filename")
                    preferred_provider = pref_metadata.get("preferred_provider")
            except Exception as e:
                logger.debug(f"Failed to load preference metadata: {e}")
        
        # ===========================================================================
        # CRITICAL: Load artist metadata and run self-healing BEFORE preference check
        # This ensures orphaned entries get cleaned up even when no preference is set
        # ===========================================================================
        
        # Load artist images metadata (from artist folder) - needed for self-healing
        if not artist_metadata_path.exists():
            # No metadata file - cache and return early
            if len(state._artist_image_load_cache) >= state._MAX_ARTIST_IMAGE_CACHE_SIZE:
                oldest_key = next(iter(state._artist_image_load_cache))
                del state._artist_image_load_cache[oldest_key]
            state._artist_image_load_cache[cache_key] = (current_time, None)
            return None
        
        with open(artist_metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        # Check if this is artist images metadata
        if metadata.get("type") != "artist_images":
            if len(state._artist_image_load_cache) >= state._MAX_ARTIST_IMAGE_CACHE_SIZE:
                oldest_key = next(iter(state._artist_image_load_cache))
                del state._artist_image_load_cache[oldest_key]
            state._artist_image_load_cache[cache_key] = (current_time, None)
            return None
        
        # CRITICAL FIX: Auto-discover custom images that aren't in metadata
        # This allows users to drop images into folders without manual JSON editing
        # Uses mtime caching to avoid performance impact on every metadata load
        metadata = discover_custom_images(artist_folder, metadata, is_artist_images=True)
        
        # If new images were discovered, save updated metadata
        try:
            folder_key = str(artist_folder.resolve())
        except (OSError, ValueError) as e:
            logger.debug(f"Could not resolve folder path for cache key: {e}")
            folder_key = str(artist_folder)
        if folder_key in state._discovery_cache:
            _, discovered_count = state._discovery_cache[folder_key]
            if discovered_count > 0:
                save_album_db_metadata(artist_folder, metadata)
                metadata_path_str = str(artist_metadata_path)
                if metadata_path_str in state._album_art_metadata_cache:
                    del state._album_art_metadata_cache[metadata_path_str]
        
        # CRITICAL FIX: Self-healing - remove images from metadata if files are deleted
        # This ensures metadata stays in sync with actual files on disk
        # Mirrors the same pattern used in album_art.py for album art providers
        # NOTE: This runs BEFORE the preference check to clean up orphaned entries
        #       even when no preference is set (e.g., user never selected an artist image)
        images = metadata.get("images", [])
        removed_count = 0
        images_to_keep = []
        for img in images:
            filename = img.get("filename")
            if not filename:
                images_to_keep.append(img)  # Keep entries without filename (shouldn't happen, but defensive)
                continue
            file_path = artist_folder / filename
            # If file doesn't exist but metadata says it's downloaded, remove it
            if img.get("downloaded", False) and not file_path.exists():
                removed_count += 1
                logger.debug(f"Self-healing: Removing missing artist image '{filename}' from metadata for '{artist}'")
            else:
                images_to_keep.append(img)
        
        # Update metadata with cleaned images list
        if removed_count > 0:
            metadata["images"] = images_to_keep
            images = images_to_keep  # Update local reference
            # Save updated metadata
            save_album_db_metadata(artist_folder, metadata)
            # Invalidate cache after save
            metadata_path_str = str(artist_metadata_path)
            if metadata_path_str in state._album_art_metadata_cache:
                del state._album_art_metadata_cache[metadata_path_str]
            logger.info(f"Self-healing: Removed {removed_count} missing artist image(s) from metadata for '{artist}'")
        
        # ===========================================================================
        # NOW check preference - if no preference set, return None
        # Self-healing has already run above, so orphaned entries are cleaned up
        # ===========================================================================
        if not preferred_filename and not preferred_provider:
            # Cache None result to avoid repeated checks
            if len(state._artist_image_load_cache) >= state._MAX_ARTIST_IMAGE_CACHE_SIZE:
                oldest_key = next(iter(state._artist_image_load_cache))
                del state._artist_image_load_cache[oldest_key]
            state._artist_image_load_cache[cache_key] = (current_time, None)
            return None
        
        # Find preferred image
        matching_image = None
        
        # 1. Match by specific filename (MOST ROBUST)
        if preferred_filename:
            for img in images:
                if img.get("filename") == preferred_filename and img.get("downloaded"):
                    matching_image = img
                    break
        
        # 2. Fallback: Parse provider name (backward compatibility for legacy mode)
        if not matching_image and preferred_provider:
            provider_name_clean = preferred_provider.replace(" (Artist)", "")
            
            if " (" in provider_name_clean:
                parts = provider_name_clean.split(" (", 1)
                if len(parts) == 2:
                    source_name = parts[0]
                    filename_from_provider = parts[1].rstrip(")")
                    source_name_lower = source_name.lower()
                    for img in images:
                        source = img.get("source", "")
                        if (source.lower() == source_name_lower and 
                            img.get("filename") == filename_from_provider and 
                            img.get("downloaded")):
                            matching_image = img
                            break
                else:
                    source_name = parts[0]
                    source_name_lower = source_name.lower()
                    for img in images:
                        source = img.get("source", "")
                        if source.lower() == source_name_lower and img.get("downloaded") and img.get("filename"):
                            matching_image = img
                            break
            else:
                source_name = provider_name_clean
                source_name_lower = source_name.lower()
                for img in images:
                    source = img.get("source", "")
                    if source.lower() == source_name_lower and img.get("downloaded") and img.get("filename"):
                        matching_image = img
                        break
        
        if not matching_image:
            logger.debug(f"Preferred artist image not found for {artist}: preferred_filename={preferred_filename}")
            if len(state._artist_image_load_cache) >= state._MAX_ARTIST_IMAGE_CACHE_SIZE:
                oldest_key = next(iter(state._artist_image_load_cache))
                del state._artist_image_load_cache[oldest_key]
            state._artist_image_load_cache[cache_key] = (current_time, None)
            return None
        
        filename = matching_image.get("filename")
        image_path = artist_folder / filename  # Always from artist folder
        
        if not image_path.exists():
            return None
        
        # OPTIMIZATION: Only update last_accessed if it's been more than 1 hour
        should_save = True
        last_accessed_str = metadata.get("last_accessed")
        if last_accessed_str:
            try:
                if last_accessed_str.endswith('Z'):
                    last_accessed_str = last_accessed_str[:-1] + '+00:00'
                last_accessed = datetime.fromisoformat(last_accessed_str)
                if last_accessed.tzinfo is not None:
                    last_accessed = last_accessed.replace(tzinfo=None)
                time_diff = (datetime.utcnow() - last_accessed).total_seconds()
                if time_diff < 3600:
                    should_save = False
            except (ValueError, AttributeError):
                pass
        
        if should_save:
            metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
            if save_album_db_metadata(artist_folder, metadata):
                metadata_path_str = str(artist_metadata_path)
                if metadata_path_str in state._album_art_metadata_cache:
                    del state._album_art_metadata_cache[metadata_path_str]
        
        result = {"path": image_path, "metadata": metadata}
        
        # OPTIMIZATION: Cache result
        if len(state._artist_image_load_cache) >= state._MAX_ARTIST_IMAGE_CACHE_SIZE:
            oldest_key = next(iter(state._artist_image_load_cache))
            del state._artist_image_load_cache[oldest_key]
        state._artist_image_load_cache[cache_key] = (current_time, result)
        
        return result
        
    except Exception as e:
        logger.debug(f"Failed to load artist image from DB: {e}")
        if len(state._artist_image_load_cache) >= state._MAX_ARTIST_IMAGE_CACHE_SIZE:
            oldest_key = next(iter(state._artist_image_load_cache))
            del state._artist_image_load_cache[oldest_key]
        state._artist_image_load_cache[cache_key] = (current_time, None)
        return None



def clear_artist_image_cache(artist: str) -> None:
    """
    Clear the artist image load cache for a specific artist.
    This is called when the user changes their artist image preference to ensure
    the new preference is immediately reflected without waiting for the cache TTL.
    
    Since cache keys are now tuples of (artist, album), this clears ALL entries
    for the given artist across all albums.
    
    Args:
        artist: Artist name to clear from cache
    """
    # Cache keys are now tuples of (artist, album)
    # Clear all entries for this artist (any album)
    # Also handle legacy string keys for smooth transition
    keys_to_delete = [key for key in state._artist_image_load_cache.keys() 
                      if (isinstance(key, tuple) and key[0] == artist) or key == artist]
    for key in keys_to_delete:
        state._artist_image_load_cache.pop(key, None)  # Safe delete
    
    if keys_to_delete:
        logger.debug(f"Cleared artist image cache for '{artist}' ({len(keys_to_delete)} entries)")


def _get_artist_image_fallback(artist: str) -> Optional[Dict[str, Any]]:
    """
    Get first available artist image as fallback (when no album art exists and no explicit preference).
    This is used as a last resort when no album art is found.
    
    Args:
        artist: Artist name
        
    Returns:
        Dictionary with 'path' (Path to image) and 'source' (source name) if found, None otherwise
    """
    try:
        artist_folder = get_album_db_folder(artist, None)
        artist_metadata_path = artist_folder / "metadata.json"
        
        if not artist_metadata_path.exists():
            return None
        
        with open(artist_metadata_path, 'r', encoding='utf-8') as f:
            artist_metadata = json.load(f)
        
        if artist_metadata.get("type") != "artist_images":
            return None
        
        artist_images = artist_metadata.get("images", [])
        
        # Defensive logging: Log if no images found or all images failed to download
        # CRITICAL FIX: Throttle log to prevent spam (30+ logs per second during polling)
        if not artist_images:
            current_time = time.time()
            log_key = f"no_fallback_{artist}"
            last_log_time = state._artist_image_log_throttle.get(log_key, 0)
            if (current_time - last_log_time) >= state._ARTIST_IMAGE_LOG_THROTTLE_SECONDS:
                logger.debug(f"No artist images found in DB for fallback: {artist}")
                state._artist_image_log_throttle[log_key] = current_time
                _cleanup_artist_image_log_throttle()
            return None
        
        # Use first available artist image as fallback (no explicit preference needed)
        for img in artist_images:
            if img.get("downloaded") and img.get("filename"):
                filename = img.get("filename")
                artist_image_path = artist_folder / filename
                
                if artist_image_path.exists():
                    return {
                        "path": artist_image_path,
                        "source": img.get("source", "Unknown")
                    }
        
        # Log if images exist but none are downloaded or available
        # CRITICAL FIX: Throttle log to prevent spam (30+ logs per second during polling)
        current_time = time.time()
        log_key = f"no_downloaded_{artist}"
        last_log_time = state._artist_image_log_throttle.get(log_key, 0)
        if (current_time - last_log_time) >= state._ARTIST_IMAGE_LOG_THROTTLE_SECONDS:
            logger.debug(f"Artist images found in DB for {artist} but none are downloaded or available")
            state._artist_image_log_throttle[log_key] = current_time
            _cleanup_artist_image_log_throttle()
        return None
    except Exception as e:
        logger.debug(f"Failed to load artist image fallback: {e}")
        return None


async def ensure_artist_image_db(artist: str, spotify_artist_id: Optional[str] = None, force: bool = False, artist_visuals: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Background task to fetch artist images and save them to the database.
    Fetches from multiple sources: Deezer, TheAudioDB, FanArt.tv, Spicetify GraphQL, Spotify, and Last.fm.
    
    Priority order:
    1. Deezer (free, 1000x1000px, no auth required)
    2. TheAudioDB (free key '123', provides MBID for FanArt.tv)
    3. FanArt.tv (requires FANART_TV_API_KEY in .env + MBID from TheAudioDB)
    4. Spicetify GraphQL (header + gallery from internal API, if provided)
    5. Spotify (fallback, if spotify_artist_id provided)
    6. Last.fm (fallback, if LASTFM_API_KEY in .env)
    
    Note: iTunes is NOT used for artist images (it rarely works for artists).
    
    Args:
        artist: Artist name
        spotify_artist_id: Spotify artist ID for Spotify API fallback (optional)
        force: If True, bypass cache and tracker checks (for manual refetch)
        artist_visuals: Spicetify GraphQL visuals dict with header_image and gallery (optional)
    """
    # Check if feature is enabled (respects album_art_db setting for both loading AND downloading)
    if not FEATURES.get("album_art_db", True):
        return []
    
    # Import here to avoid circular import
    from .metadata import get_current_song_meta_data
    
    # Check cache first (debouncing) - SKIP if force=True
    # If we checked this artist recently (within 60 seconds), return cached result
    # This prevents spamming the logic/logs when frontend polls frequently
    current_time = time.time()
    if not force:
        cached_data = state._artist_db_check_cache.get(artist)
        if cached_data:
            timestamp, cached_result = cached_data
            if current_time - timestamp < 60:
                return cached_result
    
    # Clean up old entries to prevent memory leak (keep only recent entries)
    # Remove entries older than 5 minutes to prevent unbounded growth
    if len(state._artist_db_check_cache) > 100:
        cutoff_time = current_time - 300  # 5 minutes
        state._artist_db_check_cache = {
            k: v for k, v in state._artist_db_check_cache.items()
            if v[0] > cutoff_time  # v is (timestamp, result_list) tuple
        }

    # CRITICAL FIX: Use artist name only as key (more stable than composite key)
    # Using spotify_id in key causes race conditions when spotify_id changes or is initially None
    # Artist name is stable and prevents duplicate downloads for the same artist
    request_key = artist
    
    # Prevent duplicate downloads for the same artist - SKIP if force=True
    if not force:
        if request_key in state._artist_download_tracker:
            return []
    
    # Fix 5: Add size limit to tracker (Defensive coding)
    # CRITICAL FIX: Instead of clearing all entries (which causes race conditions),
    # remove only the oldest entries to make room for new ones
    # This prevents concurrent downloads from being allowed when tracker is cleared
    if len(state._artist_download_tracker) > 50:
        logger.warning("Artist download tracker full, removing oldest entries to prevent leaks")
        # Remove oldest 10 entries (FIFO-like behavior)
        # Convert to list, remove first 10, then rebuild set
        entries_list = list(state._artist_download_tracker)
        state._artist_download_tracker = set(entries_list[10:])

    state._artist_download_tracker.add(request_key)
    
    # Store original values for validation
    original_artist = artist
    original_spotify_id = spotify_artist_id
    
    try:
        # Use dedicated semaphore for artist images to prevent deadlock with album art downloads
        async with state._artist_download_semaphore:
            try:
                folder = get_album_db_folder(artist, None) # Artist-only folder
                folder.mkdir(parents=True, exist_ok=True)
                
                metadata_path = folder / "metadata.json"
                existing_metadata = {}
                
                # Check if artist images already exist in DB (optimization)
                # If images exist, return immediately (no need to re-fetch)
                # SKIP if force=True (manual refetch request)
                if metadata_path.exists():
                    try:
                        with open(metadata_path, 'r', encoding='utf-8') as f:
                            existing_metadata = json.load(f)
                        
                        existing_images = existing_metadata.get("images", [])
                        
                        # If images exist AND not force mode, return cached paths
                        if len(existing_images) > 0 and not force:
                            encoded_folder = quote(folder.name, safe='')
                            result_paths = [
                                f"/api/album-art/image/{encoded_folder}/{quote(img.get('filename', ''), safe='')}" 
                                for img in existing_images 
                                if img.get('downloaded') and img.get('filename')
                            ]
                            
                            # Update cache
                            state._artist_db_check_cache[artist] = (time.time(), result_paths)
                            return result_paths
                            
                    except Exception as e:
                        logger.debug(f"Failed to load cached artist images: {e}")
                        # Continue to fetch if cache read fails

                # Initialize our new dedicated artist image provider (singleton pattern)
                # Use global instance to prevent re-initialization on every call
                if state._artist_image_provider is None:
                    try:
                        state._artist_image_provider = ArtistImageProvider()
                    except Exception as e:
                        logger.error(f"Failed to initialize ArtistImageProvider: {e}", exc_info=True)
                        # Graceful degradation: return empty list instead of crashing
                        return []
                artist_provider = state._artist_image_provider
                
                # Fetch from new sources (Deezer, TheAudioDB, FanArt.tv)
                # This returns: [{'url':..., 'source':..., 'type':..., 'width':..., 'height':...}]
                all_images = await artist_provider.get_artist_images(artist)
                
                # Spicetify GraphQL visuals (header + gallery from internal API)
                # These are high-res images directly from Spotify's internal GraphQL
                if artist_visuals:
                    existing_urls = {i['url'] for i in all_images}
                    added_count = 0
                    
                    # Header image (artist-curated banner, up to 2660x1140)
                    header_img = artist_visuals.get('header_image')
                    if header_img and header_img.get('url') and header_img['url'] not in existing_urls:
                        all_images.append({
                            "url": header_img['url'],
                            "source": "spicetify",
                            "type": "header",
                            "width": header_img.get('width', 2660),
                            "height": header_img.get('height', 1140)
                        })
                        existing_urls.add(header_img['url'])
                        added_count += 1
                    
                    # Gallery images (artist photos, 690x500+)
                    for img in artist_visuals.get('gallery', []):
                        if img and img.get('url') and img['url'] not in existing_urls:
                            all_images.append({
                                "url": img['url'],
                                "source": "spicetify",
                                "type": "gallery",
                                "width": img.get('width', 690),
                                "height": img.get('height', 500)
                            })
                            existing_urls.add(img['url'])
                            added_count += 1
                    
                    if added_count > 0:
                        logger.debug(f"Spicetify GraphQL: Added {added_count} artist images for {artist}")
                
                # Fallback 1: Spotify (if ID provided) - Keep as backup
                # CRITICAL FIX: Validate artist ID to prevent race conditions
                # If track changed while this function was running, spotify_artist_id might be stale
                if spotify_artist_id:
                    client = get_shared_spotify_client()
                    if client:
                        try:
                            # Verify the artist ID is still valid for this artist
                            # This prevents saving images from previous artist when track changes
                            spotify_urls = await client.get_artist_images(spotify_artist_id)
                            if spotify_urls:
                                # Only add if not already present (simple check)
                                existing_urls = {i['url'] for i in all_images}
                                for url in spotify_urls:
                                    if url not in existing_urls:
                                        all_images.append({
                                            "url": url,
                                            "source": "spotify",
                                            "type": "artist"
                                        })
                                        break # Just one from Spotify is enough if we have others
                        except Exception as e:
                            # If validation fails (e.g., artist_id is stale), skip Spotify images
                            logger.debug(f"Spotify artist image validation failed for {artist} (possible race condition): {e}")
                            # Don't add stale Spotify images from previous track
                
                # NOTE: iTunes and Last.fm are NOT used for artist images (they only work for album art)
                # iTunes Search API is designed for app icons and album art, not artist photos.
                # Last.fm artist images are often low-quality placeholders and not reliable.
                # Both iTunes and Last.fm remain enabled for ALBUM art fetching in providers/album_art.py
                # but are explicitly excluded from artist image fetching to prevent poor quality results.
                
                # Log summary with throttle (prevents spam when function runs multiple times)
                # Only log if enough time has passed since last log for this artist
                current_time = time.time()
                last_log_time = state._artist_image_log_throttle.get(artist, 0)
                should_log = (current_time - last_log_time) >= state._ARTIST_IMAGE_LOG_THROTTLE_SECONDS
                
                if should_log:
                    if all_images:
                        logger.info(f"Artist images fetched for '{artist}': {len(all_images)} total from all sources")
                    else:
                        logger.info(f"Artist images fetched for '{artist}': No images found from any source")
                    
                    # Update throttle timestamp
                    state._artist_image_log_throttle[artist] = current_time
                    
                    # Clean up old entries to prevent memory leak
                    _cleanup_artist_image_log_throttle()

                # Download and Save
                saved_images = existing_metadata.get("images", [])
                metadata_changed = False  # OPTIMIZATION: Track if we actually need to save to disk
                
                # Store original list of existing filenames (used for deduplication check)
                existing_filenames = {img.get('filename') for img in saved_images if img.get('filename')}
                
                # Simple deduplication set (by URL)
                existing_urls = {img.get('url') for img in saved_images if img.get('url')}
                
                loop = asyncio.get_running_loop()
                
                # Track counts per source for filename generation
                source_counts = {}
                
                # CRITICAL FIX: Check if artist changed BEFORE processing images (optimization)
                # This prevents running the check 18+ times inside the loop (one per image)
                # If track changed while we were fetching, discard these images immediately
                # DEBOUNCE FIX: Use retry loop to tolerate Windows SMTC lag (up to 4-5 seconds)
                # Without this, SMTC returning stale data causes false "artist changed" aborts
                validation_passed = False
                current_metadata = None  # Initialize to prevent NameError on exception
                for validation_retry in range(4):
                    try:
                        # REMOVED: get_current_song_meta_data._last_check_time = 0
                        # Don't bust global cache - causes feedback loops and system churn
                        current_metadata = await get_current_song_meta_data()
                        if current_metadata:
                            current_artist = current_metadata.get("artist", "")
                            current_artist_id = current_metadata.get("artist_id")
                            
                            # CRITICAL FIX: Only abort if artist NAME changed OR if we HAD an ID and it changed to a DIFFERENT ID
                            name_changed = current_artist != original_artist
                            
                            # Only consider ID change a failure if we HAD an ID originally and it changed to a DIFFERENT NON-NULL ID
                            id_mismatch_is_critical = (
                                original_spotify_id is not None and 
                                current_artist_id is not None and 
                                current_artist_id != original_spotify_id
                            )
                            
                            if name_changed or id_mismatch_is_critical:
                                if validation_retry < 3:
                                    # SMTC may be lagging - wait and retry
                                    await asyncio.sleep(0.5)
                                    continue
                                else:
                                    # Genuinely different song after 4 attempts (4 seconds wait)
                                    logger.info(f"Artist changed from '{original_artist}' to '{current_artist}' (ID: {original_spotify_id} -> {current_artist_id}) before download, discarding images")
                                    return []  # Abort entire operation
                            
                            # Validation passed - artist matches
                            validation_passed = True
                            break
                    except Exception as e:
                        logger.debug(f"Failed to check current artist before download (retry {validation_retry}): {e}")
                        break  # Continue with download if check fails (defensive)
                
                if not validation_passed and not current_metadata:
                    # No metadata available - proceed cautiously (better to save than lose work)
                    pass
                
                # OPTIMIZATION: Process images in parallel batches to significantly speed up downloads
                # Process 12 images at a time to balance speed with resource usage
                # This reduces total time from ~3 minutes (90 images x 2s each) to ~15 seconds (8 batches x ~2s each)
                PARALLEL_BATCH_SIZE = 12
                
                # Helper function to process a single image (extracted from loop for parallelization)
                async def _process_single_image(img_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
                    """
                    Process a single artist image: download, extract resolution, and return result.
                    Returns a dict with processing results, or None if image should be skipped.
                    This function is designed to be called in parallel batches.
                    
                    Note: File path generation and upgrade logic are handled in sequential processing
                    to avoid race conditions with source_counts and to ensure correct filename indices.
                    """
                    url = img_dict.get('url')
                    source = img_dict.get('source', 'unknown')
                    
                    # CRITICAL FIX: Filter out iTunes and LastFM from artist images
                    # These providers don't work for artist images (they only work for album art)
                    # iTunes Search API is designed for app icons and album art, not artist photos
                    # LastFM artist images are often low-quality placeholders
                    if source in ["iTunes", "LastFM", "Last.fm"]:
                        return None  # Skip these providers for artist images
                    
                    # Note: existing_urls check is redundant here since images_to_process is already filtered
                    # But we keep url validation
                    if not url:
                        return None
                    
                    # Sanitize source name (remove dots, special chars) for filename safety
                    safe_source = source.lower().replace('.', '').replace(' ', '_').replace('-', '_')
                    
                    # Get width/height from provider as fallback (will be replaced with actual values if file exists)
                    width = img_dict.get('width', 0)
                    height = img_dict.get('height', 0)
                    
                    # Download the image to a temporary location first
                    # We can't generate the final filename yet because we don't know the index (source_counts)
                    # Use a temporary filename based on URL hash to avoid conflicts
                    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
                    temp_filename = f"temp_{url_hash}"
                    temp_file_path = folder / temp_filename
                    
                    # Download the image
                    success, ext = await loop.run_in_executor(None, _download_and_save_sync, url, temp_file_path)
                    if success:
                        # Update temp_file_path to reflect actual file extension
                        temp_file_path = temp_file_path.with_suffix(ext)
                        
                        # CRITICAL FIX: Extract actual resolution from downloaded image file
                        # This ensures 100% accurate resolution information (not just provider's claimed values)
                        try:
                            def get_image_resolution(path: Path) -> tuple:
                                """Extract actual width/height from image file"""
                                with Image.open(path) as img:
                                    return img.size
                            
                            actual_width, actual_height = await loop.run_in_executor(None, get_image_resolution, temp_file_path)
                            width = actual_width
                            height = actual_height
                            logger.debug(f"Extracted actual resolution for {source} image: {width}x{height}")
                        except Exception as e:
                            logger.debug(f"Failed to extract resolution from {temp_file_path}, using provider values: {e}")
                            # Keep provider values as fallback
                        
                        # Return result for sequential processing (to avoid race conditions with source_counts)
                        # The sequential processing will rename temp_file_path to the final filename
                        return {
                            "type": "new_download",
                            "source": source,
                            "url": url,
                            "temp_file_path": temp_file_path,  # Temporary file, will be renamed
                            "width": width,
                            "height": height,
                            "ext": ext,
                            "safe_source": safe_source
                        }
                    else:
                        return None  # Download failed, skip
                
                # Filter images that need processing (skip iTunes/LastFM and duplicates)
                images_to_process = []
                for img_dict in all_images:
                    url = img_dict.get('url')
                    source = img_dict.get('source', 'unknown')
                    if source in ["iTunes", "LastFM", "Last.fm"]:
                        continue
                    if url and url not in existing_urls:
                        images_to_process.append(img_dict)
                
                # Process images in batches of 8 (parallel downloads within batch, sequential batches)
                for batch_start in range(0, len(images_to_process), PARALLEL_BATCH_SIZE):
                    batch = images_to_process[batch_start:batch_start + PARALLEL_BATCH_SIZE]
                    
                    # Process entire batch in parallel with timeout
                    try:
                        batch_results = await asyncio.wait_for(
                            asyncio.gather(*[_process_single_image(img_dict) for img_dict in batch], return_exceptions=True),
                            timeout=30.0
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"Artist image batch processing timed out for {artist}")
                        batch_results = []
                    
                    # Process results sequentially to update saved_images and avoid race conditions
                    for result in batch_results:
                        if isinstance(result, Exception):
                            logger.debug(f"Error processing image in batch: {result}")
                            continue
                        if result is None:
                            continue  # Image was skipped
                        
                        source = result["source"]
                        url = result["url"]
                        safe_source = result["safe_source"]
                        
                        # Safety check: Skip if URL was already processed (prevents duplicates from parallel processing)
                        if url in existing_urls:
                            # Clean up temp file if it exists
                            if result.get("temp_file_path") and result["temp_file_path"].exists():
                                try:
                                    result["temp_file_path"].unlink()
                                except:
                                    pass
                            continue
                        
                        # Check if we already have this image in saved_images (for upgrade logic)
                        existing_image_index = None
                        should_upgrade = False
                        for idx_check, img in enumerate(saved_images):
                            if img.get('url') == url:
                                # Found existing image with same URL - check if upgrade needed
                                if source.lower() == "spotify":
                                    existing_width = img.get('width', 0)
                                    existing_height = img.get('height', 0)
                                    existing_resolution = max(existing_width, existing_height)
                                    # If existing image is 640px (or close to it), try to upgrade
                                    if existing_resolution <= 650:  # Allow small margin for rounding
                                        should_upgrade = True
                                        existing_image_index = idx_check
                                        logger.info(f"Found existing 640px Spotify artist image, attempting upgrade to 1400px for {artist}")
                                else:
                                    # Same URL, different source or already high-res - skip
                                    existing_image_index = idx_check
                                break
                        
                        # If we found an existing image and it's not an upgrade, skip
                        if existing_image_index is not None and not should_upgrade:
                            # Clean up temp file
                            if result.get("temp_file_path") and result["temp_file_path"].exists():
                                try:
                                    result["temp_file_path"].unlink()
                                except:
                                    pass
                            continue
                        
                        # Update source_counts (sequential to avoid race conditions)
                        if safe_source not in source_counts:
                            source_counts[safe_source] = 0
                        else:
                            source_counts[safe_source] += 1
                        
                        idx = source_counts[safe_source]
                        ext = result["ext"]
                        filename = f"{safe_source}_{idx}{ext}"
                        final_file_path = folder / filename
                        
                        # Move temp file to final location
                        temp_file_path = result["temp_file_path"]
                        if temp_file_path.exists():
                            try:
                                # Delete existing file first (Windows doesn't allow overwrite with rename)
                                if final_file_path.exists():
                                    final_file_path.unlink()
                                # Move temp file to final location
                                temp_file_path.rename(final_file_path)
                            except Exception as e:
                                logger.debug(f"Failed to rename temp file {temp_file_path} to {final_file_path}: {e}")
                                # Try copy as fallback
                                try:
                                    if final_file_path.exists():
                                        final_file_path.unlink()
                                    shutil.copy2(temp_file_path, final_file_path)
                                    temp_file_path.unlink()
                                except Exception as e2:
                                    logger.debug(f"Failed to copy temp file: {e2}")
                                    continue  # Skip this image if we can't move/copy it
                        
                        if should_upgrade and existing_image_index is not None:
                            # Upgrading existing image
                            saved_images[existing_image_index].update({
                                "url": url,  # Update to enhanced URL (1400px)
                                "width": result["width"],
                                "height": result["height"],
                                "downloaded": True
                            })
                            logger.info(f"Upgraded Spotify artist image from 640px to {result['width']}x{result['height']} for {artist}")
                        else:
                            # New image - append to list
                            saved_images.append({
                                "source": source,
                                "url": url,
                                "filename": filename,
                                "width": result["width"],
                                "height": result["height"],
                                "downloaded": True,
                                "added_at": datetime.utcnow().isoformat() + "Z"
                            })
                        
                        existing_urls.add(url)  # Mark as processed
                        metadata_changed = True  # New image added or upgraded, need to save
                
                # INFORMATIONAL: Check if song changed during download (for logging only)
                # Images are saved for original_artist regardless of current playback,
                # since all API calls used original parameters and folder was created for original artist
                try:
                    current_metadata = await get_current_song_meta_data()
                    if current_metadata:
                        current_artist = current_metadata.get("artist", "")
                        if current_artist != original_artist:
                            # Log that we saved images for a different artist than currently playing
                            # This is EXPECTED behavior - images are correct for original_artist
                            logger.info(f"Song changed during download: saved artist images for '{original_artist}' (now playing: '{current_artist}'). Images are correct.")
                except Exception as e:
                    logger.debug(f"Could not check current song after download: {e}")
                
                # OPTIMIZATION: Only save metadata if it actually changed OR if file doesn't exist
                # This prevents unnecessary disk writes when ensure_artist_image_db runs but finds no new images
                # Same optimization pattern as album art to reduce metadata.json writes
                if metadata_changed or not metadata_path.exists():
                    # Save Metadata
                    metadata = {
                        "artist": artist,
                        "type": "artist_images",
                        "last_accessed": datetime.utcnow().isoformat() + "Z",
                        "images": saved_images
                    }
                    
                    await loop.run_in_executor(None, save_album_db_metadata, folder, metadata)
                else:
                    # Commented out to reduce log spam - this is internal optimization feedback, not actionable debugging info
                    # logger.debug(f"Skipping metadata save for {artist} - no changes detected")
                    pass
                
                # Return list of LOCAL paths for the frontend
                # We return paths relative to the DB root for the API to serve
                # URL encode folder name and filename to handle special characters safely
                encoded_folder = quote(folder.name, safe='')
                result_paths = [
                    f"/api/album-art/image/{encoded_folder}/{quote(img.get('filename', ''), safe='')}" 
                    for img in saved_images 
                    if img.get('downloaded') and img.get('filename')
                ]
                
                # Update cache
                state._artist_db_check_cache[artist] = (time.time(), result_paths)
                return result_paths

            except Exception as e:
                logger.error(f"Error ensuring artist image DB: {e}")
                return []
    finally:
        # Always remove from tracker, even if error occurred
        # Use artist name only (same as when we added it)
        try:
            state._artist_download_tracker.discard(original_artist)
        except:
            # Fallback if original_artist not defined (shouldn't happen, but defensive)
            state._artist_download_tracker.discard(artist)
