"""
Album Art Database module for system_utils package.
Handles album art storage, retrieval, and metadata management.

Dependencies: state, helpers, image
"""
from __future__ import annotations
import os
import json
import time
import asyncio
import threading
import uuid
import shutil
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

from PIL import Image

from . import state
from .helpers import sanitize_folder_name
from .image import save_image_original, determine_image_extension
from config import ALBUM_ART_DB_DIR, FEATURES
from logging_config import get_logger
from providers.album_art import get_album_art_provider

logger = get_logger(__name__)


def get_album_db_folder(artist: str, album: Optional[str] = None) -> Path:
    """
    Get the database folder path for an album or artist images.
    Uses Artist - Album format, with fallback to Artist - Title if no album.
    
    Args:
        artist: Artist name
        album: Album name (optional). If None, returns Artist-only folder.
        
    Returns:
        Path to the album database folder
    """
    safe_artist = sanitize_folder_name(artist or "Unknown")
    
    # Use album if available, otherwise we'll use title when called
    if album:
        safe_album = sanitize_folder_name(album)
        folder_name = f"{safe_artist} - {safe_album}"
    else:
        # This will be used when album is None - caller should pass title
        folder_name = safe_artist
    
    return ALBUM_ART_DB_DIR / folder_name


def save_album_db_metadata(folder: Path, metadata: Dict[str, Any]) -> bool:
    """
    Save album art database metadata JSON file atomically.
    Preserves unknown keys from existing metadata and includes schema version.
    
    Uses per-folder threading locks to prevent Windows file locking errors when
    multiple operations try to access the same metadata.json file concurrently.
    Each album folder has its own lock, allowing parallel writes to different albums.
    
    Args:
        folder: Path to the album folder
        metadata: Dictionary containing metadata to save
        
    Returns:
        True if successful, False otherwise
    """
    # Get or create a lock for this specific folder
    # This allows parallel writes to different albums while serializing writes to the same album
    try:
        folder_key = str(folder.resolve())  # Use resolved path to handle symlinks/relative paths
    except (OSError, ValueError) as e:
        # Handle edge cases where path resolution fails (e.g., invalid characters, permissions)
        logger.error(f"Failed to resolve folder path {folder}: {e}")
        return False
    
    with state._metadata_locks_lock:
        if folder_key not in state._metadata_file_locks:
            state._metadata_file_locks[folder_key] = threading.Lock()
        file_lock = state._metadata_file_locks[folder_key]
    
    # Protect all file I/O operations with the folder-specific lock
    # This prevents Windows file locking errors (WinError 32) when multiple threads
    # try to read/write the same metadata.json file simultaneously
    with file_lock:
        try:
            metadata_path = folder / "metadata.json"
            # FIX: Use unique temp filename to prevent concurrent writes from overwriting each other
            # This prevents race conditions when multiple tracks from the same album are processed simultaneously
            temp_filename = f"metadata_{uuid.uuid4().hex}.json.tmp"
            temp_path = folder / temp_filename
            
            # Ensure folder exists
            folder.mkdir(parents=True, exist_ok=True)
            
            # Load existing metadata to preserve unknown keys (for backward compatibility)
            existing_metadata = {}
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        existing_metadata = json.load(f)
                except Exception:
                    # If read fails, start fresh
                    pass
            
            # Preserve unknown keys from existing metadata (except schema_version which we update)
            # Also skip keys that are explicitly set to None (indicating intentional deletion)
            for key, value in existing_metadata.items():
                if key not in metadata and key != 'schema_version':
                    metadata[key] = value
            
            # Remove keys that are explicitly set to None (indicating intentional deletion)
            # This allows callers to delete keys by setting them to None
            keys_to_remove = [key for key, value in metadata.items() if value is None and key != 'schema_version']
            for key in keys_to_remove:
                del metadata[key]
            
            # Add schema version (current version is 1)
            # This allows future code to handle format changes gracefully
            metadata['schema_version'] = 1
            
            # Write to temp file first
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            
            # Atomic replace with retry for Windows file locking
            # Note: The lock above should prevent most conflicts, but we keep retries
            # as a safety measure for edge cases (e.g., external processes, antivirus)
            for attempt in range(3):
                try:
                    if metadata_path.exists():
                        os.remove(metadata_path)
                    os.replace(temp_path, metadata_path)
                    
                    # OPTIMIZATION: Invalidate cache after successful save
                    # This ensures cache is cleared when file changes
                    metadata_path_str = str(metadata_path)
                    if metadata_path_str in state._album_art_metadata_cache:
                        del state._album_art_metadata_cache[metadata_path_str]
                    
                    return True
                except OSError as e:
                    if attempt < 2:
                        # Wait briefly before retry (0.1s, 0.2s)
                        time.sleep(0.1 * (attempt + 1))
                    else:
                        logger.error(f"Failed to atomically replace metadata.json after 3 attempts: {e}")
                        # Clean up temp file
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                        return False
        except Exception as e:
            logger.error(f"Failed to save album DB metadata: {e}")
            return False


