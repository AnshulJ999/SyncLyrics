"""
Server implementation for SyncLyrics
Handles web interface and API endpoints
"""

import logging
import asyncio
from os import path
from flask import Flask, render_template, redirect, flash, request, jsonify

from lyrics import get_timed_lyrics_previous_and_next
from system_utils import get_available_fonts, get_current_song_meta_data
from state_manager import get_state, set_state, set_attribute, reset_state
from config import SERVER, RESOURCES_DIR, UI, DEBUG

# Configure logging
logger = logging.getLogger(__name__)

# Initialize Flask
app = Flask(__name__, 
           template_folder=path.join(RESOURCES_DIR, "templates"),
           static_folder=RESOURCES_DIR)

# Server configuration
app.secret_key = SERVER['secret_key']

# State variable mapping
VARIABLE_STATE_MAP = {
    "theme": "theme",
    "notification-method": "representationMethods.notifications",
    "wallpaper-method": "representationMethods.wallpaper",
    "terminal-method": "representationMethods.terminal",
    "font-size": "wallpaperSettings.fontSize",
    "font-color": "wallpaperSettings.fontColor",
    "wallpaper-font-color": "wallpaperSettings.pickColorFromWallpaper",
    "font-family": "wallpaperSettings.fontFamily",
    "font-stroke": "wallpaperSettings.fontStroke",
    "x-offset": "wallpaperSettings.xOffset",
    "y-offset": "wallpaperSettings.yOffset",
    "width": "wallpaperSettings.width",
    "height": "wallpaperSettings.height",
    "quality": "wallpaperSettings.quality",
    "scaling": "wallpaperSettings.scaling"
}

def guess_value_type(value):
    """Convert string values to appropriate types"""
    if not isinstance(value, str):
        return value
    value = value.lower()
    if value in ('true', 'on'): 
        return True
    if value == 'false': 
        return False
    if value.isdigit(): 
        return int(value)
    try:
        return float(value)
    except ValueError:
        return value

@app.context_processor
def inject_template_vars():
    """Inject variables into all templates"""
    state = get_state()
    return {
        "theme": state["theme"],
        "ui_settings": state.get("uiSettings", {}),
    }

@app.route("/")
def index():
    """Main page"""
    return render_template("index.html")

@app.route("/settings", methods=['GET', 'POST'])
def settings():
    """Settings page"""
    state = get_state()
    
    if request.method == "POST":
        try:
            for key, state_key in VARIABLE_STATE_MAP.items():
                value = request.form.get(key)
                if value is not None:
                    value = guess_value_type(value)
                    state = set_attribute(state, state_key, value)

            set_state(state)
            flash("Settings saved! Restart application to apply changes.", "success")
            
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            flash("Error saving settings. Please try again.", "error")
            return redirect("/settings")

    try:
        context = {
            key: state.get(state_key, '')
            for key, state_key in VARIABLE_STATE_MAP.items()
        }
        context["available_fonts"] = get_available_fonts()
        
        return render_template("settings.html", **context)
        
    except Exception as e:
        logger.error(f"Error loading settings: {e}")
        flash("Error loading settings.", "error")
        return redirect("/")

@app.route("/lyrics")
async def lyrics():
    """Get current lyrics"""
    try:
        # Get lyrics and metadata
        lyrics_data = await get_timed_lyrics_previous_and_next()
        metadata = await get_current_song_meta_data()
        
        # Handle no lyrics case
        if isinstance(lyrics_data, str):
            return jsonify({"msg": lyrics_data})
        
        # Get colors from metadata or use defaults
        colors = (metadata.get("colors", ["#24273a", "#363b54"]) 
                 if metadata else ["#24273a", "#363b54"])
        
        return jsonify({
            "lyrics": list(lyrics_data),
            "colors": colors,
            "minimal": request.args.get('minimal', 'false').lower() == 'true',
        })
            
    except Exception as e:
        logger.error(f"Error getting lyrics: {e}")
        return jsonify({"error": "Error getting lyrics"})

@app.route("/reset-defaults")
def reset_defaults():
    """Reset settings to defaults"""
    try:
        reset_state()
        flash("Settings have been reset!", "success")
    except Exception as e:
        logger.error(f"Error resetting settings: {e}")
        flash("Error resetting settings.", "error")
    return redirect("/settings")

@app.errorhandler(404)
def not_found_error(error):
    """Handle 404 errors"""
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    """Handle 500 errors"""
    logger.error(f"Server error: {error}")
    return render_template('500.html'), 500

if DEBUG['enabled']:
    @app.route("/debug/state")
    def debug_state():
        """Debug endpoint to view current state"""
        return jsonify(get_state())