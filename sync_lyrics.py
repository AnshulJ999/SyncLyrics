import asyncio
import webbrowser
import threading as th
import logging
import click
from os import path
import time
from time import sleep
from typing import NoReturn
from queue import Queue
from pystray import Icon, Menu, MenuItem
from PIL import Image
from config import DEBUG
from lyrics import get_timed_lyrics
# from graphics import render_text_with_background, restore_wallpaper
from state_manager import get_state
from server import app
from logging_config import setup_logging, get_logger
from providers.spotify_api import SpotifyAPI
from providers.spotify_sync import SpotifyLyricsSync
from system_utils import _get_current_song_meta_data_spotify # Import the function

logger = get_logger(__name__)

def run_tray() -> NoReturn:
    """
    Run the tray icon
    Returns:
        NoReturn: This function never returns.
    """
    import socket
    # Get local IP address
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    Icon("SyncLyrics", Image.open(ICON_URL), menu=Menu(
        MenuItem("Open Lyrics", lambda: webbrowser.open(f"http://{local_ip}:{PORT}"), default=True),
        MenuItem("Open Settings", lambda: webbrowser.open(f"http://{local_ip}:{PORT}/settings")),
        MenuItem("Quit", lambda: queue.put("exit"))
    )).run()

def run_server() -> NoReturn:
    """
    Run the flask server
    Returns:
        NoReturn: This function never returns.
    """
    # Mute output from flask
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    def secho(*args, **kwargs): pass
    def echo(*args, **kwargs): pass
    click.echo = echo
    click.secho = secho

    # Run the server
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)


async def main() -> NoReturn:
    """
    The main function of the program. It runs the server and the tray icon, and
    it also handles the lyrics rendering.
    Returns:
        NoReturn: This function never returns.
    """
    # Set up logging first thing
    # setup_logging(
    #     level=DEBUG.get("log_level"),
    #     console=DEBUG.get("log_to_console"),
    #     detailed=DEBUG.get("log_detailed")
    # )

    # Initialize Spotify client and sync
    spotify_client = SpotifyAPI()
    spotify_sync = SpotifyLyricsSync(spotify_client)
    
    # Initialize the sync system
    try:
        await spotify_sync.initialize()
    except Exception as e:
        logger.error(f"Failed to initialize Spotify sync: {e}")
        # Continue anyway as other methods might work

    # We count the average latency for wallpaper method because it's heavy
    # And we want to take the render time into account 
    delta_sum = 0
    delta_count = 0
    methods = [method for method, active in get_state()["representationMethods"].items() 
              if active and method != "notifications"]
    
    last_printed_lyric_per_method = {"terminal": None}

    while True:

        if "terminal" in methods:
            lyric = await get_timed_lyrics()
            if lyric is not None and lyric != last_printed_lyric_per_method["terminal"]:
                print(lyric)
                last_printed_lyric_per_method["terminal"] = lyric
        
        # Exit gracefully
        if queue.qsize() > 0 and queue.get() == "exit": break
        await asyncio.sleep(0.1) # Let the CPU rest a bit

ICON_URL = path.abspath("./resources/images/icon.ico")
PORT = 9012
queue = Queue()

t_tray = th.Thread(target=run_tray, daemon=True).start()
t_server = th.Thread(target=run_server, daemon=True).start()

try:
    asyncio.run(main())
except KeyboardInterrupt:
    queue.put("exit")