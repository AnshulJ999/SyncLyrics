from os import path
from typing import Any
import sys  # Added sys
from pathlib import Path  # Added Path
import json 
import time
import threading
import os
import uuid
import logging

from benedict import benedict

# Get logger for this module (will be configured by logging_config.py)
logger = logging.getLogger(__name__)

# Allow overriding state file location via environment variable for HAOS persistence
# This ensures state.json is written to /config/state.json instead of /app/state.json
if "__compiled__" in globals() or getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).parent

STATE_FILE = os.getenv("SYNCLYRICS_STATE_FILE", str(ROOT_DIR / "state.json"))

DEFAULT_STATE = {
    "theme": "dark",
    "representationMethods": {
        "terminal": False
    },
}

# In-memory cache with TTL to avoid reading from disk constantly
state = None # memory cache for state to avoid reading from disk
state_cache_time = 0
STATE_CACHE_TTL = 2.0  # Cache for 2 seconds to reduce disk I/O

# Thread lock to prevent concurrent writes (cross-platform)
# CRITICAL FIX: Use RLock (re-entrant lock) instead of Lock to prevent deadlock
# When get_state() calls reset_state() which calls set_state(), the same thread
# needs to acquire the lock multiple times. RLock allows this, Lock does not.
_state_lock = threading.RLock()


def reset_state(): 
    """
    This function resets the state to the default state.
    """
    try:
        set_state(DEFAULT_STATE)
    except Exception as e:
        # Log error but don't re-raise - let get_state() handle it
        logger.error(f"Failed to reset state to default: {e}", exc_info=True)
        # Update in-memory cache even if file write fails
        global state, state_cache_time
        state = DEFAULT_STATE.copy()
        state_cache_time = time.time()
        raise  # Re-raise so caller knows it failed


def set_state(new_state: dict):
    """
    This function sets the state to the given state.
    Uses file locking and atomic writes to prevent race conditions.

    Args:
        new_state (dict): The new state.
    """

    global state, state_cache_time
    
    # Use lock to prevent concurrent writes
    with _state_lock:
        # FIX: Use unique temp filename to prevent concurrent writes from overwriting each other
        # This provides extra safety even though we have a lock (defense in depth)
        # Temp file must be in the same directory as STATE_FILE for atomic replace to work
        state_dir = os.path.dirname(STATE_FILE) if os.path.dirname(STATE_FILE) else "."
        temp_filename = f"state_{uuid.uuid4().hex}.json.tmp"
        temp_path = os.path.join(state_dir, temp_filename) if state_dir != "." else temp_filename
        
        try:
            # CRITICAL FIX: Create directory FIRST, before trying to write
            # This prevents FileNotFoundError if the directory doesn't exist
            # Moved inside try block to handle permission errors gracefully
            if os.path.dirname(STATE_FILE):
                os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            
            with open(temp_path, "w") as f:
                json.dump(new_state, f, indent=4)
            
            # Atomic replace (works on both Windows and Unix)
            
            if path.exists(STATE_FILE):
                os.remove(STATE_FILE)
            os.replace(temp_path, STATE_FILE)
            
            # Update cache immediately
            state = new_state
            state_cache_time = time.time()
        except Exception as e:
            # If write fails, try to clean up temp file
            try:
                if path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
            # Re-raise the exception so caller knows it failed
            raise


def get_state() -> dict:
    """
    This function returns the current state.
    Uses caching with TTL to avoid reading from disk constantly.

    Returns:
        dict: The current state.
    """

    global state, state_cache_time
    
    # Check cache first (with TTL)
    current_time = time.time()
    if state is not None and (current_time - state_cache_time) < STATE_CACHE_TTL:
        return state  # Return cached version (still valid)
    
    # Cache expired or doesn't exist, read from disk
    # Use lock to prevent concurrent reads during write
    with _state_lock:
        if not path.exists(STATE_FILE):
            # File doesn't exist, try to create default state
            # Wrap in try-except to handle permission/directory errors gracefully
            try:
                reset_state()
                return state
            except Exception as e:
                # If reset_state() fails (e.g., permission error), log and return default in-memory
                logger.error(f"Failed to create state file at {STATE_FILE}: {e}", exc_info=True)
                # Return default state in memory (won't persist, but app won't crash)
                state = DEFAULT_STATE.copy()
                state_cache_time = current_time
                return state
        
        # Read from disk
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                state_cache_time = current_time
                return state
        except Exception as e:
            # If read fails (corrupted file), try to reset to default
            logger.warning(f"Failed to read state file {STATE_FILE}: {e}, attempting reset")
            try:
                reset_state()
                return state
            except Exception as reset_error:
                # If reset also fails, return default in-memory
                logger.error(f"Failed to reset state file: {reset_error}", exc_info=True)
                state = DEFAULT_STATE.copy()
                state_cache_time = current_time
                return state


def set_attribute_js_notation(state: dict, attribute: str, value: Any) -> dict:
    """
    This function sets the given attribute to the given value in the given state.

    Args:
        state (dict): The state to set the attribute in.
        attribute (str): The attribute to set in js notation.
        value (Any): The value to set the attribute to.

    Returns:
        dict: The state with the attribute set to the value.
    """

    state = benedict(state, keypath_separator=".")
    state[attribute] = value
    return state.dict()


def get_attribute_js_notation(state: dict, attribute: str) -> Any:
    """
    This function returns the value of the given attribute in the given state.

    Args:
        state (dict): The state to get the attribute from.
        attribute (str): The attribute to get in js notation.

    Returns:
        Any: The value of the attribute.
    """

    state = benedict(state, keypath_separator=".")
    return state[attribute]
