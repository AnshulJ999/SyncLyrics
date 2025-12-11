"""
Helper functions for system_utils package.
Pure utility functions with minimal dependencies.

Dependencies: state (for task tracking)
"""
from __future__ import annotations
import re
import time
import asyncio
import concurrent.futures
from typing import Optional, Callable, Any

from . import state
from logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Thread Executor for Blocking Operations
# =============================================================================
# Uses ThreadPoolExecutor for running blocking I/O (audio capture, device queries).
# Previously used custom DaemonThreadPoolExecutor but it broke on different Python
# versions due to reliance on private _worker attribute.
# Standard executor with shutdown(wait=False) is stable and sufficient.

from concurrent.futures import ThreadPoolExecutor

_thread_executor: Optional[ThreadPoolExecutor] = None


def _get_daemon_executor() -> ThreadPoolExecutor:
    """Get or create the thread executor for blocking operations."""
    global _thread_executor
    if _thread_executor is None:
        _thread_executor = ThreadPoolExecutor(
            max_workers=32,
            thread_name_prefix="SyncLyrics_Worker"
        )
    return _thread_executor


async def run_in_daemon_executor(func: Callable, *args: Any) -> Any:
    """
    Run a blocking function in a TRUE daemon thread executor.
    
    Daemon threads are automatically killed when the main program exits,
    preventing the app from hanging if the task (e.g. audio I/O, psutil) is stuck.
    This works even if cleanup() is skipped due to a crash.
    
    Args:
        func: Blocking function to run
        *args: Arguments to pass to the function
        
    Returns:
        Result of the function
    """
    func_name = getattr(func, '__name__', str(func))
    # TRACE log commented out - enable for debugging executor issues
    # logger.debug(f"TRACE: Submitting to executor: {func_name}")
    loop = asyncio.get_running_loop()
    executor = _get_daemon_executor()
    return await loop.run_in_executor(executor, func, *args)


def shutdown_daemon_executor():
    """Shutdown the thread executor. Call during app cleanup."""
    global _thread_executor
    if _thread_executor is not None:
        try:
            # wait=False ensures we don't block if threads are hung
            # cancel_futures=True cancels pending work
            _thread_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        _thread_executor = None


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


def _log_app_state() -> None:
    """Log key application state periodically."""
    import logging
    from state_manager import get_state, set_state
    from providers.spotify_api import get_shared_spotify_client
    
    current_time = time.time()
    
    if current_time - state._last_state_log_time < state.STATE_LOG_INTERVAL:
        return
        
    state._last_state_log_time = current_time
    
    # Lazy import to avoid circular dependency
    # get_current_song_meta_data is in metadata.py which imports helpers.py
    from .metadata import get_current_song_meta_data
    
    is_active = getattr(get_current_song_meta_data, '_is_active', True)
    last_song = getattr(get_current_song_meta_data, '_last_song', 'None')
    last_source = getattr(get_current_song_meta_data, '_last_source', 'None')

    # Update state file
    app_state = get_state()
    app_state['current_song'] = last_song
    app_state['active_source'] = last_source
    set_state(app_state)

    # --- LOGGING LOGIC ---
    # We log if the level is INFO or lower, regardless of "Debug Mode" toggle.
    if logger.isEnabledFor(logging.INFO):
        current_time_str = time.strftime("%I:%M %p - %b %d, %Y")
        
        # Base state summary
        state_summary = (
            f"\nApplication State Summary:\n"
            f"|- Time: {current_time_str}\n"
            f"|- Mode: {'Active' if is_active else 'Idle'}\n"
            f"|- Current Song: {last_song}\n"
            f"|- Active Source: {last_source}\n"
            f"|- Metadata Fetches:\n"
            f"|  |- Spotify: {state._metadata_fetch_counters['spotify']}\n"
            f"|  `- Windows Media: {state._metadata_fetch_counters['windows_media']}\n"
        )
        logger.info(state_summary)

        # Log Spotify API stats if available (this is the important one for rate limits)
        # Use shared singleton instance to get consolidated stats from entire app
        spotify_client = get_shared_spotify_client()
        if spotify_client and spotify_client.initialized:
            try:
                stats = spotify_client.get_request_stats()
                
                # Calculate requests per hour for rate limit awareness
                # Spotify's rate limit is typically ~180 requests/minute
                total_requests = stats['Total Requests']
                
                spotify_stats = (
                    "\nSpotify API Statistics:\n"
                    f"|- Total API Requests: {total_requests}\n"
                    f"|- Total Function Calls: {stats['Total Function Calls']}\n"
                    f"|- Cache Hits: {stats['Cached Responses']} ({stats['Cache Hit Rate']})\n"
                    f"|- API Calls by Endpoint:\n"
                )
            
                for endpoint, count in stats['API Calls'].items():
                    if count > 0:  # Only show endpoints that have been called
                        spotify_stats += f"|  |- {endpoint}: {count}\n"
                
                # Always show errors section if there are any errors
                total_errors = sum(stats['Errors'].values())
                if total_errors > 0:
                    spotify_stats += f"|- Errors ({total_errors} total):\n"
                    for error_type, count in stats['Errors'].items():
                        if count > 0:  # Only show error types that occurred
                            spotify_stats += f"|  |- {error_type}: {count}\n"
                
                spotify_stats += f"`- Cache Age: {stats['Cache Age']}"
                logger.info(spotify_stats)
                
            except Exception as e:
                logger.error(f"Failed to log Spotify stats: {e}")
        
        # Log thread executor health (for audio recognition stability monitoring)
        if _thread_executor is not None:
            try:
                # Standard ThreadPoolExecutor doesn't expose thread count easily
                # Just log that executor is active
                logger.info("Thread Executor: active")
            except Exception:
                pass
