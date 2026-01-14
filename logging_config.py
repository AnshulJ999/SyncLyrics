"""
Centralized logging configuration for SyncLyrics
Handles all logging setup and provides convenience functions
"""

import logging
import logging.handlers
import os
from pathlib import Path
from typing import Optional
from datetime import datetime
# from config import ROOT_DIR  <-- Removed to avoid circular dependency
import sys

if "__compiled__" in globals() or getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys.executable).parent  # FIX: use sys.executable instead of sys.argv[0]
    
    # Check if running as AppImage (read-only filesystem)
    if os.getenv("APPIMAGE"):
        # AppImage mounts as read-only - use XDG standard for logs
        xdg_data = os.getenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        DATA_DIR = Path(xdg_data) / "synclyrics"
    else:
        DATA_DIR = ROOT_DIR
else:
    ROOT_DIR = Path(__file__).parent
    DATA_DIR = ROOT_DIR

# Create logs directory if it doesn't exist
# Allow overriding via environment variable for HAOS/Docker persistence
env_logs_dir = os.getenv("SYNCLYRICS_LOGS_DIR")
if env_logs_dir:
    LOGS_DIR = Path(env_logs_dir)
else:
    LOGS_DIR = DATA_DIR / "logs"

try:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError) as e:
    # Fallback to temp directory if all else fails
    import tempfile
    LOGS_DIR = Path(tempfile.gettempdir()) / "synclyrics_logs"
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Warning: Using temp directory for logs: {LOGS_DIR}")

# Define log formats
CONSOLE_FORMAT = '(%(filename)s:%(lineno)d) %(levelname)s - %(message)s'
FILE_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'

def log_namer(default_name: str) -> str:
    """
    Custom namer for RotatingFileHandler to keep .log extension.
    Transforms: 'app.log.1' -> 'app.1.log'
    This ensures rotated log files retain the .log extension for easy opening.
    """
    # default_name comes as: /path/to/app.log.1
    # We want: /path/to/app.1.log
    
    # Split off the numeric suffix (e.g., '.1', '.2')
    base, numeric_ext = os.path.splitext(default_name)  # ('app.log', '.1')
    
    # Check if it's actually a numeric rotation suffix
    if numeric_ext and numeric_ext[1:].isdigit():
        # Remove .log from base and reconstruct with number before .log
        if base.endswith('.log'):
            base_without_log = base[:-4]  # Remove '.log'
            return f"{base_without_log}{numeric_ext}.log"
    
    return default_name

# Track if logging has been initialized
_logging_initialized = False

def setup_logging(
    console_level: str = "INFO",
    file_level: str = "DEBUG",
    console: bool = True,
    log_file: Optional[str] = None,
    log_providers: bool = True
) -> None:
    """
    Set up logging configuration with separate console and file handlers
    
    Args:
        console_level: Logging level for console output (default: INFO)
        file_level: Logging level for file output (default: INFO)
        console: Whether to enable console logging (default: True)
        log_file: Optional custom log file name
        log_providers: Whether to enable provider logging (default: True)
    """
    global _logging_initialized
    if _logging_initialized:
        return
        
    # Create timestamp-based log file name if not provided
    if not log_file:
        log_file = "app.log"
    
    # Path for the standard INFO log
    info_log_path = LOGS_DIR / log_file
    
    # Path for the DEBUG log
    debug_log_path = LOGS_DIR / "debug.log"
    
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture all levels
    
    # Clear any existing handlers
    root_logger.handlers = []
    
    # Console handler (simpler format)
    if console:
        console_handler = logging.StreamHandler(sys.stdout)  # Use stdout instead of stderr
        console_handler.setLevel(getattr(logging, console_level.upper()))
        console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT))
        root_logger.addHandler(console_handler)
    
    # --- INFO File Handler (Session-based, High Backups) ---
    # maxBytes=0 means it won't rotate by size automatically.
    # We will force rotation on startup to create a new file per session.
    info_file_handler = logging.handlers.RotatingFileHandler(
        info_log_path, 
        maxBytes=1*1024*1024, 
        backupCount=15, 
        encoding='utf-8'
    )
    info_file_handler.namer = log_namer
    
    # Force rotation if file exists and has content (New session = new file)
    if info_log_path.exists() and info_log_path.stat().st_size > 0:
        info_file_handler.doRollover()
        
    info_file_handler.setLevel(logging.INFO)
    info_file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root_logger.addHandler(info_file_handler)

    # DEBUG File handler (Detailed debugging)
    # Rotate logs: 1MB max size, keep 5 backups
    debug_file_handler = logging.handlers.RotatingFileHandler(
        debug_log_path, 
        maxBytes=1*1024*1024, 
        backupCount=15, 
        encoding='utf-8'
    )
    debug_file_handler.namer = log_namer
    
    # Force rotation if file exists and has content
    if debug_log_path.exists() and debug_log_path.stat().st_size > 0:
        debug_file_handler.doRollover()
        
    debug_file_handler.setLevel(logging.DEBUG)
    debug_file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root_logger.addHandler(debug_file_handler)
    
    # Configure specific loggers
    if log_providers:
        logging.getLogger('providers').setLevel(getattr(logging, console_level.upper()))
    else:
        logging.getLogger('providers').setLevel(logging.WARNING)
    
    # Disable unnecessary logging
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    # ADD THESE LINES TO SILENCE NOISE:
    logging.getLogger('spotipy').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('charset_normalizer').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)  # Suppress asyncio DEBUG noise
    logging.getLogger('hypercorn').setLevel(logging.INFO)  # Only show INFO+ from hypercorn
    logging.getLogger('httpx').setLevel(logging.WARNING)  # Suppress httpx DEBUG noise (if used)
    logging.getLogger('hpack').setLevel(logging.WARNING)  # Suppress HTTP/2 header compression noise
    
    # NEW: Suppress ShazamIO and Retry spam
    logging.getLogger('shazamio').setLevel(logging.WARNING)
    logging.getLogger('shazamio_core').setLevel(logging.WARNING) 
    logging.getLogger('aiohttp_retry').setLevel(logging.WARNING)
    logging.getLogger('fontTools').setLevel(logging.WARNING)  # Suppress fonttools table parsing spam
    
    # Force UTF-8 encoding for Windows console
    if sys.platform.startswith('win'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
        
    _logging_initialized = True
    
    # Log initial setup message
    root_logger.info(f"Logging initialized - Console: {console_level}")
    root_logger.info(f"Info Log file: {info_log_path}")
    root_logger.debug(f"Debug Log file: {debug_log_path}")

def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name"""
    # We do NOT call setup_logging() here anymore to avoid circular deps.
    # It must be called explicitly by the entry point.
    return logging.getLogger(name)