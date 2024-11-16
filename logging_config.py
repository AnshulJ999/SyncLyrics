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

# Create logs directory if it doesn't exist
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

# Default log format
# DEFAULT_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
DEFAULT_FORMAT = '(%(filename)s) %(levelname)s - %(message)s'
DETAILED_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s'

def setup_logging(
    level: str = DEBUG.get("log_level", "INFO"),
    console: bool = True,
    detailed: bool = False
) -> None:
    """
    Set up logging configuration with timestamp-based files
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional log file path
        console: Whether to log to console
        detailed: Whether to use detailed format with file names and line numbers
    """
    # Create timestamp-based log file name
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = f"synclyrics_{timestamp}.log"
    
    # Reset any existing handlers
    logging.getLogger().handlers = []
    
    # Set base configuration
    logging.basicConfig(
        level=level,
        format=DETAILED_FORMAT if detailed else DEFAULT_FORMAT,
        handlers=[]
    )
    
    root_logger = logging.getLogger()
    
    # Console handler
    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter(DETAILED_FORMAT if detailed else DEFAULT_FORMAT)
        )
        root_logger.addHandler(console_handler)
    
    # File handler
    log_path = LOGS_DIR / log_file
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter(DETAILED_FORMAT))
    root_logger.addHandler(file_handler)

    # Set provider logging levels
    if DEBUG.get("log_providers", True):
        logging.getLogger('providers').setLevel(level)
    else:
        logging.getLogger('providers').setLevel(logging.WARNING)
    
    # Disable unnecessary logging
    logging.getLogger('PIL').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)

def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name"""
    return logging.getLogger(name)