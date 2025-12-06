from os import path
from typing import Any, Optional, List, Dict
import asyncio
import time
import random  # ADD THIS IMPORT
from functools import wraps

from quart import Quart, render_template, redirect, flash, request, jsonify, url_for, send_from_directory
from lyrics import get_timed_lyrics_previous_and_next, get_current_provider, _is_manually_instrumental, set_manual_instrumental
import lyrics as lyrics_module
from system_utils import get_current_song_meta_data, get_album_db_folder, load_album_art_from_db, save_album_db_metadata, get_cached_art_path, cleanup_old_art, clear_artist_image_cache
from state_manager import *
from config import LYRICS, RESOURCES_DIR, ALBUM_ART_DB_DIR
from settings import settings
from logging_config import get_logger

# Import shared Spotify singleton for controls - ensures all stats are consolidated
from providers.spotify_api import get_shared_spotify_client

import os
from pathlib import Path
import json
import uuid

logger = get_logger(__name__)

# Cache version based on app start time for cache busting
APP_START_TIME = int(time.time())

# Add this global near other globals at the top of server.py
# Global cache for slideshow images
_slideshow_cache = {
    'images': [],
    'last_update': 0
}
_SLIDESHOW_CACHE_TTL = 3600  # 1 hour

# Global throttle for cover art logs (prevents spam when frontend makes multiple requests)
# Key: file path (str), Value: last log timestamp
_cover_art_log_throttle = {}

TEMPLATE_DIRECTORY = str(RESOURCES_DIR / "templates")
STATIC_DIRECTORY = str(RESOURCES_DIR)
app = Quart(__name__, template_folder=TEMPLATE_DIRECTORY, static_folder=STATIC_DIRECTORY)
app.config['SERVER_NAME'] = None
app.secret_key = "secret key"

# --- Helper Functions ---

def get_spotify_client():
    """
    Helper to get the shared Spotify singleton client.
    
    This ensures all API calls across the app use the same instance,
    so statistics are accurately consolidated and caching is efficient.
    """
    client = get_shared_spotify_client()
    return client if client and client.initialized else None

@app.context_processor
async def inject_cache_version() -> dict:
    """Inject cache busting version into all templates"""
    return {"cache_version": APP_START_TIME}

@app.context_processor
async def theme() -> dict: 
    return {"theme": get_attribute_js_notation(get_state(), 'theme')}

# --- Routes ---

@app.route("/")
async def index() -> str:
    """Main page - pass Spotify auth URL if not authenticated"""
    # Check if Spotify needs authentication
    spotify_auth_url = None
    spotify_needs_auth = False
    
    # Use the shared singleton client (ensures all stats consolidated)
    client = get_shared_spotify_client()
    
    # If we have a client that isn't initialized, get auth URL so user can log in
    if client and not client.initialized:
        # Get the auth URL for Spotify login
        try:
            spotify_auth_url = client.get_auth_url()
            spotify_needs_auth = True
        except Exception as e:
            logger.error(f"Failed to get Spotify auth URL: {e}")
            spotify_auth_url = None
    
    # Render the HTML template with Spotify auth info
    return await render_template('index.html', 
                                spotify_auth_url=spotify_auth_url,
                                spotify_needs_auth=spotify_needs_auth)

@app.route("/lyrics")
async def lyrics() -> dict:
    """
    API endpoint that returns lyrics data as JSON.
    Called by the frontend JavaScript to fetch lyrics updates.
    """
    lyrics_data = await get_timed_lyrics_previous_and_next()
    metadata = await get_current_song_meta_data()
    
    # Remove the early return for string type so we can wrap it properly
    # if isinstance(lyrics_data, str):
    #    return {"msg": lyrics_data}
    
    colors = ["#24273a", "#363b54"]
    if metadata and metadata.get("colors"):
        colors = metadata.get("colors")
    
    provider = get_current_provider()
    
    # Determine flags
    is_instrumental = False
    has_lyrics = True
    is_instrumental_manual = False
    
    # Check if song is manually marked as instrumental
    if metadata:
        artist = metadata.get("artist", "")
        title = metadata.get("title", "")
        if artist and title:
            is_instrumental_manual = _is_manually_instrumental(artist, title)
            if is_instrumental_manual:
                # Manually marked as instrumental - override detection
                is_instrumental = True
                has_lyrics = False
    
    if isinstance(lyrics_data, str):
        # Handle error messages or status strings
        msg = lyrics_data
        has_lyrics = False
        
        # Check for specific status messages (only if not manually marked)
        if not is_instrumental_manual and "instrumental" in msg.lower():
            is_instrumental = True
            
        return {
            "lyrics": [], 
            "msg": msg,
            "colors": colors, 
            "provider": provider,
            "has_lyrics": False,
            "is_instrumental": is_instrumental,
            "is_instrumental_manual": is_instrumental_manual
        }
    
    # Check if lyrics are actually empty or just [...]
    # (lyrics_data is a tuple of strings)
    if not lyrics_data or all(not line for line in lyrics_data):
         has_lyrics = False
         # Check for instrumental text if not manually marked
         if not is_instrumental_manual and lyrics_data and len(lyrics_data) == 1:
             text = lyrics_data[0][1].lower().strip() if len(lyrics_data[0]) > 1 else ""
             if text in ["instrumental", "music only", "no lyrics", "non-lyrical", "♪", "♫", "♬", "(instrumental)", "[instrumental]"]:
                 is_instrumental = True

    return {
        "lyrics": list(lyrics_data),
        "colors": colors,
        "provider": provider,
        "has_lyrics": has_lyrics,
        "is_instrumental": is_instrumental,
        "is_instrumental_manual": is_instrumental_manual
    }

@app.route("/current-track")
async def current_track() -> dict:
    """
    Returns detailed track info (Art, Progress, Duration).
    Used for the UI Header/Footer.
    Includes artist_id for visual mode and artist image fetching.
    """
    try:
        metadata = await get_current_song_meta_data()
        if metadata:
            # Check for manual instrumental flag first (takes precedence)
            artist = metadata.get("artist", "")
            title = metadata.get("title", "")
            is_instrumental_manual = False
            is_instrumental = False
            
            if artist and title:
                is_instrumental_manual = _is_manually_instrumental(artist, title)
                if is_instrumental_manual:
                    # Manually marked as instrumental - override detection
                    is_instrumental = True
                else:
                    # Fall back to automatic detection
                    current_lyrics = lyrics_module.current_song_lyrics
                    if current_lyrics and len(current_lyrics) == 1:
                        text = current_lyrics[0][1].lower().strip()
                        # Updated list to match lyrics.py
                        if text in ["instrumental", "music only", "no lyrics", "non-lyrical", "♪", "♫", "♬", "(instrumental)", "[instrumental]"]:
                            is_instrumental = True
            
            metadata["is_instrumental"] = is_instrumental
            metadata["is_instrumental_manual"] = is_instrumental_manual
            return metadata
        return {"error": "No track playing"}
    except Exception as e:
        logger.error(f"Track Info Error: {e}")
        return {"error": str(e)}

# --- PWA Routes ---

@app.route('/manifest.json')
async def manifest():
    """
    Serve the PWA manifest.json file with correct MIME type and icon paths.
    This enables Progressive Web App installation on Android devices.
    We generate it dynamically to ensure icon paths use the correct static URL.
    """
    import json
    
    # Generate manifest with correct icon URLs using url_for
    manifest_data = {
        "name": "SyncLyrics",
        "short_name": "SyncLyrics",
        "description": "Real-time synchronized lyrics display",
        "start_url": "/",
        "scope": "/",
        "display": "fullscreen",
        "orientation": "any",
        "theme_color": "#1db954",
        "background_color": "#000000",
        "categories": ["music", "entertainment"],
        "icons": [
            {
                "src": url_for('static', filename='images/icon-192.png'),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": url_for('static', filename='images/icon-512.png'),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any"
            },
            {
                "src": url_for('static', filename='images/icon-maskable.png'),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "maskable"
            }
        ]
    }
    
    # Return as JSON with correct MIME type
    response = jsonify(manifest_data)
    response.headers['Content-Type'] = 'application/manifest+json'
    return response