def _download_and_save_sync(url: str, path: Path) -> Tuple[bool, str]:
    """
    Helper function to run download and save in thread executor.
    This performs blocking I/O operations (network request and file save).
    Preserves the original image format without conversion.
    
    Includes retry logic with exponential backoff for transient failures (403, network errors).
    
    Args:
        url: URL to download image from
        path: Path where to save the image file (extension will be determined automatically)
        
    Returns:
        Tuple of (success: bool, extension: str)
        - success: True if download and save succeeded, False otherwise
        - extension: File extension used (e.g., '.jpg', '.png')
    """
    import requests
    
    # FIX: Convert spotify:image:xxx URIs to proper HTTPS URLs
    # Spicetify sometimes sends spotify:image:xxx format instead of HTTPS URLs
    # Format: spotify:image:ab67616d00001e023cea3f53137fcb2cc86a481c -> https://i.scdn.co/image/ab67616d00001e023cea3f53137fcb2cc86a481c
    if url and url.startswith('spotify:image:'):
        image_id = url.replace('spotify:image:', '')
        url = f'https://i.scdn.co/image/{image_id}'
        logger.debug(f"Converted spotify:image URI to HTTPS: {url}")
    
    # Add User-Agent and Referer headers (required by Wikimedia Commons and best practice)
    # Use same User-Agent as ArtistImageProvider for consistency
    # Referer header prevents hotlinking protection and reduces 403 errors

    headers = {
        'User-Agent': 'SyncLyrics/1.0.0 (https://github.com/AnshulJ999/SyncLyrics; contact@example.com)'
    }
    # Only add Referer for Wikipedia/Wikimedia to prevent hotlinking protection
    if 'wikipedia' in url.lower() or 'wikimedia' in url.lower():
        headers['Referer'] = 'https://en.wikipedia.org/'
    
    # Retry logic with very small exponential backoff (0.1s, 0.2s, 0.4s)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=10, stream=True, headers=headers)
            response.raise_for_status()
            
            # Get Content-Type from response headers
            content_type = response.headers.get('Content-Type', '')
            
            # Determine file extension from URL or Content-Type
            file_extension = determine_image_extension(url, content_type)
            
            # Save original image bytes (no conversion = pristine quality)
            success = save_image_original(response.content, path, file_extension)
            
            if success:
                return (True, file_extension)
            else:
                # Save failed, but request succeeded - don't retry
                if attempt == max_retries - 1:
                    logger.warning(f"Download succeeded but save failed for {url}")
                return (False, '.jpg')
                
        except requests.exceptions.HTTPError as e:
            # Retry on 403/429 (rate limiting) or 5xx (server errors)
            if e.response.status_code in (403, 429, 500, 502, 503, 504):
                if attempt < max_retries - 1:
                    # Very small exponential backoff: 0.1s, 0.2s, 0.4s
                    delay = 0.1 * (2 ** attempt)
                    time.sleep(delay)
                    continue
            # Don't retry on 404 or other client errors
            if attempt == max_retries - 1:
                logger.warning(f"Download failed for {url}: {e}")
            return (False, '.jpg')
            
        except (requests.exceptions.RequestException, Exception) as e:
            # Retry on network errors (timeout, connection errors, etc.)
            if attempt < max_retries - 1:
                # Very small exponential backoff: 0.1s, 0.2s, 0.4s
                delay = 0.1 * (2 ** attempt)
                time.sleep(delay)
                continue
            # Final attempt failed
            logger.warning(f"Download failed for {url}: {e}")
            return (False, '.jpg')
    
    # Should never reach here, but return failure just in case
    return (False, '.jpg')


