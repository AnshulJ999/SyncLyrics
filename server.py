from os import path, getpid, kill
from signal import SIGINT
from typing import Any

from flask import Flask, render_template, redirect, flash, request, Response, url_for

import config
from lyrics import get_timed_lyrics_previous_and_next
from system_utils import get_current_song_meta_data
from state_manager import *
from providers.spotify_api import SpotifyAPI


TEMPLATE_DIRECTORY = path.abspath("resources/templates")
STATIC_DIRECTORY = path.abspath("resources")
app = Flask(__name__, template_folder=TEMPLATE_DIRECTORY, static_folder=STATIC_DIRECTORY)
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
def theme() -> dict: 
    """
    This function is passed to every template context.
    For now, it only returns the current theme.

    Returns:
        dict: A dictionary containing the current theme.
    """
    return {"theme": get_state()["theme"]}


@app.route("/")
def index() -> str:
    """
    This function returns the index page.

    Returns:
        str: The index page.
    """
    return render_template("index.html")


@app.route("/settings", methods=['GET', 'POST'])
def settings() -> str:
    """
    This function returns the settings page.
    It is also responsible for saving the settings using
    the state manager when a POST request is sent.

    Returns:
        str: The settings page.
    """
    state = get_state()
    if request.method == "POST":
        for key, state_key in VARIABLE_STATE_MAP.items():
            value = request.form.get(key, False, type=guess_value_type) 
            state = set_attribute_js_notation(state, state_key, value)

        set_state(state)
        flash("Settings have been saved! Restart your application.", "success")

    # Create context dictionary with just the state variables
    context = {key: get_attribute_js_notation(state, state_key) 
        for key, state_key in VARIABLE_STATE_MAP.items()}
    
    return render_template("settings.html", **context)


@app.route("/lyrics")
async def lyrics() -> dict:
    """
    This function returns the lyrics and album art colors data.

    Returns:
        dict: The lyrics and color data, or an error message if lyrics not found.
    """
    lyrics_data = await get_timed_lyrics_previous_and_next()
 #   metadata = await get_current_song_meta_data()    
    # Get current track metadata including album art
    spotify = SpotifyAPI()
    current_track = spotify.get_current_track()
    
    if isinstance(lyrics_data, str):  # lyrics not found
        return {"msg": lyrics_data}
    
    return {
        "lyrics": list(lyrics_data),
        "albumArt": current_track.get('album_art') if current_track else None
    }


@app.route("/reset-defaults")
def reset_defaults() -> Response:
    """
    This function resets the settings to their default values and redirects to the settings page.

    Returns:
        Response: A redirect response to the settings page.
    """
    reset_state()
    flash("Settings have been reset!", "success")
    return redirect("/settings")


@app.route("/exit-application")
def exit_application() -> dict[str, str]:
    """
    This function exits the application.

    Returns:
        dict[str, str]: A dictionary with a success message.
    """
    kill(getpid(), SIGINT)
    return {"msg": "Application has been closed."}


@app.route("/settings", methods=["POST"])
def update_settings():
    album_colors_enabled = request.form.get("album-colors-enabled") == "on"
    state = get_state()
    state["albumColorsEnabled"] = album_colors_enabled
    set_state(state)
    # Also update config
    config.ALBUM_COLORS["enabled"] = album_colors_enabled
    return redirect(url_for("settings"))


@app.route("/api/settings")
def get_settings():
    """Return frontend-needed settings as JSON"""
    return {
        "albumColors": {
            "enabled": config.ALBUM_COLORS["enabled"],
            "fallbackColors": config.ALBUM_COLORS["fallback_colors"],
            "currentSwatch": config.ALBUM_COLORS["current_swatch"],
            "availableSwatches": config.ALBUM_COLORS["available_swatches"]
        }
    }