import sys
import os

# Safety fix for running with pythonw.exe (no console)
# When using pythonw, stdout/stderr are None, causing crashes if anything tries to print
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import asyncio
import webbrowser
import threading as th
import logging
import click
from os import path
import time
from time import sleep
from typing import NoReturn
try:
    from pystray import Icon, Menu, MenuItem
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False
from PIL import Image
from config import DEBUG, RESOURCES_DIR
from lyrics import get_timed_lyrics
from state_manager import get_state, reset_state
from server import app
from logging_config import setup_logging, get_logger
# NOTE: SpotifyAPI is accessed via get_shared_spotify_client() singleton throughout the app
from hypercorn.config import Config
from hypercorn.asyncio import serve
import signal
from context import queue
from queue import Empty

# Platform specific imports
try:
    import win32api
    import win32con
except ImportError:
    win32api = None
    win32con = None

logger = get_logger(__name__)

# Constants
from network_utils import MDNSService
from config import SERVER

# Constants
ICON_URL = str(RESOURCES_DIR / "images" / "icon.ico")
PORT = int(SERVER["port"])
_tray_icon = None
_tray_thread = None
_shutdown_event = asyncio.Event()
_server_task = None  # Global to track server task
_mdns_service = None # Global mDNS service

# Watchdog for emergency exit - prevents zombie processes when PortAudio hangs
import threading
_cleanup_watchdog_event = threading.Event()  # Thread-safe flag
_cleanup_complete_event = threading.Event()  # Signal successful cleanup
WATCHDOG_TIMEOUT = 6  # Seconds before force exit

def _watchdog_thread():
    """
    Kill process if cleanup takes too long.
    
    This runs in a separate thread so it can execute even if the main thread
    is stuck in a C-level blocking call (like PortAudio).
    """
    while True:
        # Wait for cleanup to start
        if _cleanup_watchdog_event.wait(timeout=1):
            # Cleanup started, wait for it to complete or timeout
            if not _cleanup_complete_event.wait(timeout=WATCHDOG_TIMEOUT):
                # Timeout expired, cleanup didn't finish
                print(f"WATCHDOG: Cleanup stuck for {WATCHDOG_TIMEOUT}s - force killing process")
                os._exit(1)
            else:
                # Cleanup completed successfully
                return

# Start watchdog thread (daemon so it dies with main process)
threading.Thread(target=_watchdog_thread, daemon=True, name="CleanupWatchdog").start()

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
    global _tray_icon, _tray_thread, _server_task, _mdns_service
    
    # Signal watchdog that cleanup has started (starts timeout countdown)
    _cleanup_watchdog_event.set()
    
    logger.info("Cleaning up resources...")

    # Unregister mDNS
    if _mdns_service:
        try:
            await asyncio.to_thread(_mdns_service.unregister)
        except Exception as e:
            logger.error(f"Error unregistering mDNS: {e}")

    # Stop audio recognition engine FIRST (most likely to hang)
    # FIX: Only attempt to stop if the module was ever imported (i.e., audio rec was used)
    # This prevents PortAudio initialization on shutdown when audio rec was never enabled
    if 'system_utils.reaper' in sys.modules:
        try:
            from system_utils.reaper import get_reaper_source
            import system_utils.reaper as reaper_module
            
            # Set shutdown flag to prevent auto-restart race condition during cleanup
            reaper_module._shutting_down = True
            
            source = get_reaper_source()
            if source and source.is_active:
                logger.info("Stopping audio recognition...")
                # Abort capture first to unblock any pending reads
                if source._engine and source._engine.capture:
                    source._engine.capture.abort()
                try:
                    await asyncio.wait_for(source.stop(), timeout=3.0)
                    logger.info("Audio recognition stopped")
                except asyncio.TimeoutError:
                    logger.warning("Audio recognition stop timeout - forcing cleanup")
        except Exception as e:
            logger.error(f"Failed to stop audio recognition: {e}")
    
    # Fix C2: REMOVED sd.stop() call
    # Calling sd.stop() while an InputStream is blocked in a C-level call (in the daemon thread)
    # can cause PortAudio deadlock on Windows, hanging the entire cleanup process.
    # The daemon threads will auto-terminate when Python exits, and capture.abort() above
    # handles proper stream cleanup without risking deadlock.

    # Cancel server task
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

    # Fix H3: Cancel only tracked background tasks, not all asyncio tasks
    # Cancelling all_tasks() kills library internals (aiohttp sessions, etc.) and causes issues
    from system_utils import state as app_state
    for task in list(app_state._background_tasks):
        if task is not asyncio.current_task() and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

    # Shutdown daemon executor (Fix 5) - ensures all daemon threads are stopped
    try:
        from system_utils.helpers import shutdown_daemon_executor
        shutdown_daemon_executor()
        logger.debug("Daemon executor shutdown")
    except Exception:
        pass

    queue.put("exit")
    await asyncio.sleep(0.5)
    
    # Signal watchdog that cleanup completed successfully
    _cleanup_complete_event.set()

