"""
SyncLyrics Main Application - Simplified Version
Handles main application lifecycle and coordinates components
"""

import asyncio
import webbrowser
import threading as th
import logging
from os import path
from queue import Queue
from pystray import Icon, Menu, MenuItem
from PIL import Image

from lyrics import get_timed_lyrics, get_timed_lyrics_previous_and_next
from graphics import render_text_with_background, restore_wallpaper
from state_manager import get_state
from server import app
from config import SERVER, RESOURCES_DIR, DEBUG

# Configure logging
if DEBUG['enabled']:
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
else:
    logging.basicConfig(level=logging.WARNING)

logger = logging.getLogger(__name__)

# Constants
ICON_PATH = path.join(RESOURCES_DIR, "images", "icon.ico")
PORT = SERVER['port']
exit_queue = Queue()

def get_local_ip():
    """Get local IP for web interface"""
    import socket
    try:
        hostname = socket.gethostname()
        return socket.gethostbyname(hostname)
    except:
        return "localhost"

def run_tray():
    """Run system tray icon"""
    try:
        local_ip = get_local_ip()
        icon = Icon(
            "SyncLyrics",
            Image.open(ICON_PATH),
            menu=Menu(
                MenuItem(
                    "Open Lyrics", 
                    lambda: webbrowser.open(f"http://{local_ip}:{PORT}"),
                    default=True
                ),
                MenuItem(
                    "Open Settings", 
                    lambda: webbrowser.open(f"http://{local_ip}:{PORT}/settings")
                ),
                MenuItem("Exit", lambda: exit_queue.put("exit"))
            )
        )
        icon.run()
    except Exception as e:
        logger.error(f"Tray error: {e}")
        exit_queue.put("exit")

def run_server():
    """Run web server"""
    try:
        if not DEBUG['enabled']:
            log = logging.getLogger('werkzeug')
            log.setLevel(logging.ERROR)

        app.run(
            host='0.0.0.0',
            port=PORT,
            debug=False,
            use_reloader=False
        )
    except Exception as e:
        logger.error(f"Server error: {e}")
        exit_queue.put("exit")

async def main():
    """Main application loop"""
    try:
        # Get active display methods
        methods = [
            method for method, active 
            in get_state()["representationMethods"].items()
            if active
        ]
        
        last_lyrics = {
            "wallpaper": None,
            "terminal": None
        }
        
        delta_sum = 0
        delta_count = 0

        while True:
            try:
                # Check exit signal
                if not exit_queue.empty() and exit_queue.get() == "exit":
                    break

                # Handle display methods
                if "wallpaper" in methods:
                    # Add simple performance tracking for wallpaper timing
                    avg_latency = (delta_sum / delta_count) if delta_count > 0 else 0.1
                    lyric = await get_timed_lyrics(avg_latency)
                    
                    if lyric and lyric != last_lyrics["wallpaper"]:
                        from time import time
                        t0 = time()
                        render_text_with_background(lyric)
                        delta = time() - t0
                        delta_sum += delta
                        delta_count += 1
                        last_lyrics["wallpaper"] = lyric

                if "terminal" in methods:
                    lyric = await get_timed_lyrics()
                    if lyric and lyric != last_lyrics["terminal"]:
                        print(lyric)
                        last_lyrics["terminal"] = lyric

                # Sleep briefly
                await asyncio.sleep(0.1)

            except Exception as e:
                logger.error(f"Loop iteration error: {e}")
                await asyncio.sleep(1)  # Sleep longer on error

    except Exception as e:
        logger.error(f"Main loop error: {e}")
    finally:
        await cleanup()

async def cleanup():
    """Cleanup resources"""
    try:
        restore_wallpaper()
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

def main_sync():
    """Synchronous entry point"""
    print("=== Starting SyncLyrics ===")
    
    # Start threads
    th.Thread(target=run_tray, daemon=True, name="TrayThread").start()
    th.Thread(target=run_server, daemon=True, name="ServerThread").start()
    
    try:
        # Run main loop
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        exit_queue.put("exit")
    except Exception as e:
        logger.error(f"Application error: {e}")

if __name__ == "__main__":
    main_sync()