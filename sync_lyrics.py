import asyncio
import webbrowser
import threading as th
import logging
import click
from os import path
import time
from time import sleep
from typing import NoReturn
from queue import Queue, Empty
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
import signal
import win32api
import win32con
import os
import sys

logger = get_logger(__name__)

# Constants
ICON_URL = path.abspath("./resources/images/icon.ico")
PORT = 9012
queue = Queue()
_tray_icon = None
_tray_thread = None
_shutdown_event = asyncio.Event()
_server_task = None  # Global to track server task

def force_exit():
    """Force exit the application"""
    import os, signal
    os.kill(os.getpid(), signal.SIGTERM)

def restart():
    """Restart the application"""
    logger.info("Initiating restart sequence...")
    
    # Stop the tray icon directly
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception as e:
            logger.error(f"Error stopping tray icon: {e}")
    
    # Wait for tray thread to finish
    if _tray_thread and _tray_thread.is_alive():
        try:
            _tray_thread.join(timeout=1.0)
        except Exception as e:
            logger.error(f"Error joining tray thread: {e}")

    # Replace current process with new one
    python = sys.executable
    os.execl(python, python, *sys.argv)

async def cleanup() -> None:
    """Cleanup resources before exit"""
    global _tray_icon, _tray_thread, _server_task
    logger.info("Cleaning up resources...")

    # Cancel server task first
    if _server_task:
        _server_task.cancel()
        try:
            await _server_task
        except asyncio.CancelledError:
            logger.info("Server task cancelled")

    # Stop the tray icon
    if _tray_icon:
        try:
            _tray_icon.stop()
        except Exception as e:
            logger.error(f"Error stopping tray icon: {e}")

    # Wait for tray thread to finish
    if _tray_thread and _tray_thread.is_alive():
        try:
            await asyncio.get_running_loop().run_in_executor(None, lambda: _tray_thread.join(timeout=1.0))
        except Exception as e:
            logger.error(f"Error joining tray thread: {e}")

    queue.put("exit")
    await asyncio.sleep(0.5)

def run_tray() -> NoReturn:
    """
    Run the system tray icon with menu options
    Returns:
        NoReturn: This function never returns
    """
    global _tray_icon
    
    import socket
    # Get local IP address for web interface links
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    
    def on_exit():
        queue.put("exit")
        if _tray_icon:
            _tray_icon.stop()
    
    def on_restart():
        queue.put("restart")
        if _tray_icon:
            _tray_icon.stop()
    
    menu = Menu(
        MenuItem("Open Lyrics", lambda: webbrowser.open(f"http://{local_ip}:{PORT}"), default=True),
        MenuItem("Open Settings", lambda: webbrowser.open(f"http://{local_ip}:{PORT}/settings")),
        MenuItem("Restart", on_restart),
        MenuItem("Exit", on_exit)
    )
    
    _tray_icon = Icon("SyncLyrics", Image.open(ICON_URL), menu=menu)
    _tray_icon.run()

async def run_server() -> NoReturn:
    """
    Run the Quart server using Hypercorn with minimal logging
    Returns:
        NoReturn: This function never returns
    """
    config = Config()
    config.bind = [f"0.0.0.0:{PORT}"]
    config.use_reloader = False
    config.ignore_keyboard_interrupt = True
#    config.worker_class = "asyncio"
    config.graceful_timeout = 2  # Seconds allowed for graceful shutdown
    config.shutdown_timeout = 2  # Limit time allowed for shutdown
    config.debug = False
    
    # Mute unnecessary logging
    logging.getLogger('hypercorn.error').setLevel(logging.ERROR)
    logging.getLogger('hypercorn.access').setLevel(logging.ERROR)
    
    try:
        await serve(app, config)
    except asyncio.CancelledError:
        logger.info("Server task cancelled")

async def main() -> NoReturn:
    """
    Main application loop that coordinates the server, tray icon and lyrics sync
    Returns:
        NoReturn: This function never returns
    """
    global _tray_thread, _server_task
    
    # Initialize Spotify services
    spotify_client = SpotifyAPI()
    spotify_sync = SpotifyLyricsSync(spotify_client)
    
    # Initialize the sync system
    try:
        logger.debug("Initializing Spotify sync...")
        await spotify_sync.initialize()
    except Exception as e:
        logger.error(f"Failed to initialize Spotify sync: {e}")
        logger.info("Continuing with fallback methods...")
        # Continue anyway as other methods might work
    
    # Start the server and store task globally
    logger.info("Starting server...")
    _server_task = asyncio.create_task(run_server())
    
    # Start the tray icon in a separate thread since it's blocking
    logger.info("Starting system tray...")
    _tray_thread = th.Thread(target=run_tray, daemon=False)
    _tray_thread.start()

    # Get active display methods
    methods = [method for method, active in get_state()["representationMethods"].items() 
              if active and method != "notifications"]
    
    last_printed_lyric_per_method = {"terminal": None}

    try:
        logger.info("Entering main loop...")
        while True:
            if "terminal" in methods:
                lyric = await get_timed_lyrics()
                if lyric is not None and lyric != last_printed_lyric_per_method["terminal"]:
                    print(lyric)
                    last_printed_lyric_per_method["terminal"] = lyric
            
            # Check for exit/restart signals
            try:
                signal = queue.get_nowait()
                if signal == "exit":
                    logger.info("Exit signal received, breaking main loop...")
                    break
                elif signal == "restart":
                    logger.info("Restart signal received, initiating restart...")
                    await cleanup()
                    restart()
                    return
            except Empty:
                pass
            except Exception as e:
                logger.error(f"Error processing signal: {e}")
                
            # Shorter sleep interval for more responsive interrupts
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        logger.info("Main loop cancelled...")
    finally:
        await cleanup()

if __name__ == "__main__":
    # Set up logging
    setup_logging()
    
    def handle_interrupt(signum, frame):
        """Handle keyboard interrupt"""
        logger.info("Received keyboard interrupt...")
        if _tray_icon:
            _tray_icon.stop()
        queue.put("exit")
    
    def win32_handler(ctrl_type):
        """Windows-specific control handler"""
        if ctrl_type in (win32con.CTRL_C_EVENT, win32con.CTRL_BREAK_EVENT):
            logger.info("Received Windows interrupt signal...")
            if _tray_icon:
                _tray_icon.stop()
            queue.put("exit")
            return True  # Don't chain to the next handler
        return False
    
    # Set up signal handler
    import signal
    signal.signal(signal.SIGINT, handle_interrupt)
    
    # Set up Windows-specific handler
    win32api.SetConsoleCtrlHandler(win32_handler, True)
    
    try:
        logger.info("Starting SyncLyrics...")
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt caught in main...")
        queue.put("exit")
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        raise
    finally:
        # Final cleanup
        if _tray_icon:
            _tray_icon.stop()
        if _tray_thread and _tray_thread.is_alive():
            _tray_thread.join(timeout=1.0)
        logger.info("SyncLyrics shutdown complete")