def run_tray() -> NoReturn:
    """
    Run the system tray icon with menu options
    Returns:
        NoReturn: This function never returns
    """
    global _tray_icon
    
    if not HAS_TRAY:
        logger.warning("System tray not available (headless mode or missing dependencies)")
        return

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
    Run the Quart server using Hypercorn with optional HTTPS support.
    
    Modes:
    - HTTP only: Default mode, no SSL
    - HTTPS only: When https.enabled=True and https.port=0 (same port)
    - Dual-stack: When https.enabled=True and https.port>0 (different ports)
      - Runs HTTP on PORT for local access (no cert warnings)
      - Runs HTTPS on https.port for tablet/mobile (mic access)
    
    Returns:
        NoReturn: This function never returns
    """
    from pathlib import Path
    from config import SERVER
    
    host = SERVER.get("host", "0.0.0.0")
    http_port = SERVER.get("port", 9012)
    
    https_config = SERVER.get("https", {})
    https_enabled = https_config.get("enabled", False)
    https_port = https_config.get("port", 0)  # 0 = same port, >0 = dual-stack
    auto_generate = https_config.get("auto_generate", True)
    
    # Common config for both servers
    base_config = Config()
    base_config.use_reloader = False
    base_config.ignore_keyboard_interrupt = True
    base_config.graceful_timeout = 2
    base_config.shutdown_timeout = 2
    base_config.debug = False
    
    # Mute unnecessary logging
    logging.getLogger('hypercorn.error').setLevel(logging.ERROR)
    logging.getLogger('hypercorn.access').setLevel(logging.ERROR)
    
    tasks = []
    
    if https_enabled:
        # Get certificate paths
        cert_file = Path(https_config.get("cert_file", "certs/server.crt"))
        key_file = Path(https_config.get("key_file", "certs/server.key"))
        
        # Make paths absolute if relative
        from config import ROOT_DIR
        if not cert_file.is_absolute():
            cert_file = ROOT_DIR / cert_file
        if not key_file.is_absolute():
            key_file = ROOT_DIR / key_file
        
        # Auto-generate certificates if enabled and missing
        if auto_generate and (not cert_file.exists() or not key_file.exists()):
            try:
                from ssl_utils import ensure_ssl_certs
                result = ensure_ssl_certs(cert_file.parent)
                if result[0]:
                    logger.info(f"SSL certificates generated at {cert_file.parent}")
                else:
                    logger.warning("Failed to generate SSL certificates")
            except ImportError as e:
                logger.error(f"SSL utils not available: {e}")
            except Exception as e:
                logger.error(f"Failed to generate SSL certificates: {e}")
        
        if cert_file.exists() and key_file.exists():
            if https_port and https_port != http_port:
                # DUAL-STACK MODE: Run HTTP on http_port AND HTTPS on https_port
                
                # Task A: HTTP server (no SSL) - for local PC access
                http_config = Config()
                http_config.bind = [f"{host}:{http_port}"]
                http_config.use_reloader = False
                http_config.ignore_keyboard_interrupt = True
                http_config.graceful_timeout = 2
                http_config.shutdown_timeout = 2
                http_config.debug = False
                tasks.append(serve(app, http_config))
                logger.info(f"HTTP server starting on {host}:{http_port}")
                
                # Task B: HTTPS server (with SSL) - for tablet/mobile mic access
                https_server_config = Config()
                https_server_config.bind = [f"{host}:{https_port}"]
                https_server_config.certfile = str(cert_file)
                https_server_config.keyfile = str(key_file)
                https_server_config.use_reloader = False
                https_server_config.ignore_keyboard_interrupt = True
                https_server_config.graceful_timeout = 2
                https_server_config.shutdown_timeout = 2
                https_server_config.debug = False
                tasks.append(serve(app, https_server_config))
                logger.info(f"HTTPS server starting on {host}:{https_port}")
            else:
                # HTTPS-ONLY MODE: Same port, HTTPS replaces HTTP
                base_config.bind = [f"{host}:{http_port}"]
                base_config.certfile = str(cert_file)
                base_config.keyfile = str(key_file)
                tasks.append(serve(app, base_config))
                logger.info(f"HTTPS-only server starting on {host}:{http_port}")
        else:
            # Certificates not found, fall back to HTTP only
            logger.warning(
                f"HTTPS enabled but certificates not found at {cert_file} and {key_file}. "
                f"Falling back to HTTP only. Install 'cryptography' package: pip install cryptography"
            )
            base_config.bind = [f"{host}:{http_port}"]
            tasks.append(serve(app, base_config))
            logger.info(f"HTTP server starting on {host}:{http_port}")
    else:
        # HTTP-only mode (default)
        base_config.bind = [f"{host}:{http_port}"]
        tasks.append(serve(app, base_config))
        logger.info(f"HTTP server starting on {host}:{http_port}")
    
    try:
        # Run all server tasks concurrently
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Server task cancelled")
    except OSError as e:
        # Port binding errors (e.g., HTTPS port 9013 already in use)
        if "address already in use" in str(e).lower() or e.errno == 10048:  # 10048 = Windows EADDRINUSE
            logger.error(f"Port binding failed: {e}. Check if another instance is running.")
        else:
            logger.error(f"Server error: {e}")
        raise  # Re-raise to trigger cleanup

async def main() -> NoReturn:
    """
    Main application loop that coordinates the server, tray icon and lyrics sync
    Returns:
        NoReturn: This function never returns
    """
    global _tray_thread, _server_task, _mdns_service
    
    # Start the server and store task globally
    logger.info(f"Starting server on port {PORT}...")
    _server_task = asyncio.create_task(run_server())
    
    # Register mDNS service
    try:
        _mdns_service = MDNSService(PORT)
        await asyncio.to_thread(_mdns_service.register)
    except Exception as e:
        logger.error(f"Failed to initialize mDNS: {e}")
    
    # Start the tray icon in a separate thread since it's blocking
    if HAS_TRAY:
        logger.info("Starting system tray...")
        _tray_thread = th.Thread(target=run_tray, daemon=False)
        _tray_thread.start()
    else:
        logger.info("System tray disabled (headless mode or missing dependency).")
    
    # Start audio recognition if --reaper flag was used
    # Check runtime flag (set by --reaper or config) to avoid importing audio_recognition unnecessarily
    from system_utils.metadata import _audio_rec_runtime_enabled
    if _audio_rec_runtime_enabled:
        try:
            from system_utils.reaper import get_reaper_source
            source = get_reaper_source()
            await source.start(manual=True)
            logger.info("Audio recognition started (--reaper mode)")
        except Exception as e:
            logger.error(f"Failed to start audio recognition: {e}")
            # Disable audio rec for this session to prevent further attempts
            from system_utils.metadata import set_audio_rec_runtime_enabled
            set_audio_rec_runtime_enabled(False, False)
            logger.info("Audio recognition disabled for this session")

    # Get active display methods
    # CRITICAL FIX: Use .get() with default to prevent crash if state file is missing representationMethods key
    # This handles corrupted state files or state files from old versions gracefully
    # Also handles edge case where state file contains valid JSON but not a dict (e.g., null, [], string)
    try:
        logger.debug(f"Attempting to get state from: {os.getenv('SYNCLYRICS_STATE_FILE', 'state.json')}")
        state = get_state()
        logger.debug(f"State retrieved successfully, type: {type(state)}")
    except Exception as e:
        logger.error(f"Failed to get state: {e}", exc_info=True)
        # Use default state if get_state() fails completely
        from state_manager import DEFAULT_STATE
        state = DEFAULT_STATE.copy()
        logger.warning("Using default state due to get_state() failure")
    
    if not isinstance(state, dict):
        # State file contains invalid data (not a dict), use default
        logger.warning("State file contains invalid data (not a dict), resetting to defaults")
        try:
            reset_state()
            state = get_state()
        except Exception as e:
            logger.error(f"Failed to reset state: {e}", exc_info=True)
            # Use default state if reset also fails
            from state_manager import DEFAULT_STATE
            state = DEFAULT_STATE.copy()
            logger.warning("Using default state due to reset_state() failure")
    
    representation_methods = state.get("representationMethods", {"terminal": False})
    # CRITICAL FIX: Validate that representation_methods is actually a dict
    # If state.json was manually edited to have null/[]/string, .items() would crash
    if not isinstance(representation_methods, dict):
        logger.warning(f"representationMethods is not a dict (got {type(representation_methods)}), using default")
        representation_methods = {"terminal": False}
    
    methods = [method for method, active in representation_methods.items() 
              if active and method != "notifications"]
    logger.debug(f"Active display methods: {methods}")
    
    last_printed_lyric_per_method = {"terminal": None}

    try:
        logger.info("Entering main loop...")
        state_file_path = os.getenv('SYNCLYRICS_STATE_FILE', 'state.json')
        logger.debug(f"State file path: {state_file_path}")
        logger.debug(f"State file exists: {path.exists(state_file_path)}")
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
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='SyncLyrics - Real-time lyrics display')
    parser.add_argument('--reaper', action='store_true', 
                        help='Enable Reaper DAW audio recognition mode')
    args = parser.parse_args()
    
    # Handle --reaper flag: Enable audio recognition and start immediately
    # The engine starts NOW, not when Reaper is detected
    if args.reaper:
        from config import AUDIO_RECOGNITION
        AUDIO_RECOGNITION['enabled'] = True
        AUDIO_RECOGNITION['reaper_auto_detect'] = False  # Not needed - we start immediately
        # Set runtime flags for event-driven approach
        from system_utils.metadata import set_audio_rec_runtime_enabled
        set_audio_rec_runtime_enabled(True, False)
        print("🎵 Reaper mode: Audio recognition will start after server launch")
    
    # Set up logging
    setup_logging(
        console_level=DEBUG.get("log_level", "INFO"),
        file_level="DEBUG" if DEBUG.get("log_detailed", False) else "INFO",
        console=DEBUG.get("log_to_console", True),
        log_file=DEBUG.get("log_file", "synclyrics.log"),
        log_providers=DEBUG.get("log_providers", True)
    )
    
    # Initialize runtime flags from config (if --reaper wasn't used)
    # This ensures settings.json values are respected for audio recognition
    if not args.reaper:
        from config import AUDIO_RECOGNITION
        from system_utils.metadata import set_audio_rec_runtime_enabled
        enabled = AUDIO_RECOGNITION.get("enabled", False)
        auto_detect = AUDIO_RECOGNITION.get("reaper_auto_detect", False)
        if enabled or auto_detect:
            set_audio_rec_runtime_enabled(enabled, auto_detect)
            logger.debug(f"Audio rec runtime flags from config: enabled={enabled}, auto_detect={auto_detect}")
    
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
    if win32api:
        try:

            import ctypes

            myappid = 'anshulj.synclyrics.version.1.0' # Arbitrary string

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

            win32api.SetConsoleCtrlHandler(win32_handler, True)

        except Exception:

            pass
    
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