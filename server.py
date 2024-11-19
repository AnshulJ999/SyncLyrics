from os import path, getpid, kill, execv
from signal import SIGINT
from typing import Any
import asyncio
import sys

from quart import Quart, render_template, redirect, flash, request, Response, jsonify, url_for
from lyrics import get_timed_lyrics_previous_and_next
from system_utils import get_current_song_meta_data
from state_manager import *
from config import LYRICS
from settings import settings
from logging_config import get_logger

logger = get_logger(__name__)

TEMPLATE_DIRECTORY = path.abspath("resources/templates")
STATIC_DIRECTORY = path.abspath("resources")
app = Quart(__name__, template_folder=TEMPLATE_DIRECTORY, static_folder=STATIC_DIRECTORY)
app.config['SERVER_NAME'] = None
app.secret_key = "secret key"

VARIABLE_STATE_MAP = {
    "theme": "theme",
    "terminal-method": "representationMethods.terminal"
}


def guess_value_type(value: Any) -> Any:
    """
    This function guesses the type of the value.

    Args:
        value (Any): The value to guess the type of.

    Returns:
        Any: The value with the guessed type.
    """
    if value == "true": return True
    if value == "false": return False
    if value == "on": return True
    if value.isdigit(): return int(value)
    return value


@app.context_processor
async def theme() -> dict: 
    """
    This function is passed to every template context.
    For now, it only returns the current theme.

    Returns:
        dict: A dictionary containing the current theme.
    """
    return {"theme": get_attribute_js_notation(get_state(), 'theme')}


@app.route("/")
async def index() -> str:
    """
    This function returns the index page.

    Returns:
        str: The index page.
    """
    return await render_template("index.html")


@app.route("/api/settings", methods=['GET'])
async def api_get_settings():
    """Get all application settings"""
    return jsonify(settings.get_all())

@app.route("/api/settings/<key>", methods=['GET'])
async def api_get_setting(key: str):
    """Get a specific setting value"""
    try:
        return jsonify({"value": settings.get(key)})
    except KeyError:
        return jsonify({"error": f"Setting {key} not found"}), 404

