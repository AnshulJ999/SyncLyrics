from os import path, getpid, kill
from signal import SIGINT
from typing import Any

from flask import Flask, render_template, redirect, flash, request, Response

from lyrics import get_timed_lyrics_previous_and_next
from system_utils import get_current_song_meta_data
from state_manager import *
from config import LYRICS

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

@app.route('/config')
def get_client_config():
    """Return client-side configuration"""
    return {
        "updateInterval": LYRICS["display"]["update_interval"] * 1000  # Convert seconds to milliseconds
    }

@app.route("/exit-application")
def exit_application() -> dict[str, str]:
    """
    This function exits the application.

    Returns:
        dict[str, str]: A dictionary with a success message.
    """
    kill(getpid(), SIGINT)
    return {"msg": "Application has been closed."}