# --- Settings API (Unchanged) ---

@app.route("/api/settings", methods=['GET'])
async def api_get_settings():
    return jsonify(settings.get_all())

@app.route("/api/settings/<key>", methods=['POST'])
async def api_update_setting(key: str):
    try:
        data = await request.get_json()
        if 'value' not in data: return jsonify({"error": "No value"}), 400
        needs_restart = settings.set(key, data['value'])
        settings.save_to_config()
        return jsonify({"success": True, "requires_restart": needs_restart})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/settings", methods=['POST'])
async def api_update_settings():
    try:
        data = await request.get_json()
        needs_restart = False
        for key, value in data.items():
            needs_restart |= settings.set(key, value)
        settings.save_to_config()
        return jsonify({"success": True, "requires_restart": needs_restart})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

# --- Provider Management API ---

@app.route("/api/providers/current", methods=['GET'])
async def get_current_provider_info():
    """Get info about the provider currently serving lyrics"""
    from lyrics import get_current_provider, current_song_data
    
    if not current_song_data:
        return jsonify({"error": "No song playing"}), 404
    
    provider_name = get_current_provider()
    if not provider_name:
        return jsonify({"error": "No provider active"}), 404
    
    # Find provider object for additional info
    from lyrics import providers
    provider_info = None
    for p in providers:
        if p.name == provider_name:
            provider_info = {
                "name": p.name,
                "priority": p.priority,
                "enabled": p.enabled
            }
            break
    
    return jsonify(provider_info or {"name": provider_name})

@app.route("/api/providers/available", methods=['GET'])
async def get_available_providers():
    """Get list of providers that could provide lyrics for current song"""
    from lyrics import get_available_providers_for_song, current_song_data
    
    if not current_song_data:
        return jsonify({"error": "No song playing"}), 404
    
    artist = current_song_data.get("artist", "")
    title = current_song_data.get("title", "")
    
    if not artist or not title:
        return jsonify({"error": "Invalid song data"}), 400
    
    providers_list = get_available_providers_for_song(artist, title)
    return jsonify({"providers": providers_list})

@app.route("/api/providers/preference", methods=['POST'])
async def set_provider_preference():
    """Set preferred provider for current song"""
    from lyrics import set_provider_preference as set_pref, current_song_data
    
    if not current_song_data:
        return jsonify({"error": "No song playing"}), 404
    
    data = await request.get_json()
    provider_name = data.get('provider')
    
    if not provider_name:
        return jsonify({"error": "No provider specified"}), 400
    
    artist = current_song_data.get("artist", "")
    title = current_song_data.get("title", "")
    
    result = await set_pref(artist, title, provider_name)
    
    if result['status'] == 'success':
        return jsonify(result), 200
    else:
        return jsonify(result), 400

@app.route("/api/instrumental/mark", methods=['POST'])
async def mark_instrumental():
    """
    Marks or unmarks the current song as instrumental manually.
    Body: {"is_instrumental": true/false}
    """
    try:
        data = await request.get_json()
        is_instrumental = data.get("is_instrumental", False)
        
        metadata = await get_current_song_meta_data()
        if not metadata:
            return jsonify({"error": "No track playing"}), 400
        
        artist = metadata.get("artist", "")
        title = metadata.get("title", "")
        
        if not artist or not title:
            return jsonify({"error": "Missing artist or title"}), 400
        
        success = await set_manual_instrumental(artist, title, is_instrumental)
        
        if success:
            # Force refresh lyrics to apply the change immediately
            # Clear current lyrics so it re-fetches with the new flag
            lyrics_module.current_song_lyrics = None
            lyrics_module.current_song_data = None
            
            return jsonify({
                "success": True,
                "is_instrumental": is_instrumental,
                "message": f"Song marked as {'instrumental' if is_instrumental else 'NOT instrumental'}"
            })
        else:
            return jsonify({"error": "Failed to update instrumental flag"}), 500
            
    except Exception as e:
        logger.error(f"Error marking instrumental: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/providers/preference", methods=['DELETE'])
async def clear_provider_preference_endpoint():
    """Clear provider preference for current song"""
    from lyrics import clear_provider_preference as clear_pref, current_song_data
    
    if not current_song_data:
        return jsonify({"error": "No song playing"}), 404
    
    artist = current_song_data.get("artist", "")
    title = current_song_data.get("title", "")
    
    success = await clear_pref(artist, title)
    
    if success:
        return jsonify({"status": "success", "message": "Preference cleared"}), 200
    else:
        return jsonify({"status": "error", "message": "Failed to clear preference"}), 500

@app.route("/api/lyrics/delete", methods=['DELETE'])
async def delete_cached_lyrics_endpoint():
    """Delete all cached lyrics for current song (use when lyrics are wrong)"""
    from lyrics import delete_cached_lyrics, current_song_data
    
    if not current_song_data:
        return jsonify({"error": "No song playing"}), 404
    
    artist = current_song_data.get("artist", "")
    title = current_song_data.get("title", "")
    
    if not artist or not title:
        return jsonify({"error": "Invalid song data"}), 400
    
    result = await delete_cached_lyrics(artist, title)
    
    if result['status'] == 'success':
        return jsonify(result), 200
    else:
        return jsonify(result), 500

# --- Album Art Database API ---

