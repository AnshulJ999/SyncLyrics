from os import path
from typing import Any
import asyncio
import time

from quart import Quart, render_template, redirect, flash, request, jsonify, url_for, send_from_directory
from lyrics import get_timed_lyrics_previous_and_next, get_current_provider
from system_utils import get_current_song_meta_data
from state_manager import *
from config import LYRICS, RESOURCES_DIR
from settings import settings
from logging_config import get_logger

# Import shared Spotify singleton for controls - ensures all stats are consolidated
from providers.spotify_api import get_shared_spotify_client

logger = get_logger(__name__)

# Cache version based on app start time for cache busting
APP_START_TIME = int(time.time())

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
    
    if isinstance(lyrics_data, str):
        return {"msg": lyrics_data}
    
    colors = ["#24273a", "#363b54"]
    if metadata and metadata.get("colors"):
        colors = metadata.get("colors")
    
    return {
        "lyrics": list(lyrics_data),
        "colors": colors,
        "provider": get_current_provider()  # NEW: Add provider info
    }

@app.route("/current-track")
async def current_track() -> dict:
    """
    Returns detailed track info (Art, Progress, Duration).
    Used for the UI Header/Footer.
    """
    try:
        metadata = await get_current_song_meta_data()
        if metadata:
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

# --- Playback Control API (The New Features) ---

@app.route("/cover-art")
async def get_cover_art():
    """Serves the locally cached album art, preferring Spotify/high-res over Windows Media."""
    from config import CACHE_DIR
    import os
    from system_utils import get_cached_art_path
    
    # Prefer Spotify art if it exists (higher quality)
    spotify_art = CACHE_DIR / "spotify_art.jpg"
    if spotify_art.exists():
        from quart import send_file
        return await send_file(spotify_art, mimetype='image/jpeg')
    
    # Fallback to Windows Media art (only if Spotify art doesn't exist)
    art_path = get_cached_art_path()
    if art_path and art_path.exists():
        from quart import send_file
        # Determine mimetype based on extension
        ext = art_path.suffix.lower()
        mime = 'image/jpeg' # Default
        if ext == '.png': mime = 'image/png'
        elif ext == '.bmp': mime = 'image/bmp'
        elif ext == '.gif': mime = 'image/gif'
        
        return await send_file(art_path, mimetype=mime)
    
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
        "softAlbumArt": settings.get("ui.soft_album_art")
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