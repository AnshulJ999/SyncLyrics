"""
Helper functions for system_utils package.
Pure utility functions with minimal dependencies.

Dependencies: state (for task tracking)
"""
from __future__ import annotations
import re
import time
import asyncio
from typing import Optional

from . import state
from logging_config import get_logger

logger = get_logger(__name__)


def create_tracked_task(coro):
    """
    Create a background task with automatic cleanup and error logging.
    Prevents silent failures and ensures tasks complete even if references are lost.
    """
    task = asyncio.create_task(coro)
    state._background_tasks.add(task)
    
    def cleanup(t):
        state._background_tasks.discard(t)
        try:
            t.result()
        except asyncio.CancelledError:
            pass  # Expected during shutdown
        except Exception as e:
            logger.error(f"Background task failed: {e}", exc_info=True)
    
    task.add_done_callback(cleanup)
    return task


def _cleanup_artist_image_log_throttle():
    """
    Helper function to clean up old entries from _artist_image_log_throttle.
    Prevents memory leaks by removing entries older than 5 minutes when cache exceeds 100 entries.
    This should be called periodically when the throttle is accessed.
    """
    if len(state._artist_image_log_throttle) > 100:
        current_time = time.time()
        cutoff_time = current_time - 300  # 5 minutes
        # Rebuild the throttle dict with only recent entries
        new_throttle = {
            k: v for k, v in state._artist_image_log_throttle.items()
            if v > cutoff_time
        }
        state._artist_image_log_throttle.clear()
        state._artist_image_log_throttle.update(new_throttle)


def _remove_text_inside_parentheses_and_brackets(text: str) -> str:
    """Remove text inside parentheses () and brackets []."""
    return re.sub(r"\([^)]*\)|\[[^\]]*\]", '', text)


def _normalize_track_id(artist: str, title: str) -> str:
    """
    Generates a consistent, source-agnostic track ID.
    Used to prevent UI flickering when switching sources (e.g. Windows -> Spotify Hybrid).
    """
    if not artist: 
        artist = ""
    if not title: 
        title = ""
    
    # Simple alphanumeric normalization
    norm_artist = "".join(c for c in artist.lower() if c.isalnum())
    norm_title = "".join(c for c in title.lower() if c.isalnum())
    return f"{norm_artist}_{norm_title}"


def sanitize_folder_name(name: str) -> str:
    """
    Sanitize a string to be safe for use as a folder name.
    Replaces illegal characters with underscores for cross-platform compatibility.
    
    Handles special characters like brackets [], parentheses (), and other edge cases.
    Note: Brackets [] are technically allowed in Windows folder names, but can cause
    issues in URL encoding and some file operations, so we replace them for safety.
    
    Args:
        name: String to sanitize
        
    Returns:
        Sanitized string safe for folder names
    """
    if not name:
        return "Unknown"
    
    # Replace illegal characters for Windows/Linux/Docker compatibility
    # Illegal chars: / \ : * ? " < > |
    # Also replace brackets [] and parentheses () for safety (though technically allowed)
    # This prevents issues with URL encoding, regex patterns, and some file operations
    illegal_chars = r'[<>:"/\\|?*\[\]()]'
    sanitized = re.sub(illegal_chars, '_', name)
    
    # Remove leading/trailing spaces and dots (Windows doesn't allow these)
    sanitized = sanitized.strip(' .')
    
    # Remove consecutive underscores (clean up the result)
    sanitized = re.sub(r'_+', '_', sanitized)
    
    # Truncate if too long (Windows has 260 char path limit, but we'll be conservative)
    if len(sanitized) > 100:
        sanitized = sanitized[:100]
        # If truncation happened in the middle of a word, remove trailing underscore
        sanitized = sanitized.rstrip('_')
    
    # If empty after sanitization, use fallback
    if not sanitized:
        sanitized = "Unknown"
    
    return sanitized