@app.route("/api/album-art/options", methods=['GET'])
async def get_album_art_options():
    """Get available album art options for current track from database, including artist images"""
    from system_utils import get_current_song_meta_data, load_album_art_from_db, get_album_db_folder
    from config import ALBUM_ART_DB_DIR
    from pathlib import Path
    import json
    from urllib.parse import quote
    
    metadata = await get_current_song_meta_data()
    if not metadata:
        return jsonify({"error": "No song playing"}), 404
    
    artist = metadata.get("artist", "")
    album = metadata.get("album")
    title = metadata.get("title")  # Get title for fallback when album is missing (singles)
    
    if not artist:
        return jsonify({"error": "Invalid song data"}), 400
    
    # CRITICAL FIX: Use title as fallback when album is missing (for singles)
    # This ensures we look in the correct folder: "Artist - Title" instead of just "Artist"
    # This matches the logic used in system_utils.py ensure_album_art_db() and load_album_art_from_db()
    album_or_title = album if album else title
    
    # Load album art from database
    # CRITICAL FIX: Pass album and title explicitly to match function signature
    db_result = load_album_art_from_db(artist, album, title)
    options = []
    preferred_provider = None
    
    if db_result:
        db_metadata = db_result["metadata"]
        providers = db_metadata.get("providers", {})
        preferred_provider = db_metadata.get("preferred_provider")
        
        # Build folder path for album art
        # CRITICAL FIX: Use title as fallback when album is missing (for singles)
        # This ensures we build the correct folder path: "Artist - Title" instead of just "Artist"
        folder_path = get_album_db_folder(artist, album_or_title or db_metadata.get('album'))
        folder_name = folder_path.name
        
        # Add album art options
        for provider_name, provider_data in providers.items():
            encoded_folder = quote(folder_name, safe='')
            encoded_filename = quote(provider_data.get('filename', f'{provider_name}.jpg'), safe='')
            image_url = f"/api/album-art/image/{encoded_folder}/{encoded_filename}"
            
            options.append({
                "provider": provider_name,
                "url": provider_data.get("url"),
                "image_url": image_url,
                "resolution": provider_data.get("resolution", "unknown"),
                "width": provider_data.get("width", 0),
                "height": provider_data.get("height", 0),
                "is_preferred": provider_name == preferred_provider,
                "type": "album_art"  # Distinguish from artist images
            })
    
    # Also load artist images from artist-only folder
    artist_folder = get_album_db_folder(artist, None)  # Artist-only folder
    artist_metadata_path = artist_folder / "metadata.json"
    
    if artist_metadata_path.exists():
        try:
            with open(artist_metadata_path, 'r', encoding='utf-8') as f:
                artist_metadata = json.load(f)
            
            # Check if this is artist images metadata (type: "artist_images")
            if artist_metadata.get("type") == "artist_images":
                artist_images = artist_metadata.get("images", [])
                artist_preferred = artist_metadata.get("preferred_provider")
                folder_name = artist_folder.name
                
                # Convert artist images to options format
                # CRITICAL FIX: Count images per source to create unique provider names when needed
                source_counts = {}
                for img in artist_images:
                    if img.get("downloaded") and img.get("filename"):
                        source = img.get("source", "Unknown")
                        source_counts[source] = source_counts.get(source, 0) + 1
                
                for img in artist_images:
                    if not img.get("downloaded") or not img.get("filename"):
                        continue
                    
                    source = img.get("source", "Unknown")
                    
                    # CRITICAL FIX: Filter out iTunes and LastFM from artist images
                    # These providers don't work for artist images (they only work for album art)
                    # iTunes Search API is designed for app icons and album art, not artist photos
                    # LastFM artist images are often low-quality placeholders
                    if source in ["iTunes", "LastFM", "Last.fm"]:
                        continue  # Skip these providers for artist images
                    
                    filename = img.get("filename")
                    img_url = img.get("url", "")
                    
                    # CRITICAL FIX: Create unique provider name when multiple images from same source
                    # If there are multiple images from the same source, include filename to make it unique
                    # This allows users to select the specific image they want, not just the first one
                    # UI Display: Clean names without "(Artist)" suffix - it's obvious from context
                    if source_counts.get(source, 0) > 1:
                        # Multiple images from this source - include filename for uniqueness
                        # Format: "FanArt.tv (fanart_tv_0.jpg)" - clean display name
                        provider_name = f"{source}"
                    else:
                        # Single image from this source - use simple format
                        provider_name = source
                    
                    # Build image URL
                    encoded_folder = quote(folder_name, safe='')
                    encoded_filename = quote(filename, safe='')
                    image_url = f"/api/album-art/image/{encoded_folder}/{encoded_filename}"
                    
                    # Try to get resolution from image file if available
                    image_path = artist_folder / filename
                    width = img.get("width", 0)
                    height = img.get("height", 0)
                    resolution = f"{width}x{height}" if width and height else "unknown"
                    
                    # Check if this is the preferred artist image
                    # Match by provider_name (with or without "(Artist)" suffix for backward compatibility), source, or URL
                    # Also check if saved preference matches the internal format with "(Artist)" suffix
                    is_preferred = (artist_preferred == provider_name or 
                                  artist_preferred == f"{provider_name} (Artist)" or
                                  artist_preferred == source or
                                  artist_preferred == f"{source} (Artist)" or
                                  artist_preferred == img_url or
                                  (not preferred_provider and artist_preferred and source in artist_preferred))
                    
                    options.append({
                        "provider": provider_name,
                        "url": img_url,  # Include URL for unique identification
                        "filename": filename,  # Include filename for unique identification
                        "image_url": image_url,
                        "resolution": resolution,
                        "width": width,
                        "height": height,
                        "is_preferred": is_preferred,
                        "type": "artist_image"  # Distinguish from album art
                    })
                
                # CRITICAL FIX: Update preferred_provider to reflect artist image preference if set
                # This ensures the response field accurately reflects the current selection
                # Priority: Artist image preference > Album art preference (artist images override album art)
                if artist_preferred:
                    preferred_provider = artist_preferred
        except Exception as e:
            logger.debug(f"Failed to load artist images metadata: {e}")
    
    # If no options found, return error
    if not options:
        return jsonify({"error": "No album art or artist image options found"}), 404
    
    return jsonify({
        "artist": artist,
        "album": album or (db_result["metadata"].get("album", "") if db_result else ""),
        "is_single": db_result["metadata"].get("is_single", False) if db_result else False,
        "preferred_provider": preferred_provider,
        "options": options
    })