async def ensure_album_art_db(
    artist: str, album: Optional[str], title: str, spotify_url: Optional[str] = None, retry_count: int = 0
) -> Optional[Tuple[str, str]]:
    """
    Background task to fetch all album art options and save them to the database.
    Downloads images from all providers and saves them in their original format (pristine quality).
    Creates metadata.json with URLs, resolutions, and preferences.
    
    Args:
        artist: Artist name
        album: Album name (optional)
        title: Track title
        spotify_url: Spotify album art URL (optional)
        
    Returns:
        Tuple of (preferred_url, resolution_str) of the selected art, or None if failed.
    """
    # Prevent infinite recursion for self-healing
    if retry_count > 1:
        logger.warning(f"Aborting ensure_album_art_db for {artist} - {title} after {retry_count} retries")
        return None

    # OPTIMIZATION: Acquire semaphore to limit concurrent downloads (Fix #4)
    # This prevents network saturation if user skips many tracks quickly
    async with state._art_download_semaphore:
        logger.debug(f"DEBUG: Entering ensure_album_art_db for {artist} - {title}")  # Debug Log 1

        # Check if feature is enabled
        enabled = FEATURES.get("album_art_db", True)
        logger.debug(f"DEBUG: album_art_db enabled: {enabled}")  # Debug Log 2
        if not enabled:
            return None
    
        try:
            # Get album art provider
            art_provider = get_album_art_provider()
            
            # Fetch all options in parallel
            logger.debug(f"DEBUG: Calling get_all_art_options...")  # Debug Log 3
            options = await art_provider.get_all_art_options(artist, album, title, spotify_url)
            logger.debug(f"DEBUG: get_all_art_options returned {len(options)} options")  # Debug Log 4
            
            if not options:
                logger.debug(f"No album art options found for {artist} - {album or title}")
                return None
            
            # Get folder path
            folder = get_album_db_folder(artist, album or title)
            folder.mkdir(parents=True, exist_ok=True)
            
            # Check if metadata already exists (to avoid re-downloading)
            metadata_path = folder / "metadata.json"
            existing_metadata = None
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        existing_metadata = json.load(f)
                except:
                    pass
            
            # Download and save images for each provider
            # FIX: Initialize with existing data so we don't wipe out providers if a network call fails
            providers_data = existing_metadata.get("providers", {}) if existing_metadata else {}
            
            # FIX: Check for existing user preference FIRST before auto-selecting highest resolution
            # This ensures that if user manually selected a provider (e.g., via UI), that choice is preserved
            # even if a higher-resolution image is downloaded later
            preferred_provider = None
            if existing_metadata and "preferred_provider" in existing_metadata:
                preferred_provider = existing_metadata["preferred_provider"]
            
            # Only auto-select highest resolution if no user preference exists
            highest_resolution = 0
            if not preferred_provider:
                # Re-calculate highest resolution from EXISTING data
                for provider_name, data in providers_data.items():
                    width = data.get("width", 0)
                    height = data.get("height", 0)
                    res = max(width, height)
                    if res > highest_resolution and data.get("downloaded", False):
                        highest_resolution = res
                        preferred_provider = provider_name

            # Get event loop for running blocking I/O in executor
            loop = asyncio.get_running_loop()
            
            for option in options:
                provider_name = option["provider"]
                url = option["url"]
                resolution_str = option["resolution"]
                
                # Extract resolution for comparison
                width = option.get("width", 0)
                height = option.get("height", 0)
                resolution = max(width, height) if width > 0 and height > 0 else 0
                
                # Check if we already have this image (check metadata for correct filename)
                image_filename = None
                if existing_metadata and provider_name in existing_metadata.get("providers", {}):
                    # Use existing filename from metadata (preserves original extension)
                    image_filename = existing_metadata["providers"][provider_name].get("filename", f"{provider_name}.jpg")
                else:
                    # Default filename (will be updated after download with correct extension)
                    image_filename = f"{provider_name}.jpg"
                
                image_path = folder / image_filename
                
                # NEW: Explicitly check if the file exists on disk, even if metadata says it does
                # This fixes cases where user might have deleted images but metadata.json remains
                file_exists_on_disk = image_path.exists()
                
                # UPGRADE LOGIC: If this is Spotify and we have an existing 640px image, try to upgrade to 1400px
                should_upgrade = False
                if provider_name == "Spotify" and file_exists_on_disk and existing_metadata:
                    existing_provider_data = existing_metadata.get("providers", {}).get("Spotify", {})
                    existing_width = existing_provider_data.get("width", 0)
                    existing_height = existing_provider_data.get("height", 0)
                    existing_resolution = max(existing_width, existing_height)
                    # If existing image is 640px (or close to it), try to upgrade
                    if existing_resolution <= 650:  # Allow small margin for rounding
                        should_upgrade = True
                        logger.info(f"Found existing 640px Spotify image, attempting upgrade to 1400px for {artist} - {title}")

                # Download image if we don't have it, if it's missing, or if we should upgrade
                if not file_exists_on_disk or (existing_metadata and provider_name not in existing_metadata.get("providers", {})) or should_upgrade:
                    try:
                        # FIX: Use unique temp filename to prevent concurrent downloads from overwriting each other
                        # This prevents race conditions when the same provider downloads for the same album simultaneously
                        temp_filename = f"{provider_name}_{uuid.uuid4().hex}"
                        temp_path = folder / temp_filename
                        
                        # Run blocking download/save in executor to avoid freezing the event loop
                        # Returns (success: bool, extension: str)
                        success, file_extension = await loop.run_in_executor(
                            None,
                            _download_and_save_sync,
                            url,
                            temp_path
                        )
                        
                        if success:
                            # Update filename with correct extension
                            image_filename = f"{provider_name}{file_extension}"
                            image_path = folder / image_filename
                            
                            # If temp file has different name, rename it
                            temp_path_with_ext = temp_path.with_suffix(file_extension)
                            if temp_path_with_ext.exists() and temp_path_with_ext != image_path:
                                # Move to final location
                                try:
                                    os.replace(temp_path_with_ext, image_path)
                                except:
                                    # If replace fails, try copy then delete
                                    shutil.copy2(temp_path_with_ext, image_path)
                                    try:
                                        os.remove(temp_path_with_ext)
                                    except:
                                        pass
                            
                            logger.info(f"Downloaded and saved {provider_name} art ({file_extension}) for {artist} - {album or title}")
                            
                            # Get actual resolution from saved image (also run in executor since it's I/O)
                            try:
                                def get_image_resolution(path: Path) -> tuple:
                                    with Image.open(path) as img:
                                        return img.size
                                
                                actual_width, actual_height = await loop.run_in_executor(None, get_image_resolution, image_path)
                                resolution = max(actual_width, actual_height)
                                resolution_str = f"{actual_width}x{actual_height}"
                                # Update width/height with actual values
                                width = actual_width
                                height = actual_height
                                logger.info(f"Verified resolution for {provider_name}: {resolution_str}") # Add success log
                            except Exception as e:
                                logger.warning(f"Failed to verify resolution for {image_path}: {e}") # Log error
                        else:
                            logger.warning(f"Failed to save {provider_name} art for {artist} - {album or title}")
                            # Clean up temp file if download failed
                            try:
                                temp_path_with_ext = temp_path.with_suffix(file_extension) if 'file_extension' in locals() else temp_path
                                if temp_path_with_ext.exists():
                                    os.remove(temp_path_with_ext)
                                elif temp_path.exists():
                                    os.remove(temp_path)
                            except:
                                pass
                            continue
                    except Exception as e:
                        logger.warning(f"Failed to download {provider_name} art: {e}")
                        # Clean up temp file if exception occurred
                        try:
                            if 'temp_path' in locals() and temp_path.exists():
                                # Try to remove with any possible extension
                                for ext in ['.jpg', '.png', '.webp', '']:
                                    temp_with_ext = temp_path.with_suffix(ext) if ext else temp_path
                                    if temp_with_ext.exists():
                                        os.remove(temp_with_ext)
                                        break
                        except:
                            pass
                        continue
                else:
                    # Image exists, get resolution from file (run in executor to avoid blocking)
                    try:
                        def get_image_resolution_existing(path: Path) -> tuple:
                            with Image.open(path) as img:
                                return img.size
                        
                        actual_width, actual_height = await loop.run_in_executor(None, get_image_resolution_existing, image_path)
                        resolution = max(actual_width, actual_height)
                        resolution_str = f"{actual_width}x{actual_height}"
                        # Update width/height with actual values
                        width = actual_width
                        height = actual_height
                        logger.info(f"Verified existing resolution for {provider_name}: {resolution_str}") # Add success log
                    except Exception as e:
                        logger.warning(f"Failed to verify existing resolution for {image_path}: {e}") # Log error
                        # Fallback to metadata if available
                        if existing_metadata and provider_name in existing_metadata.get("providers", {}):
                            existing_provider_data = existing_metadata["providers"][provider_name]
                            resolution_str = existing_provider_data.get("resolution", resolution_str)
                
                # Store provider data (with actual filename including extension)
                providers_data[provider_name] = {
                    "url": url,
                    "resolution": resolution_str,
                    "width": width,
                    "height": height,
                    "filename": image_filename,  # Now includes correct extension (e.g., "iTunes.png")
                    "downloaded": image_path.exists()
                }
                
                # Track highest resolution for auto-selection
                # FIX: Only select as preferred if the file was successfully downloaded/exists
                if resolution > highest_resolution and image_path.exists():
                    highest_resolution = resolution
                    preferred_provider = provider_name
            
            # Use existing preference if available, otherwise use highest resolution
            # FIX: Check existing preference FIRST before auto-selecting highest resolution
            # This ensures user's manual selection is preserved even if a higher-res image is downloaded
            if existing_metadata and "preferred_provider" in existing_metadata:
                preferred_provider = existing_metadata["preferred_provider"]
            
            # Create metadata structure
            # FIX: Preserve background_style from existing metadata to prevent it from being wiped
            # when the background task runs (e.g., for self-healing or adding new providers)
            metadata = {
                "artist": artist,
                "album": album or title,
                "is_single": album is None or album.lower() == title.lower(),
                "preferred_provider": preferred_provider,
                "created_at": existing_metadata.get("created_at") if existing_metadata else datetime.utcnow().isoformat() + "Z",
                "last_accessed": datetime.utcnow().isoformat() + "Z",
                "providers": providers_data
            }
            
            # Save metadata
            # Use lock to ensure atomic update of metadata and prevent race conditions
            # This ensures we don't overwrite changes made by the API (e.g., background_style) while we were downloading images
            async with state._art_update_lock:
                # CRITICAL: Re-read metadata inside lock to get latest state (e.g. background_style changes)
                # This prevents overwriting changes made by the API while we were downloading images
                latest_db = load_album_art_from_db(artist, album, title)
                latest_metadata = latest_db["metadata"] if latest_db else existing_metadata
                
                # Preserve background_style if it exists in LATEST metadata (not stale existing_metadata)
                # This prevents the user's saved preference (Sharp/Soft/Blur) from being lost
                # when the background task updates the metadata (e.g., adding new providers or self-healing)
                # BUT also respects if the user cleared it (Auto) while we were downloading
                # Only preserve if it's not None (None indicates intentional deletion)
                if latest_metadata and "background_style" in latest_metadata and latest_metadata["background_style"] is not None:
                    metadata["background_style"] = latest_metadata["background_style"]
                
                # OPTIMIZATION: Run file I/O in executor to avoid blocking event loop (Fix #4)
                # This prevents UI stutters if disk is busy or antivirus is scanning
                save_success = await loop.run_in_executor(None, save_album_db_metadata, folder, metadata)
            
            # Handle save result outside the lock
            if save_success:
                logger.info(f"Saved album art database for {artist} - {album or title} with {len(providers_data)} providers")
                
                # Return the preferred provider info for immediate cache update
                if preferred_provider and preferred_provider in providers_data:
                    p_data = providers_data[preferred_provider]
                    # FIX: Return actual local file path, not resolution string
                    # This allows color extraction and cache detection to work correctly
                    local_path = folder / p_data.get("filename", f"{preferred_provider}.jpg")
                    return (p_data["url"], str(local_path) if local_path.exists() else None)
            else:
                logger.error(f"Failed to save album art database metadata for {artist} - {album or title}")
        
        except Exception as e:
            logger.error(f"Error in ensure_album_art_db: {e}")
            
        return None


