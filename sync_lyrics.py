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
from state_manager import get_state
from server import app
from logging_config import setup_logging, get_logger
from providers.spotify_api import SpotifyAPI
from providers.spotify_sync import SpotifyLyricsSync
from system_utils import _get_current_song_meta_data_spotify
from hypercorn.config import Config
from hypercorn.asyncio import serve

logger = get_logger(__name__)

# Constants
ICON_URL = path.abspath("./resources/images/icon.ico")
PORT = 9012
queue = Queue()

def run_tray() -> NoReturn:
    """
    Run the system tray icon with menu options
    Returns:
        NoReturn: This function never returns
    """
    import socket
    # Get local IP address for web interface links
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    Icon("SyncLyrics", Image.open(ICON_URL), menu=Menu(
        MenuItem("Open Lyrics", lambda: webbrowser.open(f"http://{local_ip}:{PORT}"), default=True),
        MenuItem("Open Settings", lambda: webbrowser.open(f"http://{local_ip}:{PORT}/settings")),
        MenuItem("Quit", lambda: queue.put("exit"))
    )).run()

async def run_server() -> NoReturn:
    """
    Run the Quart server using Hypercorn with minimal logging
    Returns:
        NoReturn: This function never returns
    """
    config = Config()
    config.bind = [f"0.0.0.0:{PORT}"]
    config.use_reloader = False
    
    # Mute unnecessary logging
    logging.getLogger('hypercorn.error').setLevel(logging.ERROR)
    logging.getLogger('hypercorn.access').setLevel(logging.ERROR)
    
    await serve(app, config)

async def main() -> NoReturn:
    """
    Main application loop that coordinates the server, tray icon and lyrics sync
    Returns:
        NoReturn: This function never returns
    """
    # Initialize Spotify services
    spotify_client = SpotifyAPI()
    spotify_sync = SpotifyLyricsSync(spotify_client)
    
    # Initialize the sync system
    try:
        await spotify_sync.initialize()
    except Exception as e:
        logger.error(f"Failed to initialize Spotify sync: {e}")
        # Continue anyway as other methods might work

    # Start the server in the background
    server_task = asyncio.create_task(run_server())
    
    # Start the tray icon in a separate thread since it's blocking
    tray_thread = th.Thread(target=run_tray, daemon=True)
    tray_thread.start()

    # Get active display methods
    methods = [method for method, active in get_state()["representationMethods"].items() 
              if active and method != "notifications"]
    
    last_printed_lyric_per_method = {"terminal": None}

    try:
        while True:
            if "terminal" in methods:
                lyric = await get_timed_lyrics()
                if lyric is not None and lyric != last_printed_lyric_per_method["terminal"]:
                    print(lyric)
                    last_printed_lyric_per_method["terminal"] = lyric
            
            # Check for exit signal
            try:
                if queue.get_nowait() == "exit":
                    break
            except:
                pass
                
            await asyncio.sleep(0.1)
    finally:
        # Cleanup on exit
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

if __name__ == "__main__":
    # Set up logging
    setup_logging()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        queue.put("exit")