@app.route("/api/album-art/preference", methods=['POST'])
async def set_album_art_preference():
    """Set preferred album art or artist image provider for current track"""
    from system_utils import get_current_song_meta_data, get_album_db_folder, load_album_art_from_db, save_album_db_metadata, _art_update_lock
    # Note: cleanup_old_art is imported at top of file (line 11), no need to re-import here
    from config import ALBUM_ART_DB_DIR, CACHE_DIR
    import shutil
    import os
    import json
    from datetime import datetime
    from pathlib import Path
    
    metadata = await get_current_song_meta_data()
    if not metadata:
        return jsonify({"error": "No song playing"}), 404
    
    data = await request.get_json()
    provider_name = data.get('provider')
    explicit_type = data.get('type')  # ADDED: Get explicit type from frontend (most reliable)
    
    if not provider_name:
        return jsonify({"error": "No provider specified"}), 400
    
    artist = metadata.get("artist", "")
    album = metadata.get("album")
    title = metadata.get("title")  # Get title for fallback when album is missing (singles)
    
    if not artist:
        return jsonify({"error": "Invalid song data"}), 400
    
    # CRITICAL FIX: Use title as fallback when album is missing (for singles)
    # This ensures we use the correct folder: "Artist - Title" instead of just "Artist"
    # This matches the logic used in system_utils.py ensure_album_art_db() and load_album_art_from_db()
    album_or_title = album if album else title
    
    # CRITICAL FIX: Validate that we have album_or_title for album art operations
    # This prevents corrupting artist images metadata if both album and title are missing
    # Artist images don't need album/title (they use artist-only folder), but album art does
    if not album_or_title:
        # Check if this is an artist image request - if so, we can proceed without album/title
        # Otherwise, return error for album art requests without album/title
        # OPTIMIZATION: Reuse explicit_type from line 617 instead of retrieving it again
        if not explicit_type or explicit_type != "artist_image":
            logger.error(f"Missing both album and title for artist '{artist}' - cannot set album art preference")
            return jsonify({"error": "Invalid song data: Missing album and title information"}), 400
    
    # CRITICAL FIX: Use explicit type from frontend if provided (most reliable)
    # This prevents ambiguity when provider names overlap between album art and artist images
    # (e.g., "iTunes", "Spotify" can exist in both, causing false positives)
    is_artist_image = False
    
    if explicit_type:
        # Frontend explicitly told us the type - trust it (most reliable method)
        is_artist_image = (explicit_type == "artist_image")
    else:
        # Fallback to detection logic (for backward compatibility with old frontend)
        # Since we removed "(Artist)" suffix from UI, we need to check by looking up in artist images
        try:
            # Check if provider_name matches any artist image in the database
            artist_folder = get_album_db_folder(artist, None)
            artist_metadata_path = artist_folder / "metadata.json"
            if artist_metadata_path.exists():
                with open(artist_metadata_path, 'r', encoding='utf-8') as f:
                    artist_metadata_check = json.load(f)
                if artist_metadata_check.get("type") == "artist_images":
                    artist_images_check = artist_metadata_check.get("images", [])
                    for img in artist_images_check:
                        source_check = img.get("source", "Unknown")
                        filename_check = img.get("filename", "")
                        # Check if provider_name matches any artist image format (with or without "(Artist)" suffix)
                        if (provider_name == source_check or 
                            provider_name == f"{source_check} ({filename_check})" or
                            provider_name == f"{source_check} (Artist)" or
                            provider_name == f"{source_check} ({filename_check}) (Artist)"):
                            is_artist_image = True
                            break
        except Exception:
            # Fallback: check by suffix (backward compatibility)
            is_artist_image = provider_name.endswith(" (Artist)")
    
    if is_artist_image:
        # Handle artist image preference
        artist_folder = get_album_db_folder(artist, None)  # Artist-only folder
        artist_metadata_path = artist_folder / "metadata.json"
        
        if not artist_metadata_path.exists():
            return jsonify({"error": "No artist images database entry found"}), 404
        
        # CRITICAL FIX: Wrap entire Read-Modify-Write sequence in lock to prevent race conditions
        # This ensures that if a background task updates metadata simultaneously, we don't lose data
        # The lock makes the entire operation atomic: read -> modify -> save happens as one unit
        async with _art_update_lock:
            try:
                with open(artist_metadata_path, 'r', encoding='utf-8') as f:
                    artist_metadata = json.load(f)
            except (IOError, OSError, json.JSONDecodeError) as e:
                logger.error(f"Failed to load artist metadata: {e}")
                return jsonify({"error": "Failed to load artist images metadata"}), 500
            except Exception as e:
                logger.error(f"Unexpected error loading artist metadata: {e}", exc_info=True)
                return jsonify({"error": "Failed to load artist images metadata"}), 500
            
            # CRITICAL FIX: Match by provider name, URL, or filename to uniquely identify the selected image
            # This fixes the issue where multiple images from the same source (e.g., FanArt.tv) 
            # couldn't be distinguished, causing only the first one to be selected
            artist_images = artist_metadata.get("images", [])
            
            # Try to extract filename from provider name if it's in the format "Source (filename) (Artist)"
            # Otherwise, extract source name for backward compatibility
            matching_image = None
            
            # CRITICAL FIX: Match by filename first (most robust), then parse provider name
            # Priority: filename > URL > provider name parsing
            
            # 1. Match by filename if provided (MOST RELIABLE - from frontend)
            data_filename = data.get('filename')
            if data_filename:
                for img in artist_images:
                    if img.get("filename") == data_filename and img.get("downloaded"):
                        matching_image = img
                        break
            
            # 2. Match by URL if provided (also reliable)
            if not matching_image:
                data_url = data.get('url')
                if data_url:
                    for img in artist_images:
                        if img.get("url") == data_url and img.get("downloaded"):
                            matching_image = img
                            break
            
            # 3. Parse provider name (handles both old and new formats)
            if not matching_image:
                # Remove "(Artist)" suffix if present (backward compatibility)
                provider_name_clean = provider_name.replace(" (Artist)", "")
                
                # Check if provider name contains filename: "Source (filename)"
                if " (" in provider_name_clean:
                    parts = provider_name_clean.split(" (", 1)
                    if len(parts) == 2:
                        # Has filename: "Source (filename)"
                        source_name = parts[0]
                        filename_from_provider = parts[1].rstrip(")")
                        
                        # Match by source AND filename (case-insensitive source comparison)
                        source_name_lower = source_name.lower()  # Normalize to lowercase
                        for img in artist_images:
                            source = img.get("source", "")
                            if (source.lower() == source_name_lower and 
                                img.get("filename") == filename_from_provider and 
                                img.get("downloaded")):
                                matching_image = img
                                break
                    else:
                        # Fallback: just source name (case-insensitive)
                        source_name = parts[0]
                        source_name_lower = source_name.lower()
                        for img in artist_images:
                            source = img.get("source", "")
                            if source.lower() == source_name_lower and img.get("downloaded"):
                                matching_image = img
                                break
                else:
                    # No filename in provider name - match by source only (gets first match)
                    # CRITICAL FIX: Case-insensitive comparison to handle "Deezer" vs "deezer" mismatches
                    source_name = provider_name_clean
                    source_name_lower = source_name.lower()  # Normalize to lowercase for comparison
                    for img in artist_images:
                        source = img.get("source", "")
                        # Case-insensitive comparison to handle API inconsistencies
                        if source.lower() == source_name_lower and img.get("downloaded"):
                            matching_image = img
                            break
            
            if not matching_image:
                return jsonify({"error": f"Artist image '{provider_name}' not found in database"}), 404
            
            # CRITICAL FIX: Save both provider_name (for display) and filename (for robust loading)
            # The provider_name is what we show in UI, but filename is what we use to actually find the image
            artist_metadata["preferred_provider"] = provider_name
            artist_metadata["preferred_image_filename"] = matching_image.get("filename")  # NEW: Save filename for robust matching
            artist_metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
            
            # CRITICAL FIX: DO NOT clear album art preference when artist image is selected
            # Album art preference (top-left thumbnail) and artist image preference (background) are INDEPENDENT
            # When user selects an artist image:
            #   - Background should show the artist image (handled by load_artist_image_from_db in system_utils.py)
            #   - Top-left should keep the user's preferred album art (e.g., iTunes, not auto-selected LastFM)
            # The system_utils.py logic already handles this correctly:
            #   - load_album_art_from_db() respects preferred_provider for top-left display
            #   - load_artist_image_from_db() only returns image if preference is explicitly set
            #   - get_current_song_meta_data() uses artist image for background if available, but keeps album art for top-left
            
            # Save updated metadata
            if not save_album_db_metadata(artist_folder, artist_metadata):
                return jsonify({"error": "Failed to save artist image preference"}), 500
            
            # Log successful preference save for observability
            logger.info(f"Set artist image preference to '{provider_name}' for {artist}")
            
            # CRITICAL FIX: Clear artist image cache to ensure new preference is immediately reflected
            # Without this, the cache (15-second TTL) would continue serving the old image until it expires
            clear_artist_image_cache(artist)
            
            # Store filename for use outside lock
            filename = matching_image.get("filename")
        
        # Copy selected image to cache for immediate use (outside lock to avoid blocking)
        db_image_path = artist_folder / filename
    else:
        # Handle album art preference (original logic)
        # CRITICAL FIX: Wrap entire Read-Modify-Write sequence in lock to prevent race conditions
        # This ensures that if a background task updates metadata simultaneously, we don't lose data
        # The lock makes the entire operation atomic: read -> modify -> save happens as one unit
        # CRITICAL FIX: Load metadata INSIDE the lock to ensure we get fresh data
        # (Loading before the lock could result in stale data if a background task updates between load and lock)
        async with _art_update_lock:
            # CRITICAL FIX: Use title as fallback when album is missing (for singles)
            # This ensures we look in the correct folder: "Artist - Title" instead of just "Artist"
            # CRITICAL FIX: Pass album and title explicitly to match function signature
            db_result = load_album_art_from_db(artist, album, title)
            if not db_result:
                return jsonify({"error": "No album art database entry found"}), 404
            
            db_metadata = db_result["metadata"]
            providers = db_metadata.get("providers", {})
            
            if provider_name not in providers:
                return jsonify({"error": f"Provider '{provider_name}' not found in database"}), 404
            
            # Update preferred provider
            db_metadata["preferred_provider"] = provider_name
            db_metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
            
            # CRITICAL FIX: Clear artist image preference when album art is selected (mutual exclusion)
            # This ensures that selecting album art overrides any previously selected artist image
            # The user's last selection (album art) should take priority
            artist_folder_clear = get_album_db_folder(artist, None)  # Artist-only folder
            artist_metadata_path_clear = artist_folder_clear / "metadata.json"
            if artist_metadata_path_clear.exists():
                try:
                    with open(artist_metadata_path_clear, 'r', encoding='utf-8') as f:
                        artist_metadata_clear = json.load(f)
                    # Only clear if this is actually an artist images metadata file
                    if artist_metadata_clear.get("type") == "artist_images":
                        # Clear the preferred provider and filename to allow album art to be used
                        # CRITICAL FIX: Use = None instead of .pop() so save_album_db_metadata knows to delete them
                        # (pop() removes the keys, which causes save_album_db_metadata to restore them from existing metadata)
                        artist_metadata_clear["preferred_provider"] = None
                        artist_metadata_clear["preferred_image_filename"] = None
                        artist_metadata_clear["last_accessed"] = datetime.utcnow().isoformat() + "Z"
                        # Save the cleared metadata
                        save_album_db_metadata(artist_folder_clear, artist_metadata_clear)
                        logger.info(f"Cleared artist image preference when album art '{provider_name}' was selected")
                        
                        # CRITICAL FIX: Clear artist image cache to ensure album art is immediately shown
                        # When album art is selected, it overrides artist image preference, so we need to clear the cache
                        clear_artist_image_cache(artist)
                except (IOError, OSError, json.JSONDecodeError) as e:
                    # Expected errors - file issues or JSON parsing
                    logger.warning(f"Failed to clear artist image preference: {e}")
                except Exception as e:
                    # Unexpected error - log with traceback
                    logger.error(f"Unexpected error clearing artist image preference: {e}", exc_info=True)
            
            # Save updated metadata
            # CRITICAL FIX: Use title as fallback when album is missing (for singles)
            # This ensures we save to the correct folder: "Artist - Title" instead of just "Artist"
            folder = get_album_db_folder(artist, album_or_title)
            if not save_album_db_metadata(folder, db_metadata):
                return jsonify({"error": "Failed to save preference"}), 500
            
            # Log successful preference save for observability
            logger.info(f"Set album art preference to '{provider_name}' for {artist} - {album_or_title}")
            
            # Store provider data for use outside lock
            provider_data = providers[provider_name]
            filename = provider_data.get("filename", f"{provider_name}.jpg")
        
        # Copy selected image to cache for immediate use (preserving original format, outside lock to avoid blocking)
        db_image_path = folder / filename
    
    if db_image_path.exists():
        try:
            # Clean up old art first
            cleanup_old_art()
            
            # Get the original file extension from the DB image (preserves format)
            original_extension = db_image_path.suffix or '.jpg'
            
            # Copy DB image to cache with original extension (e.g., current_art.png, current_art.jpg)
            cache_path = CACHE_DIR / f"current_art{original_extension}"
            # FIX: Use unique temp filename to prevent concurrent writes from overwriting each other
            # This prevents race conditions when multiple preference updates happen simultaneously
            temp_filename = f"current_art_{uuid.uuid4().hex}{original_extension}.tmp"
            temp_path = CACHE_DIR / temp_filename
            
            shutil.copy2(db_image_path, temp_path)
            
            # Atomic replace with retry for Windows file locking (matching system_utils.py logic)
            # OPTIMIZATION: Use same lock (_art_update_lock) to prevent concurrent cache file updates
            # This ensures the cache file update is atomic with respect to other art operations (prevents flickering)
            # Note: This is a separate lock acquisition (not nested) since the metadata lock was released above
            # We keep file I/O outside the metadata lock to avoid blocking other metadata operations
            loop = asyncio.get_running_loop()
            async with _art_update_lock:
                replaced = False
                for attempt in range(3):
                    try:
                        import os
                        # Run blocking os.replace in executor to avoid blocking event loop
                        await loop.run_in_executor(None, os.replace, temp_path, cache_path)
                        replaced = True
                        break
                    except OSError:
                        if attempt < 2:
                            await asyncio.sleep(0.1)  # Wait briefly before retry
                        else:
                            logger.warning(f"Could not atomically replace current_art{original_extension} after 3 attempts (file may be locked)")
            
            # Clean up temp file if replacement failed
            if not replaced:
                try:
                    if temp_path.exists():
                        os.remove(temp_path)
                except:
                    pass
                return jsonify({"status": "error", "message": "Failed to update album art"})
            
            # OPTIMIZATION: Only delete spotify_art.jpg AFTER successful copy
            # This ensures we don't delete it if the copy failed, and prevents
            # aggressive deletion. server.py prefers spotify_art.jpg, so we delete
            # it to force fallback to our high-res current_art.*
            if replaced:
                spotify_art_path = CACHE_DIR / "spotify_art.jpg"
                if spotify_art_path.exists():
                    try:
                        os.remove(spotify_art_path)
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to copy selected art to cache: {e}")
    
    # CRITICAL FIX: Invalidate the metadata cache immediately!
    # This forces the server to reload the metadata (and thus the new art URL) on the next request.
    get_current_song_meta_data._last_check_time = 0
    # Also clear cached result to ensure fresh fetch
    if hasattr(get_current_song_meta_data, '_last_result'):
        get_current_song_meta_data._last_result = None
    
    # Add cache busting timestamp
    cache_bust = int(time.time())
    
    return jsonify({
        "status": "success",
        "message": f"Preferred provider set to {provider_name}",
        "provider": provider_name,
        "cache_bust": cache_bust
    })