def discover_custom_images(folder: Path, metadata: Dict[str, Any], is_artist_images: bool = False) -> Dict[str, Any]:
    """
    Auto-discover custom images in folder that aren't in metadata.json.
    Scans for image files and adds them to metadata automatically.
    
    Uses folder mtime caching to avoid re-scanning on every metadata load.
    Only re-discovers if folder modification time changed.
    
    Args:
        folder: Path to the album/artist folder
        metadata: Existing metadata dictionary (will be modified)
        is_artist_images: True if this is artist images metadata, False for album art
        
    Returns:
        Updated metadata dictionary (same object, modified in place)
    """
    if not folder.exists():
        return metadata
    
    try:
        # Check if we need to re-discover (folder mtime changed)
        # CRITICAL FIX: Handle folder path resolution failures to ensure cache key consistency
        # This prevents cache key mismatch if folder.resolve() fails in one function but succeeds in another
        try:
            folder_key = str(folder.resolve())
        except (OSError, ValueError) as e:
            logger.debug(f"Could not resolve folder path for cache key: {e}")
            folder_key = str(folder)  # Fallback to string representation
        folder_mtime = 0
        
        # Get max mtime of all files in folder (indicates if new files were added)
        try:
            if folder.exists():
                folder_mtime = max(
                    (f.stat().st_mtime for f in folder.iterdir() if f.is_file()),
                    default=0
                )
        except OSError:
            # Folder might not exist or be inaccessible
            return metadata
        
        # Check cache
        should_discover = True
        if folder_key in state._discovery_cache:
            cached_mtime, _ = state._discovery_cache[folder_key]
            if cached_mtime == folder_mtime:
                # Folder hasn't changed, skip discovery
                should_discover = False
        
        if not should_discover:
            return metadata
        
        # Scan for image files
        image_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp']
        discovered_count = 0
        
        for file in folder.iterdir():
            if not file.is_file():
                continue
            
            # Skip metadata.json and temp files
            if (file.name == 'metadata.json' or 
                file.name.endswith('.tmp') or 
                'metadata_' in file.name):
                continue
            
            # Check if it's an image file
            if file.suffix.lower() not in image_extensions:
                continue
            
            # Extract provider name from filename (remove extension)
            provider_name = file.stem  # "Custom.jpg" -> "Custom"
            
            if is_artist_images:
                # For artist images: check if already in images array
                images = metadata.get("images", [])
                already_exists = any(
                    img.get("filename") == file.name for img in images
                )
                
                if not already_exists:
                    # Extract actual resolution from file
                    try:
                        def get_image_size_sync(path: Path) -> tuple:
                            with Image.open(path) as img:
                                return img.size
                        
                        # Run in sync context (will be called from async context if needed)
                        width, height = get_image_size_sync(file)
                        
                        # Add to images array
                        if "images" not in metadata:
                            metadata["images"] = []
                        
                        metadata["images"].append({
                            "source": provider_name,
                            "url": f"file://local/{file.name}",  # Placeholder URL
                            "filename": file.name,
                            "width": width,
                            "height": height,
                            "downloaded": True,
                            "added_at": datetime.utcnow().isoformat() + "Z"
                        })
                        discovered_count += 1
                        logger.debug(f"Discovered custom artist image: {file.name} ({width}x{height})")
                    except Exception as e:
                        logger.debug(f"Failed to process custom image {file.name}: {e}")
            else:
                # For album art: check if already in providers dict
                providers = metadata.get("providers", {})
                already_exists = provider_name in providers
                existing_provider_data = providers.get(provider_name) if already_exists else None
                
                # CRITICAL FIX: Update existing provider if file exists but metadata is broken
                # This repairs metadata when files exist but downloaded flag is false or filename is wrong
                should_update = False
                if already_exists and existing_provider_data:
                    # Check if file exists but metadata says it's not downloaded
                    if not existing_provider_data.get("downloaded", False):
                        should_update = True
                    # Check if filename in metadata doesn't match actual file
                    elif existing_provider_data.get("filename") != file.name:
                        should_update = True
                
                if not already_exists or should_update:
                    # CRITICAL FIX: Safety check - ensure file actually exists before processing
                    # This prevents edge cases where the file was deleted between discovery and processing
                    if not file.exists():
                        continue
                    
                    # Extract actual resolution from file
                    try:
                        def get_image_size_sync(path: Path) -> tuple:
                            with Image.open(path) as img:
                                return img.size
                        
                        width, height = get_image_size_sync(file)
                        resolution_str = f"{width}x{height}"
                        
                        # Add or update providers dict
                        if "providers" not in metadata:
                            metadata["providers"] = {}
                        
                        metadata["providers"][provider_name] = {
                            "url": f"file://local/{file.name}",  # Placeholder URL
                            "filename": file.name,
                            "width": width,
                            "height": height,
                            "resolution": resolution_str,
                            "downloaded": True  # CRITICAL: Mark as downloaded since file exists
                        }
                        discovered_count += 1
                        if should_update:
                            logger.debug(f"Repaired metadata for existing album art: {file.name} ({width}x{height})")
                        else:
                            logger.debug(f"Discovered custom album art: {file.name} ({width}x{height})")
                    except Exception as e:
                        logger.debug(f"Failed to process custom image {file.name}: {e}")
        
        # Update cache
        if discovered_count > 0:
            # Update cache with new mtime
            if len(state._discovery_cache) >= state._MAX_DISCOVERY_CACHE_SIZE:
                # Remove oldest entry
                oldest_key = next(iter(state._discovery_cache))
                del state._discovery_cache[oldest_key]
            
            state._discovery_cache[folder_key] = (folder_mtime, discovered_count)
            logger.info(f"Auto-discovered {discovered_count} custom image(s) in {folder.name}")
        else:
            # No new images, but update cache to prevent re-scanning
            state._discovery_cache[folder_key] = (folder_mtime, 0)
        
    except Exception as e:
        logger.debug(f"Error during custom image discovery: {e}")
    
    return metadata


