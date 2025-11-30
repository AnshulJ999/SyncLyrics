from os import path
from typing import Any, Optional, List, Dict
import asyncio
import time
import random  # ADD THIS IMPORT
from functools import wraps

from quart import Quart, render_template, redirect, flash, request, jsonify, url_for, send_from_directory
from lyrics import get_timed_lyrics_previous_and_next, get_current_provider
import lyrics as lyrics_module
from system_utils import get_current_song_meta_data, get_album_db_folder, load_album_art_from_db, save_album_db_metadata, get_cached_art_path, cleanup_old_art
from state_manager import *
from config import LYRICS, RESOURCES_DIR, ALBUM_ART_DB_DIR
from settings import settings
from logging_config import get_logger

# Import shared Spotify singleton for controls - ensures all stats are consolidated
from providers.spotify_api import get_shared_spotify_client

import os
from pathlib import Path
import json

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
    
    if isinstance(lyrics_data, str):
        # Handle error messages or status strings
        msg = lyrics_data
        has_lyrics = False
        
        # Check for specific status messages
        if "instrumental" in msg.lower():
            is_instrumental = True
            
        return {
            "lyrics": [], 
            "msg": msg,
            "colors": colors, 
            "provider": provider,
            "has_lyrics": False,
            "is_instrumental": is_instrumental
        }
    
    # Check if lyrics are actually empty or just [...]
    # (lyrics_data is a tuple of strings)
    if not lyrics_data or all(not line for line in lyrics_data):
         has_lyrics = False

    return {
        "lyrics": list(lyrics_data),
        "colors": colors,
        "provider": provider,
        "has_lyrics": has_lyrics,
        "is_instrumental": is_instrumental
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
            # Inject instrumental flag from lyrics module to avoid double-calls in frontend
            is_instrumental = False
            current_lyrics = lyrics_module.current_song_lyrics
            if current_lyrics and len(current_lyrics) == 1:
                text = current_lyrics[0][1].lower().strip()
                # Updated list to match lyrics.py
                if text in ["instrumental", "music only", "no lyrics", "non-lyrical", "♪", "♫", "♬", "(instrumental)", "[instrumental]"]:
                    is_instrumental = True
            
            metadata["is_instrumental"] = is_instrumental
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
    """Get available album art options for current track from database"""
    from system_utils import get_current_song_meta_data, load_album_art_from_db
    
    metadata = await get_current_song_meta_data()
    if not metadata:
        return jsonify({"error": "No song playing"}), 404
    
    artist = metadata.get("artist", "")
    album = metadata.get("album")
    
    if not artist:
        return jsonify({"error": "Invalid song data"}), 400
    
    # Load from database
    db_result = load_album_art_from_db(artist, album)
    if not db_result:
        return jsonify({"error": "No album art database entry found"}), 404
    
    db_metadata = db_result["metadata"]
    
    # Format response for frontend
    options = []
    providers = db_metadata.get("providers", {})
    preferred_provider = db_metadata.get("preferred_provider")
    
    for provider_name, provider_data in providers.items():
        # Build image URL for serving (use same folder name logic as get_album_db_folder)
        from system_utils import get_album_db_folder
        folder_path = get_album_db_folder(artist, album or db_metadata.get('album'))
        folder_name = folder_path.name  # Get the actual sanitized folder name
        
        # URL encode the folder name and filename
        from urllib.parse import quote
        encoded_folder = quote(folder_name, safe='')
        encoded_filename = quote(provider_data.get('filename', f'{provider_name}.jpg'), safe='')
        image_url = f"/api/album-art/image/{encoded_folder}/{encoded_filename}"
        
        options.append({
            "provider": provider_name,
            "url": provider_data.get("url"),  # Original URL
            "image_url": image_url,  # Local server URL
            "resolution": provider_data.get("resolution", "unknown"),
            "width": provider_data.get("width", 0),
            "height": provider_data.get("height", 0),
            "is_preferred": provider_name == preferred_provider
        })
    
    return jsonify({
        "artist": artist,
        "album": album or db_metadata.get("album", ""),
        "is_single": db_metadata.get("is_single", False),
        "preferred_provider": preferred_provider,
        "options": options
    })

@app.route("/api/album-art/preference", methods=['POST'])
async def set_album_art_preference():
    """Set preferred album art provider for current track"""
    from system_utils import get_current_song_meta_data, get_album_db_folder, load_album_art_from_db, save_album_db_metadata
    from config import ALBUM_ART_DB_DIR, CACHE_DIR
    import shutil
    from datetime import datetime
    
    metadata = await get_current_song_meta_data()
    if not metadata:
        return jsonify({"error": "No song playing"}), 404
    
    data = await request.get_json()
    provider_name = data.get('provider')
    
    if not provider_name:
        return jsonify({"error": "No provider specified"}), 400
    
    artist = metadata.get("artist", "")
    album = metadata.get("album")
    
    if not artist:
        return jsonify({"error": "Invalid song data"}), 400
    
    # Load existing metadata
    db_result = load_album_art_from_db(artist, album)
    if not db_result:
        return jsonify({"error": "No album art database entry found"}), 404
    
    db_metadata = db_result["metadata"]
    providers = db_metadata.get("providers", {})
    
    if provider_name not in providers:
        return jsonify({"error": f"Provider '{provider_name}' not found in database"}), 404
    
    # Update preferred provider
    db_metadata["preferred_provider"] = provider_name
    db_metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
    
    # Save updated metadata
    folder = get_album_db_folder(artist, album)
    if not save_album_db_metadata(folder, db_metadata):
        return jsonify({"error": "Failed to save preference"}), 500
    
    # Copy selected image to cache for immediate use (preserving original format)
    provider_data = providers[provider_name]
    filename = provider_data.get("filename", f"{provider_name}.jpg")
    db_image_path = folder / filename
    
    if db_image_path.exists():
        try:
            # Clean up old art first
            from system_utils import cleanup_old_art
            cleanup_old_art()
            
            # Get the original file extension from the DB image (preserves format)
            original_extension = db_image_path.suffix or '.jpg'
            
            # Copy DB image to cache with original extension (e.g., current_art.png, current_art.jpg)
            cache_path = CACHE_DIR / f"current_art{original_extension}"
            temp_path = CACHE_DIR / f"current_art{original_extension}.tmp"
            
            shutil.copy2(db_image_path, temp_path)
            
            # Atomic replace with retry for Windows file locking (matching system_utils.py logic)
            replaced = False
            for attempt in range(3):
                try:
                    import os
                    os.replace(temp_path, cache_path)
                    replaced = True
                    break
                except OSError:
                    if attempt < 2:
                        await asyncio.sleep(0.1)  # Wait briefly before retry
                    else:
                        logger.warning(f"Could not atomically replace current_art{original_extension} after 3 attempts (file may be locked)")
            
            # OPTIMIZATION: Only delete spotify_art.jpg AFTER successful copy
            # This ensures we don't delete it if the copy failed, and prevents
            # aggressive deletion. server.py prefers spotify_art.jpg, so we delete
            # it to force fallback to our high-res current_art.*
            if replaced:
                import os
                spotify_art_path = CACHE_DIR / "spotify_art.jpg"
                if spotify_art_path.exists():
                    try:
                        os.remove(spotify_art_path)
                    except Exception:
                        pass
            
            # Clean up temp file if replace failed
            if not replaced:
                try:
                    import os
                    os.remove(temp_path)
                except:
                    pass
        except Exception as e:
            logger.warning(f"Failed to copy selected art to cache: {e}")
    
    return jsonify({
        "status": "success",
        "message": f"Preferred provider set to {provider_name}",
        "provider": provider_name
    })

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
    style = data.get('style')  # 'sharp', 'soft', 'blur', or None to clear
    
    if not style:
        return jsonify({"error": "No style specified"}), 400
    
    # Validate style value
    if style not in ['sharp', 'soft', 'blur']:
        return jsonify({"error": f"Invalid style '{style}'. Must be 'sharp', 'soft', or 'blur'"}), 400
        
    artist = metadata.get("artist", "")
    album = metadata.get("album")
    
    if not artist:
        return jsonify({"error": "Invalid song data"}), 400
        
    # Load existing metadata or create new if missing (though it should exist if art is there)
    db_result = load_album_art_from_db(artist, album)
    
    if db_result:
        db_metadata = db_result["metadata"]
    else:
        # If no DB entry exists yet, we can't save preference easily without creating the structure
        # For now, return error if no art DB exists
        return jsonify({"error": "No album art database entry found. Please wait for art to download."}), 404
        
    # Update style
    db_metadata["background_style"] = style
    db_metadata["last_accessed"] = datetime.utcnow().isoformat() + "Z"
    
    # Save
    folder = get_album_db_folder(artist, album)
    if save_album_db_metadata(folder, db_metadata):
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
    """Serves the locally cached album art, preferring Spotify/high-res over Windows Media."""
    from config import CACHE_DIR
    import os
    from system_utils import get_cached_art_path
    from quart import Response
    
    # Prefer Spotify art if it exists (higher quality)
    # spotify_art = CACHE_DIR / "spotify_art.jpg"
    # if spotify_art.exists():
      #  try:
       #     # Read file into memory to avoid race conditions with concurrent writes
        #    with open(spotify_art, 'rb') as f:
         #       image_data = f.read()
          #  return Response(
           #     image_data,
            #    mimetype='image/jpeg',
             #   headers={'Cache-Control': 'no-cache, no-store, must-revalidate'}
        #    )
       # except (OSError, IOError) as e:
        #    logger.warning(f"Failed to read Spotify art: {e}")
            # Fall through to Windows Media art
                # FIX: Removed blind preference for spotify_art.jpg to prevent race conditions.

    
    # Fallback to Windows Media art (or whatever is currently active in cache)
    art_path = get_cached_art_path()
    if art_path and art_path.exists():
        try:
            # Read file into memory to avoid race conditions with concurrent writes
            with open(art_path, 'rb') as f:
                image_data = f.read()
            
            # Determine mimetype based on extension (preserves original format)
            ext = art_path.suffix.lower()
            mime = 'image/jpeg'  # Default
            if ext == '.png': mime = 'image/png'
            elif ext == '.bmp': mime = 'image/bmp'
            elif ext == '.gif': mime = 'image/gif'
            elif ext == '.webp': mime = 'image/webp'
            
            return Response(
                image_data,
                mimetype=mime,
                headers={'Cache-Control': 'no-cache, no-store, must-revalidate'}
            )
        except (OSError, IOError) as e:
            logger.warning(f"Failed to read album art: {e}")
    
    return "", 404

@app.route("/api/playback/play-pause", methods=['POST'])
async def toggle_playback():
    client = get_spotify_client()
    if not client: return jsonify({"error": "Spotify not connected"}), 503
    
    # We need to know if playing or paused to toggle
    track = await client.get_current_track()
    if not track: return jsonify({"error": "No active session"}), 404
    
    if track.get('is_playing'):
        await client.pause_playback()
        msg = "Paused"
    else:
        await client.resume_playback()
        msg = "Resumed"
    
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
    artist_id = request.args.get('artist_id')
    
    # We also need the artist NAME to find the folder
    # Try to get from current metadata if not passed
    metadata = await get_current_song_meta_data()
    artist_name = metadata.get('artist') if metadata else None
    
    if not artist_name:
         return jsonify({"error": "No artist name available"}), 400

    # Log visual mode activity/fetching
    logger.info(f"Fetching artist images for Visual Mode: {artist_name} ({artist_id})")

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