@app.route("/api/album-art/preference", methods=['DELETE'])
async def clear_album_art_preference():
    """Clear BOTH album art and artist image preferences for current track"""
    from system_utils import get_current_song_meta_data, get_album_db_folder, save_album_db_metadata, _art_update_lock
    import json
    from datetime import datetime

    metadata = await get_current_song_meta_data()
    if not metadata:
        return jsonify({"error": "No song playing"}), 404

    artist = metadata.get("artist", "")
    album = metadata.get("album")
    title = metadata.get("title")
    album_or_title = album if album else title

    if not artist:
        return jsonify({"error": "Invalid song data"}), 400

    async with _art_update_lock:
        # 1. Clear Artist Image Preference
        try:
            artist_folder = get_album_db_folder(artist, None)
            artist_meta_path = artist_folder / "metadata.json"
            if artist_meta_path.exists():
                with open(artist_meta_path, 'r', encoding='utf-8') as f:
                    artist_data = json.load(f)
                
                if artist_data.get("type") == "artist_images":
                    # CRITICAL FIX: Explicitly set to None so save_album_db_metadata knows to delete it
                    # (pop() would be restored by safety logic in save function)
                    artist_data["preferred_provider"] = None
                    artist_data["preferred_image_filename"] = None
                    artist_data["last_accessed"] = datetime.utcnow().isoformat() + "Z"
                    save_album_db_metadata(artist_folder, artist_data)
                    logger.info(f"Cleared artist image preference for {artist}")
        except Exception as e:
            logger.error(f"Error clearing artist preference: {e}")

        # 2. Clear Album Art Preference
        if album_or_title:
            try:
                album_folder = get_album_db_folder(artist, album_or_title)
                album_meta_path = album_folder / "metadata.json"
                if album_meta_path.exists():
                    with open(album_meta_path, 'r', encoding='utf-8') as f:
                        album_data = json.load(f)
                    
                    # CRITICAL FIX: Explicitly set to None so save_album_db_metadata knows to delete it
                    # (pop() would be restored by safety logic in save function)
                    album_data["preferred_provider"] = None
                    album_data["last_accessed"] = datetime.utcnow().isoformat() + "Z"
                    save_album_db_metadata(album_folder, album_data)
                    logger.info(f"Cleared album art preference for {artist} - {album_or_title}")
            except Exception as e:
                logger.error(f"Error clearing album art preference: {e}")

    # Invalidate cache
    get_current_song_meta_data._last_check_time = 0
    if hasattr(get_current_song_meta_data, '_last_result'):
        get_current_song_meta_data._last_result = None

    return jsonify({"status": "success", "message": "Art preferences cleared"})