def load_album_art_from_db(artist: str, album: Optional[str], title: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Load album art from database if available.
    Returns the preferred image path if found.
    
    OPTIMIZED: Uses in-memory cache based on file modification time to prevent
    constant disk reads during polling. Also limits 'last_accessed' writes to once per hour.
    
    Args:
        artist: Artist name
        album: Album name (optional)
        title: Track title (optional, used as fallback if album is missing)
        
    Returns:
        Dictionary with 'path' (Path to image) and 'metadata' (full metadata dict) if found, None otherwise
    """
    # Check if feature is enabled
    if not FEATURES.get("album_art_db", True):
        return None
    
    try:
        # CRITICAL FIX: Silently return early if artist is empty (prevents log noise during track transitions)
        # This happens during the brief moment when a song ends and the next hasn't started yet
        if not artist:
            return None
        
        # Match saving logic: use title as fallback if album is missing
        folder_name = album if album else title
        if not folder_name:
            # CRITICAL FIX: If both album and title are missing, we can't determine folder
            # This handles edge cases where metadata is incomplete
            logger.debug(f"Cannot load album art: both album and title are missing for artist '{artist}'")
            return None
        
        try:
            folder = get_album_db_folder(artist, folder_name)
        except (OSError, ValueError) as e:
            # CRITICAL FIX: Handle folder path resolution failures
            # This can happen with invalid characters, permissions issues, or path length limits
            logger.warning(f"Failed to resolve folder path for {artist} - {folder_name}: {e}")
            return None
        
        metadata_path = folder / "metadata.json"
        
        if not metadata_path.exists():
            return None
        
        # OPTIMIZATION: Check cache first using file modification time
        # This is much faster than reading/parsing the JSON every time
        metadata_path_str = str(metadata_path)
        current_mtime = metadata_path.stat().st_mtime
        
        metadata = None
        if metadata_path_str in state._album_art_metadata_cache:
            cached_mtime, cached_metadata = state._album_art_metadata_cache[metadata_path_str]
            if cached_mtime == current_mtime:
                # Cache hit - file hasn't changed, use cached data
                metadata = cached_metadata.copy()  # Copy to avoid modifying cache directly
            else:
                # File changed, remove stale cache entry
                del state._album_art_metadata_cache[metadata_path_str]
        
        # If not in cache or file changed, load from disk
        if metadata is None:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            # Update cache (limit size to prevent memory leaks)
            if len(state._album_art_metadata_cache) >= state._MAX_METADATA_CACHE_SIZE:
                # Remove oldest entry (simple FIFO - remove first key)
                oldest_key = next(iter(state._album_art_metadata_cache))
                del state._album_art_metadata_cache[oldest_key]
            
            state._album_art_metadata_cache[metadata_path_str] = (current_mtime, metadata.copy())
        
        # CRITICAL FIX: Auto-discover custom images that aren't in metadata
        # This allows users to drop images into folders without manual JSON editing
        # Uses mtime caching to avoid performance impact on every metadata load
        metadata = discover_custom_images(folder, metadata, is_artist_images=False)
        
        # CRITICAL FIX: Self-healing - remove providers from metadata if files are deleted
        # This ensures metadata stays in sync with actual files on disk
        providers = metadata.get("providers", {})
        removed_count = 0
        providers_to_remove = []
        for provider_name, provider_data in providers.items():
            filename = provider_data.get("filename", f"{provider_name}.jpg")
            file_path = folder / filename
            # If file doesn't exist but metadata says it's downloaded, remove it
            if provider_data.get("downloaded", False) and not file_path.exists():
                providers_to_remove.append(provider_name)
                removed_count += 1
                logger.debug(f"Self-healing: Removing missing file '{filename}' from metadata for provider '{provider_name}'")
        
        # Remove deleted providers from metadata
        for provider_name in providers_to_remove:
            providers.pop(provider_name, None)
            # If this was the preferred provider, clear the preference
            if metadata.get("preferred_provider") == provider_name:
                metadata["preferred_provider"] = None
                logger.debug(f"Cleared preferred_provider '{provider_name}' (file deleted)")
        
        # Update metadata with cleaned providers
        if removed_count > 0:
            metadata["providers"] = providers
        
        # If new images were discovered or files were removed, save updated metadata
        # Check if discovery found new images by comparing cache
        try:
            folder_key = str(folder.resolve())
        except (OSError, ValueError) as e:
            # CRITICAL FIX: Handle folder path resolution failures
            # If we can't resolve the path, we can't update the cache, but we can still proceed
            logger.debug(f"Could not resolve folder path for cache key: {e}")
            folder_key = str(folder)  # Fallback to string representation
        
        should_save = False
        if folder_key in state._discovery_cache:
            _, discovered_count = state._discovery_cache[folder_key]
            if discovered_count > 0:
                should_save = True
        
        if removed_count > 0:
            should_save = True
        
        if should_save:
            # Save updated metadata with discovered images and cleaned providers
            # Use existing save function which handles locks properly
            save_album_db_metadata(folder, metadata)
            # Invalidate cache after save
            if metadata_path_str in state._album_art_metadata_cache:
                del state._album_art_metadata_cache[metadata_path_str]
            if removed_count > 0:
                logger.info(f"Self-healing: Removed {removed_count} missing file(s) from metadata for {artist} - {folder_name}")
        
        # Get preferred provider
        preferred_provider = metadata.get("preferred_provider")
        if not preferred_provider:
            # Auto-select highest resolution if no preference
            providers = metadata.get("providers", {})
            if not providers:
                return None
            
            highest_res = 0
            preferred_provider = None
            for provider_name, provider_data in providers.items():
                # CRITICAL FIX: Only consider providers that are actually downloaded
                # This prevents selecting providers whose files don't exist
                if not provider_data.get("downloaded", False):
                    continue
                width = provider_data.get("width", 0)
                height = provider_data.get("height", 0)
                res = max(width, height)
                if res > highest_res:
                    highest_res = res
                    preferred_provider = provider_name
            
            if not preferred_provider:
                # Fallback to first available downloaded provider
                for provider_name, provider_data in providers.items():
                    if provider_data.get("downloaded", False):
                        preferred_provider = provider_name
                        break
        
        # Get image path
        providers = metadata.get("providers", {})
        if preferred_provider not in providers:
            logger.warning(f"Preferred provider '{preferred_provider}' not found in DB for {artist} - {album}")
            return None
        
        provider_data = providers[preferred_provider]
        filename = provider_data.get("filename", f"{preferred_provider}.jpg")
        image_path = folder / filename
        
        # FIX: If preferred provider's file doesn't exist (e.g., download in progress or failed),
        # try to fall back to another available provider instead of returning None
        # This prevents the album art selector from appearing broken when a download is in progress
        if not image_path.exists():
            logger.debug(f"Preferred provider '{preferred_provider}' file not found, trying fallback providers")
            # Try to find any provider with an existing file
            for fallback_provider, fallback_data in providers.items():
                fallback_filename = fallback_data.get("filename", f"{fallback_provider}.jpg")
                fallback_path = folder / fallback_filename
                if fallback_path.exists():
                    logger.info(f"Using fallback provider '{fallback_provider}' (preferred '{preferred_provider}' file missing)")
                    # Use fallback but keep preferred_provider in metadata so UI shows correct selection
                    provider_data = fallback_data
                    filename = fallback_filename
                    image_path = fallback_path
                    break
            else:
                # No provider has a file - return None (downloads probably in progress)
                logger.debug(f"No provider files found for {artist} - {album}, downloads may be in progress")
                return None
        
        # OPTIMIZATION: Only update last_accessed if it's been more than 1 hour
        # This prevents constant disk writes on every poll cycle (every 100ms)
        should_save = True
        last_accessed_str = metadata.get("last_accessed")
        if last_accessed_str:
            try:
                # Parse the timestamp (handle Z suffix for UTC)
                if last_accessed_str.endswith('Z'):
                    last_accessed_str = last_accessed_str[:-1] + '+00:00'
                last_accessed = datetime.fromisoformat(last_accessed_str)
                # Convert to naive datetime for comparison with datetime.utcnow()
                if last_accessed.tzinfo is not None:
                    last_accessed = last_accessed.replace(tzinfo=None)
                # If less than 1 hour has passed, don't save
                time_diff = (datetime.utcnow() - last_accessed).total_seconds()
                if time_diff < 3600:  # 1 hour in seconds
                    should_save = False
            except (ValueError, AttributeError):
                # Parse error or missing datetime, save to fix format
                pass
        
        if should_save:
            metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
            if save_album_db_metadata(folder, metadata):
                # Invalidate cache after save (since file mtime changed)
                if metadata_path_str in state._album_art_metadata_cache:
                    del state._album_art_metadata_cache[metadata_path_str]
        
        # Get saved background style (NEW for Phase 2)
        background_style = metadata.get("background_style")
        
        return {
            "path": image_path,
            "metadata": metadata,
            "background_style": background_style  # Return saved style preference
        }
    
    except Exception as e:
        logger.debug(f"Error loading album art from DB: {e}")
        return None
