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
from config import DEBUG, ROOT_DIR
import sys

# Create logs directory if it doesn't exist
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Define log formats
CONSOLE_FORMAT = '(%(filename)s:%(lineno)d) %(levelname)s - %(message)s'
FILE_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'

# Track if logging has been initialized
_logging_initialized = False

def setup_logging(
    console_level: str = DEBUG.get("log_level", "INFO"),
    file_level: str = "INFO",
    console: bool = True,
    log_file: Optional[str] = None
) -> None:
    """
    Set up logging configuration with separate console and file handlers
    
    Args:
        console_level: Logging level for console output (default: INFO)
        file_level: Logging level for file output (default: DEBUG)
        console: Whether to enable console logging (default: True)
    """
    global _logging_initialized
    if _logging_initialized:
        return
        
    # Create timestamp-based log file name if not provided
    if not log_file:
        log_file = "app.log"
    log_path = LOGS_DIR / log_file
    
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
    
    # File handler (detailed format)
    # Rotate logs: 5MB max size, keep 3 backups
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, 
        maxBytes=5*1024*1024, 
        backupCount=10, 
        encoding='utf-8'
    )
    file_handler.setLevel(getattr(logging, file_level.upper()))
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    root_logger.addHandler(file_handler)
    
    # Configure specific loggers
    if DEBUG.get("log_providers", True):
        logging.getLogger('providers').setLevel(getattr(logging, console_level.upper()))
    else:
        logging.getLogger('providers').setLevel(logging.WARNING)
    
    # Disable unnecessary logging
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    
    # Force UTF-8 encoding for Windows console
    if sys.platform.startswith('win'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
        
    _logging_initialized = True
    
    # Log initial setup message
    root_logger.info(f"Logging initialized - Console: {console_level}, File: {file_level}")
    root_logger.debug(f"Log file: {log_path}")

def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name"""
    if not _logging_initialized:
        setup_logging()
    return logging.getLogger(name)