@app.route("/api/album-art/background-style", methods=['POST'])
async def set_background_style():
    """Set preferred background style for current album (Sharp, Soft, Blur) - Phase 2"""
    from system_utils import get_current_song_meta_data, get_album_db_folder, load_album_art_from_db, save_album_db_metadata
    from datetime import datetime
    
    # Get current track info to know which album to update
    metadata = await get_current_song_meta_data()
    if not metadata:
        return jsonify({"error": "No song playing"}), 404
    
    data = await request.get_json()
    style = data.get('style')  # 'sharp', 'soft', 'blur', or 'none' to clear
    
    if not style:
        return jsonify({"error": "No style specified"}), 400
    
    # Validate style value
    if style not in ['sharp', 'soft', 'blur', 'none']:
        return jsonify({"error": f"Invalid style '{style}'. Must be 'sharp', 'soft', 'blur', or 'none'"}), 400
        
    artist = metadata.get("artist", "")
    album = metadata.get("album")
    title = metadata.get("title")  # Get title for fallback when album is missing (singles)
    
    if not artist:
        return jsonify({"error": "Invalid song data"}), 400
    
    # CRITICAL FIX: Use title as fallback when album is missing (for singles)
    # This ensures background styles work for singles, not just albums
    album_or_title = album if album else title
    
    if not album_or_title:
        return jsonify({"error": "Invalid song data: Missing album and title information"}), 400
    
    # Use lock to prevent race condition with background art download task
    # This ensures that if a background task is updating metadata, we don't overwrite each other
    from system_utils import _art_update_lock
    
    async with _art_update_lock:
        # Load existing metadata or create new if missing (though it should exist if art is there)
        # CRITICAL FIX: Pass album and title explicitly to match function signature
        # CRITICAL FIX: Use title fallback for singles support
        db_result = load_album_art_from_db(artist, album, title)
        
        if db_result:
            db_metadata = db_result["metadata"]
        else:
            # If no DB entry exists yet, we can't save preference easily without creating the structure
            # For now, return error if no art DB exists
            return jsonify({"error": "No album art database entry found. Please wait for art to download."}), 404
            
        # Update style (or remove if 'none')
        if style == 'none':
            # Explicitly set to None to signal deletion (save_album_db_metadata will filter this out)
            # This prevents the save function from restoring it from existing metadata
            db_metadata["background_style"] = None
            logger.info(f"Cleared background_style preference for {artist} - {album_or_title}")
        else:
            db_metadata["background_style"] = style
            logger.info(f"Set background_style to '{style}' for {artist} - {album_or_title}")
        db_metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
        
        # Save
        # CRITICAL FIX: Use title fallback for singles support
        folder = get_album_db_folder(artist, album_or_title)
        if save_album_db_metadata(folder, db_metadata):
            # CRITICAL FIX: Invalidate metadata cache to force immediate reload of background_style
            # This ensures the "Auto" reset takes effect immediately in the UI
            get_current_song_meta_data._last_check_time = 0
            
            return jsonify({"status": "success", "style": style, "message": f"Saved {style} preference"})
        else:
            return jsonify({"error": "Failed to save preference"}), 500

@app.route("/api/album-art/image/<folder_name>/<filename>", methods=['GET'])
async def serve_album_art_image(folder_name: str, filename: str):
    """Serve album art images from database"""
    from config import ALBUM_ART_DB_DIR
    from quart import Response
    from urllib.parse import unquote
    import os
    
    try:
        # Decode URL-encoded folder name and filename
        decoded_folder = unquote(folder_name)
        decoded_filename = unquote(filename)
        
        # Build full path
        image_path = ALBUM_ART_DB_DIR / decoded_folder / decoded_filename
        
        # Security check: ensure path is within ALBUM_ART_DB_DIR
        try:
            image_path.resolve().relative_to(ALBUM_ART_DB_DIR.resolve())
        except ValueError:
            # Path outside ALBUM_ART_DB_DIR - security violation
            logger.warning(f"Security violation: Attempted to access path outside ALBUM_ART_DB_DIR: {image_path}")
            return "", 403
        
        if not image_path.exists():
            return "", 404
        
        # Read and serve image
        with open(image_path, 'rb') as f:
            image_data = f.read()
        
        # Determine mimetype based on file extension (preserves original format)
        ext = image_path.suffix.lower()
        mime = 'image/jpeg'  # Default
        if ext == '.png': mime = 'image/png'
        elif ext == '.bmp': mime = 'image/bmp'
        elif ext == '.gif': mime = 'image/gif'
        elif ext == '.webp': mime = 'image/webp'
        
        return Response(
            image_data,
            mimetype=mime,
            headers={'Cache-Control': 'public, max-age=86400'}  # Cache for 24 hours
        )
    except Exception as e:
        logger.error(f"Error serving album art image: {e}")
        return "", 500

# --- Playback Control API (The New Features) ---

@app.route("/cover-art")
async def get_cover_art():
    """Serves the album art or background image directly from the source (DB or Thumbnail) without race conditions."""
    from system_utils import get_current_song_meta_data, get_cached_art_path
    from quart import send_file
    from pathlib import Path

    global _cover_art_log_throttle  # <--- CRITICAL FIX NEEDED HERE

    # 1. Get the current song metadata to find the real path
    metadata = await get_current_song_meta_data()
    
    # CRITICAL FIX: Check if this is a background image request (separate from album art display)
    # If type=background is in query params, serve background_image_path instead of album_art_path
    is_background = request.args.get('type') == 'background'
    
    # 2. Check if we have a direct path to the image (DB file or Unique Thumbnail)
    # For background: use background_image_path if available, otherwise fallback to album_art_path
    # For album art: always use album_art_path
    if metadata:
        if is_background and metadata.get("background_image_path"):
            art_path = Path(metadata["background_image_path"])
        elif metadata.get("album_art_path"):
            art_path = Path(metadata["album_art_path"])
        else:
            art_path = None
    else:
        art_path = None
    
    if art_path:
        # CRITICAL FIX: Verify file exists before serving (handles cleanup race conditions)
        # If thumbnail was deleted during cleanup while metadata cache still references it,
        # we fall through to legacy path instead of returning 404
        if art_path.exists():
            try:
                # DEBUG: Log size to verify quality
                file_size = art_path.stat().st_size
                
                # Throttle logging: only log once every 60 seconds per file
                # This prevents spam when frontend makes multiple simultaneous requests (main display, background, thumbnails, etc.)
                current_time = time.time()
                last_log_time = _cover_art_log_throttle.get(str(art_path), 0)
                if current_time - last_log_time > 60:
                    logger.info(f"Serving cover art: {art_path.name} ({file_size} bytes)")
                    _cover_art_log_throttle[str(art_path)] = current_time
                    
                    # Clean up old entries to prevent memory leak (keep only recent entries)
                    # Remove entries older than 5 minutes to prevent unbounded growth
                    if len(_cover_art_log_throttle) > 100:
                        cutoff_time = current_time - 300  # 5 minutes
                        _cover_art_log_throttle = {
                            k: v for k, v in _cover_art_log_throttle.items()
                            if v > cutoff_time
                        }
                
                # Determine mimetype based on extension (preserves original format)
                ext = art_path.suffix.lower()
                mime = 'image/jpeg'  # Default
                if ext == '.png': mime = 'image/png'
                elif ext == '.bmp': mime = 'image/bmp'
                elif ext == '.gif': mime = 'image/gif'
                elif ext == '.webp': mime = 'image/webp'
                
                # Serve the file directly with explicit no-cache headers
                # CRITICAL FIX: Explicit headers prevent browser caching issues
                response = await send_file(art_path, mimetype=mime)
                response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response.headers['Pragma'] = 'no-cache'
                response.headers['Expires'] = '0'
                return response
            except Exception as e:
                logger.error(f"Failed to serve art from path {art_path}: {e}")
        else:
            # File was deleted (cleanup race condition), fall through to legacy path
            logger.debug(f"album_art_path {art_path} no longer exists, falling back to legacy path")

    # 3. Fallback to legacy current_art.jpg (only if no specific path found)
    # This ensures backward compatibility if metadata doesn't have album_art_path
    art_path = get_cached_art_path()
    if art_path and art_path.exists():
        try:
            # Determine mimetype based on extension (preserves original format)
            ext = art_path.suffix.lower()
            mime = 'image/jpeg'  # Default
            if ext == '.png': mime = 'image/png'
            elif ext == '.bmp': mime = 'image/bmp'
            elif ext == '.gif': mime = 'image/gif'
            elif ext == '.webp': mime = 'image/webp'
            
            # CRITICAL FIX: Explicit headers prevent browser caching issues
            response = await send_file(art_path, mimetype=mime)
            response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
            return response
        except (OSError, IOError) as e:
            logger.warning(f"Failed to read album art: {e}")
    
    return "", 404