@app.route("/api/settings/<key>", methods=['POST'])
async def api_update_setting(key: str):
    """Update a specific setting value"""
    try:
        data = await request.get_json()
        if 'value' not in data:
            return jsonify({"error": "No value provided"}), 400
            
        needs_restart = settings.set(key, data['value'])
        settings.save_to_config()
        
        return jsonify({
            "success": True,
            "requires_restart": needs_restart
        })
    except KeyError:
        return jsonify({"error": f"Setting {key} not found"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/settings", methods=['POST'])
async def api_update_settings():
    """Update multiple settings at once"""
    try:
        data = await request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid request format"}), 400
            
        needs_restart = False
        for key, value in data.items():
            try:
                needs_restart |= settings.set(key, value)
            except KeyError:
                return jsonify({"error": f"Setting {key} not found"}), 404
                
        settings.save_to_config()
        
        return jsonify({
            "success": True,
            "requires_restart": needs_restart
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.errorhandler(Exception)
async def handle_exception(e):
    """Log any errors that occur"""
    logger = get_logger(__name__)
    logger.error(f"Error handling request: {str(e)}", exc_info=True)
    return "Internal Server Error", 500

@app.route('/settings', methods=['GET', 'POST'])
async def settings_page():
    if request.method == 'POST':
        form_data = await request.form
        
        # Handle legacy settings
        theme = form_data.get('theme', 'dark')
        terminal_method = form_data.get('terminal-method', 'false').lower() == 'true'
        
        # Update state manager
        state = get_state()
        state = set_attribute_js_notation(state, 'theme', theme)
        state = set_attribute_js_notation(state, 'representationMethods.terminal', terminal_method)
        set_state(state)
        
        # Handle new settings system
        settings_updated = False
        restart_required = False
        
        for key, value in form_data.items():
            # Skip legacy settings
            if key in ['theme', 'terminal-method']:
                continue
                
            # Get setting definition
            setting = settings._definitions.get(key)
            if not setting:
                continue
                
            # Convert value to correct type
            try:
                if setting.type == bool:
                    value = value.lower() == 'true'
                elif setting.type == int:
                    value = int(value)
                elif setting.type == float:
                    value = float(value)
                    
                # Update setting if changed
                if settings.get(key) != value:
                    settings.set(key, value)
                    settings_updated = True
                    if setting.requires_restart:
                        restart_required = True
                        
            except (ValueError, TypeError) as e:
                flash(f'Error setting {key}: {str(e)}', 'danger')
                continue
        
        # Save settings if any were updated
        if settings_updated:
            try:
                settings.save_to_config()
                if restart_required:
                    flash('Settings saved. Some changes require application restart.', 'warning')
                else:
                    flash('Settings saved successfully.', 'success')
            except Exception as e:
                flash(f'Error saving settings: {str(e)}', 'danger')
        
        return redirect(url_for('settings_page'))
    
    # GET request - render settings page
    settings_by_category = {}
    for key, setting in settings._definitions.items():
        category = setting.category
        if category not in settings_by_category:
            settings_by_category[category] = {}
            
        settings_by_category[category][key] = {
            'name': setting.name,
            'type': setting.type.__name__,
            'value': settings.get(key),
            'requires_restart': setting.requires_restart,
            'description': setting.description
        }
    
    state = get_state()
    return await render_template('settings.html',
                               settings=settings_by_category,
                               theme=get_attribute_js_notation(state, 'theme'),
                               terminal_method=get_attribute_js_notation(state, 'representationMethods.terminal'))

@app.route('/reset-defaults')
async def reset_defaults():
    try:
        settings.reset_to_defaults()
        flash('Settings reset to defaults successfully.', 'success')
    except Exception as e:
        flash(f'Error resetting settings: {str(e)}', 'danger')
    return redirect(url_for('settings_page'))

@app.route("/lyrics")
async def lyrics() -> dict:
    """
    This function returns the lyrics and colors data.

    Returns:
        dict: The lyrics and color data, or an error message if lyrics not found.
    """
    lyrics_data = await get_timed_lyrics_previous_and_next()
    metadata = await get_current_song_meta_data()
    
    if isinstance(lyrics_data, str):  # lyrics not found
        return {"msg": lyrics_data}
    
    return {
        "lyrics": list(lyrics_data),
        "colors": metadata.get("colors", ["#24273a", "#363b54"]) if metadata else ["#24273a", "#363b54"]
    }


@app.route("/exit-application")
async def exit_application() -> dict[str, str]:
    """
    This function exits the application.

    Returns:
        dict[str, str]: A dictionary with a success message.
    """
    from sync_lyrics import queue, force_exit  # Import at function level to avoid circular import
    queue.put("exit")
    # Schedule force exit after 2 seconds
    import threading
    threading.Timer(2.0, force_exit).start()
    return {"msg": "Application has been closed."}

@app.route("/restart", methods=['POST'])
async def restart_server():
    """Restart the server."""
    from sync_lyrics import queue
    queue.put("restart")
    return {'status': 'ok', 'msg': 'Application is restarting...'}, 200

async def restart_application():
    """Restart the application after a short delay."""
    await asyncio.sleep(1)  # Give time for the response to be sent
    execv(sys.executable, [sys.executable] + sys.argv)

@app.route("/current-track")
async def current_track() -> dict:
    """
    This function returns the current track information.
    
    Returns:
        dict: The current track information or error message.
    """
    try:
        metadata = await get_current_song_meta_data()
        if metadata:
            return metadata
        return {"error": "No track playing"}
    except Exception as e:
        return {"error": str(e)}

@app.route('/config')
async def get_client_config():
    """Return client-side configuration"""
    return {
        "updateInterval": LYRICS["display"]["update_interval"] * 1000  # Convert seconds to milliseconds
    }