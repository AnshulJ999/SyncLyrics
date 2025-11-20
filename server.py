from os import path
from typing import Any
import asyncio

from quart import Quart, render_template, redirect, flash, request, jsonify, url_for
from lyrics import get_timed_lyrics_previous_and_next
from system_utils import get_current_song_meta_data
from state_manager import *
from config import LYRICS, RESOURCES_DIR
from settings import settings
from logging_config import get_logger

# Import shared Spotify instance if needed for controls
from system_utils import spotify_client 
from providers.spotify_api import SpotifyAPI

logger = get_logger(__name__)

TEMPLATE_DIRECTORY = str(RESOURCES_DIR / "templates")
STATIC_DIRECTORY = str(RESOURCES_DIR)
app = Quart(__name__, template_folder=TEMPLATE_DIRECTORY, static_folder=STATIC_DIRECTORY)
app.config['SERVER_NAME'] = None
app.secret_key = "secret key"

# --- Helper Functions ---

def get_spotify_client():
    """Helper to get the active Spotify client from system_utils or create one"""
    # We try to reuse the one from system_utils to share the session/cache
    from system_utils import spotify_client
    if spotify_client and spotify_client.initialized:
        return spotify_client
    
    # Fallback: Create new if not exists (e.g. first run)
    new_client = SpotifyAPI()
    return new_client if new_client.initialized else None

@app.context_processor
async def theme() -> dict: 
    return {"theme": get_attribute_js_notation(get_state(), 'theme')}

# --- Routes ---

@app.route("/")
async def index() -> str:
    return await render_template("index.html")

@app.route("/lyrics")
async def lyrics() -> dict:
    """Returns lyrics and basic color data for the main loop."""
    lyrics_data = await get_timed_lyrics_previous_and_next()
    metadata = await get_current_song_meta_data()
    
    if isinstance(lyrics_data, str):
        return {"msg": lyrics_data}
    
    colors = ["#24273a", "#363b54"]
    if metadata and metadata.get("colors"):
        colors = metadata.get("colors")
    
    return {
        "lyrics": list(lyrics_data),
        "colors": colors
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

# --- Playback Control API (The New Features) ---

@app.route("/cover-art")
async def get_cover_art():
    """Serves the locally cached album art from Windows Media."""
    from config import CACHE_DIR
    import os
    from system_utils import get_cached_art_path
    
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
        "overlayOpacity": settings.get("ui.overlay_opacity")
    }