@app.route("/api/playback/play-pause", methods=['POST'])
async def toggle_playback():
    client = get_spotify_client()
    if not client: return jsonify({"error": "Spotify not connected"}), 503
    
    # We need to know if playing or paused to toggle
    track = await client.get_current_track()
    # if not track: return jsonify({"error": "No active session"}), 404
    
    # Logic Update (Dec 1, 2025):
    # If track is None (inactive session), we should try to RESUME instead of erroring.
    # Spotify clears the active session after a few minutes of pause.
    is_playing = track.get('is_playing') if track else False
    
    if is_playing:
        await client.pause_playback()
        msg = "Paused"
    else:
        # Try to resume. This works for both "Paused" state and "Inactive/No Session" state.
        success = await client.resume_playback()
        if success:
            msg = "Resumed"
        else:
            # If resume failed and we really had no track info, then we can't do anything
            if not track:
                return jsonify({"error": "No active session"}), 404
            msg = "Resume command sent (but might have failed)"
    
    return jsonify({"status": "success", "message": msg})

@app.route("/api/playback/next", methods=['POST'])
async def next_track():
    client = get_spotify_client()
    if not client: return jsonify({"error": "Spotify not connected"}), 503
    
    await client.next_track()
    return jsonify({"status": "success", "message": "Skipped"})

@app.route("/api/playback/previous", methods=['POST'])
async def previous_track():
    client = get_spotify_client()
    if not client: return jsonify({"error": "Spotify not connected"}), 503
    
    await client.previous_track()
    return jsonify({"status": "success", "message": "Previous"})

@app.route("/api/artist/images", methods=['GET'])
async def get_artist_images():
    """
    Get artist images, preferring local DB, falling back to Spotify and caching.
    """
    # Get artist_id from query params (might be stale if frontend hasn't updated)
    artist_id = request.args.get('artist_id')
    
    # We also need the artist NAME to find the folder
    # Try to get from current metadata if not passed
    metadata = await get_current_song_meta_data()
    artist_name = metadata.get('artist') if metadata else None
    
    if not artist_name:
         return jsonify({"error": "No artist name available"}), 400

    # CRITICAL FIX: Prefer artist_id from metadata (current track) over query param (might be stale)
    # This prevents race conditions where frontend sends old ID (from previous track)
    # but backend has new Artist Name (from current track).
    # If metadata doesn't have artist_id, fall back to query param (better than nothing)
    if metadata and metadata.get('artist_id'):
        artist_id = metadata.get('artist_id')
    # Note: If metadata doesn't have artist_id, we use query param as fallback.
    # This is safe because ensure_artist_image_db uses artist_name as primary identifier
    # and artist_id is only used for Spotify fallback and race condition prevention.

    # Log visual mode activity/fetching
    # logger.info(f"Fetching artist images for Visual Mode: {artist_name} ({artist_id})")

    # 1. Try to ensure/fetch from DB (this handles caching automatically)
    from system_utils import ensure_artist_image_db
    
    # This will return local URLs like /api/album-art/image/Artist/img.jpg
    images = await ensure_artist_image_db(artist_name, artist_id)
    
    return jsonify({
        "artist_id": artist_id,
        "artist_name": artist_name,
        "images": images,
        "count": len(images)
    })

@app.route("/api/playback/queue", methods=['GET'])
async def get_playback_queue():
    client = get_spotify_client()
    if not client: return jsonify({"error": "Spotify not connected"}), 503
    
    queue_data = await client.get_queue()
    if not queue_data:
        return jsonify({"error": "Failed to fetch queue"}), 500
        
    # Simplify structure for frontend
    currently_playing = queue_data.get('currently_playing')
    queue = queue_data.get('queue', [])
    
    return jsonify({
        "current": currently_playing,
        "queue": queue[:20]  # Limit to next 20 songs
    })

@app.route("/api/playback/liked", methods=['GET'])
async def check_liked_status():
    track_id = request.args.get('track_id')
    if not track_id: return jsonify({"error": "No track_id provided"}), 400
    
    client = get_spotify_client()
    if not client: return jsonify({"error": "Spotify not connected"}), 503
    
    is_liked = await client.is_track_liked(track_id)
    return jsonify({"liked": is_liked})

@app.route("/api/playback/liked", methods=['POST'])
async def toggle_liked_status():
    data = await request.get_json()
    track_id = data.get('track_id')
    action = data.get('action') # 'like' or 'unlike'
    
    if not track_id or not action: return jsonify({"error": "Missing parameters"}), 400
    
    client = get_spotify_client()
    if not client: return jsonify({"error": "Spotify not connected"}), 503
    
    success = False
    if action == 'like':
        success = await client.like_track(track_id)
    elif action == 'unlike':
        success = await client.unlike_track(track_id)
        
    return jsonify({"success": success})


# ============================================================================
# Audio Recognition API (Reaper Integration)
# ============================================================================

@app.route('/api/audio-recognition/status', methods=['GET'])
async def audio_recognition_status():
    """
    Get audio recognition status.
    Returns current state, mode, song info, and device configuration.
    """
    try:
        from system_utils.reaper import get_reaper_source, ReaperAudioSource
        from audio_recognition import AudioCaptureManager
        
        source = get_reaper_source()
        status = source.get_status()
        
        # Add device availability info
        status["device_available"] = source._engine.capture.is_device_available() if source._engine else False
        
        return jsonify(status)
        
    except ImportError as e:
        return jsonify({
            "error": "Audio recognition not available",
            "details": str(e),
            "available": False
        })
    except Exception as e:
        logger.error(f"Audio recognition status error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/audio-recognition/start', methods=['POST'])
async def audio_recognition_start():
    """
    Start audio recognition manually.
    Body: {"manual": true} (optional, defaults to true for manual trigger)
    """
    try:
        from system_utils.reaper import get_reaper_source
        
        data = await request.get_json() or {}
        manual = data.get("manual", True)
        
        source = get_reaper_source()
        await source.start(manual=manual)
        
        return jsonify({
            "status": "started",
            "mode": "manual" if manual else "reaper"
        })
        
    except ImportError as e:
        return jsonify({
            "error": "Audio recognition not available",
            "details": str(e)
        }), 500
    except Exception as e:
        logger.error(f"Audio recognition start error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/audio-recognition/stop', methods=['POST'])
async def audio_recognition_stop():
    """Stop audio recognition."""
    try:
        from system_utils.reaper import get_reaper_source
        
        source = get_reaper_source()
        await source.stop()
        
        return jsonify({"status": "stopped"})
        
    except ImportError as e:
        return jsonify({
            "error": "Audio recognition not available",
            "details": str(e)
        }), 500
    except Exception as e:
        logger.error(f"Audio recognition stop error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/audio-recognition/devices', methods=['GET'])
async def audio_recognition_devices():
    """
    List available audio capture devices.
    Returns device list with auto-detected loopback recommendation.
    """
    try:
        from audio_recognition import AudioCaptureManager
        
        devices = AudioCaptureManager.list_devices()
        recommended = AudioCaptureManager.find_loopback_device()
        
        return jsonify({
            "devices": devices,
            "recommended": recommended,
            "count": len(devices)
        })
        
    except ImportError as e:
        return jsonify({
            "error": "Audio recognition not available",
            "details": str(e),
            "devices": []
        }), 500
    except Exception as e:
        logger.error(f"Audio recognition devices error: {e}")
        return jsonify({"error": str(e)}), 500


# --- System Routes ---

@app.route('/settings', methods=['GET', 'POST'])
async def settings_page():
    if request.method == 'POST':
        form_data = await request.form
        
        # Legacy support
        theme = form_data.get('theme', 'dark')
        terminal = form_data.get('terminal-method', 'false').lower() == 'true'
        state = get_state()
        state = set_attribute_js_notation(state, 'theme', theme)
        state = set_attribute_js_notation(state, 'representationMethods.terminal', terminal)
        set_state(state)

        # New settings support
        for key, value in form_data.items():
            if key in ['theme', 'terminal-method']: continue
            try:
                # Simple type conversion logic
                if value.lower() in ['true', 'on']: val = True
                elif value.lower() in ['false', 'off']: val = False
                elif value.isdigit(): val = int(value)
                else: val = value
                settings.set(key, val)
            except: pass
        
        settings.save_to_config()
        return redirect(url_for('settings_page'))

    # Render
    settings_by_category = {}
    for key, setting in settings._definitions.items():
        cat = setting.category or "Misc"
        if cat not in settings_by_category: settings_by_category[cat] = {}
        settings_by_category[cat][key] = {
            'name': setting.name, 'type': setting.type.__name__,
            'value': settings.get(key), 'description': setting.description
        }
    
    return await render_template('settings.html', settings=settings_by_category, theme=get_attribute_js_notation(get_state(), 'theme'))

@app.route('/reset-defaults')
async def reset_defaults():
    settings.reset_to_defaults()
    return redirect(url_for('settings_page'))

@app.route("/exit-application")
async def exit_application() -> dict:
    from context import queue
    from sync_lyrics import force_exit
    queue.put("exit")
    import threading
    threading.Timer(2.0, force_exit).start()
    return {"status": "ok"}, 200

@app.route("/restart", methods=['POST'])
async def restart_server():
    from context import queue
    queue.put("restart")
    return {'status': 'ok'}, 200

@app.route('/config')
async def get_client_config():
    return {
        "updateInterval": LYRICS["display"]["update_interval"] * 1000,
        "blurStrength": settings.get("ui.blur_strength"),
        "overlayOpacity": settings.get("ui.overlay_opacity"),
        "sharpAlbumArt": settings.get("ui.sharp_album_art"),
        "softAlbumArt": settings.get("ui.soft_album_art"),
        # Visual Mode settings
        "visualModeEnabled": settings.get("visual_mode.enabled"),
        "visualModeDelaySeconds": settings.get("visual_mode.delay_seconds"),
        "visualModeAutoSharp": settings.get("visual_mode.auto_sharp"),
        "slideshowEnabled": settings.get("visual_mode.slideshow.enabled"),
        "slideshowIntervalSeconds": settings.get("visual_mode.slideshow.interval_seconds")
    }

@app.route("/callback")
async def spotify_callback():
    """
    Handle Spotify OAuth callback.
    This route receives the authorization code from Spotify after the user logs in.
    """
    # Get the authorization code from query parameters
    code = request.args.get('code')
    error = request.args.get('error')
    
    # Check for errors from Spotify
    if error:
        logger.error(f"Spotify OAuth error: {error}")
        return """
        <html>
        <head><title>Spotify Login Failed</title></head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h1>❌ Login Failed</h1>
            <p>Spotify authentication was cancelled or failed.</p>
            <p><a href="/">Return to Home</a></p>
        </body>
        </html>
        """, 400
    
    if not code:
        logger.error("No authorization code received from Spotify")
        return """
        <html>
        <head><title>Spotify Login Failed</title></head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h1>❌ Login Failed</h1>
            <p>No authorization code received from Spotify.</p>
            <p><a href="/">Return to Home</a></p>
        </body>
        </html>
        """, 400
    
    # Get the shared singleton client and complete authentication
    # The singleton ensures all parts of the app share the same authenticated instance
    client = get_shared_spotify_client()
    
    # Complete the authentication flow
    success = await client.complete_auth(code)
    
    if success:
        # No need to update globals - the singleton pattern handles this automatically
        logger.info("Spotify authentication successful")
        return """
        <html>
        <head><title>Spotify Login Successful</title></head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h1>✅ Login Successful!</h1>
            <p>You have successfully connected to Spotify.</p>
            <p>Redirecting to home page...</p>
            <script>
                setTimeout(function() {
                    window.location.href = '/';
                }, 2000);
            </script>
            <p><a href="/">Click here if you are not redirected</a></p>
        </body>
        </html>
        """
    else:
        logger.error("Failed to complete Spotify authentication")
        return """
        <html>
        <head><title>Spotify Login Failed</title></head>
        <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
            <h1>❌ Login Failed</h1>
            <p>Failed to complete Spotify authentication. Please try again.</p>
            <p><a href="/">Return to Home</a></p>
        </body>
        </html>
        """, 500

# Add this new route near other /api routes, e.g. after /api/artist/images

@app.route('/api/slideshow/random-images')
async def get_random_slideshow_images():
    """
    Get a random selection of images from the global album art database.
    Used for the idle screen dashboard.
    """
    try:
        limit = int(request.args.get('limit', 20))
        current_time = time.time()
        
        # Check cache validity
        if not _slideshow_cache['images'] or (current_time - _slideshow_cache['last_update'] > _SLIDESHOW_CACHE_TTL):
            logger.info("Refeshing slideshow image cache...")
            
            # Helper to recursively find images
            def find_all_images():
                images = []
                if not ALBUM_ART_DB_DIR.exists():
                    return []
                    
                # Walk through the database
                for root, _, files in os.walk(ALBUM_ART_DB_DIR):
                    for file in files:
                        if file.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.bmp')):
                            # Get relative path from DB root for the API URL
                            full_path = Path(root) / file
                            try:
                                rel_path = full_path.relative_to(ALBUM_ART_DB_DIR)
                                # Convert Windows path separators to forward slashes for URL
                                url_path = str(rel_path).replace('\\', '/')
                                images.append(f"/api/album-art/image/{url_path}")
                            except ValueError:
                                pass
                return images

            # Run file scan in thread to avoid blocking
            loop = asyncio.get_running_loop()
            all_images = await loop.run_in_executor(None, find_all_images)
            
            # Update cache
            if all_images:
                _slideshow_cache['images'] = all_images
                _slideshow_cache['last_update'] = current_time
                logger.info(f"Slideshow cache updated with {len(all_images)} images")
        
        # Use cached images
        all_images = _slideshow_cache['images']
        
        if not all_images:
            return jsonify({'images': []})
            
        # Shuffle and pick random subset (from cache)
        # We copy the list to avoid modifying the cache with shuffle
        shuffled = all_images.copy()
        random.shuffle(shuffled)
        selected_images = shuffled[:limit]
        
        return jsonify({
            'images': selected_images,
            'total_available': len(all_images)
        })
        
    except Exception as e:
        logger.error(f"Error generating random slideshow: {e}")
        return jsonify({'error': str(